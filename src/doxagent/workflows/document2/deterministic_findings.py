"""Deterministic Document2 findings used by review and transaction gates."""

from __future__ import annotations

from collections.abc import Iterable

from doxagent.models import (
    AgentName,
    BlackboardPatch,
    DocumentType,
    EvidenceRef,
    ExpectationUnitDocument,
)
from doxagent.workflows.document2.contracts import Document2ReviewFinding
from doxagent.workflows.document2.evidence import evidence_assessment
from doxagent.workflows.document2.placeholders import placeholder_findings_from_document
from doxagent.workflows.document2.price_reaction import price_reaction_evidence_assessment

DETERMINISTIC_FINDING_SOURCE = "deterministic_document2_revalidation"

_UNKNOWN_PRICE_MARKERS = (
    "unknown",
    "unresolved",
    "not available",
    "missing_market_data",
    "not established",
    "未建立",
    "尚未建立",
    "无法确定",
    "证据不足",
    "待确认",
)

_GENERIC_MONITORING_MARKERS = (
    "monitor ticker-relevant signals",
    "monitor ticker-relevant signal changes",
    "confirmed deployments",
    "commercialization milestones",
    "deployment delays",
    "financing pressure",
    "已确认的部署",
    "商业化里程碑",
    "部署延迟",
    "融资压力",
    "商业化证据不足",
)


def deterministic_findings_from_patch(
    patch: BlackboardPatch,
) -> list[Document2ReviewFinding]:
    if patch.target.document_type is not DocumentType.EXPECTATION_UNIT:
        return []
    if not isinstance(patch.after, dict):
        return []
    document = ExpectationUnitDocument.model_validate(patch.after)
    return deterministic_findings_from_document(
        document,
        patch_evidence_refs=patch.evidence_refs,
    )


def deterministic_findings_from_document(
    document: ExpectationUnitDocument,
    *,
    patch_evidence_refs: Iterable[EvidenceRef] = (),
) -> list[Document2ReviewFinding]:
    patch_refs = list(patch_evidence_refs)
    findings: list[Document2ReviewFinding] = []
    findings.extend(_structure_findings(document))
    findings.extend(_evidence_ref_findings(document))
    findings.extend(_price_reaction_findings(document, patch_refs))
    findings.extend(_monitoring_findings(document))
    findings.extend(placeholder_findings_from_document(document))
    return findings


def _structure_findings(document: ExpectationUnitDocument) -> list[Document2ReviewFinding]:
    findings: list[Document2ReviewFinding] = []
    if not document.realized_facts:
        findings.append(
            _finding(
                document,
                "realized_facts",
                "Expectation unit has no realized facts for review.",
            )
        )
    if not document.key_variables:
        findings.append(
            _finding(
                document,
                "key_variables",
                "Expectation unit has no key variables for review.",
            )
        )
    return findings


def _evidence_ref_findings(document: ExpectationUnitDocument) -> list[Document2ReviewFinding]:
    findings: list[Document2ReviewFinding] = []
    for index, fact in enumerate(document.realized_facts):
        if not fact.evidence_refs:
            findings.append(
                _finding(
                    document,
                    f"realized_facts[{index}].evidence_refs",
                    "Realized fact is missing evidence refs.",
                    assessment_status="unavailable",
                )
            )
    for index, variable in enumerate(document.key_variables):
        if not variable.evidence_refs:
            findings.append(
                _finding(
                    document,
                    f"key_variables[{index}].evidence_refs",
                    "Key variable is missing evidence refs.",
                    assessment_status="unavailable",
                )
            )
    return findings


def _price_reaction_findings(
    document: ExpectationUnitDocument,
    patch_evidence_refs: list[EvidenceRef],
) -> list[Document2ReviewFinding]:
    findings: list[Document2ReviewFinding] = []
    for index, fact in enumerate(document.realized_facts):
        reaction = fact.price_reaction
        target_path = f"realized_facts[{index}].price_reaction"
        refs = _dedupe_refs(
            [
                *fact.evidence_refs,
                *reaction.evidence_refs,
                *patch_evidence_refs,
            ]
        )
        text = " ".join(
            [
                reaction.price_change,
                reaction.price_pattern,
                reaction.interpretation,
            ]
        ).lower()
        if any(marker in text for marker in _UNKNOWN_PRICE_MARKERS):
            findings.append(
                _finding(
                    document,
                    target_path,
                    "Price reaction is unknown or explicitly marked as an evidence gap.",
                    evidence_refs=refs,
                    assessment_status="unavailable",
                )
            )
            continue
        assessment = price_reaction_evidence_assessment(
            target_path=target_path,
            evidence_refs=refs,
        )
        if assessment.blocks_promotion:
            findings.append(
                Document2ReviewFinding(
                    reviewer_agent=AgentName.SYSTEM,
                    expectation_id=document.expectation_id,
                    target_path=target_path,
                    severity="blocking",
                    reason=assessment.reason,
                    evidence_assessments=[assessment],
                    supplemental_evidence_refs=refs,
                    supplemental_context=[f"finding_source: {DETERMINISTIC_FINDING_SOURCE}"],
                )
            )
    return findings


def _monitoring_findings(document: ExpectationUnitDocument) -> list[Document2ReviewFinding]:
    findings: list[Document2ReviewFinding] = []
    monitoring = document.event_monitoring_direction
    if not monitoring.positive_events:
        findings.append(
            _finding(
                document,
                "event_monitoring_direction.positive_events",
                "Event monitoring direction has no positive event triggers.",
            )
        )
    if not monitoring.negative_events:
        findings.append(
            _finding(
                document,
                "event_monitoring_direction.negative_events",
                "Event monitoring direction has no negative event triggers.",
            )
        )
    for index, event in enumerate(monitoring.positive_events):
        if _is_generic_monitoring_trigger(event):
            findings.append(
                _finding(
                    document,
                    f"event_monitoring_direction.positive_events[{index}]",
                    "Event monitoring trigger is generic and not actionable.",
                )
            )
    for index, event in enumerate(monitoring.negative_events):
        if _is_generic_monitoring_trigger(event):
            findings.append(
                _finding(
                    document,
                    f"event_monitoring_direction.negative_events[{index}]",
                    "Event monitoring trigger is generic and not actionable.",
                )
            )
    return findings


def _finding(
    document: ExpectationUnitDocument,
    target_path: str,
    reason: str,
    *,
    evidence_refs: list[EvidenceRef] | None = None,
    assessment_status: str = "insufficient",
) -> Document2ReviewFinding:
    refs = list(evidence_refs or [])
    assessment = evidence_assessment(
        target_path=target_path,
        status=assessment_status,
        reason=reason,
        evidence_refs=refs,
    )
    return Document2ReviewFinding(
        reviewer_agent=AgentName.SYSTEM,
        expectation_id=document.expectation_id,
        target_path=target_path,
        severity="blocking",
        reason=reason,
        evidence_assessments=[assessment],
        supplemental_evidence_refs=refs,
        supplemental_context=[f"finding_source: {DETERMINISTIC_FINDING_SOURCE}"],
    )


def _is_generic_monitoring_trigger(value: str) -> bool:
    normalized = " ".join(value.lower().split())
    return any(marker in normalized for marker in _GENERIC_MONITORING_MARKERS)


def _dedupe_refs(refs: Iterable[EvidenceRef]) -> list[EvidenceRef]:
    deduped: dict[str, EvidenceRef] = {}
    for ref in refs:
        deduped.setdefault(ref.evidence_id, ref)
    return list(deduped.values())
