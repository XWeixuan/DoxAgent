from __future__ import annotations

import httpx

from doxagent.codex_runtime.schema import CodexAgentRole, CodexD1Node
from doxagent.data_runtime.contracts import build_data_tool_contracts
from doxagent.data_runtime.guidance import DataToolGuide
from doxagent.data_runtime.policy import DataToolPolicyRegistry
from doxagent.mcp.data_server import _project_observation_content
from doxagent.models import AgentName, ResultStatus
from doxagent.settings import DoxAgentSettings
from doxagent.tools.factory import default_real_tool_registry
from doxagent.tools.providers.base import TTLCache
from doxagent.tools.providers.finnhub import _repair_common_mojibake
from doxagent.tools.providers.fred import FredSeriesObservationsClient
from doxagent.tools.providers.market import (
    IbkrFirstMarketClient,
    MarketProviderRoute,
    daily_close_fallback_input,
    passthrough_input,
)
from doxagent.tools.providers.o4_market import _project_alpha_o4
from doxagent.tools.providers.polymarket import _compact_markets
from doxagent.tools.providers.sec import _parse_form4_xml
from doxagent.tools.schema import ToolRequest, ToolResult


class _StaticClient:
    def __init__(self, result: ToolResult) -> None:
        self.result = result
        self.requests: list[ToolRequest] = []

    def call(self, request: ToolRequest) -> ToolResult:
        self.requests.append(request)
        return self.result.model_copy(update={"tool_name": request.tool_name}, deep=True)


def _request(tool_name: str, input_payload: dict[str, object] | None = None) -> ToolRequest:
    return ToolRequest(
        tool_name=tool_name,
        ticker="NVDA",
        agent_name=AgentName.O4_MARKET_TRACE,
        input=input_payload or {},
        metadata={"cutoff_at": "2026-08-18T16:32:48Z"},
    )


def test_o4a_policy_exposes_supported_surface_and_hides_unentitled_tools() -> None:
    allowed = DataToolPolicyRegistry().allowed_tools_for_ticker(
        CodexD1Node.O4_A,
        CodexAgentRole.O4,
        "NVDA",
    )
    assert {
        "market.relative_performance",
        "market.sell_side_consensus",
        "ibkr.shortability_snapshot",
        "alpha.valuation_snapshot",
        "alpha.institutional_holdings",
        "sec.insider_transactions_enriched",
        "fred.series_observations",
        "yfinance.adjusted_ohlcv",
    }.issubset(allowed)
    assert {
        "alpha.historical_options",
        "benzinga.analyst_events",
        "benzinga.market_signals",
        "fmp.valuation_snapshot",
        "ibkr.fed_funds_curve",
        "ibkr.historical_ticks",
        "ibkr.option_surface",
        "twelvedata.sell_side_estimates",
        "tavily.extract",
        "tavily.search",
        "anysearch.search",
    }.isdisjoint(allowed)

    # This restriction is node-specific and must not silently change O4-B.
    o4b_allowed = DataToolPolicyRegistry().allowed_tools_for_ticker(
        CodexD1Node.O4_B,
        CodexAgentRole.O4,
        "NVDA",
    )
    assert "ibkr.option_surface" in o4b_allowed
    assert "alpha.historical_options" in o4b_allowed
    assert {"tavily.extract", "tavily.search", "anysearch.search"}.isdisjoint(o4b_allowed)


def test_historical_quote_skips_live_provider_and_marks_daily_close_partial() -> None:
    live = _StaticClient(
        ToolResult(
            tool_name="ibkr.market_snapshot",
            status=ResultStatus.SUCCEEDED,
            output={"snapshot": {"last": 999}},
        )
    )
    fallback = _StaticClient(
        ToolResult(
            tool_name="twelvedata.daily_ohlcv",
            status=ResultStatus.SUCCEEDED,
            output={
                "ohlcv": [{"datetime": "2026-08-17", "close": 180.0}],
                "market_evidence_snapshot": {
                    "kind": "daily_ohlcv_snapshot",
                    "symbol": "NVDA",
                    "end_date": "2026-08-17",
                    "end_close": 180.0,
                    "data_quality_flags": [],
                },
            },
        )
    )
    client = IbkrFirstMarketClient(
        route_name="quote_snapshot",
        providers=(
            MarketProviderRoute("ibkr.market_snapshot", live, passthrough_input),
            MarketProviderRoute("twelvedata.daily_ohlcv", fallback, daily_close_fallback_input),
        ),
    )
    result = client.call(_request("market.quote_snapshot", {"fields": ["31", "84"]}))
    assert not live.requests
    assert result.status is ResultStatus.PARTIAL
    assert result.error and result.error.code == "daily_close_quote_fallback"
    assert result.output["price_kind"] == "daily_close_fallback"
    assert result.output["requested_fields"] == ["31", "84"]
    assert result.output["warnings"] == ["stale_daily_close_fallback"]
    assert fallback.requests[0].input["end_date"] == "2026-08-17"


def test_guide_keeps_multi_intent_coverage_instead_of_single_label() -> None:
    registry = default_real_tool_registry(DoxAgentSettings(ibkr_tws_enabled=True))
    contracts = build_data_tool_contracts(registry)
    allowed = DataToolPolicyRegistry().allowed_tools_for_ticker(
        CodexD1Node.O4_A, CodexAgentRole.O4, "NVDA"
    )
    payload = DataToolGuide(contracts).recommend(
        task=(
            "Need price benchmark peer relative return, valuation, consensus revisions, "
            "options IV skew, short interest institutional positioning, and earnings date"
        ),
        effective_tool_ids=allowed,
    )
    coverage = {item["capability"]: item["status"] for item in payload["capability_coverage"]}
    assert coverage["valuation"] == "covered"
    assert coverage["options_surface"] == "not_permitted"
    assert coverage["relative_performance"] == "covered"
    assert coverage["ownership_positioning"] == "covered"
    assert coverage["company_event_timing"] == "covered"
    assert {
        item["capability"] for item in payload["capability_gaps"]
    } >= {"options_surface"}
    candidate_ids = {item["canonical_tool_id"] for item in payload["candidates"]}
    assert "ibkr.option_surface" not in candidate_ids
    assert "alpha.historical_options" not in candidate_ids


def test_fred_vintage_parameters_are_sent_and_reported() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "realtime_start": "2026-06-30",
                "realtime_end": "2026-06-30",
                "observations": [{"date": "2026-06-01", "value": "4.1"}],
            },
        )

    settings = DoxAgentSettings(fred_api_key="test", fred_min_request_interval_seconds=0)
    client = FredSeriesObservationsClient(
        settings,
        TTLCache(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = client.call(
        _request(
            "fred.series_observations",
            {"series_ids": ["DFF"], "vintage_as_of": "2026-06-30"},
        )
    )
    assert result.status is ResultStatus.SUCCEEDED
    assert captured["realtime_start"] == "2026-06-30"
    assert captured["realtime_end"] == "2026-06-30"
    assert result.output["vintage"]["point_in_time_requested"] is True


def test_form4_parser_keeps_role_ownership_footnotes_and_codes() -> None:
    parsed = _parse_form4_xml(
        """
        <ownershipDocument>
          <periodOfReport>2026-08-01</periodOfReport>
          <reportingOwner><reportingOwnerId><rptOwnerCik>1</rptOwnerCik>
          <rptOwnerName>Jane Doe</rptOwnerName></reportingOwnerId>
          <reportingOwnerRelationship><isOfficer>1</isOfficer><officerTitle>CEO</officerTitle>
          </reportingOwnerRelationship></reportingOwner>
          <nonDerivativeTable><nonDerivativeTransaction>
          <securityTitle><value>Common Stock</value></securityTitle>
          <transactionDate><value>2026-08-01</value></transactionDate>
          <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
          <transactionAmounts><transactionShares><value>100</value></transactionShares>
          <transactionPricePerShare><value>180.5</value></transactionPricePerShare>
          <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
          </transactionAmounts><ownershipNature><directOrIndirectOwnership><value>D</value>
          </directOrIndirectOwnership></ownershipNature><footnoteId id="F1"/>
          </nonDerivativeTransaction></nonDerivativeTable>
          <footnotes><footnote id="F1">10b5-1 plan transaction.</footnote></footnotes>
        </ownershipDocument>
        """
    )
    assert parsed["reporting_owner"]["officer_title"] == "CEO"
    assert parsed["transactions"][0]["transaction_code"] == "S"
    assert parsed["transactions"][0]["direct_or_indirect"] == "D"
    assert parsed["footnotes"]["F1"] == "10b5-1 plan transaction."


def test_observation_projection_supports_direct_date_range() -> None:
    content, projection = _project_observation_content(
        [
            {"datetime": "2026-08-01", "close": 1},
            {"datetime": "2026-08-15", "close": 2},
            {"datetime": "2026-08-19", "close": 3},
        ],
        {"date_from": "2026-08-10", "date_to": "2026-08-18"},
    )
    assert content == [{"datetime": "2026-08-15", "close": 2}]
    assert projection["date_from"] == "2026-08-10"


def test_alpha_holdings_projection_is_bounded_and_keeps_summary() -> None:
    projected = _project_alpha_o4(
        "institutional_holdings",
        {
            "symbol": "NVDA",
            "total_institutional_holders": "2",
            "holdings": [
                {"holder_name": "A", "shares_held": "10", "last_reported": "2026-06-30"},
                {"holder_name": "B", "shares_held": "8", "last_reported": "2026-06-30"},
            ],
        },
        cutoff_at="2026-08-20T00:00:00Z",
        limit=1,
    )
    assert projected["summary"]["total_institutional_holders"] == 2
    assert projected["top_holdings"] == [
        {"holder_name": "A", "shares_held": 10, "last_reported": "2026-06-30"}
    ]


def test_polymarket_search_projection_pairs_outcomes_and_prices() -> None:
    rows = _compact_markets(
        {
            "events": [
                {
                    "title": "Fed decision",
                    "markets": [
                        {
                            "id": "1",
                            "question": "Will the Fed hike?",
                            "outcomes": '["Yes", "No"]',
                            "outcomePrices": '["0.25", "0.75"]',
                        }
                    ],
                }
            ]
        },
        limit=5,
    )
    assert rows[0]["event_title"] == "Fed decision"
    assert rows[0]["outcomes"] == ["Yes", "No"]
    assert rows[0]["outcome_probabilities"] == [0.25, 0.75]


def test_finnhub_repairs_control_character_mojibake() -> None:
    assert _repair_common_mojibake("NVIDIAâ\u0080\u0099s cycle") == "NVIDIA’s cycle"
