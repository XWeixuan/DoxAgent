"""Narrow ReAct final-payload adapters for Document 2 contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from doxagent.models import (
    AgentName,
    AgentResult,
    AgentTask,
    DocumentType,
    EvidenceRef,
    EvidenceSourceType,
    new_id,
)
from doxagent.tools import ToolResult

JsonDict = dict[str, Any]


def adapt_expectation_detail_candidate_payload(
    payload: JsonDict,
    *,
    task: AgentTask,
    tool_results: list[ToolResult],
    delegation_results: list[AgentResult],
) -> JsonDict:
    evidence_refs = _valid_evidence_ref_payloads(payload.get("evidence_refs"))
    if not evidence_refs:
        evidence_refs = _runtime_evidence_refs(tool_results, delegation_results)
    candidate_payload = payload.get("candidate")
    if isinstance(candidate_payload, dict):
        candidate = dict(candidate_payload)
    elif _payload_has_expectation_detail_fields(payload):
        candidate = dict(payload.get("expectation_unit") or payload)
    else:
        candidate = {}

    normalized_candidate: Any = candidate_payload
    if candidate:
        candidate = _apply_expectation_detail_shell_fields(candidate, task=task)
        normalized_candidate = _normalize_expectation_document_payload(
            candidate,
            task=task,
            fallback_evidence=evidence_refs,
            fallback_expectation_id=_expectation_detail_shell_id(task),
        )
        normalized_candidate = _apply_expectation_detail_shell_fields(
            normalized_candidate,
            task=task,
        )

    return {
        "candidate": normalized_candidate,
        "evidence_refs": evidence_refs,
        "delegations": _normalize_output_delegations(
            payload.get("delegations"),
            task=task,
        ),
        "unknowns": _strings(payload.get("unknowns")),
        "rationale": str(
            payload.get("rationale")
            or (candidate.get("rationale") if candidate else None)
            or "O1 expectation detail candidate."
        ),
    }


def adapt_document2_resolution_plan_payload(
    payload: JsonDict,
    *,
    task: AgentTask,
    tool_results: list[ToolResult],
    delegation_results: list[AgentResult],
) -> JsonDict:
    normalized = dict(payload)
    evidence_refs = _valid_evidence_ref_payloads(normalized.pop("evidence_refs", None))
    if not evidence_refs:
        evidence_refs = _runtime_evidence_refs(tool_results, delegation_results)
    revised_candidate = normalized.get("revised_candidate")
    if isinstance(revised_candidate, dict):
        candidate_evidence_refs = _valid_evidence_ref_payloads(
            revised_candidate.get("evidence_refs")
        )
        document_payload = revised_candidate.get("document")
        candidate = (
            dict(document_payload)
            if isinstance(document_payload, dict)
            else dict(revised_candidate)
        )
        normalized["revised_candidate"] = _normalize_expectation_document_payload(
            candidate,
            task=task,
            fallback_evidence=_merge_evidence_ref_payloads(
                candidate_evidence_refs,
                evidence_refs,
            ),
            fallback_expectation_id=str(normalized.get("expectation_id") or "")
            or None,
        )
    return normalized


def _runtime_evidence_refs(
    tool_results: list[ToolResult],
    delegation_results: list[AgentResult],
) -> list[JsonDict]:
    refs: list[JsonDict] = []
    for tool_result in tool_results:
        refs.extend(ref.model_dump(mode="json") for ref in tool_result.evidence_refs)
    for delegation_result in delegation_results:
        refs.extend(ref.model_dump(mode="json") for ref in delegation_result.evidence_refs)
    return _merge_evidence_ref_payloads(refs)


def _expectation_detail_shell_id(task: AgentTask) -> str | None:
    shell = task.input_context.get("expectation_shell")
    if not isinstance(shell, dict):
        return None
    shell_id = shell.get("expectation_id")
    return str(shell_id) if shell_id else None


def _apply_expectation_detail_shell_fields(payload: JsonDict, *, task: AgentTask) -> JsonDict:
    shell = task.input_context.get("expectation_shell")
    if not isinstance(shell, dict):
        return payload
    normalized = dict(payload)
    shell_fields = {
        "expectation_id": shell.get("expectation_id"),
        "expectation_name": shell.get("expectation_name"),
        "direction": shell.get("direction"),
        "why_it_matters": shell.get("why_it_matters"),
        "market_view": shell.get("market_view"),
    }
    for key, value in shell_fields.items():
        if value is not None:
            normalized[key] = value
    normalized["ticker"] = task.ticker
    return normalized


def _payload_has_expectation_detail_fields(payload: JsonDict) -> bool:
    detail_keys = {
        "expectation_unit",
        "realized_facts",
        "realized_facts_summary",
        "known_facts_summary",
        "key_variables",
        "event_monitoring_direction",
        "positive_events",
        "negative_events",
        "price_reaction",
        "market_reaction",
        "pricing_assessment",
        "pricing_status",
    }
    return bool(detail_keys.intersection(payload))


def _normalize_expectation_document_payload(
    payload: JsonDict,
    *,
    task: AgentTask,
    fallback_evidence: list[JsonDict],
    fallback_expectation_id: str | None,
) -> JsonDict:
    expectation_id = str(
        payload.get("expectation_id")
        or payload.get("id")
        or fallback_expectation_id
        or new_id("expectation")
    )
    name = str(
        payload.get("expectation_name")
        or payload.get("name")
        or payload.get("title")
        or expectation_id
    )
    description = str(
        payload.get("why_it_matters")
        or payload.get("description")
        or payload.get("thesis")
        or name
    )
    normalized: JsonDict = {
        "document_id": str(payload.get("document_id") or new_id("doc")),
        "document_type": DocumentType.EXPECTATION_UNIT.value,
        "ticker": str(payload.get("ticker") or task.ticker),
        "created_at": str(payload.get("created_at") or datetime.now(UTC).isoformat()),
        "updated_at": payload.get("updated_at"),
        "expectation_id": expectation_id,
        "expectation_name": name,
        "direction": _normalize_expectation_direction(payload.get("direction") or description),
        "why_it_matters": description,
    }
    market_view = payload.get("market_view")
    variable_evidence_refs: list[JsonDict] = []
    document_evidence_refs = list(fallback_evidence)
    if isinstance(market_view, dict):
        normalized_market_view = {
            "text": str(market_view.get("text") or market_view.get("description") or description),
            "summary": str(market_view.get("summary") or name),
            "evidence_refs": _valid_evidence_ref_payloads(market_view.get("evidence_refs"))
            or fallback_evidence,
            "author_agent": str(market_view.get("author_agent") or task.agent_name.value),
            "reviewer_agents": _strings(market_view.get("reviewer_agents")),
        }
        normalized["market_view"] = normalized_market_view
        variable_evidence_refs = _valid_evidence_ref_payloads(
            normalized_market_view.get("evidence_refs")
        )
        document_evidence_refs = _merge_evidence_ref_payloads(
            fallback_evidence,
            variable_evidence_refs,
        )
    if "realized_facts" in payload:
        normalized["realized_facts"] = _normalize_realized_facts(
            payload.get("realized_facts"),
            fallback_evidence_refs=document_evidence_refs,
        )
    if "realized_facts_summary" in payload or "known_facts_summary" in payload:
        normalized["realized_facts_summary"] = str(
            payload.get("realized_facts_summary") or payload.get("known_facts_summary")
        )
    if "key_variables" in payload:
        normalized["key_variables"] = _normalize_variable_statuses(
            payload.get("key_variables"),
            fallback_evidence_refs=variable_evidence_refs,
        )
    if (
        "event_monitoring_direction" in payload
        or "known_event_notice" in payload
        or "positive_events" in payload
        or "negative_events" in payload
    ):
        normalized["event_monitoring_direction"] = _normalize_event_monitoring_direction(payload)
    return normalized


def _normalize_realized_facts(
    value: Any,
    *,
    fallback_evidence_refs: list[JsonDict] | None = None,
) -> list[JsonDict]:
    facts: list[JsonDict] = []
    fallback = list(fallback_evidence_refs or [])
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            evidence_refs = _merge_evidence_ref_payloads(item.get("evidence_refs"), fallback)
            description_value = item.get("description")
            if isinstance(description_value, dict):
                description_source: Any = description_value
            elif any(
                item.get(key) not in (None, "")
                for key in (
                    "fact",
                    "when",
                    "why_it_matters",
                    "pricing_status",
                    "pricing_assessment",
                )
            ):
                description_source = item
            else:
                description_source = description_value or item.get("text") or item
            price_reaction = (
                item.get("price_reaction")
                or item.get("market_reaction")
                or item.get("pricing_assessment")
                or item.get("pricing_status")
            )
            fact: JsonDict = {
                "event_id": str(item.get("event_id") or item.get("id") or new_id("event")),
                "description": _realized_fact_description(description_source),
                "evidence_refs": evidence_refs,
            }
            if price_reaction is not None:
                fact["price_reaction"] = _normalize_price_reaction(
                    price_reaction,
                    fallback_evidence_refs=evidence_refs,
                )
            facts.append(fact)
        elif str(item).strip():
            facts.append(
                {
                    "event_id": new_id("event"),
                    "description": str(item),
                    "evidence_refs": list(fallback),
                }
            )
    return facts


def _realized_fact_description(value: Any) -> str:
    if isinstance(value, dict):
        preferred_keys = (
            "fact",
            "description",
            "when",
            "why_it_matters",
            "pricing_status",
            "pricing_assessment",
        )
        parts = [
            f"{key}: {value[key]}"
            for key in preferred_keys
            if value.get(key) not in (None, "")
        ]
        if parts:
            return "; ".join(parts)
    text = str(value or "").strip()
    return text or "Confirmed market event."


def _normalize_price_reaction(
    value: Any,
    *,
    fallback_evidence_refs: list[JsonDict] | None = None,
) -> JsonDict:
    fallback = list(fallback_evidence_refs or [])
    if isinstance(value, dict):
        evidence_refs = _valid_evidence_ref_payloads(value.get("evidence_refs")) or fallback
        normalized: JsonDict = {"evidence_refs": evidence_refs}
        price_change = value.get("price_change") or value.get("move") or value.get("reaction")
        price_pattern = value.get("price_pattern") or value.get("pattern")
        interpretation = (
            value.get("interpretation")
            or value.get("rationale")
            or value.get("description")
            or value.get("pricing_assessment")
        )
        if price_change not in (None, ""):
            normalized["price_change"] = str(price_change)
        if price_pattern not in (None, ""):
            normalized["price_pattern"] = str(price_pattern)
        if interpretation not in (None, ""):
            normalized["interpretation"] = str(interpretation)
        return normalized
    text = str(value or "").strip()
    if text:
        return {
            "price_change": text,
            "price_pattern": "described",
            "interpretation": text,
            "evidence_refs": fallback,
        }
    return {"evidence_refs": fallback}


def _normalize_variable_statuses(
    value: Any,
    *,
    fallback_evidence_refs: list[JsonDict] | None = None,
) -> list[JsonDict]:
    variables: list[JsonDict] = []
    fallback = list(fallback_evidence_refs or [])
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("variable") or item.get("id") or "variable")
            variable: JsonDict = {
                "variable_id": str(item.get("variable_id") or item.get("id") or new_id("var")),
                "name": name,
                "evidence_refs": _valid_evidence_ref_payloads(item.get("evidence_refs"))
                or fallback,
            }
            current_status = (
                item.get("current_status")
                or item.get("status")
                or item.get("description")
                or item.get("relevance")
                or item.get("unresolved")
            )
            certainty = item.get("certainty") or item.get("confidence")
            if current_status not in (None, ""):
                variable["current_status"] = str(current_status)
            if certainty not in (None, ""):
                variable["certainty"] = str(certainty)
            variables.append(variable)
        elif str(item).strip():
            variables.append(
                {
                    "variable_id": new_id("var"),
                    "name": str(item),
                    "evidence_refs": [],
                }
            )
    return variables


def _normalize_expectation_direction(value: Any) -> str:
    text = str(value or "").lower()
    for candidate in ("bullish", "bearish", "neutral", "risk"):
        if candidate in text:
            return candidate
    return "neutral"


def _normalize_event_monitoring_direction(payload: JsonDict) -> JsonDict:
    raw = payload.get("event_monitoring_direction")
    if isinstance(raw, dict):
        normalized: JsonDict = {}
        known_event_notice = (
            raw.get("known_event_notice")
            or raw.get("known_upcoming_events")
            or raw.get("notice")
        )
        if known_event_notice not in (None, ""):
            normalized["known_event_notice"] = str(known_event_notice)
        if "positive_events" in raw:
            normalized["positive_events"] = _event_strings(raw.get("positive_events"))
        if "negative_events" in raw:
            normalized["negative_events"] = _event_strings(raw.get("negative_events"))
        return normalized
    normalized = {}
    if payload.get("known_event_notice") not in (None, ""):
        normalized["known_event_notice"] = str(payload.get("known_event_notice"))
    if "positive_events" in payload:
        normalized["positive_events"] = _event_strings(payload.get("positive_events"))
    if "negative_events" in payload:
        normalized["negative_events"] = _event_strings(payload.get("negative_events"))
    return normalized


def _normalize_output_delegations(value: Any, *, task: AgentTask) -> list[JsonDict]:
    delegations: list[JsonDict] = []
    for item in _dicts(value):
        question = str(item.get("question") or item.get("task") or "").strip()
        if not question:
            continue
        target_agent = _normalize_agent_name(
            item.get("target_agent"),
            default=AgentName.A2_FACT_CHECK,
        )
        delegations.append(
            {
                "delegation_id": str(item.get("delegation_id") or new_id("delegation")),
                "requester_agent": str(item.get("requester_agent") or task.agent_name.value),
                "target_agent": target_agent,
                "question": question,
                "required_evidence": _normalize_required_evidence(
                    item.get("required_evidence"),
                    question=question,
                ),
                "blocking_scope": _normalize_delegation_scope(item.get("blocking_scope"), task),
                "status": str(item.get("status") or "open"),
                "result_summary": item.get("result_summary"),
            }
        )
    return delegations


def _normalize_agent_name(value: Any, *, default: AgentName) -> str:
    raw = str(value or default.value)
    try:
        return AgentName(raw).value
    except ValueError:
        return default.value


def _normalize_required_evidence(value: Any, *, question: str) -> list[str]:
    allowed = {item.value for item in EvidenceSourceType}
    if isinstance(value, list):
        normalized = [str(item) for item in value if str(item) in allowed]
        if normalized:
            return normalized
    lowered = question.lower()
    if any(token in lowered for token in ("ohlcv", "price", "market", "volume")):
        return [EvidenceSourceType.MARKET_DATA.value]
    return [EvidenceSourceType.EXTERNAL_REPORT.value]


def _normalize_delegation_scope(value: Any, task: AgentTask) -> JsonDict:
    raw = value if isinstance(value, dict) else {}
    return {
        "document_type": str(raw.get("document_type") or DocumentType.EXPECTATION_UNIT.value),
        "field_path": str(raw.get("field_path") or "document"),
        "ticker": str(raw.get("ticker") or task.ticker),
        "document_id": raw.get("document_id"),
        "expectation_id": raw.get("expectation_id"),
    }


def _event_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        if isinstance(item, dict):
            parts = [
                item.get("event") or item.get("trigger") or item.get("name"),
                item.get("monitoring_signal") or item.get("monitoring"),
                item.get("impact"),
            ]
            text = "; ".join(str(part).strip() for part in parts if str(part or "").strip())
        else:
            text = str(item)
        text = " ".join(text.split())
        if text:
            normalized.append(text[:600])
    return normalized


def _merge_evidence_ref_payloads(*groups: Any) -> list[JsonDict]:
    refs: list[JsonDict] = []
    seen: set[str] = set()
    for group in groups:
        for ref in _valid_evidence_ref_payloads(group):
            key = str(
                ref.get("evidence_id")
                or f"{ref.get('source_type')}:{ref.get('source_id')}:{ref.get('title')}"
            )
            if key in seen:
                continue
            seen.add(key)
            refs.append(ref)
    return refs


def _valid_evidence_ref_payloads(value: Any) -> list[JsonDict]:
    if not isinstance(value, list):
        return []
    refs: list[JsonDict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            refs.append(EvidenceRef.model_validate(item).model_dump(mode="json"))
        except ValidationError:
            continue
    return refs


def _dicts(value: Any) -> list[JsonDict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _strings(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
