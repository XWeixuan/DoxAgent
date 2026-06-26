"""Read-only Document 2 promotion boundary."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import ValidationError

from doxagent.models import BlackboardPatch, DocumentType, ExpectationUnitDocument
from doxagent.workflows.document2.contracts import (
    Document2PromotionBlocker,
    Document2PromotionCandidate,
    Document2ReviewFinding,
    Document2TransactionAudit,
    EvidenceAssessment,
)

DOCUMENT2_PROMOTION_AUDITS_KEY = "document2_promotion_audits"


class Document2PromotionBlockedError(ValueError):
    def __init__(self, blockers: list[Document2PromotionBlocker]) -> None:
        self.blockers = blockers
        details = "; ".join(
            f"{blocker.blocker_type}:{blocker.target_path}:{blocker.reason}"
            for blocker in blockers
        )
        super().__init__(f"Document2 promotion blocked: {details}")


def document2_promotion_candidate_from_patch(
    patch: BlackboardPatch,
    *,
    review_findings: Iterable[Document2ReviewFinding] = (),
) -> Document2PromotionCandidate:
    if patch.target.document_type is not DocumentType.EXPECTATION_UNIT:
        raise ValueError("Document2 promotion candidate requires an expectation_unit patch.")
    try:
        document = ExpectationUnitDocument.model_validate(patch.after)
    except ValidationError as exc:
        raise ValueError(f"promotion candidate schema validation failed: {exc}") from exc

    blocking_findings = [
        finding
        for finding in review_findings
        if finding.expectation_id == document.expectation_id and finding.blocks_promotion
    ]
    evidence_assessments = [
        assessment
        for finding in blocking_findings
        for assessment in finding.evidence_assessments
    ]
    blocking_finding_ids = [finding.finding_id for finding in blocking_findings]
    return Document2PromotionCandidate(
        document=document,
        source_revision_id=patch.patch_id,
        evidence_assessments=evidence_assessments,
        blocking_finding_ids=blocking_finding_ids,
        ready_for_promotion=not blocking_finding_ids
        and not any(assessment.blocks_promotion for assessment in evidence_assessments),
        rationale="Pending expectation revision is entering the read-only promotion gate.",
    )


def validate_document2_promotion_candidate(
    candidate: Document2PromotionCandidate,
    *,
    placeholder_findings: Iterable[str] = (),
) -> None:
    blockers = document2_promotion_blockers(candidate, placeholder_findings=placeholder_findings)
    if blockers:
        raise Document2PromotionBlockedError(blockers)


def document2_promotion_blockers(
    candidate: Document2PromotionCandidate,
    *,
    placeholder_findings: Iterable[str] = (),
) -> list[Document2PromotionBlocker]:
    blockers: list[Document2PromotionBlocker] = []
    if not candidate.ready_for_promotion:
        blockers.append(
            Document2PromotionBlocker(
                blocker_type="candidate_not_ready",
                target_path="document",
                reason="Promotion candidate is not marked ready_for_promotion.",
            )
        )
    blockers.extend(
        Document2PromotionBlocker(
            blocker_type="blocking_finding",
            target_path="document",
            reason="Review finding blocks promotion.",
            finding_id=finding_id,
        )
        for finding_id in candidate.blocking_finding_ids
    )
    blockers.extend(_evidence_assessment_blockers(candidate.evidence_assessments))
    blockers.extend(
        Document2PromotionBlocker(
            blocker_type="placeholder_text",
            target_path=_placeholder_target_path(finding),
            reason=finding,
        )
        for finding in placeholder_findings
    )
    return blockers


def blackboard_patch_from_document2_promotion_candidate(
    candidate: Document2PromotionCandidate,
    source_patch: BlackboardPatch,
) -> BlackboardPatch:
    if source_patch.target.document_type is not DocumentType.EXPECTATION_UNIT:
        raise ValueError("Document2 promotion source patch must target expectation_unit.")
    if source_patch.target.expectation_id != candidate.document.expectation_id:
        raise ValueError("Document2 promotion source patch and candidate expectation_id differ.")
    source_document = ExpectationUnitDocument.model_validate(source_patch.after)
    candidate_payload = candidate.document.model_dump(mode="json")
    if source_document.model_dump(mode="json") != candidate_payload:
        raise ValueError("Document2 promotion candidate differs from the source patch.")
    return source_patch.model_copy(update={"after": candidate_payload}, deep=True)


def document2_promotion_audit(
    candidate: Document2PromotionCandidate,
    *,
    patch: BlackboardPatch | None,
    status: str,
    blockers: list[Document2PromotionBlocker] | None = None,
) -> Document2TransactionAudit:
    return Document2TransactionAudit(
        transaction_type="promotion",
        status=status,
        expectation_id=candidate.document.expectation_id,
        input_summary={
            "promotion_candidate_id": candidate.promotion_candidate_id,
            "source_revision_id": candidate.source_revision_id,
            "ready_for_promotion": candidate.ready_for_promotion,
            "blocking_finding_ids": list(candidate.blocking_finding_ids),
            "evidence_assessment_statuses": [
                assessment.status for assessment in candidate.evidence_assessments
            ],
        },
        output_summary={
            "patch_id": patch.patch_id if patch is not None else None,
            "blockers": [
                blocker.model_dump(mode="json") for blocker in blockers or []
            ],
        },
        notes=["Document2 promotion validated and committed the candidate without mutation."],
    )


def promotion_audits_json(
    audits: list[Document2TransactionAudit],
) -> list[dict[str, object]]:
    return [audit.model_dump(mode="json") for audit in audits]


def _evidence_assessment_blockers(
    assessments: Iterable[EvidenceAssessment],
) -> list[Document2PromotionBlocker]:
    return [
        Document2PromotionBlocker(
            blocker_type="evidence_insufficient",
            target_path=assessment.target_path,
            reason=assessment.reason,
            evidence_refs=list(assessment.evidence_refs),
        )
        for assessment in assessments
        if assessment.blocks_promotion
    ]


def _placeholder_target_path(finding: str) -> str:
    path, _, _ = finding.partition(" contains ")
    return path or "document"
