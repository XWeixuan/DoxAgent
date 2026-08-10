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
        limit: int = 5,
    ) -> dict[str, object]:
        allowed = set(effective_tool_ids)
        inferred = business_category or self._infer_category(task)
        query_tokens = self._tokens(task)
        scored: list[tuple[int, DataToolContract]] = []
        gaps: list[dict[str, object]] = []
        for contract in self._contracts.all():
            if contract.canonical_tool_id not in allowed:
                continue
            score = self._score(contract, query_tokens, inferred)
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
            scored.append((score, contract))
        scored.sort(key=lambda item: (-item[0], item[1].canonical_tool_id))
        candidates = []
        for score, contract in scored[: max(1, min(limit, 5))]:
            candidates.append(
                {
                    "canonical_tool_id": contract.canonical_tool_id,
                    "mcp_name": contract.mcp_name,
                    "source": contract.source_name,
                    "score": score,
                    "use_when": contract.use_when,
                    "required_inputs": _required_inputs(contract.input_schema),
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
            "guidance": (
                "Choose one semantic tool from candidates. Do not call unavailable gaps. "
                "Use O# values returned by the tool for citations."
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
