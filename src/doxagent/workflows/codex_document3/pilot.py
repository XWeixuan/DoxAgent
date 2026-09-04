"""Read-only Pilot metrics for real O3 workspaces."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, Field

from doxagent.codex_runtime.client import WorkspaceClient

from .schema import (
    CalibrationLogEntry,
    ContractModel,
    MaintenanceCandidate,
    PathStatus,
    Policy,
    TriggerCalibrationRecord,
    TriggerCalibrationState,
    TriggerDisposition,
    WaveState,
    WorklistEntry,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


class Document3PilotReport(ContractModel):
    run_id: str
    mode: str
    gap_count: int = Field(ge=0)
    path_count: int = Field(ge=0)
    policy_count: int = Field(ge=0)
    unresolved_path_count: int = Field(ge=0)
    calibration_entry_count: int = Field(ge=0)
    web_calibration_count: int = Field(ge=0)
    data_mcp_calibration_count: int = Field(ge=0)
    missing_calibration_link_count: int = Field(ge=0)
    single_condition_policy_ratio: float = Field(ge=0, le=1)
    max_condition_count: int = Field(ge=0)
    completed_shell_count: int = Field(ge=0)
    trigger_ready_path_count: int = Field(default=0, ge=0)
    trigger_unresolved_path_count: int = Field(default=0, ge=0)
    trigger_calibration_completed_shell_count: int = Field(default=0, ge=0)
    maintenance_candidate_count: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)


class Document3PilotEvaluator:
    def __init__(self, workspace: WorkspaceClient) -> None:
        self._workspace = workspace

    async def evaluate(self, run_id: str) -> Document3PilotReport:
        inventory = await self._workspace.inventory(run_id)
        paths = {item.relative_path for item in inventory.files}
        if "output/work/worklist.jsonl" in paths:
            return await self._evaluate_initialize(run_id, paths)
        return await self._evaluate_maintenance(run_id, paths)

    async def _evaluate_initialize(self, run_id: str, paths: set[str]) -> Document3PilotReport:
        worklist = await self._jsonl(run_id, "output/work/worklist.jsonl", WorklistEntry)
        calibration = await self._jsonl(
            run_id, "output/work/calibration_log.jsonl", CalibrationLogEntry
        )
        trigger_calibrations = (
            await self._jsonl(
                run_id,
                "output/work/trigger_calibrations.jsonl",
                TriggerCalibrationRecord,
            )
            if "output/work/trigger_calibrations.jsonl" in paths
            else []
        )
        trigger_state = (
            await self._json(
                run_id,
                "output/work/trigger_calibration_state.json",
                TriggerCalibrationState,
            )
            if "output/work/trigger_calibration_state.json" in paths
            else TriggerCalibrationState()
        )
        policy_paths = sorted(
            path
            for path in paths
            if path.startswith("output/work/policies/") and path.endswith(".json")
        )
        policies = [await self._json(run_id, path, Policy) for path in policy_paths]
        wave = (
            await self._json(run_id, "output/work/wave_state.json", WaveState)
            if "output/work/wave_state.json" in paths
            else WaveState()
        )
        calibration_paths = {item.path_id for item in calibration}
        calibration_required = {
            item.path_id for item in worklist if not item.d2_boundary_sufficient
        }
        counts = [len(item.activation_conditions) for item in policies]
        warnings: list[str] = []
        if not worklist:
            warnings.append("Agent did not establish a Worklist")
        if calibration_required - calibration_paths:
            warnings.append("Some explicit calibration gaps have no calibration log entry")
        trigger_record_paths = {item.path_id for item in trigger_calibrations}
        ready_paths = {
            item.path_id
            for item in trigger_state.path_dispositions
            if item.disposition is TriggerDisposition.TRIGGER_READY
        }
        if ready_paths - trigger_record_paths:
            warnings.append("Some TRIGGER_READY paths have no strict Trigger record")
        return Document3PilotReport(
            run_id=run_id,
            mode="O3_INITIALIZE",
            gap_count=len({(x.shell_id, x.expectation_id, x.gap_id) for x in worklist}),
            path_count=len(worklist),
            policy_count=len(policies),
            unresolved_path_count=sum(x.status is PathStatus.UNRESOLVED for x in worklist),
            calibration_entry_count=len(calibration),
            web_calibration_count=sum(x.source_kind.value == "WEB" for x in calibration),
            data_mcp_calibration_count=sum(x.source_kind.value == "DATA_MCP" for x in calibration),
            missing_calibration_link_count=len(calibration_required - calibration_paths),
            single_condition_policy_ratio=(
                sum(count == 1 for count in counts) / len(counts) if counts else 0
            ),
            max_condition_count=max(counts, default=0),
            completed_shell_count=len(wave.completed_shell_ids),
            trigger_ready_path_count=sum(
                item.disposition is TriggerDisposition.TRIGGER_READY
                for item in trigger_state.path_dispositions
            ),
            trigger_unresolved_path_count=sum(
                item.disposition is TriggerDisposition.TRIGGER_UNRESOLVED
                for item in trigger_state.path_dispositions
            ),
            trigger_calibration_completed_shell_count=len(trigger_state.completed_shell_ids),
            maintenance_candidate_count=0,
            warnings=warnings,
        )

    async def _evaluate_maintenance(self, run_id: str, paths: set[str]) -> Document3PilotReport:
        candidates = (
            await self._jsonl(
                run_id,
                "output/work/maintenance_candidates.jsonl",
                MaintenanceCandidate,
            )
            if "output/work/maintenance_candidates.jsonl" in paths
            else []
        )
        return Document3PilotReport(
            run_id=run_id,
            mode="O3_MAINTAIN",
            gap_count=0,
            path_count=0,
            policy_count=0,
            unresolved_path_count=0,
            calibration_entry_count=0,
            web_calibration_count=0,
            data_mcp_calibration_count=0,
            missing_calibration_link_count=0,
            single_condition_policy_ratio=0,
            max_condition_count=0,
            completed_shell_count=0,
            maintenance_candidate_count=len(candidates),
            warnings=[],
        )

    async def _jsonl(self, run_id: str, path: str, model: type[ModelT]) -> list[ModelT]:
        response = await self._workspace.read_text(run_id, path)
        return [
            model.model_validate_json(line)
            for line in (response.content or "").splitlines()
            if line.strip()
        ]

    async def _json(self, run_id: str, path: str, model: type[ModelT]) -> ModelT:
        response = await self._workspace.read_text(run_id, path)
        if response.content is None:
            raise ValueError(f"workspace file has no text content: {path}")
        return model.model_validate_json(response.content)
