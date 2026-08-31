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
    TriggerCalibrationRecord,
    TriggerCalibrationStageStatus,
    TriggerCalibrationState,
    TriggerDisposition,
    ValidationFinding,
    ValidationReport,
    WaveState,
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


def validate_trigger_calibration_stage(
    *,
    expected_gap_refs: Iterable[tuple[str, str, str]],
    worklist: list[WorklistEntry],
    trigger_calibrations: list[TriggerCalibrationRecord],
    trigger_state: TriggerCalibrationState,
    require_pending_worklist: bool = True,
) -> ValidationReport:
    """Validate only Stage-A structural closure, never trigger research quality."""

    findings: list[ValidationFinding] = []
    expected = set(expected_gap_refs)
    expected_shells = {item[0] for item in expected}
    work_by_gap: dict[tuple[str, str, str], list[WorklistEntry]] = {}
    work_by_path: dict[str, WorklistEntry] = {}
    for item in worklist:
        gap_ref = (item.shell_id, item.expectation_id, item.gap_id)
        work_by_gap.setdefault(gap_ref, []).append(item)
        if gap_ref not in expected:
            findings.append(
                _finding(
                    "STAGE_A_UNKNOWN_D2_REF",
                    f"Stage-A path 引用了 D2 不存在的 Gap: {gap_ref}",
                    blocking=True,
                )
            )
        if item.path_id in work_by_path:
            findings.append(
                _finding(
                    "STAGE_A_DUPLICATE_PATH_ID",
                    f"Stage-A 重复 path_id: {item.path_id}",
                    blocking=True,
                )
            )
        work_by_path[item.path_id] = item
        if require_pending_worklist and item.status is not PathStatus.PENDING:
            findings.append(
                _finding(
                    "STAGE_A_WORKLIST_STATUS_MUTATED",
                    f"Node A 不得提前终结 Worklist path: {item.path_id}",
                    blocking=True,
                )
            )

    for shell_id, expectation_id, gap_id in sorted(expected - set(work_by_gap)):
        findings.append(
            _finding(
                "STAGE_A_UNCOVERED_GAP",
                f"Stage-A 未为成功 D2 Gap 建立 path: {shell_id}/{expectation_id}/{gap_id}",
                blocking=True,
            )
        )

    dispositions = {item.path_id: item for item in trigger_state.path_dispositions}
    for path_id in sorted(set(work_by_path) - set(dispositions)):
        findings.append(
            _finding(
                "STAGE_A_MISSING_DISPOSITION",
                f"Stage-A path 缺少 Trigger disposition: {path_id}",
                blocking=True,
            )
        )
    for path_id in sorted(set(dispositions) - set(work_by_path)):
        findings.append(
            _finding(
                "STAGE_A_UNKNOWN_DISPOSITION_PATH",
                f"Trigger disposition 引用了未知 path: {path_id}",
                blocking=True,
            )
        )
    for path_id, disposition in dispositions.items():
        work = work_by_path.get(path_id)
        if work is None:
            continue
        state_ref = (
            disposition.shell_id,
            disposition.expectation_id,
            disposition.gap_id,
        )
        work_ref = (work.shell_id, work.expectation_id, work.gap_id)
        if state_ref != work_ref:
            findings.append(
                _finding(
                    "STAGE_A_DISPOSITION_REF_MISMATCH",
                    f"path {path_id} 的 disposition/D2 引用不一致",
                    blocking=True,
                )
            )

    records = {item.path_id: item for item in trigger_calibrations}
    if len(records) != len(trigger_calibrations):
        findings.append(
            _finding(
                "STAGE_A_DUPLICATE_TRIGGER_RECORD",
                "Trigger Calibration records 存在重复 path_id",
                blocking=True,
            )
        )
    for path_id, record in records.items():
        work = work_by_path.get(path_id)
        state_entry = dispositions.get(path_id)
        if work is None or state_entry is None:
            findings.append(
                _finding(
                    "STAGE_A_ORPHAN_TRIGGER_RECORD",
                    f"Trigger Calibration record 无对应 Worklist/disposition: {path_id}",
                    blocking=True,
                )
            )
            continue
        record_ref = (record.shell_id, record.expectation_id, record.gap_id)
        work_ref = (work.shell_id, work.expectation_id, work.gap_id)
        if record_ref != work_ref or record.disposition is not state_entry.disposition:
            findings.append(
                _finding(
                    "STAGE_A_TRIGGER_RECORD_MISMATCH",
                    f"path {path_id} 的 Trigger record 与 Worklist/state 不一致",
                    blocking=True,
                )
            )
    for path_id, disposition in dispositions.items():
        if (
            disposition.disposition is TriggerDisposition.TRIGGER_READY
            and path_id not in records
        ):
            findings.append(
                _finding(
                    "STAGE_A_READY_WITHOUT_RECORD",
                    f"TRIGGER_READY path 缺少严格 Trigger record: {path_id}",
                    blocking=True,
                )
            )

    if trigger_state.stage_status is not TriggerCalibrationStageStatus.COMPLETED:
        findings.append(
            _finding(
                "STAGE_A_NOT_COMPLETED",
                "Trigger Calibration progress 尚未完成",
                blocking=True,
            )
        )
    if trigger_state.current_shell_id is not None or trigger_state.unprocessed_path_count:
        findings.append(
            _finding(
                "STAGE_A_PROGRESS_OPEN",
                "Trigger Calibration progress 仍有当前 Shell 或未处理 path",
                blocking=True,
            )
        )
    if set(trigger_state.completed_shell_ids) != expected_shells:
        findings.append(
            _finding(
                "STAGE_A_SHELL_PROGRESS_MISMATCH",
                "Trigger Calibration completed_shell_ids 与成功 D2 Shell 不一致",
                blocking=True,
            )
        )

    return ValidationReport(
        valid=not any(item.blocking for item in findings),
        publication_state=(PublicationState.PARTIAL if findings else PublicationState.COMPLETE),
        findings=findings,
    )


def validate_initial_artifacts(
    *,
    expected_gap_refs: Iterable[tuple[str, str, str]],
    worklist: list[WorklistEntry],
    calibration_log: list[CalibrationLogEntry],
    policies: list[Policy],
    trigger_calibrations: list[TriggerCalibrationRecord] | None = None,
    trigger_state: TriggerCalibrationState | None = None,
    wave_state: WaveState | None = None,
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
    disposition_by_path = (
        {value.path_id: value.disposition for value in trigger_state.path_dispositions}
        if trigger_state is not None
        else {}
    )
    ready_records = (
        {
            value.path_id
            for value in trigger_calibrations
            if value.disposition is TriggerDisposition.TRIGGER_READY
        }
        if trigger_calibrations is not None
        else set()
    )
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
            if trigger_calibrations is not None and trigger_state is not None:
                if (
                    disposition_by_path.get(item.path_id)
                    is not TriggerDisposition.TRIGGER_READY
                    or item.path_id not in ready_records
                ):
                    findings.append(
                        _finding(
                            "COMPILED_PATH_WITHOUT_TRIGGER_CALIBRATION",
                            f"compiled path 缺少已完成的 Stage-A Trigger 依据: {item.path_id}",
                            blocking=True,
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

    if trigger_calibrations is not None and trigger_state is not None:
        stage_report = validate_trigger_calibration_stage(
            expected_gap_refs=expected,
            worklist=worklist,
            trigger_calibrations=trigger_calibrations,
            trigger_state=trigger_state,
            require_pending_worklist=False,
        )
        findings.extend(stage_report.findings)

    if wave_state is not None:
        expected_shells = {item[0] for item in expected}
        terminal_path_ids = {
            item.path_id for item in worklist if item.status is not PathStatus.PENDING
        }
        if wave_state.current_shell_id is not None:
            findings.append(
                _finding(
                    "COMPILE_WAVE_OPEN",
                    "Policy Compile wave_state 仍有 current_shell_id",
                    blocking=True,
                )
            )
        if set(wave_state.completed_shell_ids) != expected_shells:
            findings.append(
                _finding(
                    "COMPILE_SHELL_PROGRESS_MISMATCH",
                    "Policy Compile completed_shell_ids 与成功 D2 Shell 不一致",
                    blocking=True,
                )
            )
        if not terminal_path_ids.issubset(set(wave_state.completed_path_ids)):
            findings.append(
                _finding(
                    "COMPILE_PATH_PROGRESS_MISMATCH",
                    "Policy Compile wave_state 未覆盖所有已终结 path",
                    blocking=True,
                )
            )

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
