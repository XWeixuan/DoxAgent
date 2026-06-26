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
    EvidenceRef,
    ExpectationShell,
    ExpectationShellConstructionResult,
    ExpectationUnitDocument,
    Objection,
    PatchOperation,
    ValidationStatus,
    new_id,
)
from doxagent.workflows.document2.contracts import (
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
        evidence_refs=_plan_evidence_refs(plan),
        changed_paths=_plan_changed_paths(plan),
        review_finding_ids=list(plan.target_finding_ids),
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
        evidence_refs=list(revision.evidence_refs),
        author_agent=AgentName.SYSTEM,
        validation_status=ValidationStatus.VALID,
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
) -> list[str]:
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
    for expectation_id, previous in previous_by_id.items():
        current = revised_by_id[expectation_id]
        if current.expectation_name != previous.expectation_name:
            raise ValueError(
                "Construction resolution cannot change expectation_name for "
                f"{expectation_id}."
            )
        if current.direction != previous.direction:
            raise ValueError(
                "Construction resolution cannot change direction for "
                f"{expectation_id}."
            )
        if current.model_dump(mode="json") != previous.model_dump(mode="json"):
            changed_shell_ids.append(expectation_id)

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
    return notes


def document2_construction_transaction_audit(
    *,
    revised: ExpectationShellConstructionResult,
    status: str,
    closed_objection_ids: list[str] | None = None,
    retained_objection_ids: list[str] | None = None,
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
        },
        notes=list(notes or []),
    )


def validate_resolution_plan_for_transaction(plan: Document2ResolutionPlan) -> None:
    for decision in plan.decisions:
        if decision.decision == "deferred":
            continue
        if not decision.changed_paths and not decision.evidence_refs:
            raise ValueError(
                "Document2 transaction decisions require changed_paths or evidence_refs."
            )


def _document_from_patch(patch: BlackboardPatch | None) -> ExpectationUnitDocument | None:
    if patch is None or not isinstance(patch.after, dict):
        return None
    return ExpectationUnitDocument.model_validate(patch.after)


def _plan_evidence_refs(plan: Document2ResolutionPlan) -> list[EvidenceRef]:
    refs: dict[str, EvidenceRef] = {}
    for decision in plan.decisions:
        for ref in decision.evidence_refs:
            refs.setdefault(ref.evidence_id, ref)
    return list(refs.values())


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
