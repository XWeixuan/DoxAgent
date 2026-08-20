"""Deterministic, permission-filtered Data MCP usage guidance."""

from __future__ import annotations

import re
from collections.abc import Iterable

from doxagent.data_runtime.contracts import (
    DataAvailability,
    DataToolContract,
    DataToolContractRegistry,
)

_CATEGORY_HINTS = {
    "财务": "company_financials",
    "公告": "company_filing",
    "申报": "company_filing",
    "合同": "contract_order",
    "订单": "contract_order",
    "指引": "management_guidance",
    "预期": "sell_side_consensus",
    "宏观": "macro",
    "通胀": "macro",
    "就业": "macro",
    "行业": "industry",
    "监管": "regulatory",
    "批准": "regulatory",
    "价格": "market_data",
    "行情": "market_data",
    "新闻": "company_events",
    "内部人": "ownership_positioning",
    "financial": "company_financials",
    "filing": "company_filing",
    "guidance": "management_guidance",
    "outlook": "management_guidance",
    "consensus": "sell_side_consensus",
    "estimate": "sell_side_consensus",
}

_SOURCE_PRIORITY = {
    "SEC EDGAR": 0,
    "Official issuer IR": 1,
    "Federal Register": 1,
    "Yahoo Finance via yfinance": 2,
    "Alpha Vantage": 3,
    "Twelve Data": 4,
}

_INTENT_PROFILES: tuple[dict[str, object], ...] = (
    {
        "id": "company_actor_commitments",
        "hints": (
            "hyperscaler",
            "customer",
            "capex",
            "capital expenditure",
            "commitment",
            "客户",
            "资本开支",
            "承诺",
        ),
        "preferred": (
            "sec.issuer_filings",
            "sec.filing_content",
            "sec.management_disclosures",
            "ir.official_updates",
            "ir.official_feed_discovery",
            "tavily.search",
        ),
        "excluded": ("openfda.", "eia.", "bls.", "bea.", "census.", "congress."),
        "discovery_fallback": (
            "Use native web search, then persist each official URL with Source Capture MCP."
        ),
    },
    {
        "id": "supply_chain_capacity",
        "hints": (
            "cowos",
            "hbm",
            "capacity",
            "allocation",
            "supply chain",
            "supplier",
            "产能",
            "供给",
            "供应链",
            "分配",
        ),
        "preferred": (
            "sec.issuer_filings",
            "sec.filing_content",
            "sec.management_disclosures",
            "ir.official_updates",
            "tavily.search",
            "tavily.extract",
        ),
        "excluded": ("openfda.", "eia.", "bls.", "bea.", "congress."),
        "discovery_fallback": (
            "Use native web search for official supplier/IR sources, then capture URLs one by one."
        ),
    },
    {
        "id": "export_controls",
        "hints": (
            "export control",
            "advanced computing",
            "bis",
            "commerce department",
            "出口管制",
            "先进计算",
            "商务部",
        ),
        "preferred": (
            "federal_register.documents",
            "sec.issuer_filings",
            "sec.filing_content",
            "regulations.rulemaking_records",
            "tavily.search",
        ),
        "excluded": ("openfda.", "eia.", "bls.", "bea.", "census."),
        "discovery_fallback": (
            "Use native web search only to locate BIS/Commerce primary pages, then Source Capture."
        ),
    },
    {
        "id": "government_contracts",
        "hints": ("government contract", "award", "procurement", "政府合同", "采购", "中标"),
        "preferred": (
            "usaspending.award_search",
            "usaspending.award_detail",
            "sam.contract_opportunities",
        ),
        "excluded": ("openfda.",),
    },
)

_CAPABILITY_GAPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("historical_market_data", ("historical price", "total return", "历史价格", "历史行情")),
    ("valuation", ("valuation", "multiple", "估值", "倍数")),
    (
        "sell_side_consensus",
        ("consensus", "estimate revision", "sell-side", "一致预期", "卖方预期"),
    ),
    ("earnings_transcript_qa", ("transcript", "prepared remarks", "q&a", "电话会", "问答")),
    (
        "standardized_segment_product_customer_metrics",
        ("segment", "product revenue", "customer concentration", "分部", "产品收入", "客户集中度"),
    ),
    ("options_surface", ("option", "options", "iv", "skew", "期权", "隐含波动")),
    (
        "relative_performance",
        ("benchmark", "peer return", "relative return", "基准", "同行", "相对收益"),
    ),
    (
        "ownership_positioning",
        ("short interest", "institutional", "crowding", "空头", "机构持仓", "拥挤"),
    ),
    (
        "company_event_timing",
        ("event time", "earnings date", "event window", "事件时间", "财报日期", "事件窗"),
    ),
)

_CAPABILITY_TOOLS: dict[str, tuple[str, ...]] = {
    "historical_market_data": (
        "market.daily_ohlcv",
        "market.relative_performance",
        "ibkr.historical_ticks",
    ),
    "valuation": (
        "alpha.valuation_snapshot",
        "fmp.valuation_snapshot",
        "yfinance.peer_relative_valuation",
    ),
    "sell_side_consensus": (
        "market.sell_side_consensus",
        "yfinance.sell_side_consensus",
        "alpha.earnings_events",
        "twelvedata.sell_side_estimates",
        "fmp.sell_side_estimates",
    ),
    "earnings_transcript_qa": (),
    "standardized_segment_product_customer_metrics": ("sec.company_financials",),
    "options_surface": ("ibkr.option_surface", "alpha.historical_options"),
    "relative_performance": ("market.relative_performance",),
    "ownership_positioning": (
        "yfinance.short_interest",
        "ibkr.shortability_snapshot",
        "alpha.institutional_holdings",
        "sec.insider_transactions_enriched",
    ),
    "company_event_timing": (
        "alpha.earnings_events",
        "finnhub.company_news_events",
        "sec.issuer_filings",
    ),
}


class DataToolGuide:
    def __init__(self, contracts: DataToolContractRegistry) -> None:
        self._contracts = contracts

    def recommend(
        self,
        *,
        task: str,
        effective_tool_ids: Iterable[str],
        business_category: str | None = None,
        as_of: str | None = None,
        limit: int = 12,
    ) -> dict[str, object]:
        allowed = set(effective_tool_ids)
        inferred = business_category or self._infer_category(task)
        query_tokens = self._tokens(task)
        intents = _detect_intents(task)
        requested_capabilities = _requested_capabilities(task)
        requested_tool_ids = {
            tool_id
            for capability in requested_capabilities
            for tool_id in _CAPABILITY_TOOLS.get(capability, ())
        }
        preferred_ids = {
            str(tool_id) for intent in intents for tool_id in intent.get("preferred", ())
        }
        excluded_prefixes = {
            str(prefix) for intent in intents for prefix in intent.get("excluded", ())
        }
        scored: list[tuple[int, DataToolContract]] = []
        gaps: list[dict[str, object]] = []
        for contract in self._contracts.all():
            if contract.canonical_tool_id not in allowed:
                continue
            if any(contract.canonical_tool_id.startswith(prefix) for prefix in excluded_prefixes):
                continue
            intent_match = contract.canonical_tool_id in preferred_ids
            capability_match = contract.canonical_tool_id in requested_tool_ids
            if (
                inferred
                and inferred not in contract.business_categories
                and not intent_match
                and not capability_match
            ):
                continue
            score = self._score(contract, query_tokens, inferred)
            if capability_match:
                score += 25
            if intent_match:
                preferred_order = [
                    str(item) for intent in intents for item in intent.get("preferred", ())
                ]
                score += 30 - min(preferred_order.index(contract.canonical_tool_id), 20)
            if contract.availability is DataAvailability.UNAVAILABLE:
                if score > 0 or inferred in contract.business_categories:
                    gaps.append(
                        {
                            "canonical_tool_id": contract.canonical_tool_id,
                            "source": contract.source_name,
                            "availability": contract.availability.value,
                            "reason": contract.availability_reason,
                        }
                    )
                continue
            if score > 0:
                scored.append((score, contract))
        scored.sort(
            key=lambda item: (
                -item[0],
                _SOURCE_PRIORITY.get(item[1].source_name, 2),
                item[1].canonical_tool_id,
            )
        )
        candidates = []
        for score, contract in scored[: max(1, min(limit, 12))]:
            candidates.append(
                {
                    "canonical_tool_id": contract.canonical_tool_id,
                    "mcp_name": contract.mcp_name,
                    "source": contract.source_name,
                    "score": score,
                    "use_when": contract.use_when,
                    "required_inputs": _required_inputs(contract.input_schema),
                    "recommended_inputs": _recommended_inputs(contract.canonical_tool_id),
                    "fallback_tool_ids": contract.fallback_tool_ids,
                    "point_in_time_safe": contract.point_in_time_safe,
                    "availability": contract.availability.value,
                }
            )
        return {
            "task": task,
            "business_category": inferred,
            "as_of": as_of,
            "candidates": candidates,
            "unavailable_gaps": gaps[:5],
            "intent_coverage": _intent_coverage(intents, candidates),
            "capability_gaps": _capability_gaps(task, candidates),
            "capability_coverage": _capability_coverage(task, candidates, allowed),
            "guidance": (
                "Choose one semantic tool from candidates. For SEC documents, call "
                "sec.issuer_filings(include_exhibits=true) before filing content when accession "
                "or exhibit filenames are unknown. Do not call unavailable gaps. Use O# values "
                "returned by the tool for citations."
            ),
        }

    def server_instructions(self, effective_tool_ids: Iterable[str]) -> str:
        allowed = set(effective_tool_ids)
        contracts = [
            item
            for item in self._contracts.all()
            if item.canonical_tool_id in allowed and item.exposed
        ]
        categories = sorted(
            {category for item in contracts for category in item.business_categories}
        )
        return (
            "Use semantic data tools by business question, not by guessing provider endpoints. "
            "Ticker, run, node, attempt and cutoff are injected and cannot be overridden. "
            "Tool results return cleaned attempt-local O# observations: cite only as 【cite:O#】. "
            "For delivery.mode=pack, read selected_path/catalog_path first and open only relevant "
            "blocks. succeeded/partial/empty/failed is execution state; availability is separate. "
            "Call data_tool_guide when routing is unclear. Available categories: "
            + ", ".join(categories)
            + "."
        )

    def render_catalog(self, effective_tool_ids: Iterable[str]) -> str:
        allowed = set(effective_tool_ids)
        lines = ["# Data MCP tool catalog", ""]
        for item in self._contracts.all():
            if item.canonical_tool_id not in allowed:
                continue
            lines.extend(
                [
                    f"## {item.mcp_name}",
                    "",
                    f"- Canonical id: `{item.canonical_tool_id}`",
                    f"- Source: {item.source_name}",
                    f"- Availability: `{item.availability.value}`",
                    f"- Use when: {'; '.join(item.use_when)}",
                    f"- Avoid when: {'; '.join(item.avoid_when)}",
                    f"- Input schema: `{item.input_schema}`",
                    f"- Fallbacks: {', '.join(item.fallback_tool_ids) or 'none'}",
                    "",
                ]
            )
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {token for token in re.findall(r"[a-z0-9_]+", text.lower()) if len(token) > 2}

    @staticmethod
    def _infer_category(task: str) -> str | None:
        for hint, category in _CATEGORY_HINTS.items():
            if hint in task:
                return category
        return None

    @staticmethod
    def _score(
        contract: DataToolContract,
        query_tokens: set[str],
        business_category: str | None,
    ) -> int:
        haystack = " ".join(
            [
                contract.canonical_tool_id,
                contract.description,
                contract.business_purpose,
                *contract.business_categories,
                *contract.use_when,
            ]
        ).lower()
        score = sum(1 for token in query_tokens if token in haystack)
        if business_category and business_category in contract.business_categories:
            score += 10
        return score


def _required_inputs(schema: dict[str, object]) -> list[str]:
    required = schema.get("required")
    if isinstance(required, list):
        return [str(item) for item in required]
    alternatives = schema.get("anyOf")
    if isinstance(alternatives, list):
        choices = []
        for alternative in alternatives:
            if isinstance(alternative, dict) and isinstance(alternative.get("required"), list):
                choices.append(" + ".join(str(item) for item in alternative["required"]))
        if choices:
            return ["one of: " + " | ".join(choices)]
    return []


def _recommended_inputs(tool_id: str) -> list[str]:
    return {
        "sec.issuer_filings": ["forms", "limit", "include_exhibits=true for 8-K exhibits"],
        "sec.filing_content": ["form or accession + primary_document", "sections"],
        "sec.filing_sections": ["form or accession + primary_document", "sections"],
        "sec.management_disclosures": [
            "form=8-K (default) reads Item 2.02 + EX-99.1/EX-99.2; form=10-Q reads MD&A"
        ],
    }.get(tool_id, [])


def _detect_intents(task: str) -> list[dict[str, object]]:
    lowered = task.lower()
    return [
        profile
        for profile in _INTENT_PROFILES
        if any(str(hint) in lowered for hint in profile["hints"])
    ]


def _intent_coverage(
    intents: list[dict[str, object]], candidates: list[dict[str, object]]
) -> list[dict[str, object]]:
    candidate_ids = {str(item["canonical_tool_id"]) for item in candidates}
    return [
        {
            "intent": str(intent["id"]),
            "status": "covered" if candidate_ids.intersection(intent["preferred"]) else "gap",
            "candidate_tool_ids": sorted(candidate_ids.intersection(intent["preferred"])),
            "discovery_fallback": intent.get("discovery_fallback"),
        }
        for intent in intents
    ]


def _capability_gaps(task: str, candidates: list[dict[str, object]]) -> list[dict[str, object]]:
    candidate_ids = {str(item["canonical_tool_id"]) for item in candidates}
    gaps = []
    for capability in _requested_capabilities(task):
        configured = set(_CAPABILITY_TOOLS.get(capability, ()))
        if configured.intersection(candidate_ids):
            continue
        gaps.append(
            {
                "capability": capability,
                "status": "not_covered_by_current_node_tools",
                "expected_tool_ids": sorted(configured),
            }
        )
    return gaps


def _requested_capabilities(task: str) -> list[str]:
    lowered = task.lower()
    return [
        capability
        for capability, hints in _CAPABILITY_GAPS
        if any(hint in lowered for hint in hints)
    ]


def _capability_coverage(
    task: str,
    candidates: list[dict[str, object]],
    allowed: set[str],
) -> list[dict[str, object]]:
    candidate_by_id = {str(item["canonical_tool_id"]): item for item in candidates}
    rows: list[dict[str, object]] = []
    for capability in _requested_capabilities(task):
        expected = _CAPABILITY_TOOLS.get(capability, ())
        selected = [tool_id for tool_id in expected if tool_id in candidate_by_id]
        permitted = [tool_id for tool_id in expected if tool_id in allowed]
        rows.append(
            {
                "capability": capability,
                "status": "covered"
                if selected
                else ("permitted_but_unavailable" if permitted else "not_permitted"),
                "candidate_tool_ids": selected,
                "permitted_tool_ids": permitted,
            }
        )
    return rows
