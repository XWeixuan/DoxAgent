"""Checkpointed Codex SDK DAG for Document 1 v2."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Literal
from uuid import uuid4

from doxagent.codex_runtime.client import CodexWorkerClient, WorkspaceClient
from doxagent.codex_runtime.published_storage import PublishedDocumentStorage
from doxagent.codex_runtime.repository import CodexRuntimeRepository
from doxagent.codex_runtime.schema import (
    ArtifactKind,
    ArtifactRef,
    AttemptStatus,
    CitationManifest,
    CodexAgentRole,
    CodexD1Node,
    Document1HandoffV1,
    Document1V2Bundle,
    NormalizedAgentObservation,
    PublishedDocument,
    WorkflowCheckpoint,
    WorkflowEvent,
    utc_now,
)
from doxagent.horizontal_collection.collector import HorizontalCollector
from doxagent.horizontal_collection.compiler import HorizontalStateCompiler
from doxagent.horizontal_collection.context import render_horizontal_context
from doxagent.horizontal_collection.registry import default_metric_registry
from doxagent.horizontal_collection.schema import (
    CollectionObservation,
    HorizontalCollectionBundle,
    HorizontalCollectionManifest,
    HorizontalCollectionTargetResult,
)
from doxagent.model_usage.repository import ModelUsageRepository
from doxagent.observations.promotion import CitationPromotionService
from doxagent.workflows.codex_document1.assembler import assemble_document1
from doxagent.workflows.codex_document1.node_runner import (
    CodexD1NodeRunner,
    bounded_error_message,
    validate_node_output,
)
from doxagent.workflows.codex_document1.schema import Document1V2RunRequest, NodeOutput
from doxagent.workflows.codex_document1.upstream_rebinder import upstream_handoff

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

_PUBLISHED_ARTIFACT_KINDS: dict[
    ArtifactKind, Literal["report", "bundle", "manifest"]
] = {
    ArtifactKind.REPORT: "report",
    ArtifactKind.BUNDLE: "bundle",
    ArtifactKind.MANIFEST: "manifest",
}


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
        usage_repository: ModelUsageRepository | None = None,
    ) -> None:
        self._workspace = workspace
        self._repository = repository
        self._collector = horizontal_collector
        self._horizontal_compiler = horizontal_compiler
        self._citations = CitationPromotionService(repository)
        self._max_attempts = max_attempts
        self._published_storage = published_storage
        self._node_runner = CodexD1NodeRunner(
            worker=worker,
            workspace=workspace,
            repository=repository,
            prompt_root=prompt_root,
            model=model,
            model_provider=model_provider,
            effort=effort,
            timeout_seconds=timeout_seconds,
            max_subagents=max_subagents,
            usage_repository=usage_repository,
            event_sink=self._event,
        )
        # Preserve the existing internal test/diagnostic access point while the
        # lifecycle implementation is shared through CodexD1NodeRunner.
        self._attempt_bundles = self._node_runner.attempt_bundles

    async def run(self, request: Document1V2RunRequest) -> Document1V2Bundle:
        existing = self._repository.get_bundle(request.run_id)
        if existing is not None and existing.status == "published":
            return existing
        checkpoint = self._repository.get_checkpoint(request.run_id) or WorkflowCheckpoint(
            ticker=request.ticker,
            run_id=request.run_id,
        )
        self._recover_stale_attempts(request.run_id, checkpoint)
        self._repository.save_checkpoint(checkpoint)
        await self._event(request.run_id, "workflow.started", {"ticker": request.ticker})

        horizontal = await self._collect_or_restore_horizontal(request, checkpoint)

        base_context: dict[str, object] = {
            "ticker": request.ticker,
            "company_name": request.company_name,
            "research_brief": request.research_brief,
            "base_context": request.base_context,
        }
        outputs: dict[CodexD1Node, NodeOutput] = {}
        reports: dict[str, ArtifactRef] = {}

        c4_pre, c4_pre_ref = await self._execute_or_partial(
            request,
            CodexD1Node.C4_PRE_SCAN,
            {**base_context, "task": "pre-scan entity relations and preliminary future nodes"},
            checkpoint,
        )
        outputs[CodexD1Node.C4_PRE_SCAN] = c4_pre
        if c4_pre_ref:
            reports[CodexD1Node.C4_PRE_SCAN.value] = c4_pre_ref

        parallel_specs = {
            CodexD1Node.C1: (
                {
                    **base_context,
                    "c4_pre_scan": self._handoff_output(c4_pre, c4_pre_ref),
                },
                render_horizontal_context(horizontal, role="c1"),
            ),
            CodexD1Node.C2: (
                {
                    **base_context,
                },
                render_horizontal_context(horizontal, role="c2"),
            ),
            CodexD1Node.C3: (
                {
                    **base_context,
                    "c4_pre_scan": self._handoff_output(c4_pre, c4_pre_ref),
                },
                render_horizontal_context(horizontal, role="c3"),
            ),
            CodexD1Node.O4_B: (
                {
                    **base_context,
                },
                render_horizontal_context(horizontal, role="o4_b"),
            ),
        }
        parallel_results = await asyncio.gather(
            *(
                self._execute_or_partial(
                    request, node, context, checkpoint, horizontal=horizontal_input
                )
                for node, (context, horizontal_input) in parallel_specs.items()
            )
        )
        for node, (output, ref) in zip(parallel_specs, parallel_results, strict=True):
            outputs[node] = output
            if ref:
                reports[node.value] = ref

        normalized = self._normalize_candidates(outputs, reports, horizontal)
        await self._write_normalized(request.run_id, normalized)
        self._complete_checkpoint(checkpoint, CodexD1Node.AGENT_NORMALIZATION)

        c4_enriched, ref = await self._execute_or_partial(
            request,
            CodexD1Node.C4_ENRICHMENT,
            {
                **base_context,
                "c4_pre_scan": self._handoff_output(c4_pre, c4_pre_ref),
                "c1_report": self._handoff_output(outputs[CodexD1Node.C1], reports.get("c1")),
                "c3_report": self._handoff_output(outputs[CodexD1Node.C3], reports.get("c3")),
                "agent_observations": self._observation_handoffs(normalized),
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
                "enriched_c4": self._handoff_output(c4_enriched, ref),
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
                "c1": self._handoff_output(outputs[CodexD1Node.C1], reports.get("c1")),
                "c3": self._handoff_output(outputs[CodexD1Node.C3], reports.get("c3")),
                "agent_observations": self._observation_handoffs(
                    [
                        item
                        for item in normalized
                        if item.node in {CodexD1Node.C1, CodexD1Node.C3}
                    ]
                ),
            },
            checkpoint,
            horizontal=render_horizontal_context(horizontal, role="o4_a"),
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
            if file.content is None:
                raise RuntimeError(f"published artifact content missing: {reference.artifact_id}")
            content_bytes = file.content.encode("utf-8")
            digest = hashlib.sha256(content_bytes).hexdigest()
            if digest != reference.sha256 or len(content_bytes) != reference.size_bytes:
                raise RuntimeError(f"published artifact checksum mismatch: {reference.artifact_id}")
            storage_path = None
            content_text: str | None = file.content
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
                    artifact_kind=_PUBLISHED_ARTIFACT_KINDS[reference.kind],
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
        *,
        horizontal: dict[str, object] | None = None,
    ) -> tuple[NodeOutput, ArtifactRef | None]:
        try:
            return await self._execute_node(
                request, node, payload, checkpoint, horizontal=horizontal
            )
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
        *,
        horizontal: dict[str, object] | None = None,
    ) -> tuple[NodeOutput, ArtifactRef]:
        role = _ROLE_BY_NODE[node]
        # O4-A and O4-B are independent research tracks even though they share
        # one agent role. Do not expose the earlier O4-B conversation to O4-A.
        thread = (
            None
            if node is CodexD1Node.O4_A
            else self._repository.get_thread(request.run_id, role.value)
        )
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
            self._start_checkpoint(checkpoint, node)
            try:
                result = await self._node_runner.run_attempt(
                    run_id=request.run_id,
                    ticker=request.ticker,
                    cutoff_at=request.cutoff_at,
                    node=node,
                    attempt_id=attempt_id,
                    attempt_number=attempt_number,
                    payload=payload,
                    horizontal=horizontal,
                    thread_id=request_thread_id,
                    previous_failure=bounded_error_message(last_error) if last_error else None,
                    reuse_resolver=lambda input_sha256: self._restore_successful_node(
                        run_id=request.run_id,
                        node=node,
                        input_sha256=input_sha256,
                        checkpoint=checkpoint,
                    ),
                )
                if result.reused:
                    self._leave_checkpoint(checkpoint, node)
                    return result.output, result.report
                self._complete_checkpoint(checkpoint, node)
                await self._event(request.run_id, "node.succeeded", {"node": node.value})
                return result.output, result.report
            except Exception as exc:
                last_error = exc
                self._leave_checkpoint(checkpoint, node)
                thread = self._repository.get_thread(request.run_id, role.value)
        assert last_error is not None
        raise last_error

    async def _restore_successful_node(
        self,
        *,
        run_id: str,
        node: CodexD1Node,
        input_sha256: str,
        checkpoint: WorkflowCheckpoint,
    ) -> tuple[NodeOutput, ArtifactRef] | None:
        if node not in checkpoint.completed_nodes:
            return None
        attempts = [
            item
            for item in self._repository.list_attempts(run_id, limit=500)
            if item.node is node
            and item.status is AttemptStatus.SUCCEEDED
            and item.input_sha256 == input_sha256
        ]
        artifacts = self._repository.list_artifacts(run_id, limit=500)
        for attempt in reversed(attempts):
            report = next(
                (
                    item
                    for item in artifacts
                    if item.attempt_id == attempt.attempt_id and item.kind is ArtifactKind.REPORT
                ),
                None,
            )
            completion = next(
                (
                    item
                    for item in artifacts
                    if item.attempt_id == attempt.attempt_id
                    and item.kind is ArtifactKind.STRUCTURED_COMPLETION
                ),
                None,
            )
            if report is None or completion is None:
                continue
            try:
                report_file, completion_file = await asyncio.gather(
                    self._workspace.read_text(run_id, report.relative_path),
                    self._workspace.read_text(run_id, completion.relative_path),
                )
                if (
                    report_file.sha256 != report.sha256
                    or completion_file.sha256 != completion.sha256
                ):
                    continue
                output = NodeOutput.model_validate_json(completion_file.content or "")
                validate_node_output(node, output)
                if _normalize_newlines(report_file.content or "") != _normalize_newlines(
                    output.report_markdown
                ):
                    continue
                await self._event(
                    run_id,
                    "node.restored",
                    {"node": node.value, "attempt_id": attempt.attempt_id},
                )
                return output, report
            except (OSError, ValueError):
                continue
        return None

    def _recover_stale_attempts(self, run_id: str, checkpoint: WorkflowCheckpoint) -> None:
        if not checkpoint.current_nodes:
            return
        stale_nodes = set(checkpoint.current_nodes)
        for attempt in self._repository.list_attempts(run_id, limit=500):
            if attempt.status is AttemptStatus.RUNNING and attempt.node in stale_nodes:
                self._repository.save_attempt(
                    attempt.model_copy(
                        update={
                            "status": AttemptStatus.FAILED,
                            "error_code": "WORKER_RESTARTED",
                            "error_message": "orchestrator restarted with an active node attempt",
                            "completed_at": utc_now(),
                        }
                    )
                )
        checkpoint.current_nodes = []
        checkpoint.updated_at = utc_now()

    def _normalize_candidates(
        self,
        outputs: dict[CodexD1Node, NodeOutput],
        reports: dict[str, ArtifactRef],
        horizontal: HorizontalCollectionBundle,
    ) -> list[NormalizedAgentObservation]:
        registry = default_metric_registry()
        known_metrics = {item.metric_id: item for item in registry.all()}
        governed_metrics = {item.metric_id for item in horizontal.state_values}
        deduplicated: dict[tuple[str, str, str | None], NormalizedAgentObservation] = {}
        for node in (CodexD1Node.C1, CodexD1Node.C2, CodexD1Node.C3, CodexD1Node.O4_B):
            reference = reports.get(node.value)
            if reference is None:
                continue
            manifest = self._repository.get_citation_manifest(
                reference.run_id, reference.artifact_id
            )
            cited_aliases = {
                entry.alias
                for entry in (manifest.entries if manifest is not None else [])
                if entry.resolved
            }
            for candidate in outputs[node].observation_candidates:
                definition = known_metrics.get(candidate.metric_key)
                freeform = definition is None
                if not re.fullmatch(r"[a-z][a-z0-9_]{2,127}", candidate.metric_key):
                    continue
                if not candidate.meaning or not candidate.meaning.strip():
                    continue
                if (
                    not candidate.source_aliases
                    or not set(candidate.source_aliases).issubset(cited_aliases)
                    or any(
                        re.fullmatch(r"O[1-9]\d*", item) is None
                        for item in candidate.source_aliases
                    )
                ):
                    continue
                if candidate.metric_key in governed_metrics:
                    continue
                if definition is not None:
                    if definition.value_type.value == "NUMBER" and (
                        isinstance(candidate.value, bool)
                        or not isinstance(candidate.value, (int, float))
                    ):
                        continue
                    if definition.default_unit and not candidate.unit:
                        continue
                if candidate.as_of is None:
                    continue
                key = (candidate.metric_key, str(candidate.value), candidate.as_of)
                deduplicated.setdefault(
                    key,
                    NormalizedAgentObservation(
                        **candidate.model_dump(),
                        node=node,
                        attempt_id=reference.attempt_id,
                        artifact_id=reference.artifact_id,
                        governed_metric=False,
                        freeform_metric=freeform,
                    ),
                )
        return list(deduplicated.values())

    @staticmethod
    def _handoff_output(
        output: NodeOutput, reference: ArtifactRef | None = None
    ) -> dict[str, object]:
        """Retain evidence lineage; downstream aliases are rebound per new attempt."""

        return upstream_handoff(
            output.model_dump(mode="json", by_alias=True),
            reference.artifact_id if reference is not None else None,
        )

    @staticmethod
    def _observation_handoffs(
        observations: list[NormalizedAgentObservation],
    ) -> list[dict[str, object]]:
        values: list[dict[str, object]] = []
        artifact_ids = list(dict.fromkeys(item.artifact_id for item in observations))
        for artifact_id in artifact_ids:
            grouped = [item for item in observations if item.artifact_id == artifact_id]
            values.append(
                upstream_handoff(
                    {
                        "origin_node": grouped[0].node.value,
                        "warnings": [],
                        "observation_candidates": [
                            item.model_dump(
                                mode="json",
                                exclude={
                                    "node",
                                    "attempt_id",
                                    "artifact_id",
                                    "governed_metric",
                                    "freeform_metric",
                                    "source_role",
                                },
                            )
                            for item in grouped
                        ],
                    },
                    artifact_id,
                )
            )
        return values

    async def _collect_or_restore_horizontal(
        self,
        request: Document1V2RunRequest,
        checkpoint: WorkflowCheckpoint,
    ) -> HorizontalCollectionBundle:
        if CodexD1Node.PROGRAM_COLLECTION in checkpoint.completed_nodes:
            candidates = [
                item
                for item in self._repository.list_artifacts(request.run_id, limit=500)
                if item.node is CodexD1Node.PROGRAM_COLLECTION
                and item.kind is ArtifactKind.BUNDLE
                and item.relative_path.startswith(
                    ("context/ca/hc-", "context/horizontal_collection/")
                )
            ]
            for reference in reversed(candidates):
                try:
                    file = await self._workspace.read_text(request.run_id, reference.relative_path)
                    if file.sha256 != reference.sha256:
                        continue
                    return HorizontalCollectionBundle.model_validate_json(file.content or "")
                except (OSError, ValueError):
                    continue

        collection_attempt_id = "program-collection-1"
        base = f"artifacts/program_collection/{collection_attempt_id}"
        inventory = await self._workspace.inventory(request.run_id)
        inventory_paths = {item.relative_path for item in inventory.files}
        results: list[HorizontalCollectionTargetResult] = []
        observations: list[CollectionObservation] = []
        completed_target_ids: list[str] = []
        for target in self._collector.targets:
            target_path = f"{base}/targets/{target.collection_target_id}.json"
            restored = False
            if target_path in inventory_paths:
                try:
                    file = await self._workspace.read_text(request.run_id, target_path)
                    envelope = json.loads(file.content or "")
                    result = HorizontalCollectionTargetResult.model_validate(envelope["result"])
                    target_observations = tuple(
                        CollectionObservation.model_validate(item)
                        for item in envelope.get("observations", [])
                    )
                    if result.collection_target_id == target.collection_target_id:
                        restored = True
                except (KeyError, ValueError, json.JSONDecodeError):
                    restored = False
            if not restored:
                result, target_observations = await asyncio.to_thread(
                    self._collector.collect_target,
                    run_id=request.run_id,
                    ticker=request.ticker,
                    target=target,
                )
                await self._workspace.write_text(
                    request.run_id,
                    target_path,
                    json.dumps(
                        {
                            "schema_version": "d1-horizontal-target-result-v1",
                            "result": result.model_dump(mode="json"),
                            "observations": [
                                item.model_dump(mode="json") for item in target_observations
                            ],
                        },
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    ),
                )
            results.append(result)
            observations.extend(target_observations)
            completed_target_ids.append(target.collection_target_id)
            await self._workspace.write_text(
                request.run_id,
                f"{base}/progress.json",
                json.dumps(
                    {
                        "schema_version": "d1-horizontal-progress-v1",
                        "completed_target_ids": completed_target_ids,
                        "target_count": len(self._collector.targets),
                        "status": (
                            "completed"
                            if len(completed_target_ids) == len(self._collector.targets)
                            else "in_progress"
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        manifest = HorizontalCollectionManifest(
            run_id=request.run_id,
            ticker=request.ticker,
            metric_registry_version=self._collector.metric_registry_version,
            target_registry_version=self._collector.target_registry_version,
            target_results=tuple(results),
        )
        horizontal = self._horizontal_compiler.compile(
            ticker=request.ticker,
            manifest=manifest,
            observations=tuple(observations),
        )
        manifest_text = manifest.model_dump_json(indent=2)
        bundle_text = horizontal.model_dump_json(indent=2)
        await self._workspace.write_text(request.run_id, f"{base}/manifest.json", manifest_text)
        await self._workspace.write_text(request.run_id, f"{base}/bundle.json", bundle_text)
        digest = hashlib.sha256(bundle_text.encode("utf-8")).hexdigest()
        await self._write_json_artifact(
            run_id=request.run_id,
            node=CodexD1Node.PROGRAM_COLLECTION,
            attempt_id=collection_attempt_id,
            # The ArtifactRef retains the full SHA-256.  A 96-bit address in the
            # workspace path keeps immutable content-addressing practical on
            # Windows, where atomic-write temporary names otherwise exceed the
            # legacy MAX_PATH limit in nested local/test workspaces.
            relative_path=f"context/ca/hc-{digest[:24]}.json",
            content=bundle_text,
            kind=ArtifactKind.BUNDLE,
        )
        self._complete_checkpoint(checkpoint, CodexD1Node.PROGRAM_COLLECTION)
        return horizontal

    async def _write_normalized(
        self, run_id: str, values: list[NormalizedAgentObservation]
    ) -> None:
        content = json.dumps(
            [item.model_dump(mode="json") for item in values], ensure_ascii=False, indent=2
        )
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        await self._write_json_artifact(
            run_id=run_id,
            node=CodexD1Node.AGENT_NORMALIZATION,
            attempt_id="agent-normalization-1",
            relative_path=f"context/ca/ao-{digest[:24]}.json",
            content=content,
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

    def _complete_checkpoint(self, checkpoint: WorkflowCheckpoint, node: CodexD1Node) -> None:
        completed = list(dict.fromkeys([*checkpoint.completed_nodes, node]))
        failed = [item for item in checkpoint.failed_nodes if item is not node]
        checkpoint.completed_nodes = completed
        checkpoint.current_nodes = [item for item in checkpoint.current_nodes if item is not node]
        checkpoint.failed_nodes = failed
        checkpoint.updated_at = utc_now()
        self._repository.save_checkpoint(checkpoint)

    def _fail_checkpoint(self, checkpoint: WorkflowCheckpoint, node: CodexD1Node) -> None:
        checkpoint.current_nodes = [item for item in checkpoint.current_nodes if item is not node]
        checkpoint.failed_nodes = list(dict.fromkeys([*checkpoint.failed_nodes, node]))
        checkpoint.updated_at = utc_now()
        self._repository.save_checkpoint(checkpoint)

    def _start_checkpoint(self, checkpoint: WorkflowCheckpoint, node: CodexD1Node) -> None:
        checkpoint.current_nodes = list(dict.fromkeys([*checkpoint.current_nodes, node]))
        checkpoint.failed_nodes = [item for item in checkpoint.failed_nodes if item is not node]
        checkpoint.updated_at = utc_now()
        self._repository.save_checkpoint(checkpoint)

    def _leave_checkpoint(self, checkpoint: WorkflowCheckpoint, node: CodexD1Node) -> None:
        checkpoint.current_nodes = [item for item in checkpoint.current_nodes if item is not node]
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


def _normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
