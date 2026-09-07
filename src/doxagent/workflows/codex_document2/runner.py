"""One durable Codex turn for O0/O1 and resumed D1 domain-review threads."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from doxagent.codex_runtime.client import CodexWorkerClient, WorkspaceClient
from doxagent.codex_runtime.repository import CodexRuntimeRepository
from doxagent.codex_runtime.schema import (
    CODEX_DOCUMENT2_WORKFLOW_VERSION,
    ArtifactKind,
    ArtifactRef,
    AttemptStatus,
    CitationManifest,
    CodexD2Node,
    CodexResearchAgentRole,
    NodeAttempt,
    ResearchLane,
    utc_now,
)
from doxagent.codex_worker.schema import WorkerJob, WorkerRunRequest
from doxagent.model_usage.repository import ModelUsageRepository
from doxagent.model_usage.schema import ModelUsageEvent
from doxagent.observations.promotion import CitationPromotionService
from doxagent.ticker_initialization.substeps import attempt_identity, durable
from doxagent.workflows.codex_document2.errors import (
    format_execution_error,
    raised_worker_error,
    worker_execution_error,
)
from doxagent.workflows.codex_document2.schema import strict_json_schema

OutputT = TypeVar("OutputT", bound=BaseModel)
_BARE_ALIAS = re.compile(r"^(?:【cite:)?(O[1-9]\d*)(?:】)?$")


@dataclass(frozen=True)
class Document2TurnResult:
    output: BaseModel
    artifact: ArtifactRef
    attempt: NodeAttempt
    job: WorkerJob
    thread_id: str | None
    citation_manifest: CitationManifest
    workspace_run_id: str


class Document2TurnRunner:
    def __init__(
        self,
        *,
        worker: CodexWorkerClient,
        workspace: WorkspaceClient,
        repository: CodexRuntimeRepository,
        model: str,
        model_provider: str | None,
        effort: Literal["low", "medium", "high", "xhigh", "max"],
        timeout_seconds: int,
        max_subagents: int,
        usage_repository: ModelUsageRepository | None = None,
        asset_root: str | Path | None = None,
    ) -> None:
        from doxagent.ticker_initialization.substeps import DurableWorker

        self._worker = DurableWorker(worker)
        self._workspace = workspace
        self._repository = repository
        self._model = model
        self._model_provider = model_provider
        self._effort = effort
        self._timeout = timeout_seconds
        self._max_subagents = max_subagents
        self._usage = usage_repository
        self._citations = CitationPromotionService(repository)
        self._assets = (
            Path(asset_root)
            if asset_root is not None
            else (
                Path(__file__).resolve().parents[4]
                / "prompts"
                / "codex_v2"
                / "document2"
            )
        )

    @durable("d2")
    async def run(
        self,
        *,
        persistence_run_id: str,
        workspace_run_id: str,
        ticker: str,
        cutoff_at: datetime,
        node: CodexD2Node,
        role: CodexResearchAgentRole,
        context: dict[str, object],
        output_model: type[OutputT],
        agent_asset: str,
        skill_asset: str,
        thread_id: str | None = None,
        allow_subagents: bool = False,
        previous_failure: str | None = None,
        artifact_key: str | None = None,
    ) -> Document2TurnResult:
        attempt_number = self._next_attempt_number(persistence_run_id, node)
        attempt_id = attempt_identity(self._attempt_id(node, attempt_number))
        context_text = json.dumps(context, ensure_ascii=False, indent=2, default=str)
        input_hash = hashlib.sha256(context_text.encode("utf-8")).hexdigest()
        attempt = NodeAttempt(
            workflow_version=CODEX_DOCUMENT2_WORKFLOW_VERSION,
            research_lane=ResearchLane.DOCUMENT2,
            attempt_id=attempt_id,
            cutoff_at=cutoff_at,
            ticker=ticker,
            run_id=persistence_run_id,
            node=node,
            status=AttemptStatus.RUNNING,
            attempt_number=attempt_number,
            thread_id=thread_id,
            input_sha256=input_hash,
            started_at=utc_now(),
        )
        self._repository.save_attempt(attempt)
        job: WorkerJob | None = None
        try:
            output_schema = strict_json_schema(output_model.model_json_schema())
            await self._seed_attempt(
                workspace_run_id=workspace_run_id,
                attempt_id=attempt_id,
                node=node,
                context_text=context_text,
                agent_asset=agent_asset,
                skill_asset=skill_asset,
                output_schema=output_schema,
                previous_failure=previous_failure,
            )
            prompt = (
                f"Document2 node: {node.value}. Attempt: {attempt_id}. "
                f"Read attempts/{attempt_id}/input/AGENTS.md, agent.md, skill.md, task.json, "
                "and context.json in that order. Follow those files exactly. Return one JSON "
                "object matching output_schema.json."
            )
            request = WorkerRunRequest(
                workflow_version=CODEX_DOCUMENT2_WORKFLOW_VERSION,
                research_lane=ResearchLane.DOCUMENT2,
                run_id=workspace_run_id,
                ticker=ticker,
                node=node,
                agent_role=role,
                attempt_id=attempt_id,
                cutoff_at=cutoff_at,
                prompt=prompt,
                output_schema=output_schema,
                thread_id=thread_id,
                model=self._model,
                model_provider=self._model_provider,
                effort=self._effort,
                timeout_seconds=self._timeout,
                allow_subagents=allow_subagents,
                max_subagents=self._max_subagents if allow_subagents else 0,
            )
            try:
                job = await self._worker.run(request)
            except Exception as exc:
                raise raised_worker_error(exc, node) from exc
            if job.status != "succeeded" or not job.final_response:
                raise worker_execution_error(job, node)
            try:
                parsed = output_model.model_validate_json(job.final_response)
            except ValidationError as exc:
                raise format_execution_error(exc, node) from exc
            output_json = parsed.model_dump_json(indent=2)
            local_manifest = await self._promote_citations(
                workspace_run_id=workspace_run_id,
                attempt_id=attempt_id,
                output_json=output_json,
            )
            try:
                qualified = output_model.model_validate(
                    _qualify_local_aliases(parsed.model_dump(mode="json"), attempt_id)
                )
            except ValidationError as exc:
                raise format_execution_error(exc, node) from exc
            qualified_json = qualified.model_dump_json(indent=2)
            child_path = f"artifacts/snapshots/{attempt_id}.json"
            await self._workspace.write_text(workspace_run_id, child_path, qualified_json)
            key = artifact_key or node.value
            parent_path = f"artifacts/document2/turns/{key}/{attempt_id}.json"
            metadata = await self._workspace.write_text(
                persistence_run_id, parent_path, qualified_json
            )
            artifact = ArtifactRef(
                workflow_version=CODEX_DOCUMENT2_WORKFLOW_VERSION,
                research_lane=ResearchLane.DOCUMENT2,
                artifact_id=uuid4().hex,
                run_id=persistence_run_id,
                node=node,
                attempt_id=attempt_id,
                kind=ArtifactKind.STRUCTURED_COMPLETION,
                relative_path=metadata.relative_path,
                sha256=metadata.sha256,
                size_bytes=metadata.size_bytes,
                content_type="application/json",
            )
            self._repository.save_artifact(artifact)
            attempt = attempt.model_copy(
                update={
                    "status": AttemptStatus.SUCCEEDED,
                    "thread_id": job.thread_id or thread_id,
                    "completed_at": utc_now(),
                }
            )
            self._repository.save_attempt(attempt)
            self._record_usage(request, job, status="succeeded")
            return Document2TurnResult(
                output=qualified,
                artifact=artifact,
                attempt=attempt,
                job=job,
                thread_id=job.thread_id or thread_id,
                citation_manifest=local_manifest,
                workspace_run_id=workspace_run_id,
            )
        except Exception as exc:
            attempt = attempt.model_copy(
                update={
                    "status": AttemptStatus.FAILED,
                    "thread_id": job.thread_id if job and job.thread_id else thread_id,
                    "error_code": getattr(exc, "code", "D2_TURN_FAILED"),
                    "error_message": _bounded(str(exc) or type(exc).__name__),
                    "completed_at": utc_now(),
                }
            )
            self._repository.save_attempt(attempt)
            if job is not None:
                self._record_usage(
                    request,
                    job,
                    status="failed",
                    error_code=attempt.error_code,
                    error_message=attempt.error_message,
                )
            raise

    async def _seed_attempt(
        self,
        *,
        workspace_run_id: str,
        attempt_id: str,
        node: CodexD2Node,
        context_text: str,
        agent_asset: str,
        skill_asset: str,
        output_schema: dict[str, object],
        previous_failure: str | None,
    ) -> None:
        root = f"attempts/{attempt_id}/input"
        task = {
            "schema_version": "document2-task-v1",
            "node": node.value,
            "required_files": [
                f"{root}/AGENTS.md",
                f"{root}/agent.md",
                f"{root}/skill.md",
                f"{root}/context.json",
                f"{root}/output_schema.json",
            ],
            "previous_failure": previous_failure,
        }
        files = {
            "AGENTS.md": self._read_asset("AGENTS.md"),
            "agent.md": self._read_asset(agent_asset),
            "skill.md": self._read_asset(skill_asset),
            "context.json": context_text,
            "output_schema.json": json.dumps(output_schema, ensure_ascii=False, indent=2),
            "task.json": json.dumps(task, ensure_ascii=False, indent=2),
        }
        for name, content in files.items():
            await self._workspace.write_text(workspace_run_id, f"{root}/{name}", content)

    async def _promote_citations(
        self, *, workspace_run_id: str, attempt_id: str, output_json: str
    ) -> CitationManifest:
        artifact_id = f"d2-local-{attempt_id}"
        try:
            observations = await self._workspace.read_attempt_observations(
                workspace_run_id, attempt_id
            )
            return self._citations.promote(
                run_id=workspace_run_id,
                attempt_id=attempt_id,
                artifact_id=artifact_id,
                anchor=attempt_id,
                markdown=output_json,
                observations=list(observations),
            )
        except Exception as exc:
            manifest = CitationManifest(
                run_id=workspace_run_id,
                artifact_id=artifact_id,
                warnings=[f"non-blocking citation promotion failure: {_bounded(str(exc))}"],
            )
            try:
                self._repository.save_citation_manifest(manifest)
            except Exception:
                pass
            return manifest

    def _read_asset(self, relative_path: str) -> str:
        path = (self._assets / relative_path).resolve()
        if self._assets.resolve() not in path.parents and path != self._assets.resolve():
            raise ValueError("Document2 asset path escaped its root")
        return path.read_text(encoding="utf-8")

    def _next_attempt_number(self, run_id: str, node: CodexD2Node) -> int:
        return self._repository.next_attempt_number(run_id, node)

    @staticmethod
    def _attempt_id(node: CodexD2Node, number: int) -> str:
        return f"d2-{node.value.removeprefix('d2_')[:42]}-{number}-{uuid4().hex[:10]}"

    def _record_usage(
        self,
        request: WorkerRunRequest,
        job: WorkerJob,
        *,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if self._usage is None or job.telemetry is None:
            return
        usage = job.telemetry.usage
        self._usage.save_event(
            ModelUsageEvent(
                provider=request.model_provider or "openai",
                model=request.model or "codex-default",
                status=status,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
                latency_seconds=(
                    job.telemetry.worker_wall_time_ms / 1000
                    if job.telemetry.worker_wall_time_ms is not None
                    else None
                ),
                ticker=request.ticker,
                run_id=request.run_id,
                workflow_node=request.node.value,
                runtime_node=request.node.value,
                agent_name=request.agent_role.value,
                task_type="codex_document2_turn",
                execution_id=request.attempt_id,
                error_code=error_code,
                error_message=error_message,
                metadata={"thread_id": job.thread_id or "", "turn_id": job.turn_id or ""},
                raw_usage=usage.model_dump(mode="json"),
            )
        )


def logical_workspace_id(run_id: str, logical_key: str) -> str:
    digest = hashlib.sha256(f"{run_id}\0{logical_key}".encode()).hexdigest()[:32]
    return f"d2ws-{digest}"


def _qualify_local_aliases(value: object, attempt_id: str) -> object:
    if isinstance(value, dict):
        return {key: _qualify_local_aliases(item, attempt_id) for key, item in value.items()}
    if isinstance(value, list):
        return [_qualify_local_aliases(item, attempt_id) for item in value]
    if isinstance(value, str):
        match = _BARE_ALIAS.fullmatch(value.strip())
        if match:
            return f"D2REF:{attempt_id}:{match.group(1)}"
    return value


def _bounded(value: str, limit: int = 2_000) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."
