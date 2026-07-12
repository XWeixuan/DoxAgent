from __future__ import annotations

import json

import httpx
import pytest

import doxagent.tools.providers.finnhub as finnhub_provider
from doxagent.agents import default_agent_registry
from doxagent.agents.runtime.memory.observations import ObservationService
from doxagent.models import AgentName, AgentPermissions, ResultStatus
from doxagent.settings import DoxAgentSettings
from doxagent.tools import ToolRequest, default_real_tool_registry
from doxagent.tools.market_evidence import daily_ohlcv_output_with_snapshot
from doxagent.tools.providers.alpha_vantage import (
    AlphaVantageClient,
    AlphaVantageFinancialStatementsClient,
)
from doxagent.tools.providers.anysearch import AnySearchSearchClient
from doxagent.tools.providers.base import TTLCache
from doxagent.tools.providers.bea import BeaNipaDataClient
from doxagent.tools.providers.bls import BlsTimeseriesClient
from doxagent.tools.providers.doxatlas import DoxAtlasToolClient
from doxagent.tools.providers.fed import FedFomcCalendarMaterialsClient, parse_fomc_calendar
from doxagent.tools.providers.finnhub import FinnhubPeersClient, FinnhubTradeStreamClient
from doxagent.tools.providers.fmp import FmpSectorPerformanceClient
from doxagent.tools.providers.fred import FredSeriesObservationsClient
from doxagent.tools.providers.polymarket import PolymarketMarketProbabilityClient
from doxagent.tools.providers.sec import (
    SecCompanyFactsAndFilingsClient,
    SecFilingSectionsClient,
    parse_sec_sections,
)
from doxagent.tools.providers.tavily import TavilySearchClient
from doxagent.tools.providers.twelvedata import TwelveDataDailyOhlcvClient
from doxagent.tools.providers.yfinance import (
    YFinanceDailyOhlcvClient,
    YFinanceHkBasicSnapshotClient,
)
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
        "twelvedata_api_key": "twelve-key",
        "fmp_api_key": "fmp-key",
        "finnhub_api_key": "finnhub-key",
        "tavily_api_key": "tavily-key",
        "anysearch_api_key": "anysearch-key",
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
    assert "twelvedata.daily_ohlcv" in names
    assert "yfinance.daily_ohlcv" in names
    assert "alpha.daily_ohlcv" not in names
    assert "fmp.press_releases" not in names
    assert "doxa_get_narrative_report" in names
    assert "doxa_run_narrative_research" in names
    assert "doxa_run_analysis" in names
    assert "doxa_query_analysis" in names
    assert "doxa_get_analysis" in names
    assert "doxa_query_propositions" in names
    assert "doxa_get_ignored_propositions" in names
    assert "doxa_get_social_result" in names
    assert "doxa_get_social_result_detail" in names
    assert "doxa_get_media_result" in names
    assert "doxa_get_media_result_detail" in names
    assert "doxa_get_event_source" in names
    assert "doxatlas.query" in names
    assert "doxatlas.source_lookup" in names
    assert "anysearch.search" in names


def test_real_registry_exposes_strong_tool_descriptors() -> None:
    registry = default_real_tool_registry(_settings())

    for name in registry.names():
        descriptor = registry.describe(name)
        assert descriptor is not None
        assert descriptor.description != f"{name} tool."
        assert descriptor.input_fields
        assert descriptor.business_purpose
        if name.startswith("doxa_") or name.startswith("doxatlas."):
            assert descriptor.contract_brief

    assert registry.describe("finnhub.trade_stream").concurrent_safe is False
    assert registry.describe("doxa_run_narrative_research").concurrent_safe is False
    assert registry.describe("doxa_run_narrative_research").observation_policy == "inline"
    assert registry.describe("doxa_run_analysis").concurrent_safe is False
    assert registry.describe("doxa_run_analysis").observation_policy == "inline"


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


def test_sec_company_facts_returns_failed_when_upstream_unavailable() -> None:
    client = SecCompanyFactsAndFilingsClient(_settings(), client=_json_client({}, status_code=404))

    result = client.call(
        _request("sec.company_facts_and_filings", {"ticker": "MU", "cik": "723312"})
    )

    assert result.status is ResultStatus.FAILED
    assert result.error is not None
    assert result.error.code == "not_found"


def test_sec_company_facts_keeps_full_raw_but_exposes_compact_paged_output() -> None:
    submissions = {
        "name": "Meta Platforms, Inc.",
        "tickers": ["META"],
        "exchanges": ["Nasdaq"],
        "sic": "7370",
        "sicDescription": "Services-Computer Programming",
        "filings": {
            "recent": {
                "form": ["4", "10-K", "10-Q"],
                "accessionNumber": ["a", "b", "c"],
                "filingDate": ["2026-01-03", "2026-02-01", "2026-05-01"],
                "reportDate": ["", "2025-12-31", "2026-03-31"],
                "primaryDocument": ["form4.htm", "meta-20251231.htm", "meta-20260331.htm"],
            }
        },
    }
    fact_rows = [
        {
            "start": "2025-01-01",
            "end": f"2025-{month:02d}-28",
            "val": month,
            "accn": f"accn-{month}",
            "form": "10-Q",
            "filed": f"2026-{month:02d}-01",
        }
        for month in range(1, 11)
    ]
    companyfacts = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue",
                    "description": "Revenue from contracts with customers.",
                    "units": {"USD": fact_rows},
                },
                "Assets": {
                    "label": "Assets",
                    "description": "Total assets.",
                    "units": {"USD": fact_rows},
                },
            }
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "/submissions/" in request.url.path:
            return httpx.Response(200, json=submissions)
        return httpx.Response(200, json=companyfacts)

    client = SecCompanyFactsAndFilingsClient(
        _settings(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = client.call(
        _request("sec.company_facts_and_filings", {"ticker": "META", "cik": "1326801"})
    )

    assert result.status is ResultStatus.SUCCEEDED
    assert "companyfacts" not in result.output
    assert result.output["recent_filings"][0]["form"] == "10-K"
    assert result.output["fact_directory"]["page_count"] == 2
    assert len(result.output["fact_pages"]["page_0001"]["latest_observations"]) == 1
    assert len(result.raw["companyfacts"]["facts"]["us-gaap"]["Assets"]["units"]["USD"]) == 10

    observations = ObservationService()
    index = observations.ingest(
        tool_call_id="sec",
        step=1,
        input_payload={"ticker": "META"},
        result=result,
        declared_policy="indexed",
        adapter="auto",
    )
    page_ref = "obs_sec::/fact_pages/page_0001"
    assert page_ref in index.block_refs
    page_block = observations.block_store.get_by_ref(page_ref)
    assert page_block is not None
    page_alias = observations.aliases.alias_for(page_block.block_id)
    assert page_alias is not None
    assert observations.read(page_alias)[0].content == result.output["fact_pages"]["page_0001"]
    assert max(
        len(
            json.dumps(
                block.content,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        for block in observations.block_store.blocks_for_call("sec")
    ) <= 1_200
    assert index.selected_refs[0] == "obs_sec::/fact_directory"
    assert index.selected_refs[1].startswith("obs_sec::/key_facts/rows/")
    assert index.selected_refs[2] == page_ref


def test_source_tool_descriptors_match_public_provider_parameters() -> None:
    registry = default_real_tool_registry(_settings())

    assert registry.describe("sec.filing_sections").input_fields == [
        "ticker",
        "cik",
        "form",
        "accession",
        "primary_document",
        "sections",
    ]
    assert "limit" in registry.describe("fred.series_observations").input_fields
    assert "start_year" in registry.describe("bls.timeseries").input_fields
    assert "material_type" not in registry.describe("fed.fomc_calendar_materials").input_fields
    assert "market_slug" in registry.describe("polymarket.market_probability").input_fields


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


def test_alpha_http_200_information_is_not_succeeded() -> None:
    client = AlphaVantageClient(
        _settings(),
        TTLCache(),
        "OVERVIEW",
        client=_json_client({"Information": "API rate limit reached; try again."}),
    )

    result = client.call(_request("alpha.company_overview", {"ticker": "META"}))

    assert result.status is ResultStatus.FAILED
    assert result.error is not None
    assert result.error.code == "rate_limited"


def test_alpha_multi_request_returns_partial_when_one_statement_fails() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        function = request.url.params["function"]
        if function == "BALANCE_SHEET":
            return httpx.Response(200, json={"Information": "premium endpoint unavailable"})
        return httpx.Response(
            200,
            json={"symbol": "META", "annualReports": [{"fiscalDateEnding": "2025-12-31"}]},
        )

    client = AlphaVantageFinancialStatementsClient(
        _settings(),
        TTLCache(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.call(_request("alpha.financial_statements", {"ticker": "META"}))

    assert result.status is ResultStatus.PARTIAL
    assert sorted(result.output["statements"]) == ["CASH_FLOW", "INCOME_STATEMENT"]
    assert result.output["provider_errors"][0]["function"] == "BALANCE_SHEET"
    assert len(requests) == 3


def test_bls_descriptor_years_are_translated_to_provider_body() -> None:
    requests: list[httpx.Request] = []
    client = BlsTimeseriesClient(
        _settings(),
        TTLCache(),
        client=_json_client(
            {
                "status": "REQUEST_SUCCEEDED",
                "Results": {"series": [{"data": [{"year": "2026"}]}]},
            },
            requests=requests,
        ),
    )

    result = client.call(
        _request(
            "bls.timeseries",
            {"series_ids": ["CUUR0000SA0"], "start_year": "2025", "end_year": "2026"},
        )
    )

    body = json.loads(requests[0].content)
    assert result.status is ResultStatus.SUCCEEDED
    assert body["startyear"] == "2025"
    assert body["endyear"] == "2026"


def test_fred_limit_is_sent_upstream_and_bounds_output() -> None:
    requests: list[httpx.Request] = []
    rows = [{"date": f"2026-01-{day:02d}", "value": str(day)} for day in range(1, 11)]
    client = FredSeriesObservationsClient(
        _settings(),
        TTLCache(),
        client=_json_client({"observations": rows}, requests=requests),
    )

    result = client.call(
        _request("fred.series_observations", {"series_ids": ["DGS10"], "limit": 3})
    )

    assert result.status is ResultStatus.SUCCEEDED
    assert requests[0].url.params["limit"] == "3"
    assert requests[0].url.params["sort_order"] == "desc"
    assert len(result.output["series"]["DGS10"]["observations"]) == 3
    assert len(result.raw["DGS10"]["observations"]) == 10


def test_bea_business_error_is_not_succeeded() -> None:
    client = BeaNipaDataClient(
        _settings(),
        TTLCache(),
        client=_json_client(
            {
                "BEAAPI": {
                    "Error": {
                        "APIErrorCode": "4",
                        "APIErrorDescription": "Invalid request parameter",
                    }
                }
            }
        ),
    )

    result = client.call(_request("bea.nipa_data"))

    assert result.status is ResultStatus.FAILED
    assert result.error is not None
    assert result.error.code == "upstream_provider_error"


def test_doxatlas_id_scope_validation_and_success() -> None:
    client = DoxAtlasToolClient(
        settings=_settings(),
        cache=TTLCache(),
        client=_json_client({"scope": {"event_code": "E01"}, "items": []}),
    )

    bad = client.for_tool("doxa_query_propositions").call(
        _request(
            "doxa_query_propositions",
            {"narrative_id": "n1"},
        )
    )
    good = client.for_tool("doxa_query_propositions").call(
        _request(
            "doxa_query_propositions",
            {"run_id": "run-1", "narrative_code": "N01", "event_code": "E01"},
        )
    )

    assert bad.status is ResultStatus.FAILED
    assert bad.error is not None
    assert "bare narrative_id" in bad.error.message
    assert good.status is ResultStatus.SUCCEEDED
    assert good.output["source_coordinates"]["source_kind"] == "doxatlas_source"


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
    assert result.output["source_coordinates"]["endpoint"] == "get-event-source"
    assert result.output["source_coordinates"]["http_method"] == "POST"


def test_doxatlas_narrative_report_defaults_to_agent_provenance_view() -> None:
    requests: list[httpx.Request] = []
    client = DoxAtlasToolClient(
        settings=_settings(),
        cache=TTLCache(),
        client=_json_client({"status": "ok"}, requests=requests),
    )

    result = client.for_tool("doxa_get_narrative_report").call(
        _request("doxa_get_narrative_report")
    )

    assert result.status is ResultStatus.SUCCEEDED
    assert json.loads(requests[0].read()) == {"ticker": "AAPL", "view": "agent_provenance"}


def test_doxatlas_detail_tools_require_short_code_arrays() -> None:
    requests: list[httpx.Request] = []
    client = DoxAtlasToolClient(
        settings=_settings(),
        cache=TTLCache(),
        client=_json_client({"status": "ok", "items": []}, requests=requests),
    )

    missing_codes = client.for_tool("doxa_get_media_result_detail").call(
        _request(
            "doxa_get_media_result_detail",
            {"run_id": "run-1", "narrative_code": "N01", "event_code": "E01"},
        )
    )
    good = client.for_tool("doxa_get_media_result_detail").call(
        _request(
            "doxa_get_media_result_detail",
            {
                "run_id": "run-1",
                "narrative_code": "N01",
                "event_code": "E01",
                "media_codes": ["M01"],
                "content_mode": "full",
            },
        )
    )

    assert missing_codes.status is ResultStatus.FAILED
    assert good.status is ResultStatus.SUCCEEDED
    assert requests[0].url.path == "/api/doxa-tools/get-media-result-detail"


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
        "doxa_query_analysis",
        "doxa_get_analysis",
        "doxa_query_propositions",
        "doxa_get_event_source",
        "doxa_get_social_result",
        "doxa_get_social_result_detail",
        "doxa_get_media_result",
        "doxa_get_media_result_detail",
        "doxa_get_ignored_propositions",
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
    assert result.output["source_coordinates"]["source_scope"] == "fed_fomc_calendar_materials"


def test_macro_and_market_provider_clients_parse_fixture_payloads() -> None:
    json_payload = {"observations": [{"date": "2026-01-01", "value": "4.00"}]}
    fred = FredSeriesObservationsClient(_settings(), TTLCache(), client=_json_client(json_payload))
    bls = BlsTimeseriesClient(
        _settings(),
        TTLCache(),
        client=_json_client(
            {
                "status": "REQUEST_SUCCEEDED",
                "Results": {
                    "series": [
                        {"seriesID": "CUSR0000SA0", "data": [{"year": "2026"}]}
                    ]
                },
            }
        ),
    )
    bea = BeaNipaDataClient(
        _settings(),
        TTLCache(),
        client=_json_client({"BEAAPI": {"Results": {"Data": [{"DataValue": "1"}]}}}),
    )
    twelvedata = TwelveDataDailyOhlcvClient(
        _settings(),
        TTLCache(),
        client=_json_client({"status": "ok", "values": [{"datetime": "2026-06-01"}]}),
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
        twelvedata.call(_request("twelvedata.daily_ohlcv")).output["source_coordinates"][
            "source_kind"
        ]
        == "market_data"
    )


def test_fred_filters_unallowlisted_series_without_failing_allowed_series() -> None:
    requests: list[httpx.Request] = []
    fred = FredSeriesObservationsClient(
        _settings(),
        TTLCache(),
        client=_json_client(
            {"observations": [{"date": "2026-01-01", "value": "5000"}]},
            requests=requests,
        ),
    )

    result = fred.call(
        _request(
            "fred.series_observations",
            {
                "series_ids": ["SP500", "SOX"],
                "start_date": "2026-01-01",
                "end_date": "2026-06-01",
            },
        )
    )

    assert result.status is ResultStatus.PARTIAL
    assert sorted(result.output["series"]) == ["SP500"]
    assert result.output["unsupported_series"] == ["SOX"]
    assert len(requests) == 1
    assert requests[0].url.params["series_id"] == "SP500"
    assert requests[0].url.params["observation_start"] == "2026-01-01"


def test_fred_isolates_http_400_series_and_returns_partial_success() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params["series_id"] == "GDP":
            return httpx.Response(400, json={"error": "Bad Request"})
        return httpx.Response(
            200,
            json={"observations": [{"date": "2026-01-01", "value": "4.00"}]},
        )

    fred = FredSeriesObservationsClient(
        _settings(),
        TTLCache(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = fred.call(
        _request("fred.series_observations", {"series_ids": ["GDP", "DGS10"]})
    )

    assert result.status is ResultStatus.PARTIAL
    assert sorted(result.output["series"]) == ["DGS10"]
    assert result.output["failed_series"][0]["series_id"] == "GDP"
    assert result.output["failed_series"][0]["code"] == "upstream_http_error"
    assert [request.url.params["series_id"] for request in requests] == ["GDP", "DGS10"]


def test_finnhub_trade_stream_error_message_is_never_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_capture(**_: object) -> list[dict[str, object]]:
        raise TimeoutError()

    monkeypatch.setattr(finnhub_provider, "_capture_finnhub_trades", fail_capture)
    client = FinnhubTradeStreamClient(_settings(finnhub_max_stream_seconds=30))

    result = client.call(
        _request(
            "finnhub.trade_stream",
            {"symbol": "MU", "duration_seconds": 1, "max_events": 1},
        )
    )

    assert result.status is ResultStatus.FAILED
    assert result.error is not None
    assert result.error.code == "stream_timeout"
    assert result.error.message
    assert result.error.details["provider_error"] == "TimeoutError"
    assert "TimeoutError" in result.error.details["provider_error_repr"]
    assert result.output_summary is not None


def test_twelvedata_daily_ohlcv_accepts_ticker_alias_for_symbol() -> None:
    requests: list[httpx.Request] = []
    twelvedata = TwelveDataDailyOhlcvClient(
        _settings(),
        TTLCache(),
        client=_json_client(
            {"status": "ok", "values": [{"datetime": "2026-06-01"}]},
            requests=requests,
        ),
    )

    result = twelvedata.call(
        _request("twelvedata.daily_ohlcv", {"ticker": "WDC", "outputsize": 5})
    )

    assert result.status is ResultStatus.SUCCEEDED
    assert result.output["symbol"] == "WDC"
    assert result.output["market_evidence_snapshot"]["symbol"] == "WDC"
    assert result.output["market_evidence_snapshot"]["kind"] == "daily_ohlcv_snapshot"
    assert result.output["source_coordinates"]["source_id"] == "twelvedata:daily_ohlcv:WDC"
    assert result.output["source_coordinates"]["symbol"] == "WDC"
    assert "symbol=WDC" in str(requests[0].url)


def test_daily_ohlcv_snapshot_orders_reverse_chronological_rows() -> None:
    output = daily_ohlcv_output_with_snapshot(
        {
            "symbol": "MU",
            "provider": "twelvedata",
            "interval": "1day",
            "ohlcv": [
                {
                    "datetime": "2026-06-23",
                    "close": "1051.77",
                    "high": "1060.00",
                    "low": "1040.00",
                    "volume": "30049200",
                },
                {
                    "datetime": "2026-03-02",
                    "close": "412.67",
                    "high": "420.00",
                    "low": "400.00",
                    "volume": "49046839",
                },
            ],
        },
        tool_name="twelvedata.daily_ohlcv",
    )

    snapshot = output["market_evidence_snapshot"]
    assert snapshot["start_date"] == "2026-03-02"
    assert snapshot["end_date"] == "2026-06-23"
    assert snapshot["start_close"] == 412.67
    assert snapshot["end_close"] == 1051.77
    assert snapshot["total_return_pct"] == 154.8695
    assert snapshot["latest_volume"] == 30049200


def test_yfinance_daily_ohlcv_accepts_ticker_alias_for_symbol(monkeypatch) -> None:
    class FakeIndex:
        def date(self) -> str:
            return "2026-06-01"

    class FakeRow:
        values = {
            "Open": 1.0,
            "High": 2.0,
            "Low": 0.5,
            "Close": 1.5,
            "Volume": 1000,
        }

        def get(self, key: str) -> object:
            return self.values.get(key)

        def items(self):
            return self.values.items()

    class FakeFrame:
        empty = False

        def tail(self, outputsize: int):
            return self

        def iterrows(self):
            yield FakeIndex(), FakeRow()

    class FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def history(self, *, period: str, interval: str):
            return FakeFrame()

    class FakeYFinance:
        @staticmethod
        def set_tz_cache_location(path: str) -> None:
            return None

        @staticmethod
        def Ticker(symbol: str) -> FakeTicker:
            return FakeTicker(symbol)

        @staticmethod
        def download(*args, **kwargs):
            return FakeFrame()

    def fake_import_module(name: str):
        if name == "yfinance":
            return FakeYFinance
        raise AssertionError(name)

    import importlib

    monkeypatch.setattr(importlib, "import_module", fake_import_module)
    client = YFinanceDailyOhlcvClient()

    result = client.call(_request("yfinance.daily_ohlcv", {"ticker": "STX", "outputsize": 5}))

    assert result.status is ResultStatus.SUCCEEDED
    assert result.output["symbol"] == "STX"
    assert result.output["market_evidence_snapshot"]["symbol"] == "STX"
    assert result.output["market_evidence_snapshot"]["end_close"] == 1.5
    assert result.output["source_coordinates"]["source_id"] == "yfinance:daily_ohlcv:STX"
    assert result.output["source_coordinates"]["symbol"] == "STX"


def test_fmp_finnhub_tavily_polymarket_clients_parse_fixture_payloads() -> None:
    settings = _settings()
    tavily_requests: list[httpx.Request] = []
    fmp = FmpSectorPerformanceClient(
        settings,
        TTLCache(),
        client=_json_client([{"sector": "Technology"}]),
    )
    finnhub = FinnhubPeersClient(settings, TTLCache(), client=_json_client(["MSFT", "GOOGL"]))
    tavily = TavilySearchClient(
        settings,
        TTLCache(),
        client=_json_client(
            {"results": [{"url": "https://example.com", "content": "x"}]},
            requests=tavily_requests,
        ),
    )
    polymarket = PolymarketMarketProbabilityClient(
        settings, TTLCache(), client=_json_client([{"id": "market-1"}])
    )

    assert fmp.call(_request("fmp.sector_performance")).status is ResultStatus.SUCCEEDED
    assert finnhub.call(_request("finnhub.company_peers")).status is ResultStatus.SUCCEEDED
    assert (
        tavily.call(
            _request("tavily.search", {"query": "AI semiconductors", "search_depth": "medium"})
        ).status
        is ResultStatus.SUCCEEDED
    )
    assert json.loads(tavily_requests[0].content)["search_depth"] == "basic"
    assert (
        polymarket.call(_request("polymarket.market_probability")).status is ResultStatus.SUCCEEDED
    )


def test_anysearch_client_uses_official_search_endpoint_and_env_key() -> None:
    requests: list[httpx.Request] = []
    client = AnySearchSearchClient(
        _settings(),
        TTLCache(),
        client=_json_client(
            {
                "code": 0,
                "message": "success",
                "data": {
                    "results": [{"url": "https://example.com", "title": "Example"}],
                    "metadata": {"request_id": "req_test", "total_results": 1},
                },
            },
            requests=requests,
        ),
    )

    result = client.call(
        _request(
            "anysearch.search",
            {
                "query": "Apple investor relations quarterly results",
                "max_results": 200,
                "domain": "finance",
                "content_types": ["web", "news"],
                "zone": "intl",
                "language": "en",
                "params": {"ticker": "AAPL"},
            },
        )
    )

    assert result.status is ResultStatus.SUCCEEDED
    assert result.output["provider"] == "anysearch"
    assert str(requests[0].url) == "https://api.anysearch.com/v1/search"
    assert requests[0].headers["authorization"] == "Bearer anysearch-key"
    body = json.loads(requests[0].content)
    assert body["max_results"] == 100
    assert body["domain"] == "finance"
    assert body["zone"] == "intl"
    assert body["params"] == {"ticker": "AAPL"}


def test_yfinance_tool_is_hk_only_for_us_tickers() -> None:
    client = YFinanceHkBasicSnapshotClient()

    result = client.call(_request("yfinance.hk_basic_snapshot", {"symbol": "AAPL", "market": "US"}))

    assert result.status is ResultStatus.FAILED
    assert result.error is not None
    assert result.error.code == "market_not_allowed"
