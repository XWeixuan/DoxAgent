"""Loss-limiting ingestion for Agent-authored D3 workspace artifacts.

Agent files are evidence, not trusted canonical state.  A malformed row must not
discard valid siblings or fail a ticker-level workflow.  This module performs only
mechanical normalization; it never invents direction, trigger meaning, or policy
content.  Canonical publication continues to use the strict models in ``schema``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from .schema import (
    CalibrationLogEntry,
    Policy,
    ReviewResult,
    TriggerCalibrationRecord,
    TriggerCalibrationState,
    ValidationFinding,
    ValidationScope,
    ValidationSeverity,
    WaveState,
    WorklistEntry,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(frozen=True)
class RecoveryResult(Generic[ModelT]):
    values: list[ModelT]
    findings: list[ValidationFinding]
    changed: bool = False


def _finding(
    code: str,
    message: str,
    *,
    scope: ValidationScope = ValidationScope.RECORD,
    affected_ids: list[str] | None = None,
    action: str = "continue_partial",
) -> ValidationFinding:
    return ValidationFinding(
        code=code,
        message=message,
        severity=ValidationSeverity.RECOVERABLE,
        scope=scope,
        recovery_action=action,
        affected_ids=affected_ids or [],
    )


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _nullable_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        value = [value]
    return [str(item) for item in value if item is not None and str(item).strip()]


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "y"}
    return bool(value)


def _int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _worklist(payload: dict[str, Any]) -> dict[str, Any]:
    status = _text(payload.get("status") or "PENDING").upper()
    direction = _text(payload.get("direction")).upper()
    return {
        "shell_id": _text(payload.get("shell_id")),
        "expectation_id": _text(payload.get("expectation_id")),
        "gap_id": _text(payload.get("gap_id")),
        "path_id": _text(payload.get("path_id")),
        "direction": direction,
        "path_summary": _text(payload.get("path_summary")),
        "d2_boundary_sufficient": _bool(payload.get("d2_boundary_sufficient")),
        "missing_calibration": _text(payload.get("missing_calibration")),
        "status": status,
        "policy_ids": _unique(_strings(payload.get("policy_ids"))),
        "unresolved_reason": _nullable_text(payload.get("unresolved_reason")),
    }


def _calibration_log(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "path_id": _text(payload.get("path_id")),
        "calibration_need": _text(payload.get("calibration_need")),
        "source_kind": _text(payload.get("source_kind")).upper(),
        "finding": _text(payload.get("finding")),
        "resolved": _bool(payload.get("resolved")),
    }


def _trigger_record(payload: dict[str, Any]) -> dict[str, Any]:
    disposition = _text(payload.get("disposition")).upper()
    unresolved_reason = _nullable_text(payload.get("unresolved_reason"))
    if disposition == "TRIGGER_UNRESOLVED" and unresolved_reason is None:
        unresolved_reason = "Trigger record was incomplete during deterministic recovery."
    return {
        "shell_id": _text(payload.get("shell_id")),
        "expectation_id": _text(payload.get("expectation_id")),
        "gap_id": _text(payload.get("gap_id")),
        "path_id": _text(payload.get("path_id")),
        "trigger_bearing_actor": _text(payload.get("trigger_bearing_actor")),
        "trigger_bearing_object": _text(payload.get("trigger_bearing_object")),
        "current_state": _text(payload.get("current_state")),
        "candidate_trigger": _text(payload.get("candidate_trigger")),
        "trade_sufficiency": _text(payload.get("trade_sufficiency")),
        "minimality": _text(payload.get("minimality")),
        "disclosure_route": _text(payload.get("disclosure_route")),
        "judgeability": _text(payload.get("judgeability")),
        "source_basis": _unique(_strings(payload.get("source_basis"))),
        "disposition": disposition,
        "unresolved_reason": unresolved_reason,
    }


def _trigger_state(payload: dict[str, Any]) -> dict[str, Any]:
    dispositions: list[dict[str, Any]] = []
    for raw in payload.get("path_dispositions") or []:
        if not isinstance(raw, dict):
            continue
        disposition = _text(raw.get("disposition")).upper()
        reason = _nullable_text(raw.get("unresolved_reason"))
        if disposition == "TRIGGER_UNRESOLVED" and reason is None:
            reason = "Trigger disposition was incomplete during deterministic recovery."
        dispositions.append(
            {
                "shell_id": _text(raw.get("shell_id")),
                "expectation_id": _text(raw.get("expectation_id")),
                "gap_id": _text(raw.get("gap_id")),
                "path_id": _text(raw.get("path_id")),
                "disposition": disposition,
                "unresolved_reason": reason,
            }
        )
    # Duplicate progress rows are metadata noise. Keep the last row for a path.
    by_path = {item["path_id"]: item for item in dispositions if item["path_id"]}
    normalized: dict[str, Any] = {
        "stage_status": _text(payload.get("stage_status") or "IN_PROGRESS").upper(),
        "completed_shell_ids": _unique(_strings(payload.get("completed_shell_ids"))),
        "current_shell_id": _nullable_text(payload.get("current_shell_id")),
        "path_dispositions": list(by_path.values()),
        "unprocessed_path_count": _int(payload.get("unprocessed_path_count")),
    }
    if payload.get("updated_at") is not None:
        normalized["updated_at"] = payload["updated_at"]
    if normalized["stage_status"] == "COMPLETED":
        normalized["current_shell_id"] = None
        normalized["unprocessed_path_count"] = 0
    return normalized


def _wave_state(payload: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "completed_shell_ids": _unique(_strings(payload.get("completed_shell_ids"))),
        "current_shell_id": _nullable_text(payload.get("current_shell_id")),
        "completed_path_ids": _unique(_strings(payload.get("completed_path_ids"))),
    }
    if payload.get("updated_at") is not None:
        normalized["updated_at"] = payload["updated_at"]
    return normalized


def _policy(payload: dict[str, Any]) -> dict[str, Any]:
    refs: list[dict[str, str]] = []
    seen_refs: set[tuple[str, str, str]] = set()
    for raw in payload.get("source_refs") or []:
        if not isinstance(raw, dict):
            continue
        item = (
            _text(raw.get("shell_id")),
            _text(raw.get("expectation_id")),
            _text(raw.get("gap_id")),
        )
        if not all(item) or item in seen_refs:
            continue
        seen_refs.add(item)
        refs.append({"shell_id": item[0], "expectation_id": item[1], "gap_id": item[2]})

    conditions: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, raw in enumerate(payload.get("activation_conditions") or [], start=1):
        if not isinstance(raw, dict):
            continue
        calibration = raw.get("calibration")
        if not isinstance(calibration, dict):
            calibration = {}
        condition_id = _text(raw.get("condition_id")).strip() or f"C{index}"
        if condition_id in used_ids:
            suffix = index
            while f"C{suffix}" in used_ids:
                suffix += 1
            condition_id = f"C{suffix}"
        used_ids.add(condition_id)
        conditions.append(
            {
                "condition_id": condition_id,
                "criterion": _text(raw.get("criterion")),
                "calibration": {
                    "reference_state": _text(calibration.get("reference_state")),
                    "trigger_boundary": _text(calibration.get("trigger_boundary")),
                    "qualifying_evidence": _text(calibration.get("qualifying_evidence")),
                },
            }
        )
    return {
        "policy_id": _text(payload.get("policy_id")),
        "title": _text(payload.get("title")),
        "source_refs": refs,
        "decision": _text(payload.get("decision")).upper(),
        "match_scope": _text(payload.get("match_scope")),
        "activation_conditions": conditions,
        "activation_summary": _text(payload.get("activation_summary")),
    }


def _review_result(payload: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    for raw in payload.get("issues") or []:
        if not isinstance(raw, dict):
            continue
        issues.append(
            {
                "code": _text(raw.get("code") or "AGENT_REVIEW_ISSUE"),
                "message": _text(raw.get("message")),
                "affected_policy_ids": _unique(_strings(raw.get("affected_policy_ids"))),
                "requires_research": _bool(raw.get("requires_research")),
                # Agent-authored blocking is advisory only.
                "blocking": _bool(raw.get("blocking")),
            }
        )
    status = _text(payload.get("status") or "PASSED").upper()
    if status not in {"PASSED", "REVIEW_BLOCKED"}:
        status = "PASSED"
    return {
        "status": status,
        "issue_count": len(issues),
        "blocking_issue_count": sum(bool(item["blocking"]) for item in issues),
        "issues": issues,
    }


_NORMALIZERS = {
    WorklistEntry: _worklist,
    CalibrationLogEntry: _calibration_log,
    TriggerCalibrationRecord: _trigger_record,
    TriggerCalibrationState: _trigger_state,
    WaveState: _wave_state,
    Policy: _policy,
    ReviewResult: _review_result,
}


def normalize_payload(model: type[ModelT], payload: dict[str, Any]) -> dict[str, Any]:
    normalizer = _NORMALIZERS.get(model)
    return normalizer(payload) if normalizer is not None else payload


def parse_jsonl(content: str, *, path: str, model: type[ModelT]) -> RecoveryResult[ModelT]:
    values: list[ModelT] = []
    findings: list[ValidationFinding] = []
    changed = False
    for line_number, line in enumerate(content.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            changed = True
            findings.append(
                _finding(
                    "ARTIFACT_JSONL_LINE_SKIPPED",
                    f"{path}:{line_number} is not valid JSON: {exc.msg}",
                    action="quarantine_line",
                )
            )
            continue
        if not isinstance(raw, dict):
            changed = True
            findings.append(
                _finding(
                    "ARTIFACT_JSONL_NON_OBJECT_SKIPPED",
                    f"{path}:{line_number} is not a JSON object",
                    action="quarantine_line",
                )
            )
            continue
        normalized = normalize_payload(model, raw)
        try:
            value = model.model_validate(normalized)
        except ValidationError as exc:
            changed = True
            affected = _text(raw.get("path_id") or raw.get("policy_id"))
            findings.append(
                _finding(
                    "ARTIFACT_RECORD_QUARANTINED",
                    f"{path}:{line_number} could not be normalized: {exc.errors()[0]['msg']}",
                    affected_ids=[affected] if affected else [],
                    action="quarantine_record",
                )
            )
            continue
        if normalized != raw:
            changed = True
            affected = _text(normalized.get("path_id") or normalized.get("policy_id"))
            findings.append(
                _finding(
                    "ARTIFACT_RECORD_NORMALIZED",
                    f"{path}:{line_number} contained recoverable shape/type differences",
                    affected_ids=[affected] if affected else [],
                    action="rewrite_normalized_record",
                )
            )
        values.append(value)
    return RecoveryResult(values=values, findings=findings, changed=changed)


def parse_json(content: str, *, path: str, model: type[ModelT]) -> RecoveryResult[ModelT]:
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as exc:
        return RecoveryResult(
            values=[],
            findings=[
                _finding(
                    "ARTIFACT_JSON_UNREADABLE",
                    f"{path} is not valid JSON: {exc.msg}",
                    scope=ValidationScope.FILE,
                    action="rebuild_or_quarantine_file",
                )
            ],
            changed=True,
        )
    if not isinstance(raw, dict):
        return RecoveryResult(
            values=[],
            findings=[
                _finding(
                    "ARTIFACT_JSON_NON_OBJECT",
                    f"{path} is not a JSON object",
                    scope=ValidationScope.FILE,
                    action="rebuild_or_quarantine_file",
                )
            ],
            changed=True,
        )
    normalized = normalize_payload(model, raw)
    try:
        value = model.model_validate(normalized)
    except ValidationError as exc:
        return RecoveryResult(
            values=[],
            findings=[
                _finding(
                    "ARTIFACT_FILE_QUARANTINED",
                    f"{path} could not be normalized: {exc.errors()[0]['msg']}",
                    scope=ValidationScope.FILE,
                    action="rebuild_or_quarantine_file",
                )
            ],
            changed=True,
        )
    findings: list[ValidationFinding] = []
    changed = normalized != raw
    if changed:
        findings.append(
            _finding(
                "ARTIFACT_FILE_NORMALIZED",
                f"{path} contained recoverable shape/type differences",
                scope=ValidationScope.FILE,
                action="rewrite_normalized_file",
            )
        )
    return RecoveryResult(values=[value], findings=findings, changed=changed)
