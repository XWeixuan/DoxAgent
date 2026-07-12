"""Deterministic business-content findings used by Document 2 review."""

from __future__ import annotations

from doxagent.models import AgentName, BlackboardPatch, DocumentType, ExpectationUnitDocument
from doxagent.workflows.document2.contracts import Document2ReviewFinding
from doxagent.workflows.document2.placeholders import placeholder_findings_from_document

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
)


def deterministic_findings_from_patch(
    patch: BlackboardPatch,
) -> list[Document2ReviewFinding]:
    if patch.target.document_type is not DocumentType.EXPECTATION_UNIT:
        return []
    if not isinstance(patch.after, dict):
        return []
    return deterministic_findings_from_document(
        ExpectationUnitDocument.model_validate(patch.after)
    )


def deterministic_findings_from_document(
    document: ExpectationUnitDocument,
) -> list[Document2ReviewFinding]:
    findings: list[Document2ReviewFinding] = []
    if not document.realized_facts:
        findings.append(_finding(document, "realized_facts", "Expectation unit has no realized facts for review."))
    if not document.key_variables:
        findings.append(_finding(document, "key_variables", "Expectation unit has no key variables for review."))
    for index, fact in enumerate(document.realized_facts):
        reaction = fact.price_reaction
        text = " ".join(
            [reaction.price_change, reaction.price_pattern, reaction.interpretation]
        ).lower()
        if any(marker in text for marker in _UNKNOWN_PRICE_MARKERS):
            findings.append(
                _finding(
                    document,
                    f"realized_facts[{index}].price_reaction",
                    "Price reaction is unknown or not established.",
                )
            )
    monitoring = document.event_monitoring_direction
    if not monitoring.positive_events:
        findings.append(_finding(document, "event_monitoring_direction.positive_events", "Event monitoring direction has no positive event triggers."))
    if not monitoring.negative_events:
        findings.append(_finding(document, "event_monitoring_direction.negative_events", "Event monitoring direction has no negative event triggers."))
    for polarity, events in (
        ("positive_events", monitoring.positive_events),
        ("negative_events", monitoring.negative_events),
    ):
        for index, event in enumerate(events):
            normalized = " ".join(event.lower().split())
            if any(marker in normalized for marker in _GENERIC_MONITORING_MARKERS):
                findings.append(
                    _finding(
                        document,
                        f"event_monitoring_direction.{polarity}[{index}]",
                        "Event monitoring trigger is generic and not actionable.",
                    )
                )
    findings.extend(placeholder_findings_from_document(document))
    return findings


def _finding(
    document: ExpectationUnitDocument,
    target_path: str,
    reason: str,
) -> Document2ReviewFinding:
    return Document2ReviewFinding(
        reviewer_agent=AgentName.SYSTEM,
        expectation_id=document.expectation_id,
        target_path=target_path,
        severity="blocking",
        reason=reason,
        supplemental_context=[f"finding_source: {DETERMINISTIC_FINDING_SOURCE}"],
    )


__all__ = ["deterministic_findings_from_document", "deterministic_findings_from_patch"]
