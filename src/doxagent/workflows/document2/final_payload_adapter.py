"""Normalize flexible ReAct payloads into the canonical Document 2 contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from doxagent.models import AgentName, AgentTask, DocumentType, new_id
from doxagent.tools import ToolResult

JsonDict = dict[str, Any]


def adapt_expectation_detail_candidate_payload(
    payload: JsonDict,
    *,
    task: AgentTask,
    tool_results: list[ToolResult],
    delegation_results: list[Any],
) -> JsonDict:
    candidate = payload.get("candidate")
    if not isinstance(candidate, dict):
        candidate = payload.get("expectation_unit")
    if not isinstance(candidate, dict):
        candidate = dict(payload)
    shell = task.input_context.get("expectation_shell")
    shell = shell if isinstance(shell, dict) else {}
    normalized = _normalize_document(candidate, task=task, shell=shell)
    return {
        "candidate": normalized,
        "delegations": _normalize_delegations(payload.get("delegations"), task=task),
        "unknowns": _strings(payload.get("unknowns")),
        "rationale": str(payload.get("rationale") or payload.get("summary") or "Expectation detail candidate."),
    }


def adapt_document2_resolution_plan_payload(
    payload: JsonDict,
    *,
    task: AgentTask,
    tool_results: list[ToolResult],
    delegation_results: list[Any],
) -> JsonDict:
    normalized = dict(payload)
    normalized.setdefault("plan_id", new_id("d2plan"))
    normalized.setdefault(
        "expectation_id",
        str(task.input_context.get("expectation_id") or "unknown_expectation"),
    )
    normalized.setdefault("decision", "deferred")
    normalized["target_finding_ids"] = _strings(normalized.get("target_finding_ids"))
    normalized["unresolved_finding_ids"] = _strings(normalized.get("unresolved_finding_ids"))
    normalized["decisions"] = [
        {
            "objection_id": item.get("objection_id"),
            "finding_id": item.get("finding_id"),
            "decision": str(item.get("decision") or "deferred"),
            "resolution_note": str(item.get("resolution_note") or item.get("reason") or "Deferred."),
            "changed_paths": _strings(item.get("changed_paths")),
        }
        for item in _dicts(normalized.get("decisions"))
    ]
    revised = normalized.get("revised_candidate")
    if isinstance(revised, dict):
        normalized["revised_candidate"] = _normalize_document(revised, task=task, shell={})
    normalized.setdefault("rationale", str(payload.get("summary") or "Document 2 resolution plan."))
    return normalized


def _normalize_document(payload: JsonDict, *, task: AgentTask, shell: JsonDict) -> JsonDict:
    expectation_id = str(
        shell.get("expectation_id")
        or payload.get("expectation_id")
        or task.input_context.get("expectation_id")
        or new_id("expectation")
    )
    name = str(shell.get("expectation_name") or payload.get("expectation_name") or payload.get("name") or "Expectation")
    why = str(shell.get("why_it_matters") or payload.get("why_it_matters") or payload.get("description") or name)
    market_view = payload.get("market_view")
    if not isinstance(market_view, dict):
        market_view = {}
    market_view = {
        "text": str(market_view.get("text") or market_view.get("description") or why),
        "summary": str(market_view.get("summary") or name),
        "author_agent": str(market_view.get("author_agent") or task.agent_name.value),
        "reviewer_agents": _strings(market_view.get("reviewer_agents")),
    }
    return {
        "document_id": str(payload.get("document_id") or new_id("doc")),
        "document_type": DocumentType.EXPECTATION_UNIT.value,
        "ticker": str(payload.get("ticker") or task.ticker),
        "created_at": payload.get("created_at") or datetime.now(UTC).isoformat(),
        "updated_at": payload.get("updated_at"),
        "expectation_id": expectation_id,
        "expectation_name": name,
        "direction": _direction(shell.get("direction") or payload.get("direction")),
        "why_it_matters": why,
        "market_view": market_view,
        "realized_facts": _realized_facts(payload.get("realized_facts")),
        "realized_facts_summary": str(payload.get("realized_facts_summary") or "Realized facts summarized above."),
        "key_variables": _variables(payload.get("key_variables")),
        "event_monitoring_direction": _monitoring(payload),
    }


def _realized_facts(value: Any) -> list[JsonDict]:
    facts: list[JsonDict] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            item = {"description": str(item)}
        reaction = item.get("price_reaction")
        reaction = reaction if isinstance(reaction, dict) else {}
        description = str(item.get("description") or item.get("event_text") or item.get("core_fact") or "Realized fact")
        facts.append(
            {
                "event_id": str(item.get("event_id") or item.get("id") or new_id("event")),
                "description": description,
                "price_reaction": {
                    "price_change": str(reaction.get("price_change") or "unknown"),
                    "price_pattern": str(reaction.get("price_pattern") or "unknown"),
                    "interpretation": str(reaction.get("interpretation") or "Price reaction not established."),
                },
            }
        )
    return facts


def _variables(value: Any) -> list[JsonDict]:
    variables: list[JsonDict] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            item = {"name": str(item)}
        name = str(item.get("name") or item.get("variable") or "variable")
        variables.append(
            {
                "variable_id": str(item.get("variable_id") or item.get("id") or new_id("variable")),
                "name": name,
                "current_status": str(item.get("current_status") or item.get("status") or "unknown"),
                "certainty": str(item.get("certainty") or item.get("confidence") or "unknown"),
            }
        )
    return variables


def _monitoring(payload: JsonDict) -> JsonDict:
    raw = payload.get("event_monitoring_direction")
    raw = raw if isinstance(raw, dict) else {}
    return {
        "known_event_notice": str(raw.get("known_event_notice") or "Known events are handled separately."),
        "positive_events": _strings(raw.get("positive_events")),
        "negative_events": _strings(raw.get("negative_events")),
    }


def _normalize_delegations(value: Any, *, task: AgentTask) -> list[JsonDict]:
    return [
        {
            "delegation_id": str(item.get("delegation_id") or new_id("delegation")),
            "requester_agent": str(item.get("requester_agent") or task.agent_name.value),
            "target_agent": str(item.get("target_agent") or AgentName.A2_FACT_CHECK.value),
            "question": str(item.get("question") or "Clarify the unresolved external fact."),
            "blocking_scope": item.get("blocking_scope") or {
                "document_type": DocumentType.EXPECTATION_UNIT.value,
                "field_path": "document",
                "ticker": task.ticker,
            },
            "status": str(item.get("status") or "open"),
            "result_summary": item.get("result_summary"),
        }
        for item in _dicts(value)
    ]


def _direction(value: Any) -> str:
    normalized = str(value or "neutral").lower()
    return normalized if normalized in {"bullish", "bearish", "neutral", "risk"} else "neutral"


def _dicts(value: Any) -> list[JsonDict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _strings(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []


__all__ = [
    "adapt_document2_resolution_plan_payload",
    "adapt_expectation_detail_candidate_payload",
]
