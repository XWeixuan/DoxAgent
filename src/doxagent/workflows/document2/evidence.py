"""Document 2 evidence assessment helpers.

This module is the Step 5 boundary for deterministic evidence sufficiency. It
does not mutate checkpoints or create Blackboard objections.
"""

from __future__ import annotations

from typing import cast

from doxagent.models import EvidenceRef, EvidenceSourceType
from doxagent.workflows.document2.contracts import (
    EvidenceAssessment,
    EvidenceAssessmentStatus,
)

REVIEW_STATUS_TO_EVIDENCE_STATUS: dict[str, EvidenceAssessmentStatus] = {
    "supported": "sufficient",
    "unsupported": "insufficient",
    "needs_more_evidence": "insufficient",
    "contradicted": "contradictory",
    "not_checked": "unavailable",
}

BLOCKING_EVIDENCE_STATUSES: set[EvidenceAssessmentStatus] = {
    "insufficient",
    "unavailable",
    "stale",
    "contradictory",
}


def evidence_status_from_review_status(status: str) -> EvidenceAssessmentStatus:
    return REVIEW_STATUS_TO_EVIDENCE_STATUS.get(status, "insufficient")


def evidence_assessment(
    *,
    target_path: str,
    status: EvidenceAssessmentStatus | str,
    reason: str,
    evidence_refs: list[EvidenceRef] | None = None,
) -> EvidenceAssessment:
    typed_status = cast(EvidenceAssessmentStatus, status)
    return EvidenceAssessment(
        target_path=target_path,
        status=typed_status,
        reason=reason,
        evidence_refs=list(evidence_refs or []),
        blocks_promotion=typed_status in BLOCKING_EVIDENCE_STATUSES,
    )


def evidence_assessment_from_review_status(
    *,
    target_path: str,
    review_status: str,
    reason: str,
    evidence_refs: list[EvidenceRef] | None = None,
) -> EvidenceAssessment:
    return evidence_assessment(
        target_path=target_path,
        status=evidence_status_from_review_status(review_status),
        reason=reason,
        evidence_refs=evidence_refs,
    )


def has_market_evidence(refs: list[EvidenceRef]) -> bool:
    return any(is_market_evidence_ref(ref) for ref in refs)


def is_market_evidence_ref(ref: EvidenceRef) -> bool:
    if ref.source_type is EvidenceSourceType.MARKET_DATA:
        return True
    metadata = ref.retrieval_metadata or {}
    tool_name = str(metadata.get("tool_name") or metadata.get("source") or "").lower()
    source_id = ref.source_id.lower()
    return any(
        marker in tool_name or marker in source_id
        for marker in (
            "ohlcv",
            "quote",
            "market",
            "trade",
            "price",
        )
    )
