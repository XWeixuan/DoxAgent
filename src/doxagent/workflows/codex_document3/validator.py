"""Lenient deterministic validation for D3 artifacts.

Only malformed core contracts, invalid write boundaries and stale-base conflicts are
publication blockers. Coverage and semantic heuristics are explicit warnings that
produce a PARTIAL set instead of turning D3 into an audit gate.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from .schema import (
    CalibrationLogEntry,
    PathStatus,
    Policy,
    PolicyPatchSet,
    PublicationState,
    ValidationFinding,
    ValidationReport,
    WorklistEntry,
)


def _finding(code: str, message: str, *, blocking: bool = False) -> ValidationFinding:
    return ValidationFinding(code=code, message=message, blocking=blocking)


def _chinese_ratio(value: str) -> float:
    meaningful = re.findall(r"[\u4e00-\u9fffA-Za-z]", value)
    if not meaningful:
        return 0.0
    chinese = re.findall(r"[\u4e00-\u9fff]", value)
    return len(chinese) / len(meaningful)


def validate_initial_artifacts(
    *,
    expected_gap_refs: Iterable[tuple[str, str, str]],
    worklist: list[WorklistEntry],
    calibration_log: list[CalibrationLogEntry],
    policies: list[Policy],
) -> ValidationReport:
    findings: list[ValidationFinding] = []
    expected = set(expected_gap_refs)
    work_by_gap: dict[tuple[str, str, str], list[WorklistEntry]] = {}
    work_by_path: dict[str, WorklistEntry] = {}
    for item in worklist:
        key = (item.shell_id, item.expectation_id, item.gap_id)
        work_by_gap.setdefault(key, []).append(item)
        if item.path_id in work_by_path:
            findings.append(_finding("DUPLICATE_PATH_ID", f"重复 path_id: {item.path_id}"))
        work_by_path[item.path_id] = item

    for shell_id, expectation_id, gap_id in sorted(expected - set(work_by_gap)):
        findings.append(
            _finding(
                "UNCOVERED_GAP",
                f"D2 Gap 未建立 path: {shell_id}/{expectation_id}/{gap_id}",
            )
        )

    known_policy_ids = {item.policy_id for item in policies}
    calibration_paths = {item.path_id for item in calibration_log}
    for item in worklist:
        if item.status is PathStatus.PENDING:
            findings.append(_finding("PENDING_PATH", f"path 尚未收敛: {item.path_id}"))
        elif item.status is PathStatus.COMPILED:
            missing = set(item.policy_ids) - known_policy_ids
            if not item.policy_ids or missing:
                findings.append(
                    _finding(
                        "BROKEN_PATH_POLICY_LINK",
                        f"path {item.path_id} 的 Policy 映射无效: {sorted(missing)}",
                    )
                )
        elif item.status is PathStatus.UNRESOLVED and not item.unresolved_reason:
            findings.append(
                _finding("UNRESOLVED_WITHOUT_REASON", f"path 缺少 unresolved 原因: {item.path_id}")
            )
        if not item.d2_boundary_sufficient and item.path_id not in calibration_paths:
            findings.append(
                _finding("MISSING_CALIBRATION_LOG", f"path 缺少 calibration 记录: {item.path_id}")
            )

    source_refs = {
        (ref.shell_id, ref.expectation_id, ref.gap_id)
        for policy in policies
        for ref in policy.source_refs
    }
    for ref in sorted(source_refs - expected):
        findings.append(_finding("UNKNOWN_SOURCE_REF", f"Policy 引用了 D2 不存在的 Gap: {ref}"))

    duplicate_counter = Counter(
        (
            policy.decision.value,
            re.sub(r"\s+", "", policy.match_scope).casefold(),
            tuple(
                sorted(
                    re.sub(r"\s+", "", condition.calibration.trigger_boundary).casefold()
                    for condition in policy.activation_conditions
                )
            ),
        )
        for policy in policies
    )
    for duplicate_key, count in duplicate_counter.items():
        if count > 1:
            findings.append(
                _finding(
                    "EXACT_POLICY_DUPLICATE",
                    f"检测到 {count} 条完全同构 Policy: {duplicate_key}",
                )
            )

    for policy in policies:
        fields = [policy.title, policy.match_scope, policy.activation_summary]
        fields.extend(item.criterion for item in policy.activation_conditions)
        if _chinese_ratio("".join(fields)) < 0.25:
            findings.append(
                _finding("LOW_CHINESE_RATIO", f"Policy {policy.policy_id} 中文占比偏低")
            )
        if len(policy.activation_conditions) > 4:
            findings.append(
                _finding(
                    "MANY_CONDITIONS",
                    f"Policy {policy.policy_id} 含 "
                    f"{len(policy.activation_conditions)} 个条件，请语义复核",
                )
            )

    publication_state = PublicationState.PARTIAL if findings else PublicationState.COMPLETE
    return ValidationReport(
        valid=not any(item.blocking for item in findings),
        publication_state=publication_state,
        findings=findings,
    )


def validate_patch(
    *, patch: PolicyPatchSet, current_policy_ids: set[str], current_version: int
) -> ValidationReport:
    findings: list[ValidationFinding] = []
    if patch.base_policy_set_version != current_version:
        findings.append(
            _finding(
                "STALE_BASE",
                f"patch base={patch.base_policy_set_version}, current={current_version}",
                blocking=True,
            )
        )
    missing_retire = set(patch.retire_policy_ids) - current_policy_ids
    if missing_retire:
        findings.append(
            _finding("UNKNOWN_RETIRE_ID", f"retire_policy_ids 不存在: {sorted(missing_retire)}")
        )
    return ValidationReport(
        valid=not any(item.blocking for item in findings),
        publication_state=(PublicationState.PARTIAL if findings else PublicationState.COMPLETE),
        findings=findings,
    )
