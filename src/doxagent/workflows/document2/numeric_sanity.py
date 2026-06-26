"""Deterministic numeric sanity findings for Document 2.

Step 5 keeps the legacy objection generator in place only as an adapter source.
This module exposes the typed finding layer used by the review contract.
"""

from __future__ import annotations

from collections.abc import Iterable

from doxagent.models import Objection
from doxagent.workflows.document2.contracts import Document2ReviewFinding
from doxagent.workflows.document2.review import document2_review_finding_from_objection

NUMERIC_SANITY_FINDING_SOURCE = "deterministic_numeric_sanity"


def numeric_sanity_findings_from_objections(
    objections: Iterable[Objection],
) -> list[Document2ReviewFinding]:
    findings: list[Document2ReviewFinding] = []
    for objection in objections:
        finding = document2_review_finding_from_objection(objection)
        context = [
            *finding.supplemental_context,
            f"finding_source: {NUMERIC_SANITY_FINDING_SOURCE}",
        ]
        findings.append(finding.model_copy(update={"supplemental_context": context}))
    return findings
