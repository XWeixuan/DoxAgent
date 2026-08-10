"""Checkpointed Codex SDK DAG for Document 1 v2."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Literal
from uuid import uuid4

from doxagent.codex_runtime.client import CodexWorkerClient, WorkspaceClient
from doxagent.codex_runtime.errors import StructuredOutputInvalid
from doxagent.codex_runtime.published_storage import PublishedDocumentStorage
from doxagent.codex_runtime.repository import CodexRuntimeRepository
from doxagent.codex_runtime.schema import (
    AgentObservationCandidate,
    ArtifactKind,
    ArtifactRef,
    AttemptStatus,
    CitationManifest,
    CodexAgentRole,
    CodexD1Node,
    Document1HandoffV1,
    Document1V2Bundle,
    NodeAttempt,
    PublishedDocument,
    ThreadRecord,
    WorkflowCheckpoint,
    WorkflowEvent,
    utc_now,
)
from doxagent.codex_worker.schema import WorkerRunRequest
from doxagent.horizontal_collection.collector import HorizontalCollector
from doxagent.horizontal_collection.compiler import HorizontalStateCompiler
from doxagent.horizontal_collection.context import render_horizontal_context
from doxagent.horizontal_collection.schema import HorizontalCollectionBundle
from doxagent.observations.models import PersistedObservation
from doxagent.observations.pack import render_observation_block
from doxagent.observations.promotion import CitationPromotionService
from doxagent.workflows.codex_document1.assembler import assemble_document1
from doxagent.workflows.codex_document1.context_compiler import CodexD1ContextCompiler
from doxagent.workflows.codex_document1.prompts import CodexD1PromptLoader
from doxagent.workflows.codex_document1.schema import (
    NODE_OUTPUT_SCHEMA,
    Document1V2RunRequest,
    NodeOutput,
)

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

_ATTEMPT_CITATION = re.compile(r"【cite:O[1-9]\d*】")


class CodexDocument1Orchestrator:
    def __init__(
        self,
        *,
        worker: CodexWorkerClient,
        workspace: WorkspaceClient,
        repository: CodexRuntimeRepository,
        horizontal_collector: HorizontalCollector,
        horizontal_compiler: HorizontalStateCompiler,
        prompt_root: str | Path = "codex_assets/document1_v2",
        model: str = "gpt-5.6-luna",
        model_provider: str | None = None,
        effort: Literal["low", "medium", "high", "xhigh", "max"] = "max",
        timeout_seconds: int = 1800,
        max_attempts: int = 2,
        max_subagents: int = 2,
        published_storage: PublishedDocumentStorage | None = None,
    ) -> None:
        self._worker = worker
        self._workspace = workspace
        self._repository = repository
        self._collector = horizontal_collector
        self._horizontal_compiler = horizontal_compiler
        self._contexts = CodexD1ContextCompiler(workspace, repository)
        self._prompts = CodexD1PromptLoader(prompt_root)
        self._citations = CitationPromotionService(repository)
        self._model = model
        self._model_provider = model_provider
        self._effort = effort
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._max_subagents = max_subagents
        self._published_storage = published_storage

    async def run(self, request: Document1V2RunRequest) -> Document1V2Bundle:
        existing = self._repository.get_bundle(request.run_id)
        if existing is not None and existing.status == "published":
            return existing
        checkpoint = self._repository.get_checkpoint(request.run_id) or WorkflowCheckpoint(
            ticker=request.ticker,
            run_id=request.run_id,
        )
        self._repository.save_checkpoint(checkpoint)
        await self._event(request.run_id, "workflow.started", {"ticker": request.ticker})

        manifest, observations = await asyncio.to_thread(
            self._collector.collect,
            run_id=request.run_id,
            ticker=request.ticker,
        )
        horizontal = self._horizontal_compiler.compile(
            ticker=request.ticker,
            manifest=manifest,
            observations=observations,
        )
        await self._write_horizontal_artifacts(request.run_id, horizontal)
        self._complete_checkpoint(checkpoint, CodexD1Node.PROGRAM_COLLECTION)

        base_context: dict[str, object] = {
            "ticker": request.ticker,
            "company_name": request.company_name,
            "research_brief": request.research_brief,
            "base_context": request.base_context,
        }
        outputs: dict[CodexD1Node, NodeOutput] = {}
        reports: dict[str, ArtifactRef] = {}

        c4_pre, ref = await self._execute_or_partial(
            request,
            CodexD1Node.C4_PRE_SCAN,
            {**base_context, "task": "pre-scan entity relations and preliminary future nodes"},
            checkpoint,
        )
        outputs[CodexD1Node.C4_PRE_SCAN] = c4_pre
        if ref:
            reports[CodexD1Node.C4_PRE_SCAN.value] = ref

        parallel_specs = {
            CodexD1Node.C1: {
                **base_context,
                "horizontal_context": render_horizontal_context(horizontal, target_prefix="c1_"),
                "c4_pre_scan": self._handoff_output(c4_pre),
            },
            CodexD1Node.C2: {
                **base_context,
                "horizontal_context": render_horizontal_context(horizontal, target_prefix="c2_"),
            },
            CodexD1Node.C3: {
                **base_context,
                "c4_pre_scan": self._handoff_output(c4_pre),
            },
            CodexD1Node.O4_B: {
                **base_context,
                "horizontal_context": render_horizontal_context(horizontal, target_prefix="o4_"),
            },
        }
        parallel_results = await asyncio.gather(
            *(
                self._execute_or_partial(request, node, context, checkpoint)
                for node, context in parallel_specs.items()
            )
        )
        for node, (output, ref) in zip(parallel_specs, parallel_results, strict=True):
            outputs[node] = output
            if ref:
                reports[node.value] = ref

        normalized = self._normalize_candidates(outputs)
        await self._write_normalized(request.run_id, normalized)
        self._complete_checkpoint(checkpoint, CodexD1Node.AGENT_NORMALIZATION)

        c4_enriched, ref = await self._execute_or_partial(
            request,
            CodexD1Node.C4_ENRICHMENT,
            {
                **base_context,
                "c4_pre_scan": self._handoff_output(c4_pre),
                "c1_report": self._handoff_output(outputs[CodexD1Node.C1]),
                "c3_report": self._handoff_output(outputs[CodexD1Node.C3]),
                "agent_observations": self._handoff_observations(normalized),
            },
            checkpoint,
        )
        outputs[CodexD1Node.C4_ENRICHMENT] = c4_enriched
        if ref:
            reports[CodexD1Node.C4_ENRICHMENT.value] = ref

        c4_final, ref = await self._execute_or_partial(
            request,
            CodexD1Node.C4_FINALIZATION,
            {
                **base_context,
                "enriched_c4": self._handoff_output(c4_enriched),
                "public_schema_note": (
                    "Entity relations and future nodes must contain only their five "
                    "governed fields."
                ),
            },
            checkpoint,
        )
        outputs[CodexD1Node.C4_FINALIZATION] = c4_final
        if ref:
            reports[CodexD1Node.C4_FINALIZATION.value] = ref

        o4_a, ref = await self._execute_or_partial(
            request,
            CodexD1Node.O4_A,
            {
                **base_context,
                "o4_b": self._handoff_output(outputs[CodexD1Node.O4_B]),
                "c1": self._handoff_output(outputs[CodexD1Node.C1]),
                "c2": self._handoff_output(outputs[CodexD1Node.C2]),
                "c3": self._handoff_output(outputs[CodexD1Node.C3]),
                "known_future_nodes": [
                    item.model_dump(mode="json", by_alias=True) for item in c4_final.future_nodes
                ],
                "agent_observations": self._handoff_observations(normalized),
            },
            checkpoint,
        )
        outputs[CodexD1Node.O4_A] = o4_a
        if ref:
            reports[CodexD1Node.O4_A.value] = ref

        if checkpoint.failed_nodes:
            failed = [node.value for node in checkpoint.failed_nodes]
            await self._event(request.run_id, "workflow.failed", {"failed_nodes": failed})
            raise RuntimeError("Document 1 publish blocked by failed nodes: " + ", ".join(failed))

        document = assemble_document1(request.ticker, outputs)
        final_ref = await self._write_final_document(request.run_id, document)
        reports["document1"] = final_ref
        node_manifests: list[CitationManifest] = []
        for name, reference in reports.items():
            if name == "document1":
                continue
            node_manifest = self._repository.get_citation_manifest(
                request.run_id,
                reference.artifact_id,
            )
            if node_manifest is not None:
                node_manifests.append(node_manifest)
        citation_manifest = self._citations.merge(
            run_id=request.run_id,
            artifact_id=final_ref.artifact_id,
            manifests=node_manifests,
        )
        citation_ref = await self._write_json_artifact(
            run_id=request.run_id,
            node=CodexD1Node.ASSEMBLE,
            attempt_id=final_ref.attempt_id,
            relative_path="artifacts/document1/citation_manifest.json",
            content=citation_manifest.model_dump_json(indent=2),
            kind=ArtifactKind.MANIFEST,
        )
        self._complete_checkpoint(checkpoint, CodexD1Node.ASSEMBLE)
        await self._workspace.publish(
            request.run_id,
            [
                *[reference.relative_path for reference in reports.values()],
                citation_ref.relative_path,
            ],
        )
        published_at = utc_now()
        reports = {
            name: reference.model_copy(update={"published": True})
            for name, reference in reports.items()
        }
        final_ref = reports["document1"]
        citation_ref = citation_ref.model_copy(update={"published": True})
        published_references = [*reports.values(), citation_ref]
        for reference in published_references:
            file = await self._workspace.read_text(request.run_id, reference.relative_path)
            content_bytes = file.content.encode("utf-8")
            digest = hashlib.sha256(content_bytes).hexdigest()
            if digest != reference.sha256 or len(content_bytes) != reference.size_bytes:
                raise RuntimeError(f"published artifact checksum mismatch: {reference.artifact_id}")
            storage_path = None
            content_text = file.content
            if len(content_bytes) > 2 * 1024 * 1024:
                if self._published_storage is None:
                    raise RuntimeError(
                        "PUBLISHED_DOCUMENT_STORAGE_REQUIRED: configure private Supabase "
                        f"Storage for {reference.artifact_id}"
                    )
                storage_path = f"{request.run_id}/{reference.artifact_id}"
                await self._published_storage.put(
                    storage_path, content_bytes, reference.content_type
                )
                content_text = None
            self._repository.save_published_document(
                PublishedDocument(
                    artifact_id=reference.artifact_id,
                    run_id=request.run_id,
                    artifact_kind=reference.kind.value,
                    sha256=reference.sha256,
                    size_bytes=reference.size_bytes,
                    content_type=reference.content_type,
                    content_text=content_text,
                    storage_path=storage_path,
                    published_at=published_at,
                )
            )
        for reference in published_references:
            self._repository.save_artifact(reference)
        handoff = Document1HandoffV1(
            run_id=request.run_id,
            ticker=request.ticker,
            document1_artifact_id=final_ref.artifact_id,
            citation_manifest_artifact_id=citation_ref.artifact_id,
            published_at=published_at,
        )
        bundle = Document1V2Bundle(
            run_id=request.run_id,
            ticker=request.ticker,
            status="published",
            reports=reports,
            entity_relations=c4_final.entity_relations,
            future_nodes=c4_final.future_nodes,
            citation_manifest=citation_manifest,
            handoff=handoff,
            published_at=published_at,
        )
        self._repository.save_bundle(bundle)
        self._complete_checkpoint(checkpoint, CodexD1Node.PUBLISH)
        self._repository.mark_run_published(request.run_id, published_at)
        await self._event(
            request.run_id, "workflow.published", {"artifact_id": final_ref.artifact_id}
        )
        return bundle

    async def _execute_or_partial(
        self,
        request: Document1V2RunRequest,
        node: CodexD1Node,
        payload: dict[str, object],
        checkpoint: WorkflowCheckpoint,
    ) -> tuple[NodeOutput, ArtifactRef | None]:
        try:
            return await self._execute_node(request, node, payload, checkpoint)
        except Exception as exc:
            self._fail_checkpoint(checkpoint, node)
            await self._event(
                request.run_id,
                "node.failed",
                {"node": node.value, "error": str(exc)},
            )
            return (
                NodeOutput(
                    status="failed",
                    summary=f"{node.value} failed",
                    warnings=[f"{node.value}: {exc}"],
                ),
                None,
            )

    async def _execute_node(
        self,
        request: Document1V2RunRequest,
        node: CodexD1Node,
        payload: dict[str, object],
        checkpoint: WorkflowCheckpoint,
    ) -> tuple[NodeOutput, ArtifactRef]:
        role = _ROLE_BY_NODE[node]
        thread = self._repository.get_thread(request.run_id, role.value)
        last_error: Exception | None = None
        previous_attempts = [
            item
            for item in self._repository.list_attempts(request.run_id, limit=500)
            if item.node is node
        ]
        first_attempt_number = (
            max((item.attempt_number for item in previous_attempts), default=0) + 1
        )
        for attempt_offset in range(self._max_attempts):
            attempt_number = first_attempt_number + attempt_offset
            attempt_id = f"{node.value}-{attempt_number}-{uuid4().hex[:10]}"
            request_thread_id = (
                thread.thread_id if thread is not None and attempt_offset == 0 else None
            )
            attempt = NodeAttempt(
                attempt_id=attempt_id,
                cutoff_at=request.cutoff_at,
                ticker=request.ticker,
                run_id=request.run_id,
                node=node,
                status=AttemptStatus.RUNNING,
                attempt_number=attempt_number,
                thread_id=request_thread_id,
                started_at=utc_now(),
            )
            self._repository.save_attempt(attempt)
            context_ref = await self._contexts.write_context(
                run_id=request.run_id,
                node=node,
                attempt_id=attempt_id,
                payload=payload,
            )
            prompt = self._prompts.render(
                node=node,
                attempt_id=attempt_id,
                context_path=context_ref.relative_path,
            )
            if last_error is not None:
                prompt += (
                    "\nPrevious attempt failed validation: "
                    f"{last_error}. Start fresh, read the new input file, and correct this exact "
                    "failure."
                )
            worker_request = WorkerRunRequest(
                run_id=request.run_id,
                ticker=request.ticker,
                node=node,
                agent_role=role,
                attempt_id=attempt_id,
                prompt=prompt,
                output_schema=NODE_OUTPUT_SCHEMA,
                thread_id=request_thread_id,
                model=self._model,
                model_provider=self._model_provider,
                effort=self._effort,
                timeout_seconds=self._timeout_seconds,
                allow_subagents=node in {CodexD1Node.C1, CodexD1Node.C3, CodexD1Node.O4_A},
                max_subagents=self._max_subagents,
            )
            job = None
            try:
                job = await self._worker.run(worker_request)
                if job.thread_id:
                    thread = ThreadRecord(
                        ticker=request.ticker,
                        run_id=request.run_id,
                        agent_role=role,
                        thread_id=job.thread_id,
                        model=self._model,
                        model_provider=self._model_provider,
                    )
                    self._repository.save_thread(thread)
                if job.status != "succeeded" or not job.final_response:
                    raise StructuredOutputInvalid(
                        job.error_message or "worker returned no response"
                    )
                output = NodeOutput.model_validate_json(job.final_response)
                self._validate_node_output(node, output)
                report_ref, _completion_ref = await self._contexts.write_output(
                    run_id=request.run_id,
                    node=node,
                    attempt_id=attempt_id,
                    report_markdown=output.report_markdown,
                    completion_json=output.model_dump_json(by_alias=True, indent=2),
                    persist_metadata=False,
                )
                await self._promote_attempt_citations(
                    run_id=request.run_id,
                    attempt_id=attempt_id,
                    artifact_id=report_ref.artifact_id,
                    anchor=node.value,
                    markdown=output.report_markdown,
                )
                self._repository.save_artifact(report_ref)
                self._repository.save_artifact(_completion_ref)
                self._repository.save_attempt(
                    attempt.model_copy(
                        update={
                            "status": AttemptStatus.SUCCEEDED,
                            "thread_id": job.thread_id,
                            "completed_at": utc_now(),
                        }
                    )
                )
                self._complete_checkpoint(checkpoint, node)
                await self._event(request.run_id, "node.succeeded", {"node": node.value})
                return output, report_ref
            except Exception as exc:
                last_error = exc
                self._repository.save_attempt(
                    attempt.model_copy(
                        update={
                            "status": AttemptStatus.FAILED,
                            "thread_id": job.thread_id if job is not None else request_thread_id,
                            "error_code": getattr(exc, "code", "NODE_OUTPUT_INVALID"),
                            "error_message": _bounded_error_message(exc),
                            "completed_at": utc_now(),
                        }
                    )
                )
        assert last_error is not None
        raise last_error

    @staticmethod
    def _validate_node_output(node: CodexD1Node, output: NodeOutput) -> None:
        if node is CodexD1Node.C4_FINALIZATION:
            if not output.entity_relations and not output.future_nodes:
                raise StructuredOutputInvalid(
                    "C4 finalization returned neither relation nor future nodes"
                )
        if node is CodexD1Node.O4_A and not output.report_markdown.strip():
            raise StructuredOutputInvalid("O4-A requires a separate Markdown report")

    @staticmethod
    def _normalize_candidates(
        outputs: dict[CodexD1Node, NodeOutput],
    ) -> list[AgentObservationCandidate]:
        deduplicated: dict[tuple[str, str, str | None], AgentObservationCandidate] = {}
        for node in (CodexD1Node.C1, CodexD1Node.C2, CodexD1Node.C3, CodexD1Node.O4_B):
            for candidate in outputs[node].observation_candidates:
                key = (candidate.metric_key, str(candidate.value), candidate.as_of)
                deduplicated.setdefault(key, candidate)
        return list(deduplicated.values())

    @staticmethod
    def _handoff_output(output: NodeOutput) -> dict[str, object]:
        """Remove attempt-local aliases before another node sees the output."""

        payload = output.model_dump(mode="json", by_alias=True)
        report = payload.get("report_markdown")
        if isinstance(report, str):
            payload["report_markdown"] = _ATTEMPT_CITATION.sub("[upstream citation]", report)
        candidates = payload.get("observation_candidates")
        if isinstance(candidates, list):
            for candidate in candidates:
                if isinstance(candidate, dict):
                    candidate["source_aliases"] = []
        return payload

    @staticmethod
    def _handoff_observations(
        observations: list[AgentObservationCandidate],
    ) -> list[dict[str, object]]:
        return [
            item.model_copy(update={"source_aliases": []}).model_dump(mode="json")
            for item in observations
        ]

    async def _write_horizontal_artifacts(
        self, run_id: str, bundle: HorizontalCollectionBundle
    ) -> None:
        await self._write_json_artifact(
            run_id=run_id,
            node=CodexD1Node.PROGRAM_COLLECTION,
            attempt_id="program-collection-1",
            relative_path="context/horizontal_collection_bundle.json",
            content=bundle.model_dump_json(indent=2),
            kind=ArtifactKind.MANIFEST,
        )

    async def _write_normalized(self, run_id: str, values: list[AgentObservationCandidate]) -> None:
        await self._write_json_artifact(
            run_id=run_id,
            node=CodexD1Node.AGENT_NORMALIZATION,
            attempt_id="agent-normalization-1",
            relative_path="context/agent_observations.json",
            content=json.dumps(
                [item.model_dump(mode="json") for item in values], ensure_ascii=False, indent=2
            ),
            kind=ArtifactKind.CONTEXT,
        )

    async def _write_json_artifact(
        self,
        *,
        run_id: str,
        node: CodexD1Node,
        attempt_id: str,
        relative_path: str,
        content: str,
        kind: ArtifactKind,
    ) -> ArtifactRef:
        metadata = await self._workspace.write_text(run_id, relative_path, content)
        artifact = ArtifactRef(
            artifact_id=uuid4().hex,
            run_id=run_id,
            node=node,
            attempt_id=attempt_id,
            kind=kind,
            relative_path=relative_path,
            sha256=metadata.sha256,
            size_bytes=metadata.size_bytes,
            content_type="application/json",
        )
        self._repository.save_artifact(artifact)
        return artifact

    async def _write_final_document(self, run_id: str, document: str) -> ArtifactRef:
        attempt_id = f"assemble-1-{uuid4().hex[:10]}"
        metadata = await self._workspace.write_text(
            run_id, "artifacts/document1/document1_v2.md", document
        )
        artifact = ArtifactRef(
            artifact_id=uuid4().hex,
            run_id=run_id,
            node=CodexD1Node.ASSEMBLE,
            attempt_id=attempt_id,
            kind=ArtifactKind.BUNDLE,
            relative_path=metadata.relative_path,
            sha256=metadata.sha256,
            size_bytes=metadata.size_bytes,
            content_type="text/markdown",
        )
        self._repository.save_artifact(artifact)
        return artifact

    async def _promote_attempt_citations(
        self,
        *,
        run_id: str,
        attempt_id: str,
        artifact_id: str,
        anchor: str,
        markdown: str,
    ) -> CitationManifest:
        """Promote cited blocks only; citation failures never fail the research node."""

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
                expected_mirror = observation.model_dump_json(indent=2)
                if mirror.content != expected_mirror:
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
            await self._event(
                run_id,
                "citation.promotion_failed",
                {"attempt_id": attempt_id, "warning": warning},
            )
            return manifest

    def _complete_checkpoint(self, checkpoint: WorkflowCheckpoint, node: CodexD1Node) -> None:
        completed = list(dict.fromkeys([*checkpoint.completed_nodes, node]))
        failed = [item for item in checkpoint.failed_nodes if item is not node]
        checkpoint.completed_nodes = completed
        checkpoint.failed_nodes = failed
        checkpoint.updated_at = utc_now()
        self._repository.save_checkpoint(checkpoint)

    def _fail_checkpoint(self, checkpoint: WorkflowCheckpoint, node: CodexD1Node) -> None:
        checkpoint.failed_nodes = list(dict.fromkeys([*checkpoint.failed_nodes, node]))
        checkpoint.updated_at = utc_now()
        self._repository.save_checkpoint(checkpoint)

    async def _event(self, run_id: str, event_type: str, payload: dict[str, object]) -> None:
        self._repository.append_event(
            WorkflowEvent(
                event_id=uuid4().hex,
                run_id=run_id,
                event_type=event_type,
                sequence=0,
                payload=payload,
            )
        )
        await asyncio.sleep(0)


def _bounded_error_message(exc: BaseException) -> str:
    return str(exc).encode("utf-8")[:4096].decode("utf-8", errors="ignore")
