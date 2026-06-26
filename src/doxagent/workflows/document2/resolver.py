"""Document 2 resolver protocol helpers."""

from __future__ import annotations

from typing import Any

from doxagent.models import AgentResult, Objection, ObjectionResolutionDecision
from doxagent.workflows.document2.contracts import (
    Document2ResolutionDecision,
    Document2ResolutionDecisionRecord,
    Document2ResolutionPlan,
)
from doxagent.workflows.errors import WorkflowContractError

DOCUMENT2_RESOLUTION_PLANS_KEY = "document2_resolution_plans"


def document2_resolution_plan_from_agent_result(
    result: AgentResult,
    *,
    unresolved_objections: list[Objection],
) -> Document2ResolutionPlan:
    payload = _structured_payload(result)
    if _looks_like_document2_resolution_plan(payload):
        return Document2ResolutionPlan.model_validate(payload)
    return _document2_resolution_plan_from_legacy_payload(
        payload,
        unresolved_objections=unresolved_objections,
    )


def resolution_plans_json(plans: list[Document2ResolutionPlan]) -> list[dict[str, Any]]:
    return [plan.model_dump(mode="json") for plan in plans]


def _structured_payload(result: AgentResult) -> dict[str, Any]:
    payload = result.payload.get("structured")
    if not isinstance(payload, dict):
        payload = result.payload
    if not isinstance(payload, dict):
        raise WorkflowContractError("Document2 resolver output must be a JSON object.")
    return payload


def _looks_like_document2_resolution_plan(payload: dict[str, Any]) -> bool:
    return "expectation_id" in payload and (
        "decisions" in payload
        or "revised_candidate" in payload
        or "proposed_revision" in payload
        or "unresolved_reason" in payload
    )


def _document2_resolution_plan_from_legacy_payload(
    payload: dict[str, Any],
    *,
    unresolved_objections: list[Objection],
) -> Document2ResolutionPlan:
    decisions = _legacy_decision_records(payload)
    expectation_id = _plan_expectation_id(unresolved_objections)
    if not decisions:
        return Document2ResolutionPlan(
            expectation_id=expectation_id,
            decision="deferred",
            decisions=[],
            unresolved_reason="O1 resolver returned no objection decisions.",
            rationale=str(payload.get("rationale") or "No resolver decision was returned."),
        )
    return Document2ResolutionPlan(
        expectation_id=expectation_id,
        decision=_overall_decision(decisions),
        decisions=decisions,
        rationale=str(payload.get("rationale") or "Legacy resolver output converted to plan."),
    )


def _legacy_decision_records(
    payload: dict[str, Any],
) -> list[Document2ResolutionDecisionRecord]:
    raw = payload.get("objection_resolutions")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise WorkflowContractError("O1 objection_resolutions must be a list.")
    try:
        legacy_decisions = [ObjectionResolutionDecision.model_validate(item) for item in raw]
    except ValueError as exc:
        raise WorkflowContractError(
            f"O1 objection_resolutions failed schema validation: {exc}"
        ) from exc
    return [
        Document2ResolutionDecisionRecord(
            objection_id=decision.objection_id,
            decision=decision.decision,
            resolution_note=decision.resolution_note,
            changed_paths=list(decision.changed_paths),
            evidence_refs=list(decision.evidence_refs),
        )
        for decision in legacy_decisions
    ]


def _plan_expectation_id(
    unresolved_objections: list[Objection],
) -> str:
    for objection in unresolved_objections:
        expectation_id = objection.target.expectation_id
        if expectation_id:
            return expectation_id
    return "unknown_expectation"


def _overall_decision(
    decisions: list[Document2ResolutionDecisionRecord],
) -> Document2ResolutionDecision:
    ordered: tuple[Document2ResolutionDecision, ...] = (
        "accepted",
        "partially_accepted",
        "resolved",
        "rejected",
        "deferred",
    )
    present = {item.decision for item in decisions}
    for decision in ordered:
        if decision in present:
            return decision
    return "deferred"
