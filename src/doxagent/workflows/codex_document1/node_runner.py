"""Shared single-node lifecycle for D1 orchestration and bounded evaluation loops."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from doxagent.codex_runtime.client import CodexWorkerClient, WorkspaceClient
from doxagent.codex_runtime.errors import StructuredOutputInvalid
from doxagent.codex_runtime.repository import CodexRuntimeRepository
from doxagent.codex_runtime.schema import (
    ArtifactKind,
    ArtifactRef,
    AttemptStatus,
    CitationManifest,
    CodexAgentRole,
    CodexD1Node,
    NodeAttempt,
    ThreadRecord,
    utc_now,
)
from doxagent.codex_worker.schema import WorkerJob, WorkerRunRequest
from doxagent.model_usage.repository import ModelUsageRepository
from doxagent.model_usage.schema import ModelUsageEvent
from doxagent.observations.models import PersistedObservation
from doxagent.observations.pack import render_observation_block
from doxagent.observations.promotion import CitationPromotionService
from doxagent.workflows.codex_document1.attempt_bundle import (
    AttemptBundleSeeder,
    AttemptOutputValidator,
    SeededAttemptBundle,
)
from doxagent.workflows.codex_document1.context_compiler import CodexD1ContextCompiler
from doxagent.workflows.codex_document1.prompts import CodexD1PromptLoader
from doxagent.workflows.codex_document1.schema import NODE_OUTPUT_SCHEMA, NodeOutput
from doxagent.workflows.codex_document1.upstream_rebinder import UpstreamObservationRebinder

NodeReuseResolver = Callable[
    [str], Awaitable[tuple[NodeOutput, ArtifactRef] | None]
]
NodeEventSink = Callable[[str, str, dict[str, object]], Awaitable[None]]

_ROLE_BY_NODE = {
    CodexD1Node.C1: CodexAgentRole.C1,
    CodexD1Node.C2: CodexAgentRole.C2,
    CodexD1Node.C3: CodexAgentRole.C3,
    CodexD1Node.C4_PRE_SCAN: CodexAgentRole.C4,
    CodexD1Node.C4_ENRICHMENT: CodexAgentRole.C4,
    CodexD1Node.C4_FINALIZATION: CodexAgentRole.C4,
    CodexD1Node.O4_B: CodexAgentRole.O4,
    CodexD1Node.O4_A: CodexAgentRole.O4,
}


@dataclass(frozen=True)
class NodeRunResult:
    output: NodeOutput
    report: ArtifactRef
    attempt: NodeAttempt
    worker_job: WorkerJob | None
    seeded: SeededAttemptBundle
    reused: bool = False


class CodexD1NodeRunner:
    """Execute exactly one attempt without checkpoint, assemble, or publish side effects."""

    def __init__(
        self,
        *,
        worker: CodexWorkerClient,
        workspace: WorkspaceClient,
        repository: CodexRuntimeRepository,
        prompt_root: str | Path = "codex_assets/document1_v2",
        model: str = "gpt-5.6-luna",
        model_provider: str | None = None,
        effort: Literal["low", "medium", "high", "xhigh", "max"] = "max",
        timeout_seconds: int = 1800,
        max_subagents: int = 2,
        usage_repository: ModelUsageRepository | None = None,
        event_sink: NodeEventSink | None = None,
    ) -> None:
        self._worker = worker
        self._workspace = workspace
        self._repository = repository
        self._contexts = CodexD1ContextCompiler(workspace, repository)
        self._prompts = CodexD1PromptLoader(prompt_root)
        self._attempt_bundles = AttemptBundleSeeder(workspace, prompt_root)
        self._attempt_outputs = AttemptOutputValidator(workspace)
        self._rebinder = UpstreamObservationRebinder(workspace, repository)
        self._citations = CitationPromotionService(repository)
        self._model = model
        self._model_provider = model_provider
        self._effort = effort
        self._timeout_seconds = timeout_seconds
        self._max_subagents = max_subagents
        self._usage_repository = usage_repository
        self._event_sink = event_sink

    @property
    def attempt_bundles(self) -> AttemptBundleSeeder:
        return self._attempt_bundles

    async def run_attempt(
        self,
        *,
        run_id: str,
        ticker: str,
        cutoff_at: datetime,
        node: CodexD1Node,
        attempt_id: str,
        attempt_number: int,
        payload: dict[str, object],
        horizontal: dict[str, object] | None = None,
        thread_id: str | None = None,
        previous_failure: str | None = None,
        reuse_resolver: NodeReuseResolver | None = None,
        fresh_thread: bool = False,
    ) -> NodeRunResult:
        role = role_for_node(node)
        selected_thread_id = None if fresh_thread else thread_id
        attempt = NodeAttempt(
            attempt_id=attempt_id,
            cutoff_at=cutoff_at,
            ticker=ticker,
            run_id=run_id,
            node=node,
            status=AttemptStatus.RUNNING,
            attempt_number=attempt_number,
            thread_id=selected_thread_id,
            started_at=utc_now(),
        )
        self._repository.save_attempt(attempt)
        job: WorkerJob | None = None
        seeded: SeededAttemptBundle | None = None
        worker_request: WorkerRunRequest | None = None
        try:
            rebound = await self._rebinder.rebind_payload(
                run_id=run_id,
                attempt_id=attempt_id,
                payload=payload,
            )
            seeded = await self._attempt_bundles.seed(
                run_id=run_id,
                node=node,
                attempt_id=attempt_id,
                context_payload=rebound,
                horizontal=horizontal,
                previous_failure=previous_failure,
            )
            attempt = attempt.model_copy(update={"input_sha256": seeded.input_sha256})
            self._repository.save_attempt(attempt)
            if reuse_resolver is not None:
                restored = await reuse_resolver(seeded.input_sha256)
                if restored is not None:
                    output, report = restored
                    attempt = attempt.model_copy(
                        update={
                            "status": AttemptStatus.CANCELLED,
                            "error_code": "INPUT_REUSED",
                            "error_message": "matching successful artifacts restored",
                            "completed_at": utc_now(),
                        }
                    )
                    self._repository.save_attempt(attempt)
                    return NodeRunResult(output, report, attempt, None, seeded, reused=True)

            context_file = await self._workspace.read_text(run_id, seeded.context_path)
            self._repository.save_artifact(
                ArtifactRef(
                    artifact_id=uuid4().hex,
                    run_id=run_id,
                    node=node,
                    attempt_id=attempt_id,
                    kind=ArtifactKind.CONTEXT,
                    relative_path=seeded.context_path,
                    sha256=context_file.sha256,
                    size_bytes=context_file.size_bytes,
                    content_type="application/json",
                )
            )
            prompt = self._prompts.render(
                node=node,
                attempt_id=attempt_id,
                task_path=seeded.task_path,
            )
            if previous_failure:
                prompt += (
                    "\nPrevious attempt failed validation: "
                    f"{previous_failure}. Start fresh, read the new input file, and correct this "
                    "exact failure."
                )
            worker_request = WorkerRunRequest(
                run_id=run_id,
                ticker=ticker,
                node=node,
                agent_role=role,
                attempt_id=attempt_id,
                cutoff_at=cutoff_at,
                prompt=prompt,
                output_schema=NODE_OUTPUT_SCHEMA,
                thread_id=selected_thread_id,
                model=self._model,
                model_provider=self._model_provider,
                effort=self._effort,
                timeout_seconds=self._timeout_seconds,
                allow_subagents=node in {CodexD1Node.C1, CodexD1Node.C3, CodexD1Node.O4_A},
                max_subagents=self._max_subagents,
            )
            job = await self._worker.run(worker_request)
            if job.thread_id:
                self._repository.save_thread(
                    ThreadRecord(
                        ticker=ticker,
                        run_id=run_id,
                        agent_role=role,
                        thread_id=job.thread_id,
                        model=self._model,
                        model_provider=self._model_provider,
                    )
                )
            if job.status != "succeeded" or not job.final_response:
                raise StructuredOutputInvalid(job.error_message or "worker returned no response")
            output = NodeOutput.model_validate_json(job.final_response)
            validate_node_output(node, output)
            await self._attempt_outputs.validate(
                run_id=run_id,
                node=node,
                seeded=seeded,
                output=output,
            )
            report, completion = await self._contexts.write_output(
                run_id=run_id,
                node=node,
                attempt_id=attempt_id,
                report_markdown=output.report_markdown,
                completion_json=output.model_dump_json(by_alias=True, indent=2),
                persist_metadata=False,
            )
            manifest = await self._promote_attempt_citations(
                run_id=run_id,
                attempt_id=attempt_id,
                artifact_id=report.artifact_id,
                anchor=node.value,
                markdown=output.report_markdown,
            )
            self._repository.save_artifact(report)
            self._repository.save_artifact(completion)
            attempt = attempt.model_copy(
                update={
                    "status": AttemptStatus.SUCCEEDED,
                    "thread_id": job.thread_id,
                    "completed_at": utc_now(),
                }
            )
            self._repository.save_attempt(attempt)
            await self._write_validation_audit(
                run_id=run_id,
                attempt_id=attempt_id,
                job=job,
                schema="passed",
                progressive="passed",
                citation=("passed" if not manifest.warnings else "passed_with_warnings"),
                citation_warnings=manifest.warnings,
            )
            self._record_usage(worker_request, job)
            return NodeRunResult(output, report, attempt, job, seeded)
        except Exception as exc:
            attempt = attempt.model_copy(
                update={
                    "status": AttemptStatus.FAILED,
                    "thread_id": job.thread_id if job is not None else selected_thread_id,
                    "error_code": getattr(exc, "code", "NODE_ATTEMPT_FAILED"),
                    "error_message": bounded_error_message(exc),
                    "completed_at": utc_now(),
                }
            )
            self._repository.save_attempt(attempt)
            if seeded is not None:
                await self._write_validation_audit(
                    run_id=run_id,
                    attempt_id=attempt_id,
                    job=job,
                    schema="failed",
                    progressive="failed",
                    citation="not_run",
                    error=bounded_error_message(exc),
                )
            if job is not None:
                self._record_usage(
                    worker_request
                    or WorkerRunRequest(
                        run_id=run_id,
                        ticker=ticker,
                        node=node,
                        agent_role=role,
                        attempt_id=attempt_id,
                        cutoff_at=cutoff_at,
                        prompt="audit-only",
                        output_schema=NODE_OUTPUT_SCHEMA,
                        model=self._model,
                        model_provider=self._model_provider,
                    ),
                    job,
                    status="failed",
                    error_code=attempt.error_code,
                    error_message=attempt.error_message,
                )
            raise

    async def _promote_attempt_citations(
        self,
        *,
        run_id: str,
        attempt_id: str,
        artifact_id: str,
        anchor: str,
        markdown: str,
    ) -> CitationManifest:
        observations: list[PersistedObservation] = []
        warnings: list[str] = []
        inventory = await self._workspace.inventory(run_id)
        inventory_paths = {item.relative_path for item in inventory.files}
        canonical = await self._workspace.read_attempt_observations(run_id, attempt_id)
        for observation in canonical:
            mirror_path = f"attempts/{attempt_id}/audit/observations/{observation.alias}.json"
            if mirror_path not in inventory_paths:
                warnings.append(f"missing observation projection: {observation.alias}")
                continue
            try:
                mirror = await self._workspace.read_text(run_id, mirror_path)
                if mirror.content != observation.model_dump_json(indent=2):
                    warnings.append(
                        f"observation projection checksum mismatch: {observation.alias}"
                    )
                    continue
                suffix, expected_block = render_observation_block(observation)
                block_path = (
                    f"context/mcp_data/{attempt_id}/{observation.tool_call_id}/"
                    f"blocks/{observation.alias}{suffix}"
                )
                if block_path in inventory_paths:
                    block = await self._workspace.read_text(run_id, block_path)
                    if block.content != expected_block:
                        warnings.append(f"Observation Pack checksum mismatch: {observation.alias}")
                        continue
                observations.append(observation)
            except (OSError, ValueError) as exc:
                warnings.append(f"failed to verify {observation.alias}: {exc}")
        try:
            manifest = self._citations.promote(
                run_id=run_id,
                attempt_id=attempt_id,
                artifact_id=artifact_id,
                anchor=anchor,
                markdown=markdown,
                observations=observations,
            )
            if warnings:
                manifest = manifest.model_copy(update={"warnings": [*manifest.warnings, *warnings]})
                self._repository.save_citation_manifest(manifest)
            return manifest
        except (OSError, sqlite3.Error):
            raise
        except Exception as exc:
            warning = f"citation promotion failed for {attempt_id}: {exc}"
            manifest = CitationManifest(
                run_id=run_id,
                artifact_id=artifact_id,
                warnings=[*warnings, warning],
            )
            self._repository.save_citation_manifest(manifest)
            if self._event_sink is not None:
                await self._event_sink(
                    run_id,
                    "citation.promotion_failed",
                    {"attempt_id": attempt_id, "warning": warning},
                )
            return manifest

    async def _write_validation_audit(
        self,
        *,
        run_id: str,
        attempt_id: str,
        job: WorkerJob | None,
        schema: str,
        progressive: str,
        citation: str,
        citation_warnings: list[str] | None = None,
        error: str | None = None,
    ) -> None:
        path = f"attempts/{attempt_id}/audit/turn_summary.json"
        try:
            current = await self._workspace.read_text(run_id, path)
            payload = json.loads(current.content or "{}")
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            payload = {
                "schema_version": "codex-d1-turn-summary-v1",
                "run_id": run_id,
                "attempt_id": attempt_id,
            }
        payload["job_status"] = job.status if job is not None else "not_started"
        payload["validation"] = {
            "schema": schema,
            "progressive": progressive,
            "citation": citation,
            "citation_warnings": citation_warnings or [],
            "error": error,
        }
        await self._workspace.write_text(
            run_id,
            path,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )

    def _record_usage(
        self,
        request: WorkerRunRequest,
        job: WorkerJob,
        *,
        status: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if self._usage_repository is None or job.telemetry is None:
            return
        usage = job.telemetry.usage
        self._usage_repository.save_event(
            ModelUsageEvent(
                provider=request.model_provider or "openai",
                model=request.model or "codex-default",
                status=status or job.status,
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
                task_type="codex_document1_node",
                execution_id=request.attempt_id,
                error_code=error_code or job.error_code,
                error_message=error_message or job.error_message,
                metadata={
                    "thread_id": job.thread_id or "",
                    "turn_id": job.turn_id or "",
                    "cached_input_tokens": str(usage.cached_input_tokens),
                    "reasoning_output_tokens": str(usage.reasoning_output_tokens),
                },
                raw_usage=usage.model_dump(mode="json"),
            )
        )


def role_for_node(node: CodexD1Node) -> CodexAgentRole:
    try:
        return _ROLE_BY_NODE[node]
    except KeyError as exc:
        raise ValueError(f"node is not executable by CodexD1NodeRunner: {node.value}") from exc


def validate_node_output(node: CodexD1Node, output: NodeOutput) -> None:
    if node is CodexD1Node.C4_FINALIZATION and not (
        output.entity_relations or output.future_nodes
    ):
        raise StructuredOutputInvalid("C4 finalization returned neither relation nor future nodes")
    if node is CodexD1Node.O4_A and not output.report_markdown.strip():
        raise StructuredOutputInvalid("O4-A requires a separate Markdown report")


def bounded_error_message(exc: BaseException) -> str:
    value = str(exc).strip() or exc.__class__.__name__
    return value if len(value) <= 2_000 else value[:1_997] + "..."
