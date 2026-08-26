from __future__ import annotations

import json

import httpx

from doxagent.codex_runtime.schema import CodexD1Node
from doxagent.data_runtime.contracts import DataToolContract, DataToolContractRegistry
from doxagent.data_runtime.guidance import DataToolGuide
from doxagent.models import AgentName, ResultStatus
from doxagent.observations.segmenter import segment_cleaned_output
from doxagent.pilot.case_builder import _quality_payload
from doxagent.settings import DoxAgentSettings
from doxagent.tools.providers.base import TTLCache
from doxagent.tools.providers.macro_industry import (
    BlsImportExportPricesClient,
    CensusManufacturingOrdersClient,
)
from doxagent.tools.providers.public_records import (
    SamContractOpportunitiesClient,
    UsaSpendingAwardSearchClient,
)
from doxagent.tools.providers.tavily import TavilyExtractClient
from doxagent.tools.schema import ToolRequest


def _request(name: str, payload: dict[str, object]) -> ToolRequest:
    return ToolRequest(
        tool_name=name,
        ticker="NVDA",
        agent_name=AgentName.C3_INDUSTRY_RESEARCH,
        input=payload,
    )


def _settings(**values: object) -> DoxAgentSettings:
    return DoxAgentSettings(
        CENSUS_API_KEY="census-key",
        BLS_API_KEY="bls-key",
        SAM_API_KEY="sam-key",
        TAVILY_API_KEY="tavily-key",
        **values,
    )


def _json_client(payload: object, captured: list[httpx.Request] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _contract(tool_id: str, source: str, categories: list[str]) -> DataToolContract:
    return DataToolContract(
        canonical_tool_id=tool_id,
        mcp_name=tool_id.replace(".", "_"),
        source_name=source,
        business_categories=categories,
        description=tool_id,
        business_purpose=tool_id,
        use_when=[tool_id],
        avoid_when=[],
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_profile="json",
        observation_adapter="json",
    )


def test_c3_guide_uses_actor_supply_and_export_control_routes() -> None:
    contracts = DataToolContractRegistry(
        [
            _contract("sec.issuer_filings", "SEC EDGAR", ["company_filing"]),
            _contract("ir.official_updates", "Official issuer IR", ["company_events"]),
            _contract("tavily.search", "Tavily", ["industry"]),
            _contract("federal_register.documents", "Federal Register", ["regulatory"]),
            _contract("regulations.rulemaking_records", "Regulations.gov", ["regulatory"]),
            _contract("eia.energy_prices", "U.S. Energy Information Administration", ["industry"]),
            _contract(
                "bls.industry_producer_prices",
                "U.S. Bureau of Labor Statistics",
                ["industry"],
            ),
            _contract(
                "census.manufacturing_orders",
                "U.S. Census Bureau",
                ["industry"],
            ),
            _contract("openfda.safety_actions", "openFDA", ["regulatory"]),
            _contract("congress.legislative_actions", "Congress.gov", ["regulatory"]),
        ]
    )
    guide = DataToolGuide(contracts)
    allowed = [item.canonical_tool_id for item in contracts.all()]

    actor = guide.recommend(
        task="hyperscaler customer AI capex commitments",
        effective_tool_ids=allowed,
    )
    supply = guide.recommend(
        task="CoWoS and HBM supply chain capacity allocation",
        effective_tool_ids=allowed,
    )
    export = guide.recommend(
        task="BIS advanced computing export controls",
        effective_tool_ids=allowed,
    )

    assert actor["candidates"][0]["canonical_tool_id"] == "sec.issuer_filings"
    assert all(not item["canonical_tool_id"].startswith("eia.") for item in supply["candidates"])
    assert export["candidates"][0]["canonical_tool_id"] == "federal_register.documents"
    assert all(
        not item["canonical_tool_id"].startswith("openfda.") for item in export["candidates"]
    )


def test_c3_guide_preserves_explicit_price_and_orders_intents_with_supply_context() -> None:
    contracts = DataToolContractRegistry(
        [
            _contract("sec.issuer_filings", "SEC EDGAR", ["company_filing"]),
            _contract("ir.official_updates", "Official issuer IR", ["company_events"]),
            _contract(
                "bls.industry_producer_prices",
                "U.S. Bureau of Labor Statistics",
                ["industry"],
            ),
            _contract(
                "census.manufacturing_orders",
                "U.S. Census Bureau",
                ["industry"],
            ),
        ]
    )
    allowed = [item.canonical_tool_id for item in contracts.all()]

    result = DataToolGuide(contracts).recommend(
        task=(
            "HBM supply capacity plus semiconductor producer-price direction "
            "and manufacturing orders"
        ),
        effective_tool_ids=allowed,
        business_category="industry",
    )

    candidate_ids = {item["canonical_tool_id"] for item in result["candidates"]}
    assert {
        "bls.industry_producer_prices",
        "census.manufacturing_orders",
    }.issubset(candidate_ids)
    coverage = {item["intent"]: item for item in result["intent_coverage"]}
    assert coverage["industry_producer_prices"]["status"] == "covered"
    assert coverage["manufacturing_orders"]["status"] == "covered"
    assert coverage["supply_chain_capacity"]["status"] == "covered"


def test_guide_does_not_relabel_aggregate_tools_as_memory_product_coverage() -> None:
    contracts = DataToolContractRegistry(
        [
            _contract("sec.issuer_filings", "SEC EDGAR", ["company_filing"]),
            _contract("ir.official_updates", "Official issuer IR", ["company_events"]),
            _contract(
                "bls.industry_producer_prices",
                "U.S. Bureau of Labor Statistics",
                ["industry"],
            ),
            _contract(
                "census.manufacturing_orders",
                "U.S. Census Bureau",
                ["industry"],
            ),
        ]
    )

    result = DataToolGuide(contracts).recommend(
        task=(
            "Research DRAM contract price, NAND contract price, bit shipments, memory inventory, "
            "PC/mobile absorption, and enterprise SSD demand."
        ),
        effective_tool_ids=[item.canonical_tool_id for item in contracts.all()],
        business_category="industry",
    )

    candidate_ids = {item["canonical_tool_id"] for item in result["candidates"]}
    assert "bls.industry_producer_prices" not in candidate_ids
    assert "census.manufacturing_orders" not in candidate_ids
    coverage = {item["intent"]: item for item in result["intent_coverage"]}
    assert coverage["memory_product_metrics"]["status"] == "unsupported"
    assert "product-level memory route" in coverage["memory_product_metrics"]["discovery_fallback"]


def test_census_fine_naics_is_explicit_aggregate_partial() -> None:
    client = CensusManufacturingOrdersClient(
        _settings(),
        TTLCache(),
        client=_json_client(
            [
                [
                    "time_slot_id",
                    "cell_value",
                    "error_data",
                    "seasonally_adj",
                    "category_code",
                    "data_type_code",
                ],
                ["2026-06", "100", "", "yes", "34S", "VS"],
            ]
        ),
    )
    result = client.call(
        _request(
            "census.manufacturing_orders",
            {"naics": "334413", "measure": "shipments", "time": "2026-06"},
        )
    )

    assert result.status is ResultStatus.PARTIAL
    assert result.error and result.error.code == "census_m3_aggregate_fallback"
    assert result.output["requested_naics"] == "334413"
    assert result.output["resolved_naics_aggregate"] == "334"


def test_bls_multi_series_empty_is_partial_with_per_series_status() -> None:
    payload = {
        "status": "REQUEST_SUCCEEDED",
        "message": [],
        "Results": {
            "series": [
                {"seriesID": "EIUIR", "data": [{"year": "2025", "period": "M01", "value": "100"}]},
                {"seriesID": "EIUXX", "data": []},
            ]
        },
    }
    result = BlsImportExportPricesClient(
        _settings(), TTLCache(), client=_json_client(payload)
    ).call(_request("bls.import_export_prices", {"start_year": 2025, "end_year": 2026}))

    assert result.status is ResultStatus.PARTIAL
    assert result.error and result.error.code == "bls_partial_series_empty"
    assert [item["status"] for item in result.output["series_status"]] == ["available", "empty"]


def test_usaspending_requires_and_applies_nested_time_period() -> None:
    captured: list[httpx.Request] = []
    result = UsaSpendingAwardSearchClient(
        _settings(), TTLCache(), client=_json_client({"results": []}, captured)
    ).call(
        _request(
            "usaspending.award_search",
            {
                "filters": {
                    "award_type_codes": ["A", "B", "C", "D"],
                    "time_period": [{"start_date": "2025-08-12", "end_date": "2026-08-12"}],
                    "recipient_search_text": ["NVIDIA"],
                }
            },
        )
    )

    body = json.loads(captured[0].content)
    assert body["filters"]["time_period"][0]["start_date"] == "2025-08-12"
    assert result.status is ResultStatus.PARTIAL
    assert result.output["applied_filters"]["recipient_search_text"] == ["NVIDIA"]


def test_sam_query_is_applied_and_unrelated_success_becomes_empty_partial() -> None:
    captured: list[httpx.Request] = []
    result = SamContractOpportunitiesClient(
        _settings(),
        TTLCache(),
        client=_json_client(
            {"opportunitiesData": [{"noticeId": "1", "title": "Roof replacement"}]},
            captured,
        ),
    ).call(
        _request(
            "sam.contract_opportunities",
            {
                "posted_from": "01/01/2026",
                "posted_to": "08/12/2026",
                "query": "NVIDIA",
            },
        )
    )

    assert "q=NVIDIA" in str(captured[0].url)
    assert result.status is ResultStatus.PARTIAL
    assert result.output["record_count"] == 0


def test_tavily_rejects_error_page_and_keeps_urls_as_independent_records() -> None:
    result = TavilyExtractClient(
        _settings(),
        TTLCache(),
        client=_json_client(
            {
                "results": [
                    {
                        "url": "https://bad.example",
                        "title": "Page Not Found",
                        "raw_content": "Page Not Found " * 30,
                    },
                    {
                        "url": "https://good.example",
                        "title": "Capacity update",
                        "raw_content": ("Substantive capacity and allocation evidence. " * 50),
                    },
                ],
                "failed_results": [],
            }
        ),
    ).call(_request("tavily.extract", {"urls": ["https://bad.example", "https://good.example"]}))

    assert result.status is ResultStatus.PARTIAL
    assert [item["url"] for item in result.output["results"]] == ["https://good.example"]
    assert result.output["quality_rejected"][0]["reason"] == "error_or_interstitial_page"


def test_large_url_record_is_split_into_metadata_and_bounded_text_blocks() -> None:
    blocks = segment_cleaned_output(
        {
            "results": [
                {
                    "url": "https://example.com/report",
                    "title": "Report",
                    "content": "paragraph\n" * 10_000,
                }
            ]
        }
    )
    assert blocks[0].locator.endswith("/metadata")
    assert any(block.block_type == "text" for block in blocks[1:])
    assert max(len(str(block.content)) for block in blocks[1:]) < 7_000


def test_quality_pilot_removes_unverified_c4_placeholders() -> None:
    payload = _quality_payload(
        CodexD1Node.C3,
        {
            "base_context": {},
            "c4_pre_scan": {
                "status": "completed",
                "warnings": ["当前为预扫描阶段，未进行外部研究；来源待补充。"],
                "observation_candidates": [],
                "entity_relations": [{"关系主体": "NVDA"}],
                "future_nodes": [{"时间": "待后续研究确认"}],
            },
        },
    )

    assert payload["c4_pre_scan"]["status"] == "unavailable"
    assert payload["c4_pre_scan"]["entity_relations"] == []
    assert payload["c4_pre_scan"]["future_nodes"] == []
