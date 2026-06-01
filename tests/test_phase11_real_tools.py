from __future__ import annotations

import json

import httpx

from doxagent.agents import default_agent_registry
from doxagent.models import AgentName, AgentPermissions, ResultStatus
from doxagent.settings import DoxAgentSettings
from doxagent.tools import ToolRequest, default_real_tool_registry
from doxagent.tools.providers.alpha_vantage import AlphaVantageClient
from doxagent.tools.providers.base import TTLCache
from doxagent.tools.providers.bea import BeaNipaDataClient
from doxagent.tools.providers.bls import BlsTimeseriesClient
from doxagent.tools.providers.doxatlas import DoxAtlasToolClient
from doxagent.tools.providers.fed import FedFomcCalendarMaterialsClient, parse_fomc_calendar
from doxagent.tools.providers.finnhub import FinnhubPeersClient
from doxagent.tools.providers.fmp import FmpPressReleasesClient
from doxagent.tools.providers.fred import FredSeriesObservationsClient
from doxagent.tools.providers.polymarket import PolymarketMarketProbabilityClient
from doxagent.tools.providers.sec import SecFilingSectionsClient, parse_sec_sections
from doxagent.tools.providers.tavily import TavilySearchClient
from doxagent.tools.providers.yfinance import YFinanceHkBasicSnapshotClient
from doxagent.tools.real import AlphaVantageClient as CompatAlphaVantageClient


def _request(tool_name: str, input_data: dict[str, object] | None = None) -> ToolRequest:
    return ToolRequest(
        tool_name=tool_name,
        ticker="AAPL",
        agent_name=AgentName.C1_FUNDAMENTAL_RESEARCH,
        input=input_data or {},
    )


def _settings(**overrides: object) -> DoxAgentSettings:
    defaults: dict[str, object] = {
        "fred_api_key": "fred-key",
        "bls_api_key": "bls-key",
        "bea_api_key": "bea-key",
        "alpha_vantage_api_key": "alpha-key",
        "fmp_api_key": "fmp-key",
        "finnhub_api_key": "finnhub-key",
        "tavily_api_key": "tavily-key",
        "doxatlas_tool_base_url": "https://doxatlas.example/api/doxa-tools",
        "doxatlas_tool_server_token": "token",
    }
    defaults.update(overrides)
    return DoxAgentSettings(**defaults)


def _json_client(
    payload: object,
    status_code: int = 200,
    requests: list[httpx.Request] | None = None,
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        return httpx.Response(status_code, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _text_client(payload: str, status_code: int = 200) -> httpx.Client:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_real_registry_registers_phase_3_2_tools() -> None:
    registry = default_real_tool_registry(_settings())

    names = set(registry.names())

    assert "sec.company_facts_and_filings" in names
    assert "sec.filing_sections" in names
    assert "fred.series_observations" in names
    assert "fed.fomc_calendar_materials" in names
    assert "finnhub.trade_stream" in names
    assert "doxa_get_narrative_report" in names
    assert "doxa_run_narrative_research" in names
    assert "doxa_run_analysis" in names
    assert "doxa_get_analysis" in names
    assert "doxa_query_propositions" in names
    assert "doxa_get_ignored_propositions" in names
    assert "doxa_get_social_result" in names
    assert "doxa_get_media_result" in names
    assert "doxa_get_event_source" in names
    assert "doxatlas.query" in names
    assert "doxatlas.source_lookup" in names


def test_real_registry_exposes_strong_tool_descriptors() -> None:
    registry = default_real_tool_registry(_settings())

    for name in registry.names():
        descriptor = registry.describe(name)
        assert descriptor is not None
        assert descriptor.description != f"{name} tool."
        assert descriptor.input_fields
        assert descriptor.business_purpose

    assert registry.describe("finnhub.trade_stream").concurrent_safe is False
    assert registry.describe("doxa_run_narrative_research").concurrent_safe is False
    assert registry.describe("doxa_run_narrative_research").compactable is False
    assert registry.describe("doxa_run_analysis").concurrent_safe is False
    assert registry.describe("doxa_run_analysis").compactable is False


def test_real_module_keeps_compatibility_exports() -> None:
    assert CompatAlphaVantageClient is AlphaVantageClient


def test_tool_registry_permission_denial_still_applies_to_real_tools() -> None:
    registry = default_real_tool_registry(_settings())

    result = registry.call(
        _request("fred.series_observations", {"series_ids": ["DGS10"]}),
        AgentPermissions(allowed_tools=[]),
    )

    assert result.status is ResultStatus.FAILED
    assert result.error is not None
    assert result.error.code == "tool_not_allowed"


def test_agent_permissions_include_real_tools_without_duplicate_commodity_tool() -> None:
    registry = default_agent_registry()
    c2 = registry.get(AgentName.C2_MACRO_RESEARCH)

    allowed = set(c2.runtime.allowed_tools)

    assert "fred.series_observations" in allowed
    assert "fred.commodity_series" not in allowed
    assert "fed.fomc_calendar_materials" in allowed
    assert "polymarket.market_probability" in allowed


def test_http_errors_are_mapped_to_tool_error() -> None:
    client = FredSeriesObservationsClient(
        _settings(),
        TTLCache(),
        client=_json_client({"error": "quota"}, status_code=429),
    )

    result = client.call(_request("fred.series_observations", {"series_ids": ["DGS10"]}))

    assert result.status is ResultStatus.FAILED
    assert result.error is not None
    assert result.error.code == "rate_limited"
    assert result.error.retryable is True


def test_doxatlas_id_scope_validation_and_success() -> None:
    client = DoxAtlasToolClient(
        settings=_settings(),
        cache=TTLCache(),
        client=_json_client({"scope": "narrative_id", "items": []}),
    )

    bad = client.for_tool("doxa_query_propositions").call(
        _request(
            "doxa_query_propositions",
            {"narrative_id": "n1", "proposition_id": "p1"},
        )
    )
    good = client.for_tool("doxa_query_propositions").call(
        _request("doxa_query_propositions", {"narrative_id": "n1"})
    )

    assert bad.status is ResultStatus.FAILED
    assert good.status is ResultStatus.SUCCEEDED
    assert good.evidence_refs[0].source_type.value == "doxatlas_source"


def test_doxatlas_payload_schema_and_alias_endpoint_are_enforced() -> None:
    requests: list[httpx.Request] = []
    client = DoxAtlasToolClient(
        settings=_settings(),
        cache=TTLCache(),
        client=_json_client({"narrative_event_id": "event-1", "items": []}, requests=requests),
    )

    result = client.for_tool("doxatlas.source_lookup").call(
        _request(
            "doxatlas.source_lookup",
            {"narrative_event_id": "event-1", "limit": 5, "unused": None},
        )
    )

    assert result.status is ResultStatus.FAILED
    assert requests == []

    result = client.for_tool("doxatlas.source_lookup").call(
        _request("doxatlas.source_lookup", {"narrative_event_id": "event-1", "limit": 5})
    )

    assert result.status is ResultStatus.SUCCEEDED
    assert requests[0].url.path == "/api/doxa-tools/get-event-source"
    assert requests[0].headers["authorization"] == "Bearer token"
    assert json.loads(requests[0].read()) == {"narrative_event_id": "event-1", "limit": 5}
    assert result.evidence_refs[0].retrieval_metadata["endpoint"] == "get-event-source"
    assert result.evidence_refs[0].retrieval_metadata["http_method"] == "POST"


def test_doxatlas_rejects_user_id_and_limit_bounds_without_request() -> None:
    requests: list[httpx.Request] = []
    client = DoxAtlasToolClient(
        settings=_settings(),
        cache=TTLCache(),
        client=_json_client({}, requests=requests),
    )

    user_id = client.for_tool("doxa_get_narrative_report").call(
        _request("doxa_get_narrative_report", {"user_id": "u1"})
    )
    limit = client.for_tool("doxa_get_event_source").call(
        _request("doxa_get_event_source", {"narrative_event_id": "event-1", "limit": 21})
    )

    assert user_id.status is ResultStatus.FAILED
    assert user_id.error is not None
    assert user_id.error.code == "tool_execution_failed"
    assert limit.status is ResultStatus.FAILED
    assert requests == []


def test_doxatlas_error_envelope_maps_provider_details() -> None:
    client = DoxAtlasToolClient(
        settings=_settings(),
        cache=TTLCache(),
        client=_json_client(
            {
                "error": {
                    "code": "TOOL_SERVER_NOT_CONFIGURED",
                    "message": "Server missing env.",
                    "details": {"env": "missing"},
                }
            },
            status_code=500,
        ),
    )

    result = client.for_tool("doxa_get_narrative_report").call(
        _request("doxa_get_narrative_report")
    )

    assert result.status is ResultStatus.FAILED
    assert result.error is not None
    assert result.error.code == "tool_server_not_configured"
    assert result.error.retryable is False
    assert result.error.details["provider_code"] == "TOOL_SERVER_NOT_CONFIGURED"
    assert result.error.details["status_code"] == 500
    assert result.error.details["provider_details"] == {"env": "missing"}


def test_doxatlas_run_tools_are_registered_but_not_default_authorized() -> None:
    registry = default_agent_registry()
    allowed_tools = {
        tool
        for name in registry.names()
        for tool in registry.get(name).runtime.allowed_tools
        if tool.startswith("doxa_run_")
    }

    assert allowed_tools == set()


def test_a1_uses_low_level_doxatlas_read_tools_only() -> None:
    definition = default_agent_registry().get(AgentName.A1_DOXATLAS_AUDIT)

    assert set(definition.runtime.allowed_tools) == {
        "doxa_query_propositions",
        "doxa_get_event_source",
        "doxa_get_social_result",
        "doxa_get_media_result",
        "doxa_get_ignored_propositions",
        "doxa_get_analysis",
    }
    assert "doxa_get_narrative_report" not in definition.runtime.allowed_tools
    assert all(not tool.startswith("doxa_run_") for tool in definition.runtime.allowed_tools)


def test_sec_section_parser_extracts_known_item_text() -> None:
    raw = """
    <html><body>
    <h1>Item 1A. Risk Factors</h1>
    The company faces supply-chain risk.
    <h1>Item 7. Management Discussion and Analysis</h1>
    Management discusses margin recovery.
    </body></html>
    """

    parsed = parse_sec_sections(raw, ["Item 1A", "Item 7", "Item 8"])

    sections = {item["section"]: item for item in parsed["sections"]}
    assert "supply-chain risk" in sections["Item 1A"]["text"]
    assert "margin recovery" in sections["Item 7"]["text"]
    assert parsed["unknowns"] == [{"field": "Item 8", "reason": "section heading not found"}]


def test_sec_filing_sections_client_uses_archive_url() -> None:
    client = SecFilingSectionsClient(
        _settings(), TTLCache(), client=_text_client("Item 7. MD&A body")
    )

    result = client.call(
        _request(
            "sec.filing_sections",
            {
                "cik": "320193",
                "accession": "0000320193-24-000123",
                "primary_document": "aapl-20240928.htm",
                "sections": ["Item 7"],
            },
        )
    )

    assert result.status is ResultStatus.SUCCEEDED
    assert result.output["sections"][0]["section"] == "Item 7"
    assert "source_url" in result.output


def test_fomc_calendar_parser_marks_official_html_source() -> None:
    html = """
    <h4>2026 FOMC Meetings</h4>
    <p>January 27-28 Statement: <a href="/monetarypolicy/fomcstatement20260128.htm">HTML</a></p>
    <p>Minutes: <a href="/monetarypolicy/fomcminutes20260218.htm">HTML</a></p>
    <h4>2025 FOMC Meetings</h4>
    """

    parsed = parse_fomc_calendar(html, "2026")

    assert "January 27-28" in parsed["calendar_text"]
    assert parsed["parser"] == "official_fed_html"
    assert parsed["unknowns"] == []


def test_fed_tool_returns_fixture_calendar() -> None:
    html = "<h4>2026 FOMC Meetings</h4><p>March 17-18 Projection Materials</p>"
    client = FedFomcCalendarMaterialsClient(_settings(), TTLCache(), client=_text_client(html))

    result = client.call(_request("fed.fomc_calendar_materials", {"year": "2026"}))

    assert result.status is ResultStatus.SUCCEEDED
    assert result.output["parser"] == "official_fed_html"
    assert result.evidence_refs[0].citation_scope == "fed_fomc_calendar_materials"


def test_macro_and_market_provider_clients_parse_fixture_payloads() -> None:
    json_payload = {"observations": [{"date": "2026-01-01", "value": "4.00"}]}
    fred = FredSeriesObservationsClient(_settings(), TTLCache(), client=_json_client(json_payload))
    bls = BlsTimeseriesClient(
        _settings(), TTLCache(), client=_json_client({"status": "REQUEST_SUCCEEDED"})
    )
    bea = BeaNipaDataClient(
        _settings(), TTLCache(), client=_json_client({"BEAAPI": {"Results": {}}})
    )
    alpha = AlphaVantageClient(
        _settings(),
        TTLCache(),
        "TIME_SERIES_DAILY",
        client=_json_client({"Time Series (Daily)": {}}),
    )

    assert (
        fred.call(_request("fred.series_observations", {"series_ids": ["DGS10"]})).status
        is ResultStatus.SUCCEEDED
    )
    assert (
        bls.call(_request("bls.timeseries", {"series_ids": ["CUSR0000SA0"]})).status
        is ResultStatus.SUCCEEDED
    )
    assert bea.call(_request("bea.nipa_data")).status is ResultStatus.SUCCEEDED
    assert (
        alpha.call(_request("alpha.daily_ohlcv")).evidence_refs[0].source_type.value
        == "market_data"
    )


def test_fmp_finnhub_tavily_polymarket_clients_parse_fixture_payloads() -> None:
    settings = _settings()
    fmp = FmpPressReleasesClient(settings, TTLCache(), client=_json_client([{"symbol": "AAPL"}]))
    finnhub = FinnhubPeersClient(settings, TTLCache(), client=_json_client(["MSFT", "GOOGL"]))
    tavily = TavilySearchClient(settings, TTLCache(), client=_json_client({"results": []}))
    polymarket = PolymarketMarketProbabilityClient(
        settings, TTLCache(), client=_json_client({"markets": []})
    )

    assert fmp.call(_request("fmp.press_releases")).status is ResultStatus.SUCCEEDED
    assert finnhub.call(_request("finnhub.company_peers")).status is ResultStatus.SUCCEEDED
    assert (
        tavily.call(_request("tavily.search", {"query": "AI semiconductors"})).status
        is ResultStatus.SUCCEEDED
    )
    assert (
        polymarket.call(_request("polymarket.market_probability")).status is ResultStatus.SUCCEEDED
    )


def test_yfinance_tool_is_hk_only_for_us_tickers() -> None:
    client = YFinanceHkBasicSnapshotClient()

    result = client.call(_request("yfinance.hk_basic_snapshot", {"symbol": "AAPL", "market": "US"}))

    assert result.status is ResultStatus.FAILED
    assert result.error is not None
    assert result.error.code == "market_not_allowed"
