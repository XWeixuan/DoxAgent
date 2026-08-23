"""Versioned contracts shared by direct data execution and Data MCP."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, model_validator

from doxagent.codex_runtime.schema import (
    CODEX_D1_WORKFLOW_VERSION,
    CodexResearchAgentRole,
    CodexResearchNode,
    CodexWorkflowVersion,
    ResearchLane,
)
from doxagent.models import ResultStatus
from doxagent.tools.registry import ToolDescriptor, ToolRegistry

DATA_TOOL_CONTRACT_VERSION = "1.0"
DATA_MCP_LAUNCH_SCHEMA_VERSION = "data_mcp_launch/1.0"
DATA_MCP_RESULT_SCHEMA_VERSION = "data_mcp_result/1.0"

# Codex has native web search. Keep these clients available to the legacy
# direct ToolRegistry path, but never turn their namespaces into Data MCP tools.
# Namespace filtering also prevents a newly registered provider endpoint from
# becoming agent-visible merely because a role's legacy allowlist includes it.
DATA_MCP_EXCLUDED_TOOL_PREFIXES = ("anysearch.", "tavily.")


def is_data_mcp_excluded_tool(tool_id: str) -> bool:
    return tool_id.startswith(DATA_MCP_EXCLUDED_TOOL_PREFIXES)


class DataRuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DataAvailability(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class DataToolContract(DataRuntimeModel):
    canonical_tool_id: str
    mcp_name: str
    source_name: str
    business_categories: list[str]
    description: str
    business_purpose: str
    use_when: list[str]
    avoid_when: list[str]
    required_context: list[str] = Field(default_factory=lambda: ["ticker"])
    input_schema: dict[str, Any]
    output_profile: str
    fallback_tool_ids: list[str] = Field(default_factory=list)
    freshness: str = "provider_current"
    point_in_time_safe: bool = False
    read_only: bool = True
    contract_version: Literal["1.0"] = "1.0"
    availability: DataAvailability = DataAvailability.AVAILABLE
    availability_reason: str | None = None
    concurrent_safe: bool = True
    observation_policy: Literal["inline", "indexed", "recomputable"] = "inline"
    observation_adapter: Literal[
        "auto",
        "json",
        "search_results",
        "text",
        "table",
        "time_series",
        "doxatlas",
    ] = "auto"

    @model_validator(mode="after")
    def validate_mcp_contract(self) -> DataToolContract:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", self.mcp_name):
            raise ValueError(f"invalid MCP tool name: {self.mcp_name}")
        if self.input_schema.get("type") != "object":
            raise ValueError("Data MCP input_schema must be an object schema")
        if self.input_schema.get("additionalProperties") is not False:
            raise ValueError("Data MCP input_schema must forbid unknown top-level fields")
        Draft202012Validator.check_schema(self.input_schema)
        return self

    @property
    def exposed(self) -> bool:
        return self.read_only and self.availability is not DataAvailability.UNAVAILABLE

    def mcp_description(self) -> str:
        use = "；".join(self.use_when[:2])
        avoid = "；".join(self.avoid_when[:1])
        return (
            f"[{self.source_name}] {self.description} "
            f"Use when: {use}. Avoid when: {avoid}. "
            f"Returns cleaned observations with attempt-local O# citations."
        )


class DataToolContractRegistry:
    def __init__(self, contracts: list[DataToolContract]) -> None:
        self._by_id = {item.canonical_tool_id: item for item in contracts}
        self._by_mcp_name = {item.mcp_name: item for item in contracts}
        if len(self._by_id) != len(contracts) or len(self._by_mcp_name) != len(contracts):
            raise ValueError("duplicate Data Tool contract id or MCP name")

    def all(self) -> list[DataToolContract]:
        return [self._by_id[key].model_copy(deep=True) for key in sorted(self._by_id)]

    def get(self, canonical_tool_id: str) -> DataToolContract | None:
        value = self._by_id.get(canonical_tool_id)
        return value.model_copy(deep=True) if value else None

    def get_by_mcp_name(self, mcp_name: str) -> DataToolContract | None:
        value = self._by_mcp_name.get(mcp_name)
        return value.model_copy(deep=True) if value else None

    def require(self, canonical_tool_id: str) -> DataToolContract:
        value = self.get(canonical_tool_id)
        if value is None:
            raise KeyError(canonical_tool_id)
        return value


class DataMcpLaunchSpec(DataRuntimeModel):
    schema_version: Literal["data_mcp_launch/1.0"] = "data_mcp_launch/1.0"
    workflow_version: CodexWorkflowVersion = CODEX_D1_WORKFLOW_VERSION
    research_lane: ResearchLane = ResearchLane.LEGACY_DOCUMENT1
    run_id: str
    node_id: CodexResearchNode
    node_attempt_id: str
    agent_role: CodexResearchAgentRole
    ticker: str
    cutoff_at: datetime
    enabled_tool_ids: list[str]
    observation_projection_root: str


class DataExecutionContext(DataRuntimeModel):
    workflow_version: CodexWorkflowVersion = CODEX_D1_WORKFLOW_VERSION
    research_lane: ResearchLane = ResearchLane.LEGACY_DOCUMENT1
    run_id: str
    node_id: CodexResearchNode
    node_attempt_id: str
    agent_role: CodexResearchAgentRole
    ticker: str
    cutoff_at: datetime
    enabled_tool_ids: frozenset[str]


class DataObservationView(DataRuntimeModel):
    """Agent-facing Observation content; runtime integrity fields stay private."""

    alias: str
    title: str
    content: Any
    source: dict[str, str]


class DataPackLocator(DataRuntimeModel):
    root: str
    manifest_path: str
    selected_path: str
    catalog_path: str


class DataDelivery(DataRuntimeModel):
    mode: Literal["inline", "pack"]
    observations: list[DataObservationView] = Field(default_factory=list)
    pack: DataPackLocator | None = None
    total_blocks: int = Field(ge=0)
    inline_chars: int = Field(ge=0)


class DataProvenance(DataRuntimeModel):
    provider: str
    retrieved_at: datetime
    published_at: str | None = None
    as_of: str | None = None
    method_version: str


class DataMcpResult(DataRuntimeModel):
    schema_version: Literal["data_mcp_result/1.0"] = "data_mcp_result/1.0"
    canonical_tool_id: str
    tool_call_id: str
    node_attempt_id: str
    execution_status: ResultStatus
    availability: DataAvailability
    summary: str
    delivery: DataDelivery
    provenance: DataProvenance
    warnings: list[str] = Field(default_factory=list)
    error: dict[str, Any] | None = None


_SOURCE_NAMES = {
    "alpha": "Alpha Vantage",
    "anysearch": "AnySearch",
    "bea": "U.S. Bureau of Economic Analysis",
    "benzinga": "Benzinga",
    "bls": "U.S. Bureau of Labor Statistics",
    "census": "U.S. Census Bureau",
    "congress": "Congress.gov",
    "doxa": "DoxAtlas",
    "doxatlas": "DoxAtlas",
    "eia": "U.S. Energy Information Administration",
    "fed": "Federal Reserve",
    "federal_register": "Federal Register",
    "finnhub": "Finnhub",
    "fmp": "Financial Modeling Prep",
    "fred": "Federal Reserve Economic Data",
    "ibkr": "Interactive Brokers",
    "ir": "Official issuer IR",
    "monitoring": "DoxAgent Monitoring Store",
    "openfda": "openFDA",
    "polymarket": "Polymarket",
    "regulations": "Regulations.gov",
    "sam": "SAM.gov",
    "sec": "SEC EDGAR",
    "tavily": "Tavily",
    "twelvedata": "Twelve Data",
    "usaspending": "USAspending.gov",
    "yfinance": "Yahoo Finance",
}

_UNAVAILABLE_PREFIXES = {
    "benzinga.": "provider entitlement is not available",
    "fmp.": "provider entitlement is not available",
}

_REQUIRED_FIELDS: dict[str, list[str]] = {
    "congress.legislative_actions": ["resource"],
    "federal_register.documents": [],
    "ir.official_feed_discovery": ["url", "official_domains"],
    "ir.official_updates": ["url", "official_domains"],
    "openfda.approval_milestones": ["dataset"],
    "openfda.safety_actions": ["dataset"],
    "regulations.rulemaking_records": ["mode"],
    "sam.contract_opportunities": ["posted_from", "posted_to"],
    "tavily.extract": ["urls"],
    "usaspending.award_detail": [],
    "usaspending.award_search": ["filters"],
}

_FALLBACKS: dict[str, list[str]] = {
    "sec.company_financials": ["sec.company_facts_and_filings"],
    "sec.filing_content": ["sec.filing_sections"],
    "sec.management_disclosures": ["sec.filing_content"],
    "sec.material_contracts_projects": ["sec.filing_content"],
    "twelvedata.sell_side_estimates": ["fmp.sell_side_estimates"],
    "twelvedata.daily_ohlcv": ["yfinance.daily_ohlcv"],
}

_ARRAY_FIELDS = {
    "content_types",
    "cik_values",
    "keywords",
    "concepts",
    "conids",
    "fields",
    "forms",
    "media_codes",
    "metric_keys",
    "official_domains",
    "proposition_codes",
    "rss_urls",
    "sections",
    "search_terms",
    "series_ids",
    "social_codes",
    "source_codes",
    "source_filters",
    "symbols",
    "urls",
    "usernames",
}
_INTEGER_FIELDS = {
    "capsule_limit",
    "limit",
    "max_results",
    "outputsize",
    "max_events",
    "max_contracts",
    "min_days",
    "max_days",
    "number_of_ticks",
    "page",
    "page_size",
    "preview_chars",
    "start_year",
    "end_year",
    "year",
}
_NUMBER_FIELDS = {"duration_seconds"}
_BOOLEAN_FIELDS = {
    "annual_average",
    "calculations",
    "catalog",
    "enabled",
    "force",
    "include_facts",
    "include_exhibits",
    "limit_per_form",
    "include_reasoning",
    "include_source_propositions",
    "outside_rth",
    "include_earnings",
    "reuse_recent",
}
_OBJECT_FIELDS = {"filters", "params"}


def safe_mcp_name(canonical_tool_id: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "_", canonical_tool_id)
    if not name or not name[0].isalpha():
        name = f"data_{name}"
    return name[:128]


def build_data_tool_contracts(registry: ToolRegistry) -> DataToolContractRegistry:
    contracts: list[DataToolContract] = []
    for tool_id in registry.names():
        if is_data_mcp_excluded_tool(tool_id):
            continue
        descriptor = registry.describe(tool_id)
        if descriptor is None:
            continue
        contracts.append(_contract_from_descriptor(descriptor))
    return DataToolContractRegistry(contracts)


def _contract_from_descriptor(descriptor: ToolDescriptor) -> DataToolContract:
    namespace = descriptor.name.split(".", maxsplit=1)[0]
    prefix = namespace if namespace in _SOURCE_NAMES else namespace.split("_", maxsplit=1)[0]
    availability = DataAvailability(descriptor.availability)
    reason = descriptor.availability_reason
    for blocked_prefix, blocked_reason in _UNAVAILABLE_PREFIXES.items():
        if descriptor.name.startswith(blocked_prefix):
            availability = DataAvailability.UNAVAILABLE
            reason = blocked_reason
            break
    schema = descriptor.input_schema or _build_input_schema(descriptor)
    source_name = descriptor.source_name or _SOURCE_NAMES.get(prefix, prefix.upper())
    purpose = descriptor.business_purpose or descriptor.description
    categories = descriptor.business_categories or _infer_categories(descriptor.name, purpose)
    use_when = descriptor.use_when or [purpose]
    avoid_when = descriptor.avoid_when or [
        "the requested fact falls outside this source's governed endpoint contract"
    ]
    return DataToolContract(
        canonical_tool_id=descriptor.name,
        mcp_name=safe_mcp_name(descriptor.name),
        source_name=source_name,
        business_categories=categories,
        description=descriptor.description,
        business_purpose=purpose,
        use_when=use_when,
        avoid_when=avoid_when,
        input_schema=schema,
        output_profile=descriptor.output_profile or descriptor.observation_adapter,
        fallback_tool_ids=descriptor.fallback_tool_ids or _FALLBACKS.get(descriptor.name, []),
        freshness=descriptor.freshness or "provider_current",
        point_in_time_safe=descriptor.point_in_time_safe,
        read_only=descriptor.read_only and descriptor.concurrent_safe,
        availability=availability,
        availability_reason=reason,
        concurrent_safe=descriptor.concurrent_safe,
        observation_policy=descriptor.observation_policy,
        observation_adapter=descriptor.observation_adapter,
    )


def _build_input_schema(descriptor: ToolDescriptor) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for field_name in descriptor.input_fields:
        if field_name in {"ticker", "symbol"}:
            continue
        properties[field_name] = _field_schema(field_name)
    required = [
        field_name
        for field_name in _REQUIRED_FIELDS.get(descriptor.name, [])
        if field_name in properties
    ]
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    if descriptor.name == "federal_register.documents":
        params_schema = properties.get("params")
        if params_schema is not None:
            params_schema["minProperties"] = 1
        schema["anyOf"] = [
            {"required": ["document_number"]},
            {"required": ["params"]},
        ]
    if descriptor.name == "census.manufacturing_orders":
        schema["required"] = ["naics"]
        properties["measure"] = {
            "type": "string",
            "enum": ["shipments", "inventories", "new_orders", "unfilled_orders"],
        }
    if descriptor.name == "bea.industry_accounts":
        properties["dataset"] = {
            "type": "string",
            "enum": [
                "GDPByIndustry",
                "InputOutput",
                "UnderlyingGDPByIndustry",
                "FixedAssets",
            ],
        }
    if descriptor.name == "usaspending.award_search":
        properties["filters"] = {
            "type": "object",
            "properties": {
                "award_type_codes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 100,
                },
                "time_period": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "start_date": {"type": "string", "format": "date"},
                            "end_date": {"type": "string", "format": "date"},
                        },
                        "required": ["start_date", "end_date"],
                        "additionalProperties": False,
                    },
                    "minItems": 1,
                    "maxItems": 20,
                },
                "recipient_search_text": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 300},
                    "maxItems": 20,
                },
                "agencies": {"type": "array", "maxItems": 20},
                "naics_codes": {"type": "object"},
                "psc_codes": {"type": "object"},
            },
            "required": ["award_type_codes", "time_period"],
            "additionalProperties": True,
            "maxProperties": 40,
        }
    if descriptor.name == "usaspending.award_detail":
        schema["anyOf"] = [
            {"required": ["generated_internal_id"]},
            {"required": ["award_id"]},
        ]
    if descriptor.name == "regulations.rulemaking_records":
        properties["mode"] = {"type": "string", "enum": ["documents", "dockets"]}
    if descriptor.name == "congress.legislative_actions":
        properties["resource"] = {
            "type": "string",
            "enum": ["bill", "committee-report", "hearing"],
        }
    metric_enums = {
        "bls.industry_producer_prices": [
            "final_demand_ppi",
            "processed_goods_ppi",
            "semiconductor_manufacturing_ppi",
            "electronic_computer_manufacturing_ppi",
        ],
        "bls.import_export_prices": [
            "import_all_commodities",
            "export_all_commodities",
        ],
        "eia.energy_prices": [
            "retail_gasoline",
            "industrial_electricity_price",
        ],
        "eia.energy_supply_operations": [
            "us_crude_oil_inventory",
            "industrial_electricity_sales",
        ],
    }
    if descriptor.name in metric_enums:
        properties["metric_keys"] = {
            "type": "array",
            "items": {"type": "string", "enum": metric_enums[descriptor.name]},
            "minItems": 1,
            "maxItems": 100,
        }
    dataset_enums = {
        "openfda.approval_milestones": ["device/510k", "device/pma", "drug/drugsfda"],
        "openfda.safety_actions": [
            "device/enforcement",
            "drug/enforcement",
            "device/event",
            "drug/event",
        ],
    }
    if descriptor.name in dataset_enums:
        properties["dataset"] = {
            "type": "string",
            "enum": dataset_enums[descriptor.name],
        }
    return schema


def _field_schema(field_name: str) -> dict[str, Any]:
    if field_name == "seasonally_adjusted":
        return {
            "anyOf": [
                {"type": "boolean"},
                {"type": "string", "enum": ["yes", "no", "both", "true", "false"]},
            ]
        }
    if field_name in _ARRAY_FIELDS or field_name.endswith("_codes"):
        return {"type": "array", "items": {"type": "string"}, "maxItems": 100}
    if field_name in _INTEGER_FIELDS:
        schema: dict[str, Any] = {"type": "integer"}
        if field_name in {"limit", "max_results", "outputsize", "page", "page_size"}:
            schema["minimum"] = 1
        return schema
    if field_name in _NUMBER_FIELDS:
        return {"type": "number"}
    if field_name in _BOOLEAN_FIELDS:
        return {"type": "boolean"}
    if field_name in _OBJECT_FIELDS:
        return {
            "type": "object",
            "maxProperties": 40,
            "additionalProperties": {
                "type": ["string", "number", "integer", "boolean", "array", "null"]
            },
        }
    return {"type": "string", "maxLength": 2_000}


def _infer_categories(tool_id: str, purpose: str) -> list[str]:
    haystack = f"{tool_id} {purpose}".lower()
    categories: list[str] = []
    for token, category in (
        ("filing", "company_filing"),
        ("financial", "company_financials"),
        ("estimate", "sell_side_consensus"),
        ("guidance", "management_guidance"),
        ("contract", "contract_order"),
        ("award", "contract_order"),
        ("macro", "macro"),
        ("inflation", "macro"),
        ("labor", "macro"),
        ("industry", "industry"),
        ("energy", "industry"),
        ("regulat", "regulatory"),
        ("approval", "regulatory"),
        ("safety", "regulatory"),
        ("price", "market_data"),
        ("ohlcv", "market_data"),
        ("market", "market_data"),
        ("news", "company_events"),
        ("insider", "ownership_positioning"),
        ("search", "source_discovery"),
    ):
        if token in haystack and category not in categories:
            categories.append(category)
    return categories or ["company_research"]
