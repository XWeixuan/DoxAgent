"""Price-reaction evidence helpers for Document 2 review."""

from __future__ import annotations

from doxagent.models import EvidenceRef
from doxagent.workflows.document2.contracts import EvidenceAssessment
from doxagent.workflows.document2.evidence import evidence_assessment, has_market_evidence


def price_reaction_evidence_assessment(
    *,
    target_path: str,
    evidence_refs: list[EvidenceRef],
) -> EvidenceAssessment:
    if has_market_evidence(evidence_refs):
        return evidence_assessment(
            target_path=target_path,
            status="sufficient",
            reason="Price-reaction claim has market-data evidence.",
            evidence_refs=evidence_refs,
        )
    return evidence_assessment(
        target_path=target_path,
        status="insufficient",
        reason=(
            "Price-reaction claim requires OHLCV, quote, trade-stream, or other "
            "market-data evidence."
        ),
        evidence_refs=evidence_refs,
    )
