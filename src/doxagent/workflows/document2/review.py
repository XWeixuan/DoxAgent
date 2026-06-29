"""Document 2 review finding helpers.

Reviewers produce findings and evidence assessments. Legacy Blackboard
objections are still projected by the old pipeline until Step 6 replaces the
resolver transaction path.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from doxagent.models import (
    AgentResult,
    BlackboardPatch,
    EvidenceRef,
    Objection,
    ObjectionSeverity,
    ResultStatus,
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
from doxagent.workflows.errors import WorkflowContractError

DOCUMENT2_REVIEW_FINDINGS_KEY = "document2_review_findings"
REVIEWER_ACCEPTANCE_WARNINGS_KEY = "reviewer_acceptance_warnings"
_FORBIDDEN_REVIEW_PAYLOAD_KEYS = frozenset(
    {"patches", "proposed_patches", "changes", "path_map", "path_maps"}
)
_REQUIRED_EVIDENCE_REF_FIELDS = frozenset(
    {
        "evidence_id",
        "source_type",
        "source_id",
        "title",
        "summary",
        "confidence",
        "citation_scope",
    }
)
_RECOMMENDED_STATEMENT_KEYS = (
    "recommended_statement",
    "corrected_formulation",
    "corrected_statement",
    "recommended_formulation",
)
_FIELD_REVIEW_STATUSES = frozenset(
    {"supported", "unsupported", "needs_more_evidence", "contradicted"}
)
_DOXATLAS_AUDIT_STATUSES = _FIELD_REVIEW_STATUSES | {"not_checked"}


def sanitize_document2_reviewer_result(
    result: AgentResult,
    *,
    expected_schema: str,
) -> tuple[AgentResult | None, list[dict[str, Any]]]:
    """Apply reviewer-node-only acceptance layering before workflow ingestion."""

    existing_warnings = result.payload.get(REVIEWER_ACCEPTANCE_WARNINGS_KEY, [])
    warnings = (
        [dict(item) for item in existing_warnings if isinstance(item, dict)]
        if isinstance(existing_warnings, list)
        else []
    )
    if result.status is not ResultStatus.SUCCEEDED:
        warnings.append(
            {
                "issue": "reviewer_result_failed",
                "severity": "fatal",
                "message": result.error.message if result.error else "Reviewer failed.",
            }
        )
        return None, warnings
    if result.proposed_patches:
        warnings.append(
            {
                "issue": "reviewer_proposed_patches_rejected",
                "severity": "fatal",
                "patch_count": len(result.proposed_patches),
            }
        )
        return None, warnings
    structured = result.payload.get("structured")
    if not isinstance(structured, dict):
        warnings.append(
            {
                "issue": "reviewer_result_not_object",
                "severity": "fatal",
                "actual_type": type(structured).__name__,
            }
        )
        return None, warnings
    forbidden = sorted(key for key in _FORBIDDEN_REVIEW_PAYLOAD_KEYS if key in structured)
    if forbidden:
        warnings.append(
            {
                "issue": "reviewer_patch_like_fields_rejected",
                "severity": "fatal",
                "forbidden_fields": forbidden,
            }
        )
        return None, warnings

    raw_findings = structured.get("findings", [])
    if raw_findings is None:
        raw_findings = []
    if not isinstance(raw_findings, list):
        warnings.append(
            {
                "issue": "reviewer_findings_not_list",
                "severity": "fatal",
                "actual_type": type(raw_findings).__name__,
            }
        )
        return None, warnings

    sanitized_findings: list[dict[str, Any]] = []
    for index, raw_finding in enumerate(raw_findings):
        finding = _sanitize_reviewer_finding(
            raw_finding,
            expected_schema=expected_schema,
            finding_index=index,
            warnings=warnings,
        )
        if finding is not None:
            sanitized_findings.append(finding)

    sanitized_structured = dict(structured)
    sanitized_structured["findings"] = sanitized_findings
    if "evidence_refs" in sanitized_structured:
        sanitized_structured["evidence_refs"] = _sanitize_reviewer_evidence_refs(
            sanitized_structured.get("evidence_refs"),
            finding_index=-1,
            finding_path="result",
            warnings=warnings,
            present=True,
        )
    sanitized_structured = _strip_reviewer_schema_extras(
        sanitized_structured,
        expected_schema=expected_schema,
    )
    payload = dict(result.payload)
    payload["structured"] = sanitized_structured
    payload[REVIEWER_ACCEPTANCE_WARNINGS_KEY] = warnings
    return result.model_copy(update={"payload": payload}, deep=True), warnings


def _sanitize_reviewer_finding(
    raw_finding: Any,
    *,
    expected_schema: str,
    finding_index: int,
    warnings: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not isinstance(raw_finding, dict):
        warnings.append(
            {
                "issue": "reviewer_finding_discarded",
                "severity": "fatal",
                "finding_index": finding_index,
                "reason": "finding_not_object",
                "actual_type": type(raw_finding).__name__,
            }
        )
        return None
    field_path = raw_finding.get("field_path")
    if not isinstance(field_path, str) or not field_path.strip():
        warnings.append(
            {
                "issue": "reviewer_finding_discarded",
                "severity": "fatal",
                "finding_index": finding_index,
                "reason": "missing_field_path",
            }
        )
        return None
    status = raw_finding.get("status")
    valid_statuses = (
        _DOXATLAS_AUDIT_STATUSES
        if expected_schema == "DoxAtlasAuditResult"
        else _FIELD_REVIEW_STATUSES
    )
    if not isinstance(status, str) or status not in valid_statuses:
        warnings.append(
            {
                "issue": "reviewer_finding_discarded",
                "severity": "fatal",
                "finding_index": finding_index,
                "finding_path": field_path,
                "reason": "invalid_status",
                "status": status,
            }
        )
        return None
    rationale = raw_finding.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        warnings.append(
            {
                "issue": "reviewer_finding_discarded",
                "severity": "fatal",
                "finding_index": finding_index,
                "finding_path": field_path,
                "reason": "missing_rationale",
            }
        )
        return None

    sanitized = dict(raw_finding)
    for key in _RECOMMENDED_STATEMENT_KEYS:
        if key not in sanitized or sanitized.get(key) is None:
            continue
        value = sanitized.get(key)
        if isinstance(value, str):
            sanitized[key] = value.strip() or None
            continue
        sanitized.pop(key, None)
        warnings.append(
            {
                "issue": "invalid_recommended_statement_removed",
                "severity": "non_fatal",
                "finding_index": finding_index,
                "finding_path": field_path,
                "field": key,
                "expected": "plain string",
                "actual_type": type(value).__name__,
            }
        )
    if "target_paths" in sanitized and not isinstance(sanitized.get("target_paths"), list):
        sanitized.pop("target_paths", None)
        warnings.append(
            {
                "issue": "invalid_target_paths_removed",
                "severity": "non_fatal",
                "finding_index": finding_index,
                "finding_path": field_path,
                "expected": "list[str]",
            }
        )
    sanitized["evidence_refs"] = _sanitize_reviewer_evidence_refs(
        sanitized.get("evidence_refs"),
        finding_index=finding_index,
        finding_path=field_path,
        warnings=warnings,
        present="evidence_refs" in sanitized,
    )
    return _strip_reviewer_finding_extras(sanitized, expected_schema=expected_schema)


def _sanitize_reviewer_evidence_refs(
    raw_refs: Any,
    *,
    finding_index: int,
    finding_path: str,
    warnings: list[dict[str, Any]],
    present: bool,
) -> list[dict[str, Any]]:
    if not present or raw_refs is None:
        return []
    if not isinstance(raw_refs, list):
        warnings.append(
            {
                "issue": "invalid_evidence_refs_removed",
                "severity": "non_fatal",
                "finding_index": finding_index,
                "finding_path": finding_path,
                "invalid_evidence_ref_count": 1,
                "missing_fields": sorted(_REQUIRED_EVIDENCE_REF_FIELDS),
                "actual_type": type(raw_refs).__name__,
            }
        )
        return []
    refs: list[dict[str, Any]] = []
    invalid_count = 0
    missing_fields: set[str] = set()
    invalid_types: list[str] = []
    for item in raw_refs:
        if isinstance(item, EvidenceRef):
            refs.append(item.model_dump(mode="json"))
            continue
        if not isinstance(item, dict):
            invalid_count += 1
            invalid_types.append(type(item).__name__)
            missing_fields.update(_REQUIRED_EVIDENCE_REF_FIELDS)
            continue
        try:
            refs.append(EvidenceRef.model_validate(item).model_dump(mode="json"))
        except ValidationError as exc:
            invalid_count += 1
            for error in exc.errors():
                if error.get("type") == "missing" and error.get("loc"):
                    missing_fields.add(str(error["loc"][-1]))
    if invalid_count:
        warning: dict[str, Any] = {
            "issue": "invalid_evidence_refs_removed",
            "severity": "non_fatal",
            "finding_index": finding_index,
            "finding_path": finding_path,
            "invalid_evidence_ref_count": invalid_count,
            "missing_fields": sorted(missing_fields),
        }
        if invalid_types:
            warning["invalid_types"] = sorted(set(invalid_types))
        warnings.append(warning)
    return refs


def _strip_reviewer_schema_extras(
    structured: dict[str, Any],
    *,
    expected_schema: str,
) -> dict[str, Any]:
    if expected_schema == "DoxAtlasAuditResult":
        allowed = {
            "verdict",
            "revision_required",
            "findings",
            "evidence_refs",
            "objections",
            "delegations",
            "unknowns",
            "rationale",
        }
    else:
        allowed = {
            "findings",
            "evidence_refs",
            "objections",
            "delegations",
            "unknowns",
            "rationale",
        }
    return {key: value for key, value in structured.items() if key in allowed}


def _strip_reviewer_finding_extras(
    finding: dict[str, Any],
    *,
    expected_schema: str,
) -> dict[str, Any]:
    allowed = {
        "field_path",
        "status",
        "rationale",
        "recommended_statement",
        "evidence_refs",
    }
    if expected_schema == "ExpectationFieldReviewResult":
        allowed.add("target_paths")
    return {key: value for key, value in finding.items() if key in allowed}


def document2_review_findings_from_agent_result(
    result: AgentResult,
    pending_patches: list[BlackboardPatch],
) -> list[Document2ReviewFinding]:
    findings: list[Document2ReviewFinding] = []
    structured = result.payload.get("structured", {})
    if isinstance(structured, dict):
        _assert_no_forbidden_review_payload_keys(structured)
        raw_findings = structured.get("findings", [])
    else:
        raw_findings = []
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
        target_paths=[target_path],
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
    _validate_structured_review_finding(raw_finding)
    target_path = str(
        raw_finding.get("target_path") or raw_finding.get("field_path") or "document"
    )
    target_paths = _target_paths_from_raw(raw_finding, primary=target_path)
    review_status = str(raw_finding.get("status") or "needs_more_evidence")
    reason = str(
        raw_finding.get("rationale")
        or raw_finding.get("reason")
        or f"{result.agent_name.value} review finding."
    )
    recommended_statement = _recommended_statement_from_raw(raw_finding)
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
            target_paths=target_paths,
            severity=_severity_from_review_status(review_status),
            reason=reason,
            recommended_statement=recommended_statement,
            evidence_assessments=[assessment],
            supplemental_evidence_refs=list(evidence_refs),
            supplemental_context=[f"review_status: {review_status}"],
        )
        for expectation_id in _expectation_ids_for_finding(raw_finding, pending_patches)
    ]


def _target_paths_from_raw(raw_finding: dict[str, Any], *, primary: str) -> list[str]:
    raw_paths = raw_finding.get("target_paths")
    if raw_paths is None:
        raw_paths = raw_finding.get("field_paths")
    paths: list[str] = []
    if isinstance(raw_paths, list):
        paths.extend(str(path) for path in raw_paths if str(path or "").strip())
    if primary and primary not in paths:
        paths.insert(0, primary)
    return list(dict.fromkeys(paths))


def _assert_no_forbidden_review_payload_keys(structured: dict[str, Any]) -> None:
    forbidden = sorted(key for key in _FORBIDDEN_REVIEW_PAYLOAD_KEYS if key in structured)
    if forbidden:
        raise WorkflowContractError(
            "ReviewExpectationFields output must not include patch-like fields: "
            + ", ".join(forbidden)
        )


def _validate_structured_review_finding(raw_finding: dict[str, Any]) -> None:
    for key in _RECOMMENDED_STATEMENT_KEYS:
        if key in raw_finding and raw_finding.get(key) is not None:
            value = raw_finding.get(key)
            if not isinstance(value, str):
                return
    if "evidence_refs" not in raw_finding:
        return
    raw_refs = raw_finding.get("evidence_refs")
    if raw_refs is None:
        return
    if not isinstance(raw_refs, list):
        return
    for item in raw_refs:
        if isinstance(item, EvidenceRef):
            continue
        if not isinstance(item, dict):
            continue
        try:
            EvidenceRef.model_validate(item)
        except ValidationError:
            continue


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


def _recommended_statement_from_raw(raw_finding: dict[str, Any]) -> str | None:
    for key in _RECOMMENDED_STATEMENT_KEYS:
        if key not in raw_finding:
            continue
        raw = raw_finding.get(key)
        if raw is None:
            return None
        if not isinstance(raw, str):
            return None
        return raw.strip() or None
    return None


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
            try:
                refs.append(EvidenceRef.model_validate(item))
            except ValidationError:
                continue
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
