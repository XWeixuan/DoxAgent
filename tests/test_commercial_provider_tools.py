from __future__ import annotations

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
from doxagent.tools.providers.ibkr import IbkrMarketHistoryClient, IbkrMarketSnapshotClient
from doxagent.tools.providers.twelvedata import TwelveDataSellSideEstimatesClient
from doxagent.tools.schema import ToolRequest


def _settings(**overrides: object) -> DoxAgentSettings:
    defaults = {
        "benzinga_api_key": "benzinga-test-key",
        "fmp_api_key": "fmp-test-key",
        "finnhub_api_key": "finnhub-test-key",
        "twelvedata_api_key": "twelve-test-key",
    }
    defaults.update(overrides)
    settings = DoxAgentSettings(**defaults)
    # IBKR settings are introduced by the owning settings/factory change.  The
    # provider is deliberately compatible with its absence while unit testing.
    object.__setattr__(settings, "ibkr_base_url", "https://ibkr.example/v1/api")
    object.__setattr__(settings, "ibkr_api_key", "ibkr-test-key")
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


def test_ibkr_raw_snapshot_and_history_keep_contract_args_and_map_http_auth_failure() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("history"):
            return httpx.Response(403, json={"error": "market data not subscribed"})
        return httpx.Response(200, json=[{"conid": 265598, "31": "201.23"}])

    settings = _settings()
    snapshot = IbkrMarketSnapshotClient(settings, TTLCache(), client=_client(handler)).call(
        _request("ibkr.market_snapshot", {"conid": "265598", "fields": ["31", "84"]})
    )
    history = IbkrMarketHistoryClient(settings, TTLCache(), client=_client(handler)).call(
        _request("ibkr.market_history", {"conid": "265598", "period": "1m", "bar": "1d"})
    )

    assert snapshot.status is ResultStatus.SUCCEEDED
    assert requests[0].url.params["conids"] == "265598"
    assert requests[0].url.params["fields"] == "31,84"
    assert requests[0].headers["authorization"] == "Bearer ibkr-test-key"
    assert history.status is ResultStatus.FAILED
    assert history.error is not None
    assert history.error.code == "entitlement_or_permission_denied"


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
