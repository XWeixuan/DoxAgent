"""Single-thread O3 workspace runner for initialize, review, and maintenance."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from doxagent.codex_runtime.client import CodexWorkerClient, WorkspaceClient
from doxagent.codex_runtime.schema import (
    CODEX_DOCUMENT3_WORKFLOW_VERSION,
    CodexD3AgentRole,
    CodexD3Node,
    ResearchLane,
)
from doxagent.codex_worker.schema import WorkerJob, WorkerRunRequest

from .schema import O3RunResult, ReviewResult, strict_json_schema


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

    async def seed_initialize(
        self,
        *,
        run_id: str,
        document2_json: str,
        reference_view: str,
        previous_policy_set_json: str | None,
        metadata: dict[str, Any],
    ) -> None:
        await self._seed_shared(run_id)
        files = {
            "context/document3/document2.json": document2_json,
            "context/document3/reference_event_view.md": reference_view,
            "context/document3/previous_policy_set.json": previous_policy_set_json or "null\n",
            "context/document3/task.json": json.dumps(
                metadata, ensure_ascii=False, indent=2, default=str
            ),
            "output/work/worklist.jsonl": "",
            "output/work/calibration_log.jsonl": "",
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
        for path, content in files.items():
            await self.workspace.write_text(run_id, path, content)

    async def seed_maintenance(
        self,
        *,
        run_id: str,
        policy_set_json: str,
        reference_view: str,
        metadata: dict[str, Any],
    ) -> None:
        await self._seed_shared(run_id)
        files = {
            "context/document3/current_policy_set.json": policy_set_json,
            "context/document3/reference_event_view.md": reference_view,
            "context/document3/task.json": json.dumps(
                metadata, ensure_ascii=False, indent=2, default=str
            ),
            "output/work/maintenance_candidates.jsonl": "",
        }
        for path, content in files.items():
            await self.workspace.write_text(run_id, path, content)

    async def _seed_shared(self, run_id: str) -> None:
        assets = {
            "context/document3/AGENTS.md": "AGENTS.md",
            "context/document3/agent.md": "agents/o3.md",
            "context/document3/foundation.md": "skills/foundation.md",
            "context/document3/initialize.md": "skills/initialize.md",
            "context/document3/initialize_final_review.md": "skills/initialize_final_review.md",
            "context/document3/maintain.md": "skills/maintain.md",
            "context/document3/policy_set.schema.json": "schemas/policy_set.schema.json",
            "context/document3/policy_patch.schema.json": "schemas/policy_patch.schema.json",
        }
        for target, source in assets.items():
            await self.workspace.write_text(
                run_id, target, (self._prompt_root / source).read_text(encoding="utf-8")
            )

    async def run_initialize(
        self, *, run_id: str, ticker: str, cutoff_at: datetime
    ) -> tuple[O3RunResult, str | None]:
        return await self._run_with_resume(
            run_id=run_id,
            ticker=ticker,
            cutoff_at=cutoff_at,
            node=CodexD3Node.O3_INITIALIZE,
            skill_path="skills/initialize.md",
            output_model=O3RunResult,
            max_attempts=2,
            instruction=(
                "Build worklist/calibration/wave checkpoints and progressive Policy draft "
                "files. Resume from wave_state when prior work exists."
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
            skill_path="skills/initialize_final_review.md",
            output_model=ReviewResult,
            max_attempts=2,
            thread_id=thread_id,
            instruction=(
                "Perform the Final Global Pass. You may directly edit Policy drafts and "
                "related worklist, calibration, wave, and coverage files. Research only "
                "when needed, then leave all files mutually consistent."
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
            skill_path="skills/maintain.md",
            output_model=O3RunResult,
            max_attempts=2,
            instruction="Scan for possible changes and write output/work/policy_patch.json.",
        )

    async def _run_with_resume(
        self,
        *,
        run_id: str,
        ticker: str,
        cutoff_at: datetime,
        node: CodexD3Node,
        skill_path: str,
        output_model: type[BaseModel],
        max_attempts: int,
        instruction: str,
        thread_id: str | None = None,
    ) -> tuple[Any, str | None]:
        schema = strict_json_schema(output_model.model_json_schema())
        schema_path = f"context/document3/{node.value}.output_schema.json"
        skill_context_path = f"context/document3/{Path(skill_path).name}"
        await self.workspace.write_text(
            run_id,
            schema_path,
            json.dumps(schema, ensure_ascii=False, indent=2),
        )
        last_job: WorkerJob | None = None
        current_thread = thread_id
        for attempt_number in range(1, max_attempts + 1):
            attempt_id = f"{node.value}-{attempt_number:02d}"
            prompt = (
                f"D3 node {node.value}; attempt {attempt_id}. Read "
                "context/document3/AGENTS.md, agent.md, foundation.md, "
                f"{skill_context_path}, task.json, and {schema_path}. {instruction} "
                "Return only the small JSON result."
            )
            if attempt_number > 1:
                prompt += (
                    " The previous turn was interrupted or invalid; resume existing "
                    "files without rerunning completed waves."
                )
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
            last_job = await self._worker.run(request)
            current_thread = last_job.thread_id or current_thread
            if last_job.status != "succeeded" or not last_job.final_response:
                continue
            try:
                return output_model.model_validate_json(last_job.final_response), current_thread
            except ValidationError:
                continue
        raise O3TurnError(f"{node.value} failed after {max_attempts} attempts", job=last_job)
