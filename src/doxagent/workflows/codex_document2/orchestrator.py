"""Full O0 shell construction and per-shell O1 research workflow for Document2."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, Literal, TypeVar, cast
from uuid import uuid4

from pydantic import BaseModel

from doxagent.codex_runtime.client import CodexWorkerClient, WorkspaceClient
from doxagent.codex_runtime.concurrency import TickerConcurrency
from doxagent.codex_runtime.published_storage import PublishedDocumentStorage
from doxagent.codex_runtime.repository import CodexRuntimeRepository
from doxagent.codex_runtime.schema import (
    CODEX_DOCUMENT2_WORKFLOW_VERSION,
    ArtifactKind,
    ArtifactRef,
    CodexAgentRole,
    CodexD2AgentRole,
    CodexD2Node,
    GlobalResearchBundle,
    PublishedDocument,
    ResearchLane,
    WorkflowCheckpoint,
    WorkflowEvent,
    utc_now,
)
from doxagent.model_usage.repository import ModelUsageRepository
from doxagent.workflows.codex_document2.acceptance import metadata_path
from doxagent.workflows.codex_document2.assembler import (
    assemble_document2,
    render_document2_markdown,
)
from doxagent.workflows.codex_document2.errors import Document2ExecutionError
from doxagent.workflows.codex_document2.inputs import (
    Document2InputLoader,
    EventLibraryProvider,
    NarrativeReportProvider,
    PreparedDocument2Inputs,
)
from doxagent.workflows.codex_document2.runner import (
    Document2TurnResult,
    Document2TurnRunner,
    logical_workspace_id,
)
from doxagent.workflows.codex_document2.schema import (
    CandidateDiscoveryResult,
    Document2Bundle,
    Document2Checkpoint,
    Document2HandoffV1,
    Document2RunRequest,
    DomainReviewResult,
    ExpectationShell,
    ExpectationShellSeed,
    ExpectationState,
    ExpectationUnit,
    InputAvailability,
    ShellFinalizationResult,
    ShellOutcome,
    ShellResearchStage,
    ShellRunState,
    ShellSynthesisResult,
)

from . import schema as v21

ModelT = TypeVar("ModelT", bound=BaseModel)


class Document2VersionMismatch(RuntimeError):
    """A request cannot mutate a run bound to another document contract."""


class CodexDocument2Orchestrator:
    async def _bounded_o0(
        self, ticker: str, turn: Callable[[], Awaitable[Document2TurnResult]]
    ) -> Document2TurnResult:
        async with self._shell_concurrency.slot(ticker):
            return await turn()

    def __init__(
        self,
        *,
        worker: CodexWorkerClient,
        workspace: WorkspaceClient,
        repository: CodexRuntimeRepository,
        narrative_provider: NarrativeReportProvider,
        event_library_provider: EventLibraryProvider | None = None,
        model: str = "gpt-6-luna",
        model_provider: str | None = None,
        effort: Literal["low", "medium", "high", "xhigh", "max"] = "max",
        timeout_seconds: int = 1800,
        max_attempts: int = 2,
        max_subagents: int = 2,
        max_shell_concurrency: int | None = None,
        published_storage: PublishedDocumentStorage | None = None,
        usage_repository: ModelUsageRepository | None = None,
        asset_root: str | Path | None = None,
    ) -> None:
        self._workspace = workspace
        self._repository = repository
        self._inputs = Document2InputLoader(
            repository=repository,
            workspace=workspace,
            narrative_provider=narrative_provider,
            event_library_provider=event_library_provider,
        )
        self._runner = Document2TurnRunner(
            worker=worker,
            workspace=workspace,
            repository=repository,
            model=model,
            model_provider=model_provider,
            effort=effort,
            timeout_seconds=timeout_seconds,
            max_subagents=max_subagents,
            usage_repository=usage_repository,
            asset_root=asset_root,
        )
        self._max_attempts = max_attempts
        if max_shell_concurrency is None:
            from doxagent.settings import DoxAgentSettings

            max_shell_concurrency = DoxAgentSettings().codex_d2_max_concurrency
        self._shell_concurrency = TickerConcurrency(max_shell_concurrency)
        self._published_storage = published_storage
        self._checkpoint_lock = asyncio.Lock()

    async def run(self, request: Document2RunRequest) -> Document2Bundle:
        prior = self._repository.get_bundle(request.run_id)
        try:
            return await self._run(request)
        except asyncio.CancelledError:
            raise
        except Document2VersionMismatch:
            raise
        except Exception as exc:
            self._record_run_failure(request, prior, exc)
            raise

    async def _run(self, request: Document2RunRequest) -> Document2Bundle:
        existing = self._repository.get_bundle(request.run_id)
        if isinstance(existing, Document2Bundle):
            version = existing.checkpoint.document_schema_version if existing.checkpoint else None
            if version is None and "document2" in existing.artifacts:
                ref = existing.artifacts["document2"]
                published = self._repository.get_published_document(request.run_id, ref.artifact_id)
                if published is not None and published.content_text is not None:
                    content = published.content_text
                elif published is not None and published.storage_path and self._published_storage:
                    content = (await self._published_storage.get(published.storage_path)).decode(
                        "utf-8"
                    )
                else:
                    body = await self._workspace.read_text(request.run_id, ref.relative_path)
                    content = body.content
                if content is None or hashlib.sha256(content.encode()).hexdigest() != ref.sha256:
                    raise RuntimeError("Document2 published version artifact integrity failure")
                version = json.loads(content)["schema_version"]
            if version is None:
                version = "document2.v2"
            if version != request.document_schema_version:
                raise Document2VersionMismatch(
                    "Document2 run_id schema version mismatch; use a new run_id"
                )
        if (
            isinstance(existing, Document2Bundle)
            and request.document_schema_version == "document2.v2.1"
            and (
                existing.checkpoint is None
                or existing.checkpoint.discovery_contract_version != "single-v1"
            )
        ):
            raise Document2VersionMismatch(
                "Document2 Discovery contract mismatch (split-v1); use a new run_id"
            )
        if (
            isinstance(existing, Document2Bundle)
            and existing.status == "published"
            and (existing.publication_state == "COMPLETE" or request.reuse_published_partial)
        ):
            return existing
        workflow_checkpoint = self._repository.get_checkpoint(request.run_id) or WorkflowCheckpoint(
            workflow_version=CODEX_DOCUMENT2_WORKFLOW_VERSION,
            research_lane=ResearchLane.DOCUMENT2,
            ticker=(request.ticker or "UNKNOWN").upper(),
            run_id=request.run_id,
        )
        if workflow_checkpoint.research_lane is not ResearchLane.DOCUMENT2:
            raise ValueError("run_id belongs to a different research lane")
        self._repository.save_checkpoint(workflow_checkpoint)
        await self._event(
            request.run_id,
            "workflow.started",
            {"source_global_run_id": request.source_global_run_id},
        )

        prepared = await self._load_or_prepare_inputs(request)
        workflow_checkpoint.ticker = prepared.ticker.upper()
        self._complete_workflow_node(workflow_checkpoint, CodexD2Node.INPUT_PREPARATION)
        checkpoint = (
            existing.checkpoint
            if isinstance(existing, Document2Bundle) and existing.checkpoint is not None
            else Document2Checkpoint(
                run_id=request.run_id,
                source_global_run_id=request.source_global_run_id,
                document_schema_version=request.document_schema_version,
                discovery_contract_version=(
                    "single-v1"
                    if request.document_schema_version == "document2.v2.1"
                    else "split-v1"
                ),
                o0_workspace_run_id=logical_workspace_id(request.run_id, "o0-synthesis"),
            )
        )
        bundle = (
            existing
            if isinstance(existing, Document2Bundle)
            else Document2Bundle(
                run_id=request.run_id,
                ticker=prepared.ticker,
                source_global_run_id=request.source_global_run_id,
                status="draft",
                checkpoint=checkpoint,
            )
        )
        bundle = bundle.model_copy(
            update={
                "checkpoint": checkpoint,
                "ticker": prepared.ticker,
                "status": "draft",
                "current": False,
                "publication_state": None,
                "handoff": None,
                "published_at": None,
            }
        )
        self._repository.save_bundle(bundle)

        o0_finalization, o0_artifacts = await self._construct_shells(
            request=request,
            prepared=prepared,
            checkpoint=checkpoint,
            bundle=bundle,
        )
        final_seeds = o0_finalization.shells
        self._complete_workflow_node(workflow_checkpoint, CodexD2Node.O0_FINALIZATION)
        bundle.artifacts.update(o0_artifacts)
        self._repository.save_bundle(bundle)

        shell_results = await asyncio.gather(
            *(
                self._research_shell(
                    request=request,
                    prepared=prepared,
                    seed=seed,
                    o0_finalization=o0_finalization,
                    checkpoint=checkpoint,
                    bundle=bundle,
                )
                for seed in final_seeds
            ),
            return_exceptions=True,
        )
        successful_shells: list[ExpectationShell] = []
        shell_artifacts: list[ArtifactRef] = []
        outcomes: list[ShellOutcome] = []
        for seed, result in zip(final_seeds, shell_results, strict=True):
            if isinstance(result, BaseException):
                if not isinstance(result, Exception) or not _allows_branch_degradation(result):
                    raise result
                outcomes.append(
                    ShellOutcome(
                        shell_id=_shell_name(seed),
                        status="failed",
                        failed_stage=_failed_stage(result),
                        failure_kind=(
                            result.kind.value
                            if isinstance(result, Document2ExecutionError)
                            else "SHELL"
                        ),
                        error_code=str(getattr(result, "code", type(result).__name__)),
                        error=_bounded(str(result)),
                        seed=seed,
                    )
                )
                continue
            shell, artifact = result
            successful_shells.append(shell)
            shell_artifacts.append(artifact)
            outcomes.append(
                ShellOutcome(
                    shell_id=_shell_name(shell),
                    status="completed",
                    artifact_id=artifact.artifact_id,
                    seed=seed,
                )
            )
            bundle.artifacts[f"shell:{_shell_name(seed)}"] = artifact
        bundle.shell_outcomes = outcomes
        all_shells_completed = bool(outcomes) and all(
            item.status == "completed" for item in outcomes
        )
        if all_shells_completed:
            self._complete_workflow_node(workflow_checkpoint, CodexD2Node.O1_FINALIZATION)
        else:
            self._uncomplete_workflow_node(workflow_checkpoint, CodexD2Node.O1_FINALIZATION)
        if not outcomes:
            _append_warning(checkpoint, "O1 produced no shell outcomes; publication is PARTIAL.")

        document_artifact_id = uuid4().hex
        source_bundle = self._repository.get_bundle(request.source_global_run_id)
        d1_manifest = None
        if isinstance(source_bundle, GlobalResearchBundle) and source_bundle.handoff is not None:
            try:
                d1_manifest = self._repository.get_citation_manifest(
                    request.source_global_run_id,
                    source_bundle.handoff.document_artifact_id,
                )
            except Exception:
                d1_manifest = None
        local_manifests = {}
        for attempt_id, workspace_id in checkpoint.attempt_workspaces.items():
            try:
                manifest = self._repository.get_citation_manifest(
                    workspace_id, f"d2-local-{attempt_id}"
                )
            except Exception:
                manifest = None
            if manifest is not None:
                local_manifests[attempt_id] = manifest
        assembled = assemble_document2(
            run_id=request.run_id,
            ticker=prepared.ticker,
            as_of=prepared.as_of,
            source_global_run_id=request.source_global_run_id,
            input_manifest=prepared.manifest,
            shells=successful_shells,
            shell_outcomes=outcomes,
            document_artifact_id=document_artifact_id,
            d1_manifest=d1_manifest,
            local_manifests=local_manifests,
            narrative_run_id=prepared.narrative_research.source_run_id,
            document_schema_version=request.document_schema_version,
        )
        document_ref = await self._write_artifact(
            run_id=request.run_id,
            node=CodexD2Node.ASSEMBLE,
            attempt_id=f"d2-assemble-{uuid4().hex[:10]}",
            relative_path="artifacts/document2/document2.json",
            content=assembled.document.model_dump_json(indent=2),
            kind=ArtifactKind.BUNDLE,
            content_type="application/json",
            artifact_id=document_artifact_id,
        )
        citation_ref = await self._write_artifact(
            run_id=request.run_id,
            node=CodexD2Node.ASSEMBLE,
            attempt_id=document_ref.attempt_id,
            relative_path="artifacts/document2/document2_citation_manifest.json",
            content=assembled.citation_manifest.model_dump_json(indent=2),
            kind=ArtifactKind.MANIFEST,
            content_type="application/json",
        )
        markdown_ref = await self._write_artifact(
            run_id=request.run_id,
            node=CodexD2Node.ASSEMBLE,
            attempt_id=document_ref.attempt_id,
            relative_path="artifacts/document2/document2.md",
            content=render_document2_markdown(assembled.document),
            kind=ArtifactKind.REPORT,
            content_type="text/markdown; charset=utf-8",
        )
        bundle.artifacts.update(
            {"document2": document_ref, "citation_manifest": citation_ref, "markdown": markdown_ref}
        )
        self._complete_workflow_node(workflow_checkpoint, CodexD2Node.ASSEMBLE)

        publication_state: Literal["COMPLETE", "PARTIAL"] = (
            "COMPLETE"
            if all_shells_completed
            and not any(w.startswith("D2_ACCEPTANCE:") for w in checkpoint.warnings)
            else "PARTIAL"
        )
        publish_refs = [
            document_ref,
            citation_ref,
            markdown_ref,
            *shell_artifacts,
            *[value for key, value in o0_artifacts.items() if key == "final_shell_seeds"],
        ]
        published_at, published = await self._publish(request.run_id, publish_refs)
        published_by_id = {item.artifact_id: item for item in published}
        bundle.artifacts = {
            key: published_by_id.get(value.artifact_id, value)
            for key, value in bundle.artifacts.items()
        }
        handoff = Document2HandoffV1(
            run_id=request.run_id,
            ticker=prepared.ticker,
            source_global_run_id=request.source_global_run_id,
            document2_artifact_id=document_ref.artifact_id,
            citation_manifest_artifact_id=citation_ref.artifact_id,
            publication_state=publication_state,
            citation_status=assembled.citation_status,
            published_at=published_at,
        )
        bundle = bundle.model_copy(
            update={
                "status": "published",
                "publication_state": publication_state,
                "citation_status": assembled.citation_status,
                "handoff": handoff,
                "current": publication_state == "COMPLETE"
                and request.document_schema_version == "document2.v2",
                "checkpoint": checkpoint,
                "published_at": published_at,
            }
        )
        self._repository.save_bundle(bundle)
        self._complete_workflow_node(workflow_checkpoint, CodexD2Node.PUBLISH)
        self._repository.mark_run_published(request.run_id, published_at)
        await self._event(
            request.run_id,
            "workflow.published",
            {
                "document2_artifact_id": document_ref.artifact_id,
                "publication_state": publication_state,
                "citation_status": assembled.citation_status.value,
            },
        )
        return bundle

    async def _load_or_prepare_inputs(
        self, request: Document2RunRequest
    ) -> PreparedDocument2Inputs:
        path = "context/document2/prepared_inputs.json"
        try:
            current = await self._workspace.read_text(request.run_id, path)
            if current.content:
                return PreparedDocument2Inputs.model_validate_json(current.content)
        except FileNotFoundError:
            pass
        prepared = await self._inputs.load(
            source_global_run_id=request.source_global_run_id,
            requested_ticker=request.ticker,
            requested_as_of=request.as_of,
        )
        metadata = await self._workspace.write_text(
            request.run_id, path, prepared.model_dump_json(indent=2)
        )
        artifact = ArtifactRef(
            workflow_version=CODEX_DOCUMENT2_WORKFLOW_VERSION,
            research_lane=ResearchLane.DOCUMENT2,
            artifact_id=uuid4().hex,
            run_id=request.run_id,
            node=CodexD2Node.INPUT_PREPARATION,
            attempt_id="d2-input-preparation-1",
            kind=ArtifactKind.CONTEXT,
            relative_path=metadata.relative_path,
            sha256=metadata.sha256,
            size_bytes=metadata.size_bytes,
            content_type="application/json",
        )
        self._repository.save_artifact(artifact)
        return prepared

    async def _construct_shells(
        self,
        *,
        request: Document2RunRequest,
        prepared: PreparedDocument2Inputs,
        checkpoint: Document2Checkpoint,
        bundle: Document2Bundle,
    ) -> tuple[ShellFinalizationResult, dict[str, ArtifactRef]]:
        artifacts: dict[str, ArtifactRef] = {}
        if checkpoint.final_shell_seed_path:
            restored_final = await self._restore_stage(
                request.run_id,
                checkpoint,
                "o0:final",
                _contract_model(request, ShellFinalizationResult),
            )
            if restored_final is not None:
                finalized, reference = restored_final
                return finalized, {"final_shell_seeds": reference}
        common = self._common_context(prepared)
        if (prepared.manifest.global_research.warning or "").startswith("D2_ACCEPTANCE:"):
            _warn_checkpoint(checkpoint, prepared.manifest.global_research.warning)
        if request.document_schema_version == "document2.v2.1":
            common.update(
                document_schema_version=request.document_schema_version,
                discovery_contract_version=(
                    "single-v1"
                    if request.document_schema_version == "document2.v2.1"
                    else "split-v1"
                ),
                event_library=prepared.event_library.model_dump(mode="json"),
            )
        specs: list[tuple[str, CodexD2Node, str, object]] = [
            ("c1", CodexD2Node.O0_CANDIDATE_C1, prepared.global_research.reports["c1"], None),
            ("c3", CodexD2Node.O0_CANDIDATE_C3, prepared.global_research.reports["c3"], None),
            ("c5", CodexD2Node.O0_CANDIDATE_C5, prepared.global_research.reports["c5"], None),
        ]
        if prepared.narrative_research.status is InputAvailability.AVAILABLE:
            specs.append(
                (
                    "narrative",
                    CodexD2Node.O0_CANDIDATE_NARRATIVE,
                    json.dumps(
                        prepared.narrative_research.payload,
                        ensure_ascii=False,
                        default=str,
                    ),
                    prepared.narrative_research.source_run_id,
                )
            )
        candidates: dict[str, CandidateDiscoveryResult] = {}
        missing_specs: list[tuple[str, CodexD2Node, str, object]] = []
        for spec in specs:
            key = spec[0]
            if not spec[2].strip():
                candidates[key] = _contract_model(request, CandidateDiscoveryResult)(
                    warnings=["REPORT_UNAVAILABLE: domain not researched"]
                )
                _warn_checkpoint(checkpoint, f"D2_ACCEPTANCE:{key}:report_unavailable")
                continue
            restored_candidate = await self._restore_stage(
                request.run_id,
                checkpoint,
                f"o0:candidate:{key}",
                _contract_model(request, CandidateDiscoveryResult),
            )
            if restored_candidate is None:
                missing_specs.append(spec)
            else:
                candidates[key], reference = restored_candidate
                artifacts[f"candidate:{key}"] = reference

        async def candidate_checkpointed(
            key: str, node: CodexD2Node, primary: str, source_id: object
        ) -> Document2TurnResult:
            result = await self._run_candidate(
                request=request,
                prepared=prepared,
                checkpoint=checkpoint,
                key=key,
                node=node,
                primary_source=primary,
                narrative_run_id=source_id,
                common=common,
            )
            checkpoint.stage_artifacts[f"o0:candidate:{key}"] = result.artifact.relative_path
            checkpoint.o0_thread_ids[f"candidate:{key}"] = result.thread_id or ""
            self._remember_attempt(checkpoint, result)
            await self._save_progress(bundle, checkpoint)
            return result

        missing_results = await asyncio.gather(
            *(
                self._bounded_o0(
                    prepared.ticker, partial(candidate_checkpointed, key, node, primary, source_id)
                )
                for key, node, primary, source_id in missing_specs
            ),
            return_exceptions=True,
        )
        candidate_errors: list[BaseException] = []
        for (key, _, _, _), result in zip(missing_specs, missing_results, strict=True):
            if isinstance(result, BaseException):
                candidate_errors.append(result)
                continue
            candidates[key] = cast(CandidateDiscoveryResult, result.output)
            artifacts[f"candidate:{key}"] = result.artifact
            checkpoint.stage_artifacts[f"o0:candidate:{key}"] = result.artifact.relative_path
            checkpoint.o0_thread_ids[f"candidate:{key}"] = result.thread_id or ""
            self._remember_attempt(checkpoint, result)
        await self._save_progress(bundle, checkpoint)
        for error in candidate_errors:
            if not _allows_branch_degradation(error):
                raise error
            _append_warning(
                checkpoint,
                f"O0 candidate branch unavailable: {_bounded(str(error))}",
            )
        if candidate_errors:
            await self._save_progress(bundle, checkpoint)

        restored_synthesis = await self._restore_stage(
            request.run_id,
            checkpoint,
            "o0:synthesis",
            _contract_model(request, ShellSynthesisResult),
        )
        if restored_synthesis is None:
            synthesis = await self._run_with_retry(
                persistence_run_id=request.run_id,
                workspace_run_id=checkpoint.o0_workspace_run_id,
                ticker=prepared.ticker,
                cutoff_at=prepared.as_of,
                node=CodexD2Node.O0_SYNTHESIS,
                role=CodexD2AgentRole.O0,
                context={
                    "candidate_sets": _candidate_sets_context(candidates),
                    "global_research": {"reports": prepared.global_research.reports},
                    "narrative_research": prepared.narrative_research.model_dump(mode="json"),
                    **common,
                },
                output_model=_contract_model(request, ShellSynthesisResult),
                agent_asset="agents/o0.md",
                skill_asset="skills/shell-synthesis.md",
                thread_id=checkpoint.o0_thread_ids.get("synthesis") or None,
                artifact_key="o0/synthesis",
                initialization_id=request.initialization_id,
            )
            provisional = cast(ShellSynthesisResult, synthesis.output)
            synthesis_ref = synthesis.artifact
            checkpoint.stage_artifacts["o0:synthesis"] = synthesis.artifact.relative_path
            checkpoint.o0_thread_ids["synthesis"] = synthesis.thread_id or ""
            self._remember_attempt(checkpoint, synthesis)
        else:
            provisional, synthesis_ref = restored_synthesis
        artifacts["provisional_shells"] = synthesis_ref
        await self._save_progress(bundle, checkpoint)

        review_specs = [
            ("C1", CodexD2Node.O0_REVIEW_C1, CodexAgentRole.C1, "c1"),
            ("C3", CodexD2Node.O0_REVIEW_C3, CodexAgentRole.C3, "c3"),
            ("C5", CodexD2Node.O0_REVIEW_C5, CodexAgentRole.C5, "c5"),
        ]
        review_payload: dict[str, object] = {}
        missing_reviews: list[tuple[str, CodexD2Node, CodexAgentRole, str]] = []
        for spec in review_specs:
            label = spec[0]
            restored_review = await self._restore_stage(
                request.run_id,
                checkpoint,
                f"o0:review:{label.lower()}",
                _contract_model(request, DomainReviewResult),
            )
            if restored_review is None:
                missing_reviews.append(spec)
            else:
                review, reference = restored_review
                review_payload[label] = review.model_dump(mode="json")
                artifacts[f"review:{label.lower()}"] = reference

        async def review_checkpointed(
            label: str, node: CodexD2Node, role: CodexAgentRole, report_key: str
        ) -> Document2TurnResult:
            result = await self._run_domain_review(
                request=request,
                prepared=prepared,
                checkpoint=checkpoint,
                provisional=provisional,
                reviewer_label=label,
                node=node,
                role=role,
                report_key=report_key,
                common=common,
            )
            checkpoint.stage_artifacts[f"o0:review:{label.lower()}"] = result.artifact.relative_path
            self._remember_attempt(checkpoint, result)
            await self._save_progress(bundle, checkpoint)
            return result

        review_results = await asyncio.gather(
            *(
                self._bounded_o0(
                    prepared.ticker, partial(review_checkpointed, label, node, role, report_key)
                )
                for label, node, role, report_key in missing_reviews
            ),
            return_exceptions=True,
        )
        review_errors: list[BaseException] = []
        for (label, _, _, _), result in zip(missing_reviews, review_results, strict=True):
            if isinstance(result, BaseException):
                review_errors.append(result)
                continue
            review_payload[label] = result.output.model_dump(mode="json")
            artifacts[f"review:{label.lower()}"] = result.artifact
            checkpoint.stage_artifacts[f"o0:review:{label.lower()}"] = result.artifact.relative_path
            self._remember_attempt(checkpoint, result)
        await self._save_progress(bundle, checkpoint)
        for error in review_errors:
            if not _allows_branch_degradation(error):
                raise error
            _append_warning(
                checkpoint,
                f"O0 domain review unavailable: {_bounded(str(error))}",
            )
        if review_errors:
            await self._save_progress(bundle, checkpoint)

        finalization = await self._run_with_retry(
            persistence_run_id=request.run_id,
            workspace_run_id=checkpoint.o0_workspace_run_id,
            ticker=prepared.ticker,
            cutoff_at=prepared.as_of,
            node=CodexD2Node.O0_FINALIZATION,
            role=CodexD2AgentRole.O0,
            context={
                "provisional_shells": provisional.model_dump(mode="json"),
                "domain_reviews": review_payload,
                **common,
            },
            output_model=_contract_model(request, ShellFinalizationResult),
            agent_asset="agents/o0.md",
            skill_asset="skills/shell-finalization.md",
            thread_id=checkpoint.o0_thread_ids.get("synthesis") or None,
            artifact_key="o0/finalization",
            initialization_id=request.initialization_id,
        )
        finalized = cast(ShellFinalizationResult, finalization.output)
        checkpoint.o0_thread_ids["synthesis"] = finalization.thread_id or ""
        self._remember_attempt(checkpoint, finalization)
        final_ref = await self._write_artifact(
            run_id=request.run_id,
            node=CodexD2Node.O0_FINALIZATION,
            attempt_id=finalization.attempt.attempt_id,
            relative_path="artifacts/document2/o0/final_shell_seeds.json",
            content=finalized.model_dump_json(indent=2),
            kind=ArtifactKind.BUNDLE,
            content_type="application/json",
        )
        checkpoint.final_shell_seed_path = final_ref.relative_path
        checkpoint.stage_artifacts["o0:final"] = final_ref.relative_path
        checkpoint.completed_stages = list(dict.fromkeys([*checkpoint.completed_stages, "o0"]))
        checkpoint.updated_at = utc_now()
        artifacts["final_shell_seeds"] = final_ref
        bundle.checkpoint = checkpoint
        bundle.artifacts.update(artifacts)
        self._repository.save_bundle(bundle)
        return finalized, artifacts

    async def _run_candidate(
        self,
        *,
        request: Document2RunRequest,
        prepared: PreparedDocument2Inputs,
        checkpoint: Document2Checkpoint,
        key: str,
        node: CodexD2Node,
        primary_source: str,
        narrative_run_id: object,
        common: dict[str, object],
    ) -> Document2TurnResult:
        workspace_id = logical_workspace_id(request.run_id, f"o0-candidate-{key}")
        result = await self._run_with_retry(
            persistence_run_id=request.run_id,
            workspace_run_id=workspace_id,
            ticker=prepared.ticker,
            cutoff_at=prepared.as_of,
            node=node,
            role=CodexD2AgentRole.O0,
            context={
                "source_role": key,
                "primary_source": primary_source,
                "narrative_run_id": narrative_run_id,
                **common,
            },
            output_model=_contract_model(request, CandidateDiscoveryResult),
            agent_asset="agents/o0.md",
            skill_asset="skills/candidate-discovery.md",
            thread_id=checkpoint.o0_thread_ids.get(f"candidate:{key}") or None,
            artifact_key=f"o0/candidates/{key}",
            initialization_id=request.initialization_id,
        )
        return result

    async def _run_domain_review(
        self,
        *,
        request: Document2RunRequest,
        prepared: PreparedDocument2Inputs,
        checkpoint: Document2Checkpoint,
        provisional: ShellSynthesisResult,
        reviewer_label: str,
        node: CodexD2Node,
        role: CodexAgentRole,
        report_key: str,
        common: dict[str, object],
    ) -> Document2TurnResult:
        original = self._repository.get_thread(request.source_global_run_id, role.value)
        return await self._run_with_retry(
            persistence_run_id=request.run_id,
            workspace_run_id=request.source_global_run_id,
            ticker=prepared.ticker,
            cutoff_at=prepared.as_of,
            node=node,
            role=role,
            context={
                "reviewer_role": reviewer_label,
                "original_domain_report": prepared.global_research.reports[report_key],
                "provisional_shells": provisional.model_dump(mode="json"),
                **common,
            },
            output_model=_contract_model(request, DomainReviewResult),
            agent_asset=f"agents/{report_key}-review.md",
            skill_asset="skills/domain-review.md",
            thread_id=original.thread_id if original else None,
            artifact_key=f"o0/reviews/{report_key}",
            fresh_on_retry=False,
            initialization_id=request.initialization_id,
        )

    async def _research_shell(
        self,
        *,
        request: Document2RunRequest,
        prepared: PreparedDocument2Inputs,
        seed: ExpectationShellSeed,
        o0_finalization: ShellFinalizationResult,
        checkpoint: Document2Checkpoint,
        bundle: Document2Bundle,
    ) -> tuple[ExpectationShell, ArtifactRef] | Exception:
        if request.document_schema_version == "document2.v2.1":
            return await self._research_shell_v21(
                request=request,
                prepared=prepared,
                seed=seed,
                o0_finalization=o0_finalization,
                checkpoint=checkpoint,
                bundle=bundle,
            )
        async with self._shell_concurrency.slot(prepared.ticker):
            try:
                key = _shell_key(_shell_name(seed))
                state = checkpoint.shell_runs.get(key) or ShellRunState(
                    shell_id=_shell_name(seed),
                    workspace_run_id=logical_workspace_id(request.run_id, f"shell-{key}"),
                )
                shell = await self._restore_or_initialize_shell(state, seed)
                stages = [
                    (
                        ShellResearchStage.STATE,
                        CodexD2Node.O1_STATE,
                        "skills/state-research.md",
                    ),
                    (
                        ShellResearchStage.REALIZATION,
                        CodexD2Node.O1_REALIZATION,
                        "skills/realization-research.md",
                    ),
                    (
                        ShellResearchStage.GAPS,
                        CodexD2Node.O1_GAPS,
                        "skills/gap-research.md",
                    ),
                    (
                        ShellResearchStage.FINALIZATION,
                        CodexD2Node.O1_FINALIZATION,
                        "skills/research-finalization.md",
                    ),
                ]
                completed_order = {
                    ShellResearchStage.PENDING: 0,
                    ShellResearchStage.STATE: 1,
                    ShellResearchStage.REALIZATION: 2,
                    ShellResearchStage.GAPS: 3,
                    ShellResearchStage.FINALIZATION: 4,
                    ShellResearchStage.COMPLETED: 5,
                    ShellResearchStage.FAILED: 0,
                }
                for index, (stage, node, skill) in enumerate(stages, start=1):
                    if completed_order[state.stage] >= index:
                        continue
                    turn = await self._run_with_retry(
                        persistence_run_id=request.run_id,
                        workspace_run_id=state.workspace_run_id,
                        ticker=prepared.ticker,
                        cutoff_at=prepared.as_of,
                        node=node,
                        role=CodexD2AgentRole.O1,
                        context={
                            "canonical_shell": shell.model_dump(mode="json"),
                            "o0_finalization": o0_finalization.model_dump(mode="json"),
                            "research_cutoff_at": prepared.as_of.isoformat(),
                            "source_global_research_published_at": (
                                prepared.global_research.published_at.isoformat()
                            ),
                            "global_research": prepared.global_research.model_dump(mode="json"),
                            **self._common_context(prepared),
                            "narrative_research": prepared.narrative_research.model_dump(
                                mode="json"
                            ),
                            "event_library": self._event_library_turn_context(prepared, state),
                            "turn": stage.value,
                        },
                        output_model=ExpectationShell,
                        agent_asset="agents/o1.md",
                        skill_asset=skill,
                        thread_id=state.thread_id,
                        allow_subagents=False,
                        fresh_on_retry=False,
                        artifact_key=f"shells/{key}/{stage.value.lower()}",
                        initialization_id=request.initialization_id,
                    )
                    shell = cast(ExpectationShell, turn.output)
                    state.thread_id = turn.thread_id
                    if prepared.event_library.status is InputAvailability.AVAILABLE:
                        state.event_library_injected = True
                    state.stage = stage
                    state.error = None
                    state.snapshot_paths.append(turn.artifact.relative_path)
                    self._remember_attempt(checkpoint, turn)
                    canonical = await self._workspace.write_text(
                        state.workspace_run_id,
                        "artifacts/shell.json",
                        shell.model_dump_json(indent=2),
                    )
                    state.canonical_path = canonical.relative_path
                    checkpoint.shell_runs[key] = state
                    await self._save_progress(bundle, checkpoint)
                state.stage = ShellResearchStage.COMPLETED
                state.error = None
                checkpoint.shell_runs[key] = state
                final_ref = await self._write_artifact(
                    run_id=request.run_id,
                    node=CodexD2Node.O1_FINALIZATION,
                    attempt_id=f"d2-shell-final-{uuid4().hex[:10]}",
                    relative_path=f"artifacts/document2/shells/{key}/shell.json",
                    content=shell.model_dump_json(indent=2),
                    kind=ArtifactKind.BUNDLE,
                    content_type="application/json",
                )
                await self._save_progress(bundle, checkpoint)
                return shell, final_ref
            except (Document2ExecutionError, ValueError) as exc:
                if getattr(exc, "thread_id", None) and "state" in locals():
                    state.thread_id = exc.thread_id
                    await self._save_progress(bundle, checkpoint)
                if not _allows_branch_degradation(exc):
                    raise
                if "state" in locals():
                    state.error = _bounded(str(exc))
                    checkpoint.shell_runs[key] = state
                    await self._save_progress(bundle, checkpoint)
                return exc

    async def _read_verified_output(self, run_id, reference, model):
        try:
            body = await self._workspace.read_text(run_id, reference.relative_path)
            if body.content is None or body.sha256 != reference.sha256:
                raise RuntimeError("hash mismatch")
            return model.model_validate_json(body.content)
        except (OSError, ValueError, RuntimeError) as exc:
            raise RuntimeError(
                f"D2 successful output integrity failure: {reference.relative_path}"
            ) from exc

    async def _research_shell_v21(
        self,
        *,
        request,
        prepared,
        seed,
        o0_finalization,
        checkpoint,
        bundle,
    ):
        stages = [
            (
                ShellResearchStage.OPEN_DISCOVERY,
                CodexD2Node.O1_OPEN_DISCOVERY,
                "skills/open-discovery.md",
                v21.OpenDiscoveryResultV21,
            ),
            (
                ShellResearchStage.STATE,
                CodexD2Node.O1_STATE,
                "skills/state-research.md",
                v21.ShellResearchTurnResultV21,
            ),
            (
                ShellResearchStage.REALIZATION,
                CodexD2Node.O1_REALIZATION,
                "skills/realization-research.md",
                v21.ShellResearchTurnResultV21,
            ),
            (
                ShellResearchStage.GAPS,
                CodexD2Node.O1_GAPS,
                "skills/gap-research.md",
                v21.ShellResearchTurnResultV21,
            ),
            (
                ShellResearchStage.FINALIZATION,
                CodexD2Node.O1_FINALIZATION,
                "skills/research-finalization.md",
                v21.ShellResearchTurnResultV21,
            ),
        ]
        async with self._shell_concurrency.slot(prepared.ticker):
            key = _shell_key(seed.name)
            state = checkpoint.shell_runs.get(key) or ShellRunState(
                shell_id=seed.name,
                workspace_run_id=logical_workspace_id(request.run_id, f"shell-{key}"),
            )
            checkpoint.shell_runs[key] = state
            restored_stage = state.stage
            shell = v21.ExpectationShellV21.model_validate(seed.model_dump())
            scan = selection = None
            additions = {}
            resolution = []
            orchestration_diagnostics = []
            # A bounded, shell-specific recovery lookup once on entry, rather
            # than a history scan before each healthy turn.
            recovered_outputs = {}
            recovered_threads = {
                attempt.attempt_id: attempt.thread_id
                for attempt in self._repository.list_attempts(request.run_id)
            }
            prefix = f"artifacts/document2/turns/v21/shells/{key}/"
            for ref in self._repository.list_artifacts(request.run_id):
                if ref.relative_path.startswith(prefix):
                    recovered_outputs[ref.relative_path[len(prefix) :].split("/", 1)[0].upper()] = (
                        ref
                    )
            try:
                for stage, node, skill, model in stages:
                    context = {
                        "ticker": prepared.ticker,
                        "document_schema_version": request.document_schema_version,
                        "canonical_shell": shell.model_dump(mode="json"),
                        "o0_finalization": o0_finalization.model_dump(mode="json"),
                        "research_cutoff_at": prepared.as_of.isoformat(),
                        "source_global_research_published_at": (
                            prepared.global_research.published_at.isoformat()
                        ),
                        "global_research": prepared.global_research.model_dump(mode="json"),
                        **self._common_context(prepared),
                        "narrative_research": prepared.narrative_research.model_dump(mode="json"),
                        "event_library": self._event_library_turn_context(prepared, state),
                        "turn": stage.value,
                        "orchestration_diagnostics": orchestration_diagnostics,
                        "workspace_run_id": state.workspace_run_id,
                        "discovery_contract_version": "single-v1",
                    }
                    if scan is not None:
                        context["open_discovery_scan"] = scan.model_dump(mode="json")
                    if selection is not None:
                        context["open_discovery_selection"] = selection.model_dump(mode="json")
                        context["open_discovery_late_additions"] = list(additions.values())
                    if resolution:
                        context["open_discovery_resolution"] = resolution
                    ref = state.stage_outputs.get(stage.value)
                    if ref is None:
                        # Runner success is durable before applying canonical/sidecars. Recover
                        # that output even if applying it failed before checkpoint persistence.
                        ref = recovered_outputs.get(stage.value)
                    completed_stages = [item[0] for item in stages]
                    if ref is None and (
                        restored_stage == ShellResearchStage.COMPLETED
                        or (
                            restored_stage in completed_stages
                            and completed_stages.index(restored_stage)
                            >= completed_stages.index(stage)
                        )
                    ):
                        from .acceptance import accept, accept_discovery, diagnostic
                        from .discovery_checkpoint import recover_checkpoint

                        recovered_attempt = (
                            "d2-recovered-"
                            + hashlib.sha256(
                                f"{state.workspace_run_id}:{stage.value}".encode()
                            ).hexdigest()[:20]
                        )
                        if stage == ShellResearchStage.OPEN_DISCOVERY:
                            frozen, bound, ds = await recover_checkpoint(
                                self._workspace, state.workspace_run_id, recovered_attempt, context
                            )
                            recovered = accept_discovery(frozen, context, selection_bound=bound)
                            recovered.diagnostics.extend(ds)
                        else:
                            recovered = accept(model, context)
                        diagnostic(recovered.diagnostics, "missing_snapshot", stage.value)
                        ref = await self._write_artifact(
                            run_id=request.run_id,
                            node=node,
                            attempt_id=recovered_attempt,
                            relative_path=f"artifacts/document2/accepted/{recovered_attempt}.json",
                            content=recovered.output.model_dump_json(indent=2),
                            kind=ArtifactKind.STRUCTURED_COMPLETION,
                            content_type="application/json",
                        )
                        await self._workspace.write_text(
                            request.run_id,
                            metadata_path(ref.relative_path),
                            json.dumps(recovered.metadata(), ensure_ascii=False, indent=2),
                        )
                    if ref is not None:
                        from .acceptance import accept, accept_discovery
                        from .discovery_checkpoint import recover_checkpoint, validate_checkpoint

                        try:
                            restored = await self._read_verified_output(request.run_id, ref, model)
                        except (FileNotFoundError, ValueError, RuntimeError, OSError):
                            restored = None
                        if stage == ShellResearchStage.OPEN_DISCOVERY:
                            if restored is not None:
                                try:
                                    validate_checkpoint(
                                        restored.checkpoint, context, state.workspace_run_id
                                    )
                                except ValueError:
                                    restored = None
                            if restored is None:
                                frozen, bound, ds = await recover_checkpoint(
                                    self._workspace, state.workspace_run_id, ref.attempt_id, context
                                )
                                accepted = accept_discovery(frozen, context, selection_bound=bound)
                                accepted.diagnostics.extend(ds)
                            else:
                                accepted = accept_discovery(
                                    restored.checkpoint,
                                    context,
                                    file_text=restored.model_dump_json(),
                                )
                        else:
                            accepted = accept(
                                model,
                                context,
                                file_text=restored.model_dump_json() if restored else None,
                            )
                        output = accepted.output
                        if restored is None or output != restored:
                            ref = await self._write_artifact(
                                run_id=request.run_id,
                                node=node,
                                attempt_id=ref.attempt_id,
                                relative_path=f"artifacts/document2/accepted/{hashlib.sha256(ref.relative_path.encode()).hexdigest()[:20]}.json",
                                content=output.model_dump_json(indent=2),
                                kind=ArtifactKind.STRUCTURED_COMPLETION,
                                content_type="application/json",
                            )
                            await self._workspace.write_text(
                                request.run_id,
                                metadata_path(ref.relative_path),
                                json.dumps(accepted.metadata(), ensure_ascii=False, indent=2),
                            )
                        for item in accepted.diagnostics:
                            _warn_checkpoint(
                                checkpoint,
                                f"D2_ACCEPTANCE:{seed.name}:{stage.value}:{item['code']}",
                            )
                        try:
                            metadata = await self._workspace.read_text(
                                request.run_id, metadata_path(ref.relative_path)
                            )
                            inherited = json.loads(metadata.content or "{}").get("diagnostics", [])
                        except (FileNotFoundError, ValueError):
                            inherited = []
                        for item in inherited:
                            if item not in orchestration_diagnostics:
                                orchestration_diagnostics.append(item)
                        state.thread_id = recovered_threads.get(ref.attempt_id) or state.thread_id
                    else:
                        completed = [item[0] for item in stages]
                        if restored_stage == ShellResearchStage.COMPLETED or (
                            restored_stage in completed
                            and completed.index(restored_stage) >= completed.index(stage)
                        ):
                            _warn_checkpoint(
                                checkpoint,
                                f"D2_ACCEPTANCE:{seed.name}:{stage.value}:missing_snapshot",
                            )
                        try:
                            turn = await self._run_with_retry(
                                persistence_run_id=request.run_id,
                                workspace_run_id=state.workspace_run_id,
                                ticker=prepared.ticker,
                                cutoff_at=prepared.as_of,
                                node=node,
                                role=CodexD2AgentRole.O1,
                                context=context,
                                output_model=model,
                                agent_asset="agents/o1.md",
                                skill_asset=skill,
                                thread_id=state.thread_id,
                                fresh_on_retry=False,
                                artifact_key=f"v21/shells/{key}/{stage.value.lower()}",
                                initialization_id=request.initialization_id,
                            )
                        finally:
                            if stage == ShellResearchStage.OPEN_DISCOVERY:
                                await self._sync_discovery_scan(
                                    request, state, checkpoint, bundle, seed, context
                                )
                        output, ref = turn.output, turn.artifact
                        state.thread_id = turn.thread_id
                        self._remember_attempt(checkpoint, turn)
                    try:
                        meta = await self._workspace.read_text(
                            request.run_id, metadata_path(ref.relative_path)
                        )
                        for item in json.loads(meta.content or "{}").get("diagnostics", []):
                            if item not in orchestration_diagnostics:
                                orchestration_diagnostics.append(item)
                            _warn_checkpoint(
                                checkpoint,
                                f"D2_ACCEPTANCE:{seed.name}:{stage.value}:{item['code']}",
                            )
                    except (FileNotFoundError, ValueError):
                        pass
                    checkpoint.attempt_workspaces[ref.attempt_id] = state.workspace_run_id
                    state.stage_outputs[stage.value] = ref
                    # Persist the success pointer before effects; a restart can replay effects.
                    await self._save_progress(bundle, checkpoint)
                    root = f"artifacts/document2/shells/{key}"
                    sidecars = []
                    if stage == ShellResearchStage.OPEN_DISCOVERY:
                        scan, selection = output.checkpoint.scan, output.selection
                        await self._sync_discovery_scan(
                            request, state, checkpoint, bundle, seed, context, output.checkpoint
                        )
                        sidecars.append(
                            (
                                "selection",
                                "discovery_selection_ref",
                                f"{root}/open_discovery_selection.json",
                                selection.model_dump_json(indent=2),
                            )
                        )
                    else:
                        shell = output.canonical_shell
                        from .acceptance import merge_discovery_records

                        merged = merge_discovery_records(
                            {
                                "late_additions": list(additions.values()),
                                "open_discovery_resolution": resolution,
                            },
                            output.model_dump(mode="json"),
                        )
                        additions = {
                            (item["unit"], item["name"]): item for item in merged["late_additions"]
                        }
                        resolution = merged["open_discovery_resolution"]
                        sidecars.extend(
                            [
                                (
                                    "late_additions",
                                    "late_additions_ref",
                                    f"{root}/open_discovery_late_additions.json",
                                    json.dumps(
                                        list(additions.values()), ensure_ascii=False, indent=2
                                    ),
                                ),
                                (
                                    "resolution",
                                    "discovery_resolution_ref",
                                    f"{root}/open_discovery_resolution.json",
                                    json.dumps(resolution, ensure_ascii=False, indent=2),
                                ),
                            ]
                        )
                    for label, field, path, content in sidecars:
                        prior = getattr(state, field)
                        # Immutable scan is rewritten only with identical content; mutable
                        # accumulated sidecars are deterministically rebuilt in stage order.
                        if prior is not None:
                            try:
                                body = await self._workspace.read_text(request.run_id, path)
                            except FileNotFoundError:
                                body = None
                        else:
                            body = None
                        if body is not None and body.content == content:
                            artifact = prior
                        else:
                            artifact = await self._write_artifact(
                                run_id=request.run_id,
                                node=node,
                                attempt_id=ref.attempt_id,
                                relative_path=path,
                                content=content,
                                kind=ArtifactKind.CONTEXT,
                                content_type="application/json",
                            )
                        setattr(state, field, artifact)
                        bundle.artifacts[f"discovery:{seed.name}:{label}"] = artifact
                    canonical = await self._workspace.write_text(
                        state.workspace_run_id,
                        "artifacts/shell.json",
                        shell.model_dump_json(indent=2),
                    )
                    state.canonical_path, state.canonical_sha256 = (
                        canonical.relative_path,
                        canonical.sha256,
                    )
                    state.stage, state.error = stage, None
                    state.event_library_injected = (
                        prepared.event_library.status is InputAvailability.AVAILABLE
                    )
                    if ref.relative_path not in state.snapshot_paths:
                        state.snapshot_paths.append(ref.relative_path)
                    await self._save_progress(bundle, checkpoint)
                final_ref = await self._write_artifact(
                    run_id=request.run_id,
                    node=CodexD2Node.O1_FINALIZATION,
                    attempt_id=state.stage_outputs[
                        ShellResearchStage.FINALIZATION.value
                    ].attempt_id,
                    relative_path=f"artifacts/document2/shells/{key}/shell.json",
                    content=shell.model_dump_json(indent=2),
                    kind=ArtifactKind.BUNDLE,
                    content_type="application/json",
                )
                state.stage = ShellResearchStage.COMPLETED
                await self._save_progress(bundle, checkpoint)
                return shell, final_ref
            except Document2ExecutionError as exc:
                if exc.thread_id:
                    state.thread_id = exc.thread_id
                    await self._save_progress(bundle, checkpoint)
                if not exc.allows_partial:
                    raise
                state.error = _bounded(str(exc))
                await self._save_progress(bundle, checkpoint)
                return exc

    async def _sync_discovery_scan(
        self, request, state, checkpoint, bundle, seed, context, committed=None
    ):
        from .discovery_checkpoint import (
            CHECKPOINT_PATH,
            SCAN_PATH,
            checkpoint_error,
            read_checkpoint,
            sha,
            stable_json,
            validate_checkpoint,
        )

        try:
            frozen = await read_checkpoint(self._workspace, state.workspace_run_id, context)
        except Document2ExecutionError:
            frozen = None
            if committed is None:
                return
        if committed is not None:
            validate_checkpoint(committed, context, state.workspace_run_id)
            if frozen is not None and frozen != committed:
                raise checkpoint_error("saved success differs from the frozen Scan checkpoint")
            if frozen is None:
                for frozen_path, content in (
                    (CHECKPOINT_PATH, stable_json(committed)),
                    (SCAN_PATH, stable_json(committed.scan)),
                ):
                    try:
                        existing = await self._workspace.read_text(
                            state.workspace_run_id, frozen_path
                        )
                    except FileNotFoundError:
                        await self._workspace.write_text(
                            state.workspace_run_id, frozen_path, content
                        )
                    else:
                        if existing.content != content:
                            _warn_checkpoint(checkpoint, "D2_ACCEPTANCE:original_freeze_retained")
            frozen = committed
        if frozen is None:
            return
        checkpoint.attempt_workspaces[frozen.producer_attempt_id] = state.workspace_run_id
        path = f"context/document2/discovery/{_shell_key(seed.name)}/open_discovery_scan.json"
        content = stable_json(frozen.scan)
        prior = state.discovery_scan_ref
        body = None
        invalid_parent = False
        if prior is not None:
            invalid_parent = (
                prior.sha256 != sha(content) or prior.attempt_id != frozen.producer_attempt_id
            )
            try:
                body = await self._workspace.read_text(request.run_id, prior.relative_path)
            except FileNotFoundError:
                invalid_parent = True
            else:
                invalid_parent = invalid_parent or body.content != content
        if invalid_parent:
            _warn_checkpoint(checkpoint, f"D2_ACCEPTANCE:{seed.name}:parent_scan_recovered")
            path = (
                f"context/document2/discovery/{_shell_key(seed.name)}/"
                f"recovered-{sha(content)[:16]}.json"
            )
            prior, body = None, None
        if body is None:
            prior = await self._write_artifact(
                run_id=request.run_id,
                node=CodexD2Node.O1_OPEN_DISCOVERY,
                attempt_id=frozen.producer_attempt_id,
                relative_path=path,
                content=content,
                kind=ArtifactKind.CONTEXT,
                content_type="application/json",
            )
        state.discovery_scan_ref = prior
        bundle.artifacts[f"discovery:{seed.name}:scan"] = prior
        await self._save_progress(bundle, checkpoint)

    async def _restore_or_initialize_shell(
        self, state: ShellRunState, seed: ExpectationShellSeed
    ) -> ExpectationShell:
        if state.canonical_path:
            try:
                file = await self._workspace.read_text(state.workspace_run_id, state.canonical_path)
                if file.content:
                    return ExpectationShell.model_validate_json(file.content)
            except (OSError, ValueError):
                pass
        shell = ExpectationShell(
            shell_id=_shell_name(seed),
            core_question=seed.core_question,
            boundary_rule=seed.boundary_rule,
            units=[
                ExpectationUnit(
                    expectation_id=unit.expectation_id,
                    proposition=unit.proposition,
                    horizon=unit.horizon,
                    state=ExpectationState(),
                )
                for unit in seed.units
            ],
        )
        file = await self._workspace.write_text(
            state.workspace_run_id, "artifacts/shell.json", shell.model_dump_json(indent=2)
        )
        state.canonical_path = file.relative_path
        return shell

    async def _run_with_retry(
        self,
        *,
        persistence_run_id: str,
        workspace_run_id: str,
        ticker: str,
        cutoff_at: datetime,
        node: CodexD2Node,
        role: object,
        context: dict[str, object],
        output_model: type[ModelT],
        agent_asset: str,
        skill_asset: str,
        thread_id: str | None,
        allow_subagents: bool = False,
        artifact_key: str,
        fresh_on_retry: bool = True,
        initialization_id: str | None = None,
    ) -> Document2TurnResult:
        previous_failure: str | None = None
        active_thread = thread_id
        for attempt_index in range(self._max_attempts):
            if node == CodexD2Node.O1_OPEN_DISCOVERY:
                from .discovery_checkpoint import read_checkpoint

                try:
                    frozen = await read_checkpoint(self._workspace, workspace_run_id, context)
                except Document2ExecutionError:
                    frozen = None
                if frozen is not None:
                    context = {
                        **context,
                        "resume_from": "SELECTION",
                        "open_discovery_scan": frozen.scan.model_dump(mode="json"),
                        "scan_sha256": frozen.scan_sha256,
                        "scan_producer_attempt_id": frozen.producer_attempt_id,
                    }
            try:
                return await self._runner.run(
                    persistence_run_id=persistence_run_id,
                    workspace_run_id=workspace_run_id,
                    ticker=ticker,
                    cutoff_at=cutoff_at,
                    node=node,
                    role=cast(Any, role),
                    context=context,
                    output_model=output_model,
                    agent_asset=agent_asset,
                    skill_asset=skill_asset,
                    thread_id=active_thread,
                    allow_subagents=allow_subagents,
                    previous_failure=previous_failure,
                    artifact_key=artifact_key,
                    initialization_id=initialization_id,
                )
            except Document2ExecutionError as exc:
                previous_failure = _bounded(str(exc))
                if role == CodexD2AgentRole.O1:
                    active_thread = exc.thread_id or active_thread
                if not exc.retryable:
                    raise
                if attempt_index + 1 >= self._max_attempts:
                    raise
                if fresh_on_retry and role != CodexD2AgentRole.O1:
                    active_thread = None
        raise RuntimeError("unreachable Document2 retry state")

    def _common_context(self, prepared: PreparedDocument2Inputs) -> dict[str, object]:
        source = self._repository.get_bundle(prepared.global_research.run_id)
        return {
            "ticker": prepared.ticker,
            "as_of": prepared.as_of.isoformat(),
            "future_nodes": prepared.global_research.future_nodes,
            "entity_relations": prepared.global_research.entity_relations,
            "entity_network_report": prepared.global_research.entity_network_report,
            "d1_product_status": prepared.global_research.d1_product_status,
            "d1_product_sources": prepared.global_research.d1_product_sources,
            "horizontal_indicators": prepared.global_research.horizontal_collection,
            "research_asset_sources": {
                **{
                    key: {
                        k: v
                        for k, v in value.items()
                        if k not in {"source_files", "citation_manifest"}
                    }
                    for key, value in prepared.global_research.d1_product_sources.items()
                },
                **{
                    role: {
                        "role": role,
                        "source_run_id": prepared.global_research.run_id,
                        "original_ref": ref.relative_path,
                        "source_sha256": ref.sha256,
                    }
                    for role, ref in getattr(source, "reports", {}).items()
                },
            },
        }

    def _event_library_turn_context(
        self,
        prepared: PreparedDocument2Inputs,
        state: ShellRunState,
    ) -> dict[str, object]:
        value = prepared.event_library
        if value.status is InputAvailability.AVAILABLE:
            return value.model_dump(mode="json")
        return {
            "status": value.status.value,
            "source_run_id": value.source_run_id,
            "as_of": None if value.as_of is None else value.as_of.isoformat(),
            "metadata": value.metadata,
            "payload_injected_earlier_in_thread": state.event_library_injected,
            "warning": value.warning,
        }

    async def _save_progress(
        self, bundle: Document2Bundle, checkpoint: Document2Checkpoint
    ) -> None:
        async with self._checkpoint_lock:
            checkpoint.updated_at = utc_now()
            bundle.checkpoint = checkpoint
            self._repository.save_bundle(bundle)

    async def _restore_stage(
        self,
        run_id: str,
        checkpoint: Document2Checkpoint,
        stage_key: str,
        model: type[ModelT],
    ) -> tuple[ModelT, ArtifactRef] | None:
        relative_path = checkpoint.stage_artifacts.get(stage_key)
        if not relative_path:
            return None
        reference = self._repository.get_artifact_by_path(run_id, relative_path)
        if reference is None:
            _warn_checkpoint(checkpoint, f"D2_ACCEPTANCE:{stage_key}:missing_snapshot")
            return None
        try:
            file = await self._workspace.read_text(run_id, relative_path)
            if file.content is None or file.sha256 != reference.sha256:
                _warn_checkpoint(checkpoint, f"D2_ACCEPTANCE:{stage_key}:snapshot_unusable")
                return None
            return model.model_validate_json(file.content), reference
        except (OSError, ValueError):
            _warn_checkpoint(checkpoint, f"D2_ACCEPTANCE:{stage_key}:snapshot_unusable")
            return None

    @staticmethod
    def _remember_attempt(checkpoint: Document2Checkpoint, result: Document2TurnResult) -> None:
        checkpoint.attempt_workspaces[result.attempt.attempt_id] = result.workspace_run_id
        for item in getattr(result, "diagnostics", ()):
            _warn_checkpoint(
                checkpoint, f"D2_ACCEPTANCE:{result.attempt.node.value}:{item['code']}"
            )

    async def _write_artifact(
        self,
        *,
        run_id: str,
        node: CodexD2Node,
        attempt_id: str,
        relative_path: str,
        content: str,
        kind: ArtifactKind,
        content_type: str,
        artifact_id: str | None = None,
    ) -> ArtifactRef:
        metadata = await self._workspace.write_text(run_id, relative_path, content)
        reference = ArtifactRef(
            workflow_version=CODEX_DOCUMENT2_WORKFLOW_VERSION,
            research_lane=ResearchLane.DOCUMENT2,
            artifact_id=artifact_id or uuid4().hex,
            run_id=run_id,
            node=node,
            attempt_id=attempt_id,
            kind=kind,
            relative_path=metadata.relative_path,
            sha256=metadata.sha256,
            size_bytes=metadata.size_bytes,
            content_type=content_type,
        )
        self._repository.save_artifact(reference)
        return reference

    async def _publish(
        self, run_id: str, references: list[ArtifactRef]
    ) -> tuple[datetime, list[ArtifactRef]]:
        unique = list({item.artifact_id: item for item in references}.values())
        await self._workspace.publish(run_id, [item.relative_path for item in unique])
        published_at = utc_now()
        published: list[ArtifactRef] = []
        for reference in unique:
            file = await self._workspace.read_text(run_id, reference.relative_path)
            if file.content is None:
                raise RuntimeError(
                    f"published Document2 artifact body missing: {reference.artifact_id}"
                )
            raw = file.content.encode("utf-8")
            checksum_mismatch = hashlib.sha256(raw).hexdigest() != reference.sha256
            if checksum_mismatch or len(raw) != reference.size_bytes:
                raise RuntimeError(
                    f"published Document2 checksum mismatch: {reference.artifact_id}"
                )
            storage_path: str | None = None
            content_text: str | None = file.content
            if self._published_storage is not None:
                storage_path = f"{run_id}/{reference.artifact_id}"
                await self._published_storage.put(storage_path, raw, reference.content_type)
                content_text = None
            elif len(raw) > 2 * 1024 * 1024:
                from doxagent.ticker_initialization.substeps import managed

                if not managed():
                    raise RuntimeError("PUBLISHED_DOCUMENT_STORAGE_REQUIRED for Document2 artifact")
            published_ref = reference.model_copy(update={"published": True})
            self._repository.save_artifact(published_ref)
            self._repository.save_published_document(
                PublishedDocument(
                    artifact_id=reference.artifact_id,
                    run_id=run_id,
                    artifact_kind=(
                        "manifest"
                        if reference.kind is ArtifactKind.MANIFEST
                        else "report"
                        if reference.kind is ArtifactKind.REPORT
                        else "bundle"
                    ),
                    sha256=reference.sha256,
                    size_bytes=reference.size_bytes,
                    content_type=reference.content_type,
                    content_text=content_text,
                    storage_path=storage_path,
                    published_at=published_at,
                )
            )
            published.append(published_ref)
        return published_at, published

    def _complete_workflow_node(self, checkpoint: WorkflowCheckpoint, node: CodexD2Node) -> None:
        checkpoint.completed_nodes = list(dict.fromkeys([*checkpoint.completed_nodes, node]))
        checkpoint.current_nodes = [item for item in checkpoint.current_nodes if item != node]
        checkpoint.failed_nodes = [item for item in checkpoint.failed_nodes if item != node]
        checkpoint.updated_at = utc_now()
        self._repository.save_checkpoint(checkpoint)

    def _uncomplete_workflow_node(self, checkpoint: WorkflowCheckpoint, node: CodexD2Node) -> None:
        checkpoint.completed_nodes = [item for item in checkpoint.completed_nodes if item != node]
        checkpoint.current_nodes = [item for item in checkpoint.current_nodes if item != node]
        checkpoint.failed_nodes = [item for item in checkpoint.failed_nodes if item != node]
        checkpoint.updated_at = utc_now()
        self._repository.save_checkpoint(checkpoint)

    def _record_run_failure(
        self,
        request: Document2RunRequest,
        prior: object,
        error: Exception,
    ) -> None:
        current = self._repository.get_bundle(request.run_id)
        workflow_checkpoint = self._repository.get_checkpoint(request.run_id)
        if isinstance(error, Document2ExecutionError) and workflow_checkpoint is not None:
            workflow_checkpoint.failed_nodes = list(
                dict.fromkeys([*workflow_checkpoint.failed_nodes, error.node])
            )
            workflow_checkpoint.current_nodes = [
                item for item in workflow_checkpoint.current_nodes if item != error.node
            ]
            workflow_checkpoint.updated_at = utc_now()
            self._repository.save_checkpoint(workflow_checkpoint)
        if isinstance(prior, Document2Bundle) and prior.status == "published":
            restored = prior.model_copy(
                update={
                    "checkpoint": (
                        current.checkpoint
                        if isinstance(current, Document2Bundle)
                        else prior.checkpoint
                    )
                }
            )
            self._repository.save_bundle(restored)
            return
        if isinstance(current, Document2Bundle):
            failed = current.model_copy(
                update={
                    "status": "failed",
                    "publication_state": None,
                    "current": False,
                    "handoff": None,
                    "published_at": None,
                }
            )
        else:
            failed = Document2Bundle(
                run_id=request.run_id,
                ticker=(request.ticker or "UNKNOWN").upper(),
                source_global_run_id=request.source_global_run_id,
                status="failed",
                checkpoint=Document2Checkpoint(
                    run_id=request.run_id,
                    source_global_run_id=request.source_global_run_id,
                    document_schema_version=request.document_schema_version,
                    discovery_contract_version=(
                        "single-v1"
                        if request.document_schema_version == "document2.v2.1"
                        else "split-v1"
                    ),
                    o0_workspace_run_id=logical_workspace_id(request.run_id, "o0-synthesis"),
                ),
            )
        self._repository.save_bundle(failed)

    async def _event(self, run_id: str, event_type: str, payload: dict[str, object]) -> None:
        self._repository.append_event(
            WorkflowEvent(
                workflow_version=CODEX_DOCUMENT2_WORKFLOW_VERSION,
                research_lane=ResearchLane.DOCUMENT2,
                event_id=uuid4().hex,
                run_id=run_id,
                event_type=event_type,
                sequence=0,
                payload=payload,
            )
        )
        await asyncio.sleep(0)


def _bounded(value: str, limit: int = 2_000) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _candidate_sets_context(
    candidates: dict[str, CandidateDiscoveryResult],
) -> dict[str, dict[str, object]]:
    """Add a deterministic cross-source handle without changing branch-local U# ids."""

    contextualized: dict[str, dict[str, object]] = {}
    for source_role, result in candidates.items():
        payload = result.model_dump(mode="json")
        payload["candidates"] = [
            {
                **candidate.model_dump(mode="json"),
                "candidate_ref": source_role.upper()
                + ":"
                + (
                    candidate.name
                    if isinstance(candidate, v21.CandidateUnitV21)
                    else candidate.candidate_id
                ),
            }
            for candidate in result.candidates
        ]
        contextualized[source_role] = payload
    return contextualized


def _shell_key(shell_id: str) -> str:
    return hashlib.sha256(shell_id.encode("utf-8")).hexdigest()[:16]


def _allows_branch_degradation(error: BaseException) -> bool:
    from doxagent.codex_runtime.errors import (
        CapabilityDenied,
        ImmutableWorkspacePath,
        InvalidWorkspacePath,
    )
    from doxagent.ticker_initialization.schema import LeaseLost

    if isinstance(
        error, (LeaseLost, CapabilityDenied, InvalidWorkspacePath, ImmutableWorkspacePath)
    ):
        return False
    # Storage/lease failures are not research branch gaps. Model/transport
    # failures may isolate one shell after bounded retries, while request/schema
    # failures must still stop the workflow.
    if isinstance(error, Document2ExecutionError):
        return error.allows_partial
    if isinstance(error, ValueError):
        return True
    return isinstance(error, RuntimeError) and "client has been closed" in str(error).lower()


def _append_warning(checkpoint: Document2Checkpoint, warning: str) -> None:
    if warning not in checkpoint.warnings:
        checkpoint.warnings.append(warning)


def _failed_stage(error: Exception) -> ShellResearchStage | None:
    if not isinstance(error, Document2ExecutionError):
        return None
    return {
        CodexD2Node.O1_STATE: ShellResearchStage.STATE,
        CodexD2Node.O1_OPEN_DISCOVERY: ShellResearchStage.OPEN_DISCOVERY,
        CodexD2Node.O1_REALIZATION: ShellResearchStage.REALIZATION,
        CodexD2Node.O1_GAPS: ShellResearchStage.GAPS,
        CodexD2Node.O1_FINALIZATION: ShellResearchStage.FINALIZATION,
    }.get(error.node)


def _contract_model(request, legacy):
    if request.document_schema_version == "document2.v2.1":
        return getattr(v21, legacy.__name__ + "V21")
    return legacy


def _shell_name(shell):
    return shell.name if isinstance(shell, v21.ExpectationShellSeedV21) else shell.shell_id


def _warn_checkpoint(checkpoint, warning):
    if warning not in checkpoint.warnings:
        checkpoint.warnings.append(warning)
