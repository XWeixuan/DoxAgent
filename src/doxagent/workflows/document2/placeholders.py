"""Typed placeholder and generic-text findings for Document 2 candidates."""

from __future__ import annotations

import re
from collections.abc import Iterable

from doxagent.models import (
    AgentName,
    BlackboardPatch,
    DocumentType,
    ExpectationUnitDocument,
)
from doxagent.workflows.document2.contracts import Document2ReviewFinding

PLACEHOLDER_FINDING_SOURCE = "deterministic_placeholder_detector"

_EXACT_GENERIC_TEXT = {
    "monitor ticker-relevant signals",
    "monitor ticker-relevant signals.",
    "monitor ticker-relevant signal changes",
    "monitor ticker-relevant signal changes.",
    "known upcoming events",
    "known upcoming events.",
}

_GENERIC_PHRASES = (
    "confirmed deployments",
    "commercialization milestones",
    "deployment delays",
    "commercialization evidence insufficient",
)

_PLACEHOLDER_RE = re.compile(
    r"\b(tbd|todo|placeholder|lorem ipsum|not available|n/a)\b",
    re.IGNORECASE,
)


def placeholder_findings_from_patch(
    patch: BlackboardPatch,
) -> list[Document2ReviewFinding]:
    if patch.target.document_type is not DocumentType.EXPECTATION_UNIT:
        return []
    if not isinstance(patch.after, dict):
        return []
    document = ExpectationUnitDocument.model_validate(patch.after)
    return placeholder_findings_from_document(document)


def placeholder_findings_from_patches(
    patches: Iterable[BlackboardPatch],
) -> list[Document2ReviewFinding]:
    findings: list[Document2ReviewFinding] = []
    for patch in patches:
        findings.extend(placeholder_findings_from_patch(patch))
    return findings


def placeholder_findings_from_document(
    document: ExpectationUnitDocument,
) -> list[Document2ReviewFinding]:
    findings: list[Document2ReviewFinding] = []
    for target_path, text in _document_text_fields(document):
        match = _generic_text_reason(text)
        if match is None:
            continue
        findings.append(
            Document2ReviewFinding(
                reviewer_agent=AgentName.SYSTEM,
                expectation_id=document.expectation_id,
                target_path=target_path,
                severity="blocking",
                reason=(
                    "Document2 candidate contains placeholder or generic text at "
                    f"{target_path}: {match}."
                ),
                supplemental_context=[
                    f"finding_source: {PLACEHOLDER_FINDING_SOURCE}",
                    "detector: small_generic_text_detector",
                    f"matched_rule: {match}",
                ],
            )
        )
    return findings


def _document_text_fields(
    document: ExpectationUnitDocument,
) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = [
        ("market_view.text", document.market_view.text),
        ("market_view.summary", document.market_view.summary),
        ("realized_facts_summary", document.realized_facts_summary),
        (
            "event_monitoring_direction.known_event_notice",
            document.event_monitoring_direction.known_event_notice,
        ),
    ]
    for index, fact in enumerate(document.realized_facts):
        fields.extend(
            [
                (f"realized_facts[{index}].description", fact.description),
                (
                    f"realized_facts[{index}].price_reaction.price_change",
                    fact.price_reaction.price_change,
                ),
                (
                    f"realized_facts[{index}].price_reaction.price_pattern",
                    fact.price_reaction.price_pattern,
                ),
                (
                    f"realized_facts[{index}].price_reaction.interpretation",
                    fact.price_reaction.interpretation,
                ),
            ]
        )
    for index, variable in enumerate(document.key_variables):
        fields.append((f"key_variables[{index}].current_status", variable.current_status))
    for index, event in enumerate(document.event_monitoring_direction.positive_events):
        fields.append((f"event_monitoring_direction.positive_events[{index}]", event))
    for index, event in enumerate(document.event_monitoring_direction.negative_events):
        fields.append((f"event_monitoring_direction.negative_events[{index}]", event))
    return fields


def _generic_text_reason(text: str) -> str | None:
    normalized = " ".join(text.lower().split())
    if normalized in _EXACT_GENERIC_TEXT:
        return f"exact:{normalized}"
    placeholder_match = _PLACEHOLDER_RE.search(normalized)
    if placeholder_match is not None:
        return f"placeholder:{placeholder_match.group(1).lower()}"
    for phrase in _GENERIC_PHRASES:
        if phrase in normalized:
            return f"generic_phrase:{phrase}"
    return None
