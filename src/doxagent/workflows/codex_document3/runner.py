"""Single-thread O3 workspace runner for initialize, review, and maintenance."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ValidationError

from doxagent.codex_runtime.client import CodexWorkerClient, WorkspaceClient
from doxagent.codex_runtime.schema import (
    CODEX_DOCUMENT3_WORKFLOW_VERSION,
    AttemptStatus,
    CodexD3AgentRole,
    CodexD3Node,
    CodexResearchNode,
    NodeAttempt,
    ResearchLane,
    ThreadRecord,
    utc_now,
)
from doxagent.codex_worker.schema import WorkerJob, WorkerRunRequest

from .schema import (
    CalibrationLogEntry,
    Document3InitializeTask,
    O3RunResult,
    O3RunStatus,
    ReviewIssue,
    ReviewResult,
    SemanticDiagnostics,
    TriggerCalibrationRecord,
    TriggerCalibrationRunResult,
    TriggerCalibrationStageStatus,
    TriggerCalibrationState,
    WaveState,
    WorklistEntry,
    strict_json_schema,
)

INITIALIZE_BUSINESS_INPUT_PATHS = (
    "context/document3/document2.json",
    "context/document3/reference_event_view.md",
    "context/document3/previous_policy_set.json",
    "context/document3/task.json",
)
INPUT_MANIFEST_PATH = "context/document3/input_manifest.json"


class O3ExecutionStateRepository(Protocol):
    def next_attempt_number(self, run_id: str, node: CodexResearchNode) -> int: ...

    def save_attempt(self, attempt: NodeAttempt) -> None: ...

    def save_thread(self, record: ThreadRecord) -> None: ...

    def get_thread(self, run_id: str, agent_role: str) -> ThreadRecord | None: ...


class O3TurnError(RuntimeError):
    def __init__(self, message: str, *, job: WorkerJob | None = None) -> None:
        super().__init__(message)
        self.job = job


class Document3AgentRunner:
    def __init__(
        self,
        *,
        worker: CodexWorkerClient,
        workspace: WorkspaceClient,
        prompt_root: str | Path | None = None,
        model: str,
        model_provider: str | None,
        effort: Literal["low", "medium", "high", "xhigh", "max"] = "max",
        timeout_seconds: int = 1800,
        runtime_repository: O3ExecutionStateRepository | None = None,
    ) -> None:
        self._worker = worker
        self.workspace = workspace
        self._prompt_root = (
            Path(prompt_root)
            if prompt_root is not None
            else Path(__file__).resolve().parents[4] / "prompts" / "codex_v2" / "document3"
        )
        self._model = model
        self._model_provider = model_provider
        self._effort = effort
        self._timeout_seconds = timeout_seconds
        self._runtime_repository = runtime_repository

    async def seed_initialize(
        self,
        *,
        run_id: str,
        document2_json: str,
        reference_view: str,
        previous_policy_set_json: str | None,
        task: Document3InitializeTask,
    ) -> None:
        files = self._load_prompt_assets(
            {
                "context/document3/AGENTS.md": "AGENTS.md",
                "context/document3/agent.md": "agents/o3.md",
                "context/document3/foundation.md": "skills/foundation.md",
                "context/document3/initialize_trigger_calibration.md": (
                    "skills/initialize_trigger_calibration.md"
                ),
                "context/document3/initialize_policy_compile.md": (
                    "skills/initialize_policy_compile.md"
                ),
                "context/document3/initialize_final_review.md": (
                    "skills/initialize_final_review.md"
                ),
                "context/document3/policy_set.schema.json": ("schemas/policy_set.schema.json"),
            }
        )
        files.update(
            {
                "context/document3/document2.json": document2_json,
                "context/document3/reference_event_view.md": reference_view,
                "context/document3/previous_policy_set.json": (
                    previous_policy_set_json or "null\n"
                ),
                "context/document3/task.json": task.model_dump_json(indent=2),
                "context/document3/trigger_calibration_record.schema.json": json.dumps(
                    strict_json_schema(TriggerCalibrationRecord.model_json_schema()),
                    ensure_ascii=False,
                    indent=2,
                ),
                "context/document3/trigger_calibration_state.schema.json": json.dumps(
                    strict_json_schema(TriggerCalibrationState.model_json_schema()),
                    ensure_ascii=False,
                    indent=2,
                ),
                "context/document3/worklist.schema.json": json.dumps(
                    strict_json_schema(WorklistEntry.model_json_schema()),
                    ensure_ascii=False,
                    indent=2,
                ),
                "context/document3/calibration_log.schema.json": json.dumps(
                    strict_json_schema(CalibrationLogEntry.model_json_schema()),
                    ensure_ascii=False,
                    indent=2,
                ),
                "context/document3/wave_state.schema.json": json.dumps(
                    strict_json_schema(WaveState.model_json_schema()),
                    ensure_ascii=False,
                    indent=2,
                ),
                "output/work/worklist.jsonl": "",
                "output/work/calibration_log.jsonl": "",
                "output/work/trigger_calibrations.jsonl": "",
                "output/work/trigger_calibration_state.json": (
                    TriggerCalibrationState(
                        stage_status=TriggerCalibrationStageStatus.IN_PROGRESS
                    ).model_dump_json(indent=2)
                ),
                "output/work/wave_state.json": json.dumps(
                    {
                        "completed_shell_ids": [],
                        "current_shell_id": None,
                        "completed_path_ids": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            }
        )
        for path, content in files.items():
            await self.workspace.write_text(run_id, path, content)

    async def seed_maintenance(
        self,
        *,
        run_id: str,
        policy_set_json: str,
        reference_view: str,
        metadata: dict[str, Any],
        maintenance_feed_json: str | None = None,
    ) -> None:
        files = self._load_prompt_assets(
            {
                "context/document3/AGENTS.md": "AGENTS.md",
                "context/document3/agent.md": "agents/o3.md",
                "context/document3/foundation.md": "skills/foundation.md",
                "context/document3/maintain.md": "skills/maintain.md",
                "context/document3/policy_set.schema.json": ("schemas/policy_set.schema.json"),
                "context/document3/policy_patch.schema.json": ("schemas/policy_patch.schema.json"),
            }
        )
        files.update(
            {
                "context/document3/current_policy_set.json": policy_set_json,
                "context/document3/reference_event_view.md": reference_view,
                "context/document3/task.json": json.dumps(
                    metadata, ensure_ascii=False, indent=2, default=str
                ),
                "output/work/maintenance_candidates.jsonl": "",
            }
        )
        if maintenance_feed_json is not None:
            files["context/document3/runtime_maintenance_feed.json"] = maintenance_feed_json
        for path, content in files.items():
            await self.workspace.write_text(run_id, path, content)

    def _load_prompt_assets(self, assets: dict[str, str]) -> dict[str, str]:
        loaded: dict[str, str] = {}
        for target, source in assets.items():
            source_path = self._prompt_root / source
            if not source_path.is_file():
                raise FileNotFoundError(
                    "D3 prompt/skill layer is not installed for the refactored "
                    f"orchestration: {source_path}"
                )
            loaded[target] = source_path.read_text(encoding="utf-8")
        return loaded

    async def run_trigger_calibration(
        self,
        *,
        run_id: str,
        ticker: str,
        cutoff_at: datetime,
        thread_id: str | None = None,
    ) -> tuple[TriggerCalibrationRunResult, str | None]:
        return await self._run_with_resume(
            run_id=run_id,
            ticker=ticker,
            cutoff_at=cutoff_at,
            node=CodexD3Node.O3_TRIGGER_CALIBRATION,
            output_model=TriggerCalibrationRunResult,
            max_attempts=2,
            thread_id=thread_id,
            required_context_paths=(
                "context/document3/task.json",
                "context/document3/AGENTS.md",
                "context/document3/agent.md",
                "context/document3/foundation.md",
                "context/document3/initialize_trigger_calibration.md",
                "context/document3/trigger_calibration_record.schema.json",
                "context/document3/trigger_calibration_state.schema.json",
                "context/document3/worklist.schema.json",
                "context/document3/document2.json",
                "context/document3/reference_event_view.md",
                "context/document3/previous_policy_set.json",
                "output/work/worklist.jsonl",
                "output/work/trigger_calibrations.jsonl",
                "output/work/trigger_calibration_state.json",
            ),
            instruction=(
                "Complete every Shell Trigger Calibration wave before any Policy drafting. "
                "Keep Worklist status PENDING; persist all dispositions and Stage-A progress."
            ),
        )

    async def run_policy_compile(
        self,
        *,
        run_id: str,
        ticker: str,
        cutoff_at: datetime,
        thread_id: str | None,
    ) -> tuple[O3RunResult, str | None]:
        return await self._run_with_resume(
            run_id=run_id,
            ticker=ticker,
            cutoff_at=cutoff_at,
            node=CodexD3Node.O3_POLICY_COMPILE,
            output_model=O3RunResult,
            max_attempts=2,
            thread_id=thread_id,
            required_context_paths=(
                "context/document3/task.json",
                "context/document3/AGENTS.md",
                "context/document3/agent.md",
                "context/document3/foundation.md",
                "context/document3/initialize_policy_compile.md",
                "context/document3/policy_set.schema.json",
                "context/document3/worklist.schema.json",
                "context/document3/calibration_log.schema.json",
                "context/document3/wave_state.schema.json",
                "context/document3/document2.json",
                "context/document3/previous_policy_set.json",
                "output/work/trigger_calibrations.jsonl",
                "output/work/trigger_calibration_state.json",
                "output/work/worklist.jsonl",
                "output/work/calibration_log.jsonl",
                "output/work/wave_state.json",
                "output/work/policies/",
            ),
            instruction=(
                "Compile the frozen Stage-A Trigger surface in Shell waves. Do not rebuild "
                "the path surface. Update final Worklist statuses, Policy drafts, calibration "
                "compatibility log, and wave_state. Resume existing compile checkpoints."
            ),
        )

    async def run_final_review(
        self,
        *,
        run_id: str,
        ticker: str,
        cutoff_at: datetime,
        thread_id: str | None,
    ) -> tuple[ReviewResult, str | None]:
        return await self._run_with_resume(
            run_id=run_id,
            ticker=ticker,
            cutoff_at=cutoff_at,
            node=CodexD3Node.O3_FINAL_REVIEW,
            output_model=ReviewResult,
            max_attempts=2,
            thread_id=thread_id,
            required_context_paths=(
                "context/document3/task.json",
                "context/document3/AGENTS.md",
                "context/document3/agent.md",
                "context/document3/foundation.md",
                "context/document3/initialize_final_review.md",
                "context/document3/semantic_diagnostics.json",
                "context/document3/semantic_diagnostics.schema.json",
                "context/document3/policy_set.schema.json",
                "context/document3/worklist.schema.json",
                "context/document3/calibration_log.schema.json",
                "context/document3/wave_state.schema.json",
                "context/document3/document2.json",
                "context/document3/reference_event_view.md",
                "context/document3/previous_policy_set.json",
                "output/work/worklist.jsonl",
                "output/work/trigger_calibrations.jsonl",
                "output/work/trigger_calibration_state.json",
                "output/work/calibration_log.jsonl",
                "output/work/wave_state.json",
                "output/work/coverage_map.json",
                "output/work/policies/",
            ),
            instruction=(
                "Perform the Final Global Pass. You may directly edit Policy drafts and all "
                "related work files. If Trigger semantics change, synchronize the strict "
                "Trigger Calibration artifact/state before returning. Read semantic_diagnostics "
                "and explicitly report diagnostics_reviewed plus a substantive "
                "diagnostics_explanation; significant patterns require repair, explanation, "
                "or a retained issue."
            ),
        )

    async def run_maintain(
        self, *, run_id: str, ticker: str, cutoff_at: datetime
    ) -> tuple[O3RunResult, str | None]:
        return await self._run_with_resume(
            run_id=run_id,
            ticker=ticker,
            cutoff_at=cutoff_at,
            node=CodexD3Node.O3_MAINTAIN,
            output_model=O3RunResult,
            max_attempts=2,
            required_context_paths=(
                "context/document3/task.json",
                "context/document3/AGENTS.md",
                "context/document3/agent.md",
                "context/document3/foundation.md",
                "context/document3/maintain.md",
                "context/document3/policy_patch.schema.json",
                "context/document3/current_policy_set.json",
                "context/document3/reference_event_view.md",
            ),
            instruction=(
                "Scan the Reference View Delta and, when present, the complete local "
                "runtime_maintenance_feed.json (Trade, BADCASE, and W3 coverage-gap "
                "records). Write "
                "output/work/policy_patch.json."
            ),
        )

    async def _run_with_resume(
        self,
        *,
        run_id: str,
        ticker: str,
        cutoff_at: datetime,
        node: CodexD3Node,
        output_model: type[BaseModel],
        max_attempts: int,
        required_context_paths: tuple[str, ...],
        instruction: str,
        thread_id: str | None = None,
    ) -> tuple[Any, str | None]:
        schema = await self.prepare_node_contracts(
            run_id=run_id,
            node=node,
            output_model=output_model,
        )
        schema_path = f"context/document3/{node.value}.output_schema.json"
        last_job: WorkerJob | None = None
        current_thread = thread_id or self._load_saved_thread(run_id)
        first_attempt = (
            self._runtime_repository.next_attempt_number(run_id, node)
            if self._runtime_repository is not None
            else 1
        )
        for offset in range(max_attempts):
            attempt_number = first_attempt + offset
            attempt_id = f"{node.value}-{attempt_number:02d}"
            prompt = (
                f"D3 node {node.value}; attempt {attempt_id}. Read these frozen/local "
                f"workspace paths in order: {', '.join(required_context_paths)}, and "
                f"{schema_path}. {instruction} Return only the small JSON result."
            )
            if offset > 0 or attempt_number > 1:
                prompt += (
                    " This is a resume attempt: preserve completed waves and continue from "
                    "the existing node checkpoint without clearing workspace artifacts."
                )
            attempt = NodeAttempt(
                attempt_id=attempt_id,
                workflow_version=CODEX_DOCUMENT3_WORKFLOW_VERSION,
                research_lane=ResearchLane.DOCUMENT3,
                cutoff_at=cutoff_at,
                ticker=ticker.upper(),
                run_id=run_id,
                node=node,
                status=AttemptStatus.RUNNING,
                attempt_number=attempt_number,
                thread_id=current_thread,
                started_at=utc_now(),
            )
            self._save_attempt(attempt)
            request = WorkerRunRequest(
                workflow_version=CODEX_DOCUMENT3_WORKFLOW_VERSION,
                research_lane=ResearchLane.DOCUMENT3,
                run_id=run_id,
                ticker=ticker.upper(),
                node=node,
                agent_role=CodexD3AgentRole.O3,
                attempt_id=attempt_id,
                cutoff_at=cutoff_at,
                prompt=prompt,
                output_schema=schema,
                thread_id=current_thread,
                model=self._model,
                model_provider=self._model_provider,
                effort=self._effort,
                timeout_seconds=self._timeout_seconds,
                allow_subagents=False,
                max_subagents=0,
            )
            try:
                last_job = await self._worker.run(request)
            except Exception as exc:
                self._save_attempt(
                    attempt.model_copy(
                        update={
                            "status": AttemptStatus.FAILED,
                            "error_code": "WORKER_ERROR",
                            "error_message": str(exc)[:4000],
                            "completed_at": utc_now(),
                        }
                    )
                )
                continue
            current_thread = last_job.thread_id or current_thread
            self._save_thread(run_id, ticker, current_thread)
            if last_job.status != "succeeded":
                status = (
                    AttemptStatus.CANCELLED
                    if last_job.status == "cancelled"
                    else AttemptStatus.FAILED
                )
                self._save_attempt(
                    attempt.model_copy(
                        update={
                            "status": status,
                            "thread_id": current_thread,
                            "error_code": "WORKER_TURN_FAILED",
                            "error_message": (last_job.error_message or last_job.status)[:4000],
                            "completed_at": utc_now(),
                        }
                    )
                )
                continue
            if not last_job.final_response:
                fallback = self._fallback_result(
                    output_model,
                    reason="SDK turn succeeded without a structured final response",
                )
                self._save_attempt(
                    attempt.model_copy(
                        update={
                            "status": AttemptStatus.SUCCEEDED,
                            "thread_id": current_thread,
                            "error_code": "RECOVERED_MISSING_STRUCTURED_OUTPUT",
                            "error_message": (
                                "Workspace artifacts, not the missing response receipt, "
                                "will decide node progression."
                            ),
                            "completed_at": utc_now(),
                        }
                    )
                )
                return fallback, current_thread
            try:
                result = output_model.model_validate_json(last_job.final_response)
            except ValidationError as exc:
                fallback = self._fallback_result(
                    output_model,
                    reason="SDK turn returned an invalid structured response",
                )
                self._save_attempt(
                    attempt.model_copy(
                        update={
                            "status": AttemptStatus.SUCCEEDED,
                            "thread_id": current_thread,
                            "error_code": "RECOVERED_INVALID_STRUCTURED_OUTPUT",
                            "error_message": str(exc)[:4000],
                            "completed_at": utc_now(),
                        }
                    )
                )
                return fallback, current_thread
            self._save_attempt(
                attempt.model_copy(
                    update={
                        "status": AttemptStatus.SUCCEEDED,
                        "thread_id": current_thread,
                        "completed_at": utc_now(),
                    }
                )
            )
            return result, current_thread
        raise O3TurnError(f"{node.value} failed after {max_attempts} attempts", job=last_job)

    async def prepare_node_contracts(
        self,
        *,
        run_id: str,
        node: CodexD3Node,
        output_model: type[BaseModel] | None = None,
    ) -> dict[str, Any]:
        """Materialize runtime-owned schemas before the agent write snapshot.

        The orchestrator calls this before taking its boundary baseline.  Keeping
        these deterministic writes outside the SDK turn prevents the boundary
        guard from attributing runtime schema creation to the model.
        """

        model_by_node: dict[CodexD3Node, type[BaseModel]] = {
            CodexD3Node.O3_TRIGGER_CALIBRATION: TriggerCalibrationRunResult,
            CodexD3Node.O3_POLICY_COMPILE: O3RunResult,
            CodexD3Node.O3_FINAL_REVIEW: ReviewResult,
            CodexD3Node.O3_MAINTAIN: O3RunResult,
        }
        selected_model = output_model or model_by_node[node]
        await self._ensure_recoverable_artifact_schemas(run_id)
        schema = cast(
            dict[str, Any],
            strict_json_schema(selected_model.model_json_schema()),
        )
        await self.workspace.write_text(
            run_id,
            f"context/document3/{node.value}.output_schema.json",
            json.dumps(schema, ensure_ascii=False, indent=2),
        )
        return schema

    async def _ensure_recoverable_artifact_schemas(self, run_id: str) -> None:
        schemas: dict[str, type[BaseModel]] = {
            "context/document3/worklist.schema.json": WorklistEntry,
            "context/document3/calibration_log.schema.json": CalibrationLogEntry,
            "context/document3/wave_state.schema.json": WaveState,
            "context/document3/semantic_diagnostics.schema.json": SemanticDiagnostics,
        }
        for path, model in schemas.items():
            await self.workspace.write_text(
                run_id,
                path,
                json.dumps(
                    strict_json_schema(model.model_json_schema()),
                    ensure_ascii=False,
                    indent=2,
                ),
            )

    @staticmethod
    def _fallback_result(output_model: type[BaseModel], *, reason: str) -> BaseModel:
        """Return an advisory receipt when the SDK turn itself succeeded.

        The orchestrator still inspects and normalizes the workspace.  This fallback
        cannot turn absent business artifacts into a successful node.
        """

        if output_model is TriggerCalibrationRunResult:
            return TriggerCalibrationRunResult(status="COMPLETED")
        if output_model is O3RunResult:
            return O3RunResult(status=O3RunStatus.PARTIAL, warning_count=1)
        if output_model is ReviewResult:
            return ReviewResult(
                status="PASSED",
                issue_count=1,
                blocking_issue_count=0,
                issues=[
                    ReviewIssue(
                        code="RECOVERED_STRUCTURED_RESPONSE",
                        message=reason,
                    )
                ],
                diagnostics_reviewed=False,
                diagnostics_explanation=reason,
            )
        raise O3TurnError(f"No artifact-first fallback is defined for {output_model.__name__}")

    def _load_saved_thread(self, run_id: str) -> str | None:
        if self._runtime_repository is None:
            return None
        record = self._runtime_repository.get_thread(run_id, CodexD3AgentRole.O3.value)
        return record.thread_id if record is not None else None

    def _save_thread(self, run_id: str, ticker: str, thread_id: str | None) -> None:
        if self._runtime_repository is None or thread_id is None:
            return
        prior = self._runtime_repository.get_thread(run_id, CodexD3AgentRole.O3.value)
        self._runtime_repository.save_thread(
            ThreadRecord(
                workflow_version=CODEX_DOCUMENT3_WORKFLOW_VERSION,
                research_lane=ResearchLane.DOCUMENT3,
                ticker=ticker.upper(),
                run_id=run_id,
                agent_role=CodexD3AgentRole.O3,
                thread_id=thread_id,
                model=self._model,
                model_provider=self._model_provider,
                created_at=prior.created_at if prior is not None else utc_now(),
                updated_at=utc_now(),
            )
        )

    def _save_attempt(self, attempt: NodeAttempt) -> None:
        if self._runtime_repository is not None:
            self._runtime_repository.save_attempt(attempt)
