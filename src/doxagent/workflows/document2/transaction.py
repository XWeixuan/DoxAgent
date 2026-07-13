"""Document 2 transaction helpers.

The transaction layer converts typed resolution plans into revisions, legacy
pending-patch projections, and transaction audits. It does not let O1 directly
close objections or replace checkpoint state.
"""

from __future__ import annotations

from typing import Any

from doxagent.models import (
    AgentName,
    BlackboardPatch,
    BlackboardTarget,
    DocumentType,
    ExpectationShell,
    ExpectationShellConstructionResult,
    ExpectationUnitDocument,
    Objection,
    PatchOperation,
    ValidationStatus,
    new_id,
)
from doxagent.workflows.document2.contracts import (
    Document2FieldRepairResult,
    Document2ResolutionPlan,
    Document2Revision,
    Document2TransactionAudit,
)

DOCUMENT2_TRANSACTION_AUDITS_KEY = "document2_transaction_audits"
DOCUMENT2_CONSTRUCTION_TRANSACTION_AUDITS_KEY = (
    "document2_construction_transaction_audits"
)


def document2_revision_from_resolution_plan(
    plan: Document2ResolutionPlan,
    *,
    before_patch: BlackboardPatch | None = None,
) -> Document2Revision | None:
    if plan.proposed_revision is not None:
        return plan.proposed_revision
    if plan.revised_candidate is None:
        return None
    before = _document_from_patch(before_patch)
    return Document2Revision(
        expectation_id=plan.expectation_id,
        before=before,
        after=plan.revised_candidate,
        source="resolution_plan",
        rationale=plan.rationale,
        changed_paths=_plan_changed_paths(plan),
        review_finding_ids=list(plan.target_finding_ids),
    )


def document2_revision_from_field_repair_result(
    result: Document2FieldRepairResult,
    *,
    before_patch: BlackboardPatch | None = None,
) -> Document2Revision | None:
    before = _document_from_patch(before_patch)
    if result.field_family == "cross_field":
        if result.revised_candidate is None:
            return None
        _validate_cross_field_field_repair_identity(result, before)
        return Document2Revision(
            expectation_id=result.expectation_id,
            before=before,
            after=result.revised_candidate,
            source="resolution_plan",
            rationale=result.rationale,
            changed_paths=_field_repair_changed_paths(result),
            review_finding_ids=list(result.target_finding_ids),
        )
    if not _field_repair_has_typed_update(result):
        return None
    if before is None:
        raise ValueError("single-field Document2 repair requires an existing candidate.")
    payload = before.model_dump(mode="json")
    changed_path = _field_repair_merge_path(result)
    payload[changed_path] = _field_repair_update_payload(result)
    after = ExpectationUnitDocument.model_validate(payload)
    return Document2Revision(
        expectation_id=result.expectation_id,
        before=before,
        after=after,
        source="resolution_plan",
        rationale=result.rationale,
        changed_paths=_field_repair_changed_paths(result) or [f"document.{changed_path}"],
        review_finding_ids=list(result.target_finding_ids),
    )


def legacy_patch_from_document2_revision(
    revision: Document2Revision,
    *,
    ticker: str,
) -> BlackboardPatch:
    return BlackboardPatch(
        patch_id=new_id("patch_d2txn"),
        target=BlackboardTarget(
            document_type=DocumentType.EXPECTATION_UNIT,
            ticker=ticker,
            expectation_id=revision.expectation_id,
            field_path="document",
        ),
        operation=PatchOperation.UPDATE,
        before=revision.before.model_dump(mode="json") if revision.before is not None else None,
        after=revision.after.model_dump(mode="json"),
        rationale=revision.rationale,
        author_agent=AgentName.SYSTEM,
        validation_status=ValidationStatus.VALID,
    )


def document2_transaction_audit_from_field_repair(
    result: Document2FieldRepairResult,
    *,
    status: str,
    revision: Document2Revision | None = None,
    closed_objection_ids: list[str] | None = None,
    retained_objection_ids: list[str] | None = None,
    notes: list[str] | None = None,
) -> Document2TransactionAudit:
    return Document2TransactionAudit(
        transaction_type="resolution",
        status=status,
        expectation_id=result.expectation_id,
        input_summary={
            "task_id": result.task_id,
            "field_family": result.field_family,
            "decision": result.decision,
            "decision_count": len(result.decisions),
            "target_finding_ids": list(result.target_finding_ids),
        },
        output_summary={
            "revision_id": revision.revision_id if revision is not None else None,
            "closed_objection_ids": list(closed_objection_ids or []),
            "retained_objection_ids": list(retained_objection_ids or []),
        },
        notes=list(notes or []),
    )


def document2_transaction_audit(
    plan: Document2ResolutionPlan,
    *,
    status: str,
    revision: Document2Revision | None = None,
    closed_objection_ids: list[str] | None = None,
    retained_objection_ids: list[str] | None = None,
    notes: list[str] | None = None,
) -> Document2TransactionAudit:
    return Document2TransactionAudit(
        transaction_type="resolution",
        status=status,
        expectation_id=plan.expectation_id,
        input_summary={
            "plan_id": plan.plan_id,
            "decision": plan.decision,
            "decision_count": len(plan.decisions),
            "target_finding_ids": list(plan.target_finding_ids),
        },
        output_summary={
            "revision_id": revision.revision_id if revision is not None else None,
            "closed_objection_ids": list(closed_objection_ids or []),
            "retained_objection_ids": list(retained_objection_ids or []),
        },
        notes=list(notes or []),
    )


def transaction_audits_json(audits: list[Document2TransactionAudit]) -> list[dict[str, Any]]:
    return [audit.model_dump(mode="json") for audit in audits]


def validate_construction_resolution_transaction(
    *,
    previous_shells: list[ExpectationShell],
    revised: ExpectationShellConstructionResult,
    unresolved_objections: list[Objection],
) -> tuple[list[str], dict[str, list[str]]]:
    """Validate that construction objections may be closed by revised shells."""

    notes: list[str] = []
    previous_by_id = {shell.expectation_id: shell for shell in previous_shells}
    revised_by_id = {shell.expectation_id: shell for shell in revised.shells}
    if set(previous_by_id) != set(revised_by_id):
        missing = sorted(set(previous_by_id) - set(revised_by_id))
        added = sorted(set(revised_by_id) - set(previous_by_id))
        raise ValueError(
            "Construction resolution cannot change expectation_id set; "
            f"missing={missing}, added={added}."
        )

    changed_shell_ids: list[str] = []
    changed_fields: dict[str, list[str]] = {}
    for expectation_id, previous in previous_by_id.items():
        current = revised_by_id[expectation_id]
        field_changes: list[str] = []
        if current.expectation_name != previous.expectation_name:
            field_changes.append("expectation_name")
        if current.direction != previous.direction:
            field_changes.append("direction")
        if current.why_it_matters != previous.why_it_matters:
            field_changes.append("why_it_matters")
        if current.market_view.model_dump(mode="json") != previous.market_view.model_dump(
            mode="json"
        ):
            field_changes.append("market_view")
        if current.unknowns != previous.unknowns:
            field_changes.append("unknowns")
        if current.rationale != previous.rationale:
            field_changes.append("rationale")
        if current.model_dump(mode="json") != previous.model_dump(mode="json"):
            changed_shell_ids.append(expectation_id)
            changed_fields[expectation_id] = field_changes or ["document"]

    if unresolved_objections and not changed_shell_ids:
        raise ValueError("Construction resolution cannot close blockers with an empty revision.")

    unrelated = [
        objection.objection_id
        for objection in unresolved_objections
        if not _construction_objection_targets_shell(objection, revised_by_id)
    ]
    if unrelated:
        raise ValueError(
            "Construction resolution cannot close unrelated objections: "
            + ", ".join(unrelated)
        )

    notes.append(
        "Construction resolution transaction validated revised shells before "
        "closing construction objections."
    )
    notes.append("changed_shell_ids=" + ",".join(changed_shell_ids))
    return notes, changed_fields


def document2_construction_transaction_audit(
    *,
    revised: ExpectationShellConstructionResult,
    status: str,
    closed_objection_ids: list[str] | None = None,
    retained_objection_ids: list[str] | None = None,
    changed_fields: dict[str, list[str]] | None = None,
    notes: list[str] | None = None,
) -> Document2TransactionAudit:
    return Document2TransactionAudit(
        transaction_type="construction_resolution",
        status=status,
        input_summary={
            "shell_count": len(revised.shells),
            "revised_expectation_ids": [
                shell.expectation_id for shell in revised.shells
            ],
        },
        output_summary={
            "closed_objection_ids": list(closed_objection_ids or []),
            "retained_objection_ids": list(retained_objection_ids or []),
            "changed_fields": changed_fields or {},
        },
        notes=list(notes or []),
    )


def validate_resolution_plan_for_transaction(plan: Document2ResolutionPlan) -> list[str]:
    notes: list[str] = []
    for decision in plan.decisions:
        if decision.decision == "deferred":
            continue
        if not decision.changed_paths:
            notes.append(
                "Document2 transaction accepted a decision without changed_paths; "
                "blocker closure still depends on transaction "
                "application and deterministic revalidation."
            )
    return notes


def validate_field_repair_result_for_transaction(
    result: Document2FieldRepairResult,
) -> list[str]:
    notes: list[str] = []
    for decision in result.decisions:
        if decision.decision == "deferred":
            continue
        if not decision.changed_paths:
            notes.append(
                "Document2 field repair accepted a decision without changed_paths; "
                "blocker closure still depends on transaction "
                "application and deterministic revalidation."
            )
    return notes


def _document_from_patch(patch: BlackboardPatch | None) -> ExpectationUnitDocument | None:
    if patch is None or not isinstance(patch.after, dict):
        return None
    return ExpectationUnitDocument.model_validate(patch.after)


def _validate_cross_field_field_repair_identity(
    result: Document2FieldRepairResult,
    before: ExpectationUnitDocument | None,
) -> None:
    if result.revised_candidate is None:
        return
    if before is None:
        raise ValueError(
            "cross-field Document2 repair requires an existing candidate to verify identity."
        )
    changed_fields = [
        field
        for field in ("expectation_id", "expectation_name", "direction")
        if getattr(result.revised_candidate, field) != getattr(before, field)
    ]
    if changed_fields:
        raise ValueError(
            "cross-field Document2 repair must preserve immutable identity fields: "
            + ", ".join(changed_fields)
        )


def _field_repair_has_typed_update(result: Document2FieldRepairResult) -> bool:
    return any(
        item is not None
        for item in (
            result.realized_facts,
            result.key_variables,
            result.event_monitoring_direction,
            result.market_view,
            result.market_evidence,
        )
    )


def _field_repair_merge_path(result: Document2FieldRepairResult) -> str:
    if result.realized_facts is not None:
        return "realized_facts"
    if result.key_variables is not None:
        return "key_variables"
    if result.event_monitoring_direction is not None:
        return "event_monitoring_direction"
    if result.market_view is not None:
        return "market_view"
    if result.market_evidence is not None:
        return "market_view"
    raise ValueError("Document2 field repair result has no typed update.")


def _field_repair_update_payload(result: Document2FieldRepairResult) -> Any:
    if result.realized_facts is not None:
        return [item.model_dump(mode="json") for item in result.realized_facts]
    if result.key_variables is not None:
        return [item.model_dump(mode="json") for item in result.key_variables]
    if result.event_monitoring_direction is not None:
        return result.event_monitoring_direction.model_dump(mode="json")
    if result.market_view is not None:
        return result.market_view.model_dump(mode="json")
    if result.market_evidence is not None:
        return result.market_evidence.model_dump(mode="json")
    raise ValueError("Document2 field repair result has no typed update.")


def _field_repair_changed_paths(result: Document2FieldRepairResult) -> list[str]:
    paths: dict[str, None] = {}
    for decision in result.decisions:
        for path in decision.changed_paths:
            paths.setdefault(path, None)
    return list(paths)


def _plan_changed_paths(plan: Document2ResolutionPlan) -> list[str]:
    paths: dict[str, None] = {}
    for decision in plan.decisions:
        for path in decision.changed_paths:
            paths.setdefault(path, None)
    return list(paths)


def _construction_objection_targets_shell(
    objection: Objection,
    shells_by_id: dict[str, ExpectationShell],
) -> bool:
    target = objection.target
    if target.document_type is not DocumentType.EXPECTATION_UNIT:
        return False
    if target.expectation_id is not None:
        return target.expectation_id in shells_by_id
    target_path = " ".join(
        str(value or "")
        for value in (
            target.field_path,
            objection.target_path,
            objection.taxonomy,
            objection.reason,
        )
    ).lower()
    construction_markers = (
        "construction",
        "expectation_shell",
        "expectation shell",
        "market_view",
        "expectation_name",
        "direction",
    )
    return any(marker in target_path for marker in construction_markers)
