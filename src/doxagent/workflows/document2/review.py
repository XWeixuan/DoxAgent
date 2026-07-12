"""Business-content review adapters for Document 2."""

from __future__ import annotations

from typing import Any

from doxagent.models import AgentName, AgentResult, Objection, ObjectionSeverity
from doxagent.workflows.document2.contracts import (
    Document2FindingSeverity,
    Document2ReviewFinding,
)

DOCUMENT2_REVIEW_FINDINGS_KEY = "document2_review_findings"


def sanitize_document2_reviewer_result(
    result: AgentResult,
    *,
    required_output_schema: str | None = None,
) -> tuple[AgentResult, list[dict[str, Any]]]:
    """Apply business-content review acceptance without provenance handling."""

    del required_output_schema
    return result, []


def document2_review_findings_from_agent_result(
    result: AgentResult,
    *,
    expectation_ids: list[str] | None = None,
) -> list[Document2ReviewFinding]:
    structured = result.payload.get("structured")
    if not isinstance(structured, dict):
        structured = result.payload
    ids = [item for item in (expectation_ids or []) if item]
    findings: list[Document2ReviewFinding] = []
    raw_findings = structured.get("findings")
    for raw in raw_findings if isinstance(raw_findings, list) else []:
        if not isinstance(raw, dict):
            continue
        target_path = str(raw.get("field_path") or raw.get("target_path") or "document")
        target_paths = [
            str(item) for item in raw.get("target_paths", []) if str(item).strip()
        ] if isinstance(raw.get("target_paths"), list) else []
        status = str(raw.get("status") or "needs_revision")
        reason = str(raw.get("rationale") or raw.get("reason") or "Reviewer raised a content issue.")
        for expectation_id in _finding_expectation_ids(raw, ids):
            findings.append(
                Document2ReviewFinding(
                    reviewer_agent=result.agent_name,
                    expectation_id=expectation_id,
                    target_path=target_path,
                    target_paths=target_paths,
                    severity=_severity_from_status(status),
                    reason=reason,
                    recommended_statement=_optional_text(raw.get("recommended_statement")),
                    supplemental_context=["source:agent_review"],
                )
            )
    for objection in result.objections:
        findings.append(document2_review_finding_from_objection(objection))
    return findings


def document2_review_finding_from_objection(
    objection: Objection,
) -> Document2ReviewFinding:
    return Document2ReviewFinding(
        reviewer_agent=objection.source_agent,
        expectation_id=objection.target.expectation_id or objection.target.document_id or "unknown",
        target_path=objection.target_path or objection.target.field_path,
        severity=_severity_from_objection(objection),
        reason=objection.reason,
        source_objection_id=objection.objection_id,
        supplemental_context=["source:blackboard_objection"],
    )


def review_findings_json(
    findings: list[Document2ReviewFinding],
) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in findings]


def _finding_expectation_ids(raw: dict[str, Any], fallback: list[str]) -> list[str]:
    value = raw.get("expectation_id")
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return fallback or ["unknown"]


def _severity_from_status(status: str) -> Document2FindingSeverity:
    if status in {"unsupported", "contradicted", "needs_revision", "blocked"}:
        return "blocking"
    if status in {"needs_more_evidence", "not_checked", "warning"}:
        return "warning"
    return "info"


def _severity_from_objection(objection: Objection) -> Document2FindingSeverity:
    if objection.severity in {ObjectionSeverity.BLOCKING, ObjectionSeverity.HIGH}:
        return "blocking"
    if objection.severity is ObjectionSeverity.MEDIUM:
        return "warning"
    return "info"


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


__all__ = [
    "DOCUMENT2_REVIEW_FINDINGS_KEY",
    "document2_review_finding_from_objection",
    "document2_review_findings_from_agent_result",
    "review_findings_json",
    "sanitize_document2_reviewer_result",
]
