"""Full O0 shell construction and per-shell O1 research workflow for Document2."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from typing import Any, Literal, TypeVar, cast
from uuid import uuid4

from pydantic import BaseModel

from doxagent.codex_runtime.client import CodexWorkerClient, WorkspaceClient
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

ModelT = TypeVar("ModelT", bound=BaseModel)


class CodexDocument2Orchestrator:
    def __init__(
        self,
        *,
        worker: CodexWorkerClient,
        workspace: WorkspaceClient,
        repository: CodexRuntimeRepository,
        narrative_provider: NarrativeReportProvider,
        event_library_provider: EventLibraryProvider | None = None,
        model: str = "gpt-5.6-luna",
        model_provider: str | None = None,
        effort: Literal["low", "medium", "high", "xhigh", "max"] = "max",
        timeout_seconds: int = 1800,
        max_attempts: int = 2,
        max_subagents: int = 2,
        max_shell_concurrency: int = 4,
        published_storage: PublishedDocumentStorage | None = None,
        usage_repository: ModelUsageRepository | None = None,
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
        )
        self._max_attempts = max_attempts
        self._shell_semaphore = asyncio.Semaphore(max_shell_concurrency)
        self._published_storage = published_storage
        self._checkpoint_lock = asyncio.Lock()

    async def run(self, request: Document2RunRequest) -> Document2Bundle:
        prior = self._repository.get_bundle(request.run_id)
        try:
            return await self._run(request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_run_failure(request, prior, exc)
            raise

    async def _run(self, request: Document2RunRequest) -> Document2Bundle:
        existing = self._repository.get_bundle(request.run_id)
        if (
            isinstance(existing, Document2Bundle)
            and existing.status == "published"
            and existing.publication_state == "COMPLETE"
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
            )
        )
        successful_shells: list[ExpectationShell] = []
        shell_artifacts: list[ArtifactRef] = []
        outcomes: list[ShellOutcome] = []
        for seed, result in zip(final_seeds, shell_results, strict=True):
            if isinstance(result, Exception):
                outcomes.append(
                    ShellOutcome(
                        shell_id=seed.shell_id,
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
                    shell_id=shell.shell_id,
                    status="completed",
                    artifact_id=artifact.artifact_id,
                    seed=seed,
                )
            )
            bundle.artifacts[f"shell:{seed.shell_id}"] = artifact
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
            "COMPLETE" if all_shells_completed else "PARTIAL"
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
                "current": publication_state == "COMPLETE",
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
                ShellFinalizationResult,
            )
            if restored_final is not None:
                finalized, reference = restored_final
                return finalized, {"final_shell_seeds": reference}
        common = self._common_context(prepared)
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
            restored_candidate = await self._restore_stage(
                request.run_id,
                checkpoint,
                f"o0:candidate:{key}",
                CandidateDiscoveryResult,
            )
            if restored_candidate is None:
                missing_specs.append(spec)
            else:
                candidates[key], reference = restored_candidate
                artifacts[f"candidate:{key}"] = reference
        missing_results = await asyncio.gather(
            *(
                self._run_candidate(
                    request=request,
                    prepared=prepared,
                    checkpoint=checkpoint,
                    key=key,
                    node=node,
                    primary_source=primary,
                    narrative_run_id=source_id,
                    common=common,
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
            request.run_id, checkpoint, "o0:synthesis", ShellSynthesisResult
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
                    **common,
                },
                output_model=ShellSynthesisResult,
                agent_asset="agents/o0.md",
                skill_asset="skills/shell-synthesis.md",
                thread_id=checkpoint.o0_thread_ids.get("synthesis") or None,
                artifact_key="o0/synthesis",
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
                DomainReviewResult,
            )
            if restored_review is None:
                missing_reviews.append(spec)
            else:
                review, reference = restored_review
                review_payload[label] = review.model_dump(mode="json")
                artifacts[f"review:{label.lower()}"] = reference
        review_results = await asyncio.gather(
            *(
                self._run_domain_review(
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
            output_model=ShellFinalizationResult,
            agent_asset="agents/o0.md",
            skill_asset="skills/shell-finalization.md",
            thread_id=checkpoint.o0_thread_ids.get("synthesis") or None,
            artifact_key="o0/finalization",
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
            output_model=CandidateDiscoveryResult,
            agent_asset="agents/o0.md",
            skill_asset="skills/candidate-discovery.md",
            thread_id=checkpoint.o0_thread_ids.get(f"candidate:{key}") or None,
            artifact_key=f"o0/candidates/{key}",
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
        if original is None:
            raise ValueError(f"original Global Research {reviewer_label} thread is unavailable")
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
            output_model=DomainReviewResult,
            agent_asset=f"agents/{report_key}-review.md",
            skill_asset="skills/domain-review.md",
            thread_id=original.thread_id,
            artifact_key=f"o0/reviews/{report_key}",
            fresh_on_retry=False,
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
        async with self._shell_semaphore:
            try:
                key = _shell_key(seed.shell_id)
                state = checkpoint.shell_runs.get(key) or ShellRunState(
                    shell_id=seed.shell_id,
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
                        artifact_key=f"shells/{key}/{stage.value.lower()}",
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
            except Document2ExecutionError as exc:
                if not exc.allows_partial:
                    raise
                if "state" in locals():
                    state.error = _bounded(str(exc))
                    checkpoint.shell_runs[key] = state
                    await self._save_progress(bundle, checkpoint)
                return exc

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
            shell_id=seed.shell_id,
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
    ) -> Document2TurnResult:
        previous_failure: str | None = None
        active_thread = thread_id
        for attempt_index in range(self._max_attempts):
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
                )
            except Document2ExecutionError as exc:
                previous_failure = _bounded(str(exc))
                if not exc.retryable:
                    raise
                if attempt_index + 1 >= self._max_attempts:
                    raise
                if fresh_on_retry:
                    active_thread = None
        raise RuntimeError("unreachable Document2 retry state")

    def _common_context(self, prepared: PreparedDocument2Inputs) -> dict[str, object]:
        return {
            "ticker": prepared.ticker,
            "as_of": prepared.as_of.isoformat(),
            "future_nodes": prepared.global_research.future_nodes,
            "horizontal_indicators": prepared.global_research.horizontal_collection,
        }

    def _event_library_turn_context(
        self,
        prepared: PreparedDocument2Inputs,
        state: ShellRunState,
    ) -> dict[str, object]:
        value = prepared.event_library
        if value.status is InputAvailability.AVAILABLE and not state.event_library_injected:
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
            return None
        try:
            file = await self._workspace.read_text(run_id, relative_path)
            if file.content is None or file.sha256 != reference.sha256:
                return None
            return model.model_validate_json(file.content), reference
        except (OSError, ValueError):
            return None

    @staticmethod
    def _remember_attempt(checkpoint: Document2Checkpoint, result: Document2TurnResult) -> None:
        checkpoint.attempt_workspaces[result.attempt.attempt_id] = result.workspace_run_id

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
                "candidate_ref": f"{source_role.upper()}:{candidate.candidate_id}",
            }
            for candidate in result.candidates
        ]
        contextualized[source_role] = payload
    return contextualized


def _shell_key(shell_id: str) -> str:
    return hashlib.sha256(shell_id.encode("utf-8")).hexdigest()[:16]


def _allows_branch_degradation(error: BaseException) -> bool:
    return isinstance(error, Document2ExecutionError) and error.allows_partial


def _append_warning(checkpoint: Document2Checkpoint, warning: str) -> None:
    if warning not in checkpoint.warnings:
        checkpoint.warnings.append(warning)


def _failed_stage(error: Exception) -> ShellResearchStage | None:
    if not isinstance(error, Document2ExecutionError):
        return None
    return {
        CodexD2Node.O1_STATE: ShellResearchStage.STATE,
        CodexD2Node.O1_REALIZATION: ShellResearchStage.REALIZATION,
        CodexD2Node.O1_GAPS: ShellResearchStage.GAPS,
        CodexD2Node.O1_FINALIZATION: ShellResearchStage.FINALIZATION,
    }.get(error.node)
