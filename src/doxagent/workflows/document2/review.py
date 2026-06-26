"""Document 2 review finding helpers.

Reviewers produce findings and evidence assessments. Legacy Blackboard
objections are still projected by the old pipeline until Step 6 replaces the
resolver transaction path.
"""

from __future__ import annotations

from typing import Any

from doxagent.models import (
    AgentResult,
    BlackboardPatch,
    EvidenceRef,
    Objection,
    ObjectionSeverity,
)
from doxagent.workflows.document2.contracts import (
    Document2FindingSeverity,
    Document2ReviewFinding,
    EvidenceAssessmentStatus,
)
from doxagent.workflows.document2.evidence import (
    evidence_assessment,
    evidence_assessment_from_review_status,
)

DOCUMENT2_REVIEW_FINDINGS_KEY = "document2_review_findings"


def document2_review_findings_from_agent_result(
    result: AgentResult,
    pending_patches: list[BlackboardPatch],
) -> list[Document2ReviewFinding]:
    findings: list[Document2ReviewFinding] = []
    structured = result.payload.get("structured", {})
    raw_findings = structured.get("findings", []) if isinstance(structured, dict) else []
    if isinstance(raw_findings, list):
        for raw_finding in raw_findings:
            if not isinstance(raw_finding, dict):
                continue
            findings.extend(
                _document2_review_findings_from_structured_finding(
                    result,
                    raw_finding,
                    pending_patches,
                )
            )
    findings.extend(
        document2_review_finding_from_objection(objection)
        for objection in result.objections
    )
    return findings


def document2_review_finding_from_objection(
    objection: Objection,
) -> Document2ReviewFinding:
    target_path = objection.target_path or objection.target.field_path
    status = _evidence_status_from_objection(objection)
    assessment = evidence_assessment(
        target_path=target_path,
        status=status,
        reason=objection.reason,
        evidence_refs=objection.evidence_refs,
    )
    supplemental_context = []
    if objection.taxonomy:
        supplemental_context.append(f"legacy_objection_taxonomy: {objection.taxonomy}")
    return Document2ReviewFinding(
        reviewer_agent=objection.source_agent,
        expectation_id=_objection_expectation_id(objection),
        target_path=target_path,
        severity=_severity_from_objection(objection),
        reason=objection.reason,
        evidence_assessments=[assessment],
        supplemental_evidence_refs=list(objection.evidence_refs),
        supplemental_context=supplemental_context,
        source_objection_id=objection.objection_id,
    )


def review_findings_json(findings: list[Document2ReviewFinding]) -> list[dict[str, Any]]:
    return [finding.model_dump(mode="json") for finding in findings]


def _document2_review_findings_from_structured_finding(
    result: AgentResult,
    raw_finding: dict[str, Any],
    pending_patches: list[BlackboardPatch],
) -> list[Document2ReviewFinding]:
    target_path = str(
        raw_finding.get("target_path") or raw_finding.get("field_path") or "document"
    )
    review_status = str(raw_finding.get("status") or "needs_more_evidence")
    reason = str(
        raw_finding.get("rationale")
        or raw_finding.get("reason")
        or f"{result.agent_name.value} review finding."
    )
    evidence_refs = _evidence_refs_from_raw(raw_finding.get("evidence_refs"))
    assessment = evidence_assessment_from_review_status(
        target_path=target_path,
        review_status=review_status,
        reason=reason,
        evidence_refs=evidence_refs,
    )
    return [
        Document2ReviewFinding(
            reviewer_agent=result.agent_name,
            expectation_id=expectation_id,
            target_path=target_path,
            severity=_severity_from_review_status(review_status),
            reason=reason,
            evidence_assessments=[assessment],
            supplemental_evidence_refs=list(evidence_refs),
            supplemental_context=[f"review_status: {review_status}"],
        )
        for expectation_id in _expectation_ids_for_finding(raw_finding, pending_patches)
    ]


def _expectation_ids_for_finding(
    raw_finding: dict[str, Any],
    pending_patches: list[BlackboardPatch],
) -> list[str]:
    raw_expectation_id = raw_finding.get("expectation_id")
    if raw_expectation_id:
        return [str(raw_expectation_id)]
    expectation_ids = [
        patch.target.expectation_id
        for patch in pending_patches
        if patch.target.expectation_id is not None
    ]
    if expectation_ids:
        return list(dict.fromkeys(expectation_ids))
    return ["unknown_expectation"]


def _evidence_refs_from_raw(raw: object) -> list[EvidenceRef]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        return []
    refs: list[EvidenceRef] = []
    for item in raw:
        if isinstance(item, EvidenceRef):
            refs.append(item)
        elif isinstance(item, dict):
            refs.append(EvidenceRef.model_validate(item))
    return refs


def _severity_from_review_status(status: str) -> Document2FindingSeverity:
    if status == "supported":
        return "info"
    if status == "needs_more_evidence":
        return "warning"
    return "blocking"


def _severity_from_objection(objection: Objection) -> Document2FindingSeverity:
    if objection.severity is ObjectionSeverity.BLOCKING:
        return "blocking"
    if objection.severity in {ObjectionSeverity.HIGH, ObjectionSeverity.MEDIUM}:
        return "warning"
    return "info"


def _evidence_status_from_objection(objection: Objection) -> EvidenceAssessmentStatus:
    text = f"{objection.taxonomy} {objection.reason}".lower()
    if "contradict" in text:
        return "contradictory"
    if "stale" in text or "outdated" in text:
        return "stale"
    if "unavailable" in text or "missing" in text or "gap" in text:
        return "unavailable"
    return "insufficient"


def _objection_expectation_id(objection: Objection) -> str:
    return (
        objection.target.expectation_id
        or objection.target.document_id
        or "unknown_expectation"
    )
