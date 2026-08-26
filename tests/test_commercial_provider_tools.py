from __future__ import annotations

from datetime import UTC, datetime

import httpx

from doxagent.models import AgentName, ResultStatus
from doxagent.settings import DoxAgentSettings
from doxagent.tools.providers.base import TTLCache
from doxagent.tools.providers.benzinga import (
    BenzingaManagementGuidanceClient,
    BenzingaShortInterestClient,
)
from doxagent.tools.providers.finnhub import (
    FinnhubCompanyNewsEventsClient,
    FinnhubInsiderTransactionsClient,
)
from doxagent.tools.providers.fmp import (
    FmpSellSideEstimatesClient,
    FmpTranscriptFallbackClient,
    FmpValuationSnapshotClient,
)
from doxagent.tools.providers.ibkr import (
    IbkrMarketHistoryClient,
    IbkrMarketSnapshotClient,
    IbkrTradeTapeClient,
)
from doxagent.tools.providers.ibkr_tws import ResolvedContract
from doxagent.tools.providers.market import (
    IbkrFirstMarketClient,
    MarketProviderRoute,
    passthrough_input,
)
from doxagent.tools.providers.twelvedata import TwelveDataSellSideEstimatesClient
from doxagent.tools.schema import ToolError, ToolRequest, ToolResult


def _settings(**overrides: object) -> DoxAgentSettings:
    defaults = {
        "benzinga_api_key": "benzinga-test-key",
        "fmp_api_key": "fmp-test-key",
        "finnhub_api_key": "finnhub-test-key",
        "twelvedata_api_key": "twelve-test-key",
        "ibkr_tws_enabled": True,
    }
    defaults.update(overrides)
    settings = DoxAgentSettings(**defaults)
    return settings


def _request(name: str, data: dict[str, object] | None = None) -> ToolRequest:
    return ToolRequest(
        tool_name=name,
        ticker="AAPL",
        agent_name=AgentName.C1_FUNDAMENTAL_RESEARCH,
        input=data or {},
    )


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


class _FakeIbkrSession:
    def __init__(self) -> None:
        self.connect_count = 0
        self.close_count = 0

    def connect(self) -> dict[str, object]:
        self.connect_count += 1
        return {"connected": True}

    def close(self) -> None:
        self.close_count += 1

    def resolve_stock(self, symbol: str, **_kwargs: object) -> list[ResolvedContract]:
        return [
            ResolvedContract(
                con_id=265598,
                symbol=symbol,
                security_type="STK",
                exchange="SMART",
                currency="USD",
                primary_exchange="NASDAQ",
            )
        ]

    def market_snapshot(self, contract: ResolvedContract) -> dict[str, object]:
        return {
            "con_id": contract.con_id,
            "symbol": contract.symbol,
            "market_data_type": 1,
            "values": {"last": 201.23, "bid": 200.1, "ask": 201.5},
        }

    def historical_bars(self, contract: ResolvedContract, **_kwargs: object) -> dict[str, object]:
        return {
            "con_id": contract.con_id,
            "symbol": contract.symbol,
            "use_rth": True,
            "bars": [
                {
                    "date": "20260807",
                    "open": 199.0,
                    "high": 202.0,
                    "low": 198.5,
                    "close": 201.23,
                    "volume": 1_000,
                    "wap": 200.4,
                    "bar_count": 500,
                }
            ],
        }

    def capture_trade_ticks(
        self, contract: ResolvedContract, **_kwargs: object
    ) -> dict[str, object]:
        return {
            "con_id": contract.con_id,
            "symbol": contract.symbol,
            "duration_seconds": 1,
            "event_count": 1,
            "events": [
                {
                    "timestamp": "2026-08-10T09:30:00+00:00",
                    "price": 201.23,
                    "size": 10,
                }
            ],
        }


def test_benzinga_business_tools_bind_symbol_and_source_coordinates() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("guidance"):
            return httpx.Response(
                200,
                json={"guidance": [{"ticker": "AAPL", "revenue_guidance_est": "100"}]},
            )
        return httpx.Response(
            200,
            json={
                "shortInterestData": {
                    "AAPL": {"data": [{"symbol": "AAPL", "shortPercentOfFloat": 2.1}]}
                }
            },
        )

    settings = _settings()
    short_interest = BenzingaShortInterestClient(
        settings, TTLCache(), client=_client(handler)
    ).call(_request("benzinga.short_interest"))
    guidance = BenzingaManagementGuidanceClient(settings, TTLCache(), client=_client(handler)).call(
        _request("benzinga.management_guidance", {"date_from": "2026-01-01"})
    )

    assert short_interest.status is ResultStatus.SUCCEEDED
    assert guidance.status is ResultStatus.SUCCEEDED
    assert requests[0].url.path == "/api/v1/shortinterest"
    assert requests[0].url.params["symbols"] == "AAPL"
    assert requests[0].url.params["token"] == "benzinga-test-key"
    assert requests[1].url.params["parameters[date_from]"] == "2026-01-01"
    assert requests[1].url.params["parameters[tickers]"] == "AAPL"
    assert guidance.output["source_coordinates"]["endpoint"] == "/api/v2.1/calendar/guidance"


def test_benzinga_http_200_entitlement_envelope_fails_stably() -> None:
    client = BenzingaShortInterestClient(
        _settings(),
        TTLCache(),
        client=_client(
            lambda _: httpx.Response(
                200, json={"status": "error", "message": "Subscription required"}
            )
        ),
    )
    result = client.call(_request("benzinga.short_interest"))
    assert result.status is ResultStatus.FAILED
    assert result.error is not None
    assert result.error.code == "entitlement_or_permission_denied"


def test_ibkr_snapshot_and_history_use_official_tws_session_and_compact_outputs() -> None:
    settings = _settings()
    session = _FakeIbkrSession()

    def session_factory(_config):
        return session

    snapshot = IbkrMarketSnapshotClient(settings, TTLCache(), session_factory=session_factory).call(
        _request("ibkr.market_snapshot", {"conid": "265598", "fields": ["31", "84"]})
    )
    history = IbkrMarketHistoryClient(settings, TTLCache(), session_factory=session_factory).call(
        _request("ibkr.market_history", {"conid": "265598", "period": "1m", "bar": "1d"})
    )

    assert snapshot.status is ResultStatus.SUCCEEDED
    assert snapshot.output["snapshot"] == {"bid": 200.1, "last": 201.23}
    assert snapshot.output["transport"] == "official_tws_socket"
    assert history.status is ResultStatus.SUCCEEDED
    assert history.output["bars"][0]["close"] == 201.23
    assert history.output["as_of"] == "20260807"
    assert session.connect_count == 2
    assert session.close_count == 2


def test_ibkr_snapshot_keeps_delayed_equivalents_for_requested_canonical_fields() -> None:
    session = _FakeIbkrSession()
    session.market_snapshot = lambda contract: {
        "con_id": contract.con_id,
        "symbol": contract.symbol,
        "market_data_type": 3,
        "values": {
            "delayed_last": 201.23,
            "delayed_close": 199.5,
            "delayed_volume": 12345,
            "delayed_high": 203.0,
        },
    }
    result = IbkrMarketSnapshotClient(
        _settings(),
        TTLCache(),
        session_factory=lambda _config: session,
    ).call(
        _request(
            "ibkr.market_snapshot",
            {"symbol": "MU", "fields": ["last", "close", "volume"]},
        )
    )

    assert result.status is ResultStatus.SUCCEEDED
    assert result.output["snapshot"] == {
        "delayed_last": 201.23,
        "delayed_close": 199.5,
        "delayed_volume": 12345,
    }


def test_ibkr_trade_tape_uses_official_tws_session() -> None:
    session = _FakeIbkrSession()
    result = IbkrTradeTapeClient(
        _settings(),
        TTLCache(),
        session_factory=lambda _config: session,
    ).call(
        _request(
            "ibkr.trade_tape",
            {"symbol": "MU", "duration_seconds": 1, "max_events": 5},
        )
    )

    assert result.status is ResultStatus.SUCCEEDED
    assert result.output["event_count"] == 1
    assert result.output["events"][0]["price"] == 201.23


class _FixedResultClient:
    def __init__(self, result: ToolResult) -> None:
        self.result = result
        self.calls: list[str] = []

    def call(self, request: ToolRequest) -> ToolResult:
        self.calls.append(request.tool_name)
        return self.result.model_copy(update={"tool_name": request.tool_name}, deep=True)


def test_provider_neutral_market_route_prefers_ibkr_and_records_fallback() -> None:
    ibkr = _FixedResultClient(
        ToolResult(
            tool_name="ibkr.market_history",
            status=ResultStatus.FAILED,
            error=ToolError(code="market_data_unavailable", message="no data"),
        )
    )
    fallback = _FixedResultClient(
        ToolResult(
            tool_name="twelvedata.daily_ohlcv",
            status=ResultStatus.SUCCEEDED,
            output={
                "provider": "twelvedata",
                "source_coordinates": {"provider": "twelvedata"},
                "ohlcv": [{"datetime": "2026-08-08", "close": "201.23"}],
            },
        )
    )
    route = IbkrFirstMarketClient(
        route_name="daily_ohlcv",
        providers=(
            MarketProviderRoute("ibkr.market_history", ibkr, passthrough_input),
            MarketProviderRoute("twelvedata.daily_ohlcv", fallback, passthrough_input),
        ),
    )

    result = route.call(_request("market.daily_ohlcv", {"symbol": "MU"}))

    assert result.status is ResultStatus.SUCCEEDED
    assert result.tool_name == "market.daily_ohlcv"
    assert result.output["provider"] == "twelvedata"
    assert result.output["provider_routing"]["fallback_used"] is True
    assert result.output["provider_routing"]["selected_tool"] == "twelvedata.daily_ohlcv"
    assert ibkr.calls == ["ibkr.market_history"]
    assert fallback.calls == ["twelvedata.daily_ohlcv"]


def test_provider_neutral_trade_route_rejects_empty_partial_payloads() -> None:
    empty_partial = _FixedResultClient(
        ToolResult(
            tool_name="finnhub.trade_stream",
            status=ResultStatus.PARTIAL,
            output={"events": [], "event_count": 0},
            error=ToolError(code="empty_stream_sample", message="no trades"),
        )
    )
    route = IbkrFirstMarketClient(
        route_name="trade_tape",
        providers=(
            MarketProviderRoute("ibkr.trade_tape", empty_partial, passthrough_input),
            MarketProviderRoute("finnhub.trade_stream", empty_partial, passthrough_input),
        ),
    )

    result = route.call(_request("market.trade_tape", {"symbol": "MU"}))

    assert result.status is ResultStatus.FAILED
    assert result.output["provider_routing"]["selected_tool"] is None
    assert all(
        attempt["usable"] is False for attempt in result.output["provider_routing"]["attempts"]
    )


def test_fmp_composite_tools_return_partial_with_stable_endpoint_provenance() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("price-target-summary"):
            return httpx.Response(403, json={"message": "Subscription required"})
        return httpx.Response(200, json=[{"symbol": "AAPL", "targetConsensus": 200}])

    client = FmpSellSideEstimatesClient(_settings(), TTLCache(), client=_client(handler))
    result = client.call(_request("fmp.sell_side_estimates", {"period": "quarter"}))
    assert result.status is ResultStatus.PARTIAL
    assert result.output["sell_side_estimates"]["analyst_estimates"][0]["symbol"] == "AAPL"
    assert result.output["source_coordinates"]["source_scope"] == "fmp_sell_side_estimates"
    assert result.error is not None
    assert result.error.details["provider_errors"][-1]["endpoint"] == "price_target_summary"


def test_fmp_valuation_and_transcript_tools_bind_only_their_declared_endpoints() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json=[{"symbol": "AAPL"}])

    settings = _settings()
    valuation = FmpValuationSnapshotClient(settings, TTLCache(), client=_client(handler)).call(
        _request("fmp.valuation_snapshot")
    )
    transcript = FmpTranscriptFallbackClient(settings, TTLCache(), client=_client(handler)).call(
        _request("fmp.transcript_fallback", {"year": "2025", "quarter": "4"})
    )
    assert valuation.status is ResultStatus.SUCCEEDED
    assert transcript.status is ResultStatus.SUCCEEDED
    assert paths[:4] == [
        "/stable/profile",
        "/stable/enterprise-values",
        "/stable/key-metrics-ttm",
        "/stable/ratios-ttm",
    ]
    assert paths[4] == "/stable/earning-call-transcript"


def test_twelve_data_estimates_and_finnhub_composites_have_bounded_endpoint_sets() -> None:
    twelve_paths: list[str] = []
    finnhub_paths: list[str] = []

    def twelve_handler(request: httpx.Request) -> httpx.Response:
        twelve_paths.append(request.url.path)
        label = (
            "earnings_estimate"
            if request.url.path.endswith("earnings_estimate")
            else "revenue_estimate"
        )
        return httpx.Response(
            200,
            json={
                "meta": {"symbol": "AAPL", "currency": "USD"},
                label: [{"date": "2026-09-30", "avg_estimate": 1.0}],
            },
        )

    def finnhub_handler(request: httpx.Request) -> httpx.Response:
        finnhub_paths.append(request.url.path)
        if request.url.path.endswith("insider-transactions"):
            return httpx.Response(
                200,
                json={"symbol": "AAPL", "data": [{"name": "Officer", "change": 1}]},
            )
        if request.url.path.endswith("company-news"):
            return httpx.Response(200, json=[{"related": "AAPL", "headline": "Update"}])
        return httpx.Response(
            200,
            json={"symbol": "AAPL", "data": [{"period": "2026-03-31", "actual": 1}]},
        )

    settings = _settings()
    estimates = TwelveDataSellSideEstimatesClient(
        settings, TTLCache(), client=_client(twelve_handler)
    ).call(_request("twelvedata.sell_side_estimates"))
    insiders = FinnhubInsiderTransactionsClient(
        settings, TTLCache(), client=_client(finnhub_handler)
    ).call(_request("finnhub.insider_transactions"))
    news = FinnhubCompanyNewsEventsClient(
        settings, TTLCache(), client=_client(finnhub_handler)
    ).call(
        _request(
            "finnhub.company_news_events", {"date_from": "2026-01-01", "date_to": "2026-01-31"}
        )
    )
    assert estimates.status is ResultStatus.SUCCEEDED
    assert twelve_paths == ["/earnings_estimate", "/revenue_estimate"]
    assert insiders.status is ResultStatus.SUCCEEDED
    assert insiders.output["insider_transactions"]["records"][0] == {
        "name": "Officer",
        "change": 1,
    }
    assert news.status is ResultStatus.SUCCEEDED
    assert finnhub_paths == [
        "/api/v1/stock/insider-transactions",
        "/api/v1/company-news",
        "/api/v1/stock/earnings",
    ]
    assert news.output["source_coordinates"]["source_scope"] == "finnhub_company_news_events"
    news_record = news.output["company_news_events"]["company_news"]["records"][0]
    earnings_record = news.output["company_news_events"]["earnings"]["records"][0]
    assert set(news_record) == {"related", "headline"}
    assert set(earnings_record) == {"period", "actual"}


def test_finnhub_company_news_soft_clamps_and_filters_cutoff() -> None:
    seen_to: list[str] = []
    before = int(datetime(2026, 8, 20, 10, tzinfo=UTC).timestamp())
    after = int(datetime(2026, 8, 21, 10, tzinfo=UTC).timestamp())

    def handler(request: httpx.Request) -> httpx.Response:
        seen_to.append(request.url.params["to"])
        return httpx.Response(
            200,
            json=[
                {
                    "related": "AAPL",
                    "headline": "AAPL earnings update",
                    "datetime": after,
                    "url": "https://example.com/after",
                },
                {
                    "related": "AAPL",
                    "headline": "AAPL earnings update before cutoff",
                    "datetime": before,
                    "url": "https://example.com/before",
                },
            ],
        )

    request = _request(
        "finnhub.company_news_events",
        {
            "date_from": "2026-08-01",
            "date_to": "2026-08-31",
            "include_earnings": False,
        },
    ).model_copy(update={"metadata": {"cutoff_at": "2026-08-20T12:00:00Z"}})
    result = FinnhubCompanyNewsEventsClient(_settings(), TTLCache(), client=_client(handler)).call(
        request
    )

    assert result.status is ResultStatus.PARTIAL
    assert seen_to == ["2026-08-20"]
    records = result.output["company_news_events"]["company_news"]["records"]
    assert [item["url"] for item in records] == ["https://example.com/before"]
    assert result.output["cutoff_filter"]["filtered_record_count"] == 1
