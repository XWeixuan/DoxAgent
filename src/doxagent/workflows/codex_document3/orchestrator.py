"""D3 initialization/maintenance orchestration and canonical publication."""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeVar
from uuid import uuid4

from pydantic import BaseModel

from doxagent.codex_runtime.published_storage import PublishedDocumentStorage
from doxagent.codex_runtime.schema import (
    CODEX_DOCUMENT3_WORKFLOW_VERSION,
    ArtifactKind,
    ArtifactRef,
    CodexD3Node,
    PublishedDocument,
    ResearchLane,
    WorkflowCheckpoint,
    utc_now,
)
from doxagent.workflows.codex_document2.schema import Document2Document

if TYPE_CHECKING:
    from doxagent.persistent_runtime_v2.schema import O3MaintenanceFeed

from .assembler import apply_patch, assemble_initial_policy_set, build_coverage_map
from .identity import allocate_stable_policy_ids
from .inputs import Document3InputPreparer, PreparedDocument3Inputs
from .repository import Document3PolicyRepository
from .runner import (
    INITIALIZE_BUSINESS_INPUT_PATHS,
    INPUT_MANIFEST_PATH,
    Document3AgentRunner,
)
from .runtime_projection import project_policy_set
from .schema import (
    CalibrationLogEntry,
    CoverageMap,
    Document3Bundle,
    Document3Handoff,
    Document3InitializeTask,
    Document3InputManifest,
    FrozenInputFile,
    O3RunResult,
    O3RunStatus,
    Policy,
    PolicyPatchSet,
    PolicySet,
    PublicationState,
    ReviewResult,
    TriggerCalibrationRecord,
    TriggerCalibrationState,
    WaveState,
    WorklistEntry,
)
from .validator import (
    validate_initial_artifacts,
    validate_patch,
    validate_trigger_calibration_stage,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


class Document3RuntimeRepository(Protocol):
    def save_bundle(self, bundle: Any) -> None: ...

    def save_artifact(self, artifact: ArtifactRef) -> None: ...

    def save_published_document(self, document: PublishedDocument) -> None: ...

    def get_bundle(self, run_id: str) -> object | None: ...

    def save_checkpoint(self, checkpoint: WorkflowCheckpoint) -> None: ...

    def get_checkpoint(self, run_id: str) -> WorkflowCheckpoint | None: ...


class Document3Orchestrator:
    def __init__(
        self,
        *,
        input_preparer: Document3InputPreparer,
        agent_runner: Document3AgentRunner,
        policy_repository: Document3PolicyRepository,
        runtime_repository: Document3RuntimeRepository,
        published_storage: PublishedDocumentStorage | None = None,
    ) -> None:
        self._inputs = input_preparer
        self._agent = agent_runner
        self._policy_repository = policy_repository
        self._runtime_repository = runtime_repository
        self._published_storage = published_storage

    async def initialize(
        self,
        *,
        ticker: str,
        document2_run_id: str,
        event_library_version: int | None = None,
        run_id: str | None = None,
        cutoff_at: datetime | None = None,
    ) -> O3RunResult:
        normalized_ticker = ticker.upper()
        selected_run_id = run_id or f"d3-{normalized_ticker.lower()}-{uuid4().hex[:20]}"
        existing = self._runtime_repository.get_bundle(selected_run_id)
        if isinstance(existing, Document3Bundle) and existing.status == "published":
            if existing.handoff is None:
                raise ValueError("Published D3 bundle is missing its handoff")
            published = self._policy_repository.get_version(
                normalized_ticker, existing.handoff.policy_set_version
            )
            if published is None:
                raise ValueError("Published D3 bundle has no canonical Policy Set")
            return O3RunResult(
                status=(
                    O3RunStatus.COMPLETED
                    if published.publication_state is PublicationState.COMPLETE
                    else O3RunStatus.PARTIAL
                ),
                policy_count=len(published.policies),
                policy_set_version=published.policy_set_version,
            )

        checkpoint = self._runtime_repository.get_checkpoint(selected_run_id)
        if checkpoint is None:
            checkpoint = WorkflowCheckpoint(
                workflow_version=CODEX_DOCUMENT3_WORKFLOW_VERSION,
                research_lane=ResearchLane.DOCUMENT3,
                ticker=normalized_ticker,
                run_id=selected_run_id,
            )
            self._runtime_repository.save_checkpoint(checkpoint)
        elif (
            checkpoint.research_lane is not ResearchLane.DOCUMENT3
            or checkpoint.ticker.upper() != normalized_ticker
        ):
            raise ValueError("run_id belongs to another workflow lane or ticker")

        self._runtime_repository.save_bundle(
            Document3Bundle(
                ticker=normalized_ticker,
                run_id=selected_run_id,
                status="draft",
            )
        )
        active_node: CodexD3Node | None = None
        try:
            inventory = await self._agent.workspace.inventory(selected_run_id)
            workspace_paths = {item.relative_path for item in inventory.files}
            if INPUT_MANIFEST_PATH in workspace_paths:
                prepared, task = await self._load_frozen_initialize_inputs(
                    run_id=selected_run_id,
                    ticker=normalized_ticker,
                    document2_run_id=document2_run_id,
                    requested_event_library_version=event_library_version,
                )
                if cutoff_at is not None and cutoff_at != task.cutoff_at:
                    raise ValueError("resume cutoff_at differs from frozen task context")
                cutoff = task.cutoff_at
                await self._verify_input_manifest(selected_run_id)
            else:
                if workspace_paths:
                    raise ValueError(
                        "D3 workspace contains files but no frozen input manifest; "
                        "refusing a destructive reseed"
                    )
                cutoff = cutoff_at or utc_now()
                prepared = await self._inputs.prepare_initialize(
                    ticker=normalized_ticker,
                    document2_run_id=document2_run_id,
                    event_library_version=event_library_version,
                )
                task = Document3InitializeTask(
                    ticker=normalized_ticker,
                    run_id=selected_run_id,
                    cutoff_at=cutoff,
                    document2_ref=prepared.document2_ref,
                    requested_event_library_version=event_library_version,
                    event_library_ref=prepared.event_library_ref,
                    failed_shells=prepared.failed_shells,
                    warnings=prepared.warnings,
                )
                previous = prepared.previous_policy_set
                await self._agent.seed_initialize(
                    run_id=selected_run_id,
                    document2_json=prepared.document2.model_dump_json(indent=2),
                    reference_view=prepared.reference_view,
                    previous_policy_set_json=(
                        previous.model_dump_json(indent=2) if previous else None
                    ),
                    task=task,
                )
                await self._create_input_manifest(selected_run_id)
                await self._verify_input_manifest(selected_run_id)
            self._complete_node(checkpoint, CodexD3Node.INPUT_PREPARATION)

            previous = prepared.previous_policy_set
            thread_id: str | None = None

            if CodexD3Node.O3_TRIGGER_CALIBRATION in checkpoint.completed_nodes:
                await self._assert_agent_write_boundary(
                    selected_run_id, initialize=True
                )
                await self._verify_input_manifest(selected_run_id)
                try:
                    await self._validate_stage_a_checkpoint(
                        selected_run_id,
                        prepared,
                        require_pending_worklist=False,
                    )
                except (FileNotFoundError, ValueError):
                    self._reset_from_stage_a(checkpoint)

            if CodexD3Node.O3_TRIGGER_CALIBRATION not in checkpoint.completed_nodes:
                active_node = CodexD3Node.O3_TRIGGER_CALIBRATION
                self._start_node(checkpoint, active_node)
                stage_a_result, thread_id = await self._agent.run_trigger_calibration(
                    run_id=selected_run_id,
                    ticker=normalized_ticker,
                    cutoff_at=cutoff,
                    thread_id=thread_id,
                )
                if stage_a_result.status != "COMPLETED":
                    raise ValueError("O3 Trigger Calibration declared a failed turn")
                await self._assert_agent_write_boundary(selected_run_id, initialize=True)
                await self._verify_input_manifest(selected_run_id)
                await self._validate_stage_a_checkpoint(
                    selected_run_id,
                    prepared,
                    require_pending_worklist=True,
                )
                self._complete_node(checkpoint, active_node)
                active_node = None

            if CodexD3Node.O3_POLICY_COMPILE in checkpoint.completed_nodes:
                await self._assert_agent_write_boundary(
                    selected_run_id, initialize=True
                )
                await self._verify_input_manifest(selected_run_id)
                try:
                    await self._validate_compile_checkpoint(selected_run_id, prepared)
                except (FileNotFoundError, ValueError):
                    self._reset_from_compile(checkpoint)

            if CodexD3Node.O3_POLICY_COMPILE not in checkpoint.completed_nodes:
                active_node = CodexD3Node.O3_POLICY_COMPILE
                self._start_node(checkpoint, active_node)
                compile_result, thread_id = await self._agent.run_policy_compile(
                    run_id=selected_run_id,
                    ticker=normalized_ticker,
                    cutoff_at=cutoff,
                    thread_id=thread_id,
                )
                if compile_result.status not in {
                    O3RunStatus.COMPLETED,
                    O3RunStatus.PARTIAL,
                }:
                    raise ValueError("O3 Policy Compile did not complete its lifecycle")
                await self._assert_agent_write_boundary(selected_run_id, initialize=True)
                await self._verify_input_manifest(selected_run_id)
                await self._validate_compile_checkpoint(selected_run_id, prepared)
                self._complete_node(checkpoint, active_node)
                active_node = None

            await self._assert_agent_write_boundary(selected_run_id, initialize=True)
            await self._verify_input_manifest(selected_run_id)
            review: ReviewResult
            if CodexD3Node.O3_FINAL_REVIEW in checkpoint.completed_nodes:
                try:
                    review = await self._read_json(
                        selected_run_id,
                        "output/work/final_review_result.json",
                        ReviewResult,
                    )
                except (FileNotFoundError, ValueError):
                    self._reset_final_review(checkpoint)
            if CodexD3Node.O3_FINAL_REVIEW not in checkpoint.completed_nodes:
                worklist = await self._read_jsonl(
                    selected_run_id, "output/work/worklist.jsonl", WorklistEntry
                )
                provisional = build_coverage_map(
                    ticker=normalized_ticker,
                    worklist=worklist,
                    expected_gap_refs=prepared.expected_gap_refs,
                    failed_shells=prepared.failed_shells,
                    warnings=prepared.warnings,
                )
                await self._agent.workspace.write_text(
                    selected_run_id,
                    "output/work/coverage_map.json",
                    provisional.model_dump_json(indent=2),
                )
                active_node = CodexD3Node.O3_FINAL_REVIEW
                self._start_node(checkpoint, active_node)
                review, thread_id = await self._agent.run_final_review(
                    run_id=selected_run_id,
                    ticker=normalized_ticker,
                    cutoff_at=cutoff,
                    thread_id=thread_id,
                )
                if review.status == "REVIEW_BLOCKED" and review.blocking_issue_count:
                    raise ValueError(
                        "O3 Final Global Pass reported a structural blocking issue"
                    )
                await self._agent.workspace.write_text(
                    selected_run_id,
                    "output/work/final_review_result.json",
                    review.model_dump_json(indent=2),
                )
                await self._assert_agent_write_boundary(selected_run_id, initialize=True)
                await self._verify_input_manifest(selected_run_id)
                self._complete_node(checkpoint, active_node)
                active_node = None

            # Final Review may edit every mutable initialize artifact, so the
            # frozen inputs and write boundary are checked again before reread.
            await self._assert_agent_write_boundary(selected_run_id, initialize=True)
            await self._verify_input_manifest(selected_run_id)
            active_node = CodexD3Node.VALIDATE
            self._start_node(checkpoint, active_node)
            worklist = await self._read_jsonl(
                selected_run_id, "output/work/worklist.jsonl", WorklistEntry
            )
            calibration_log = await self._read_jsonl(
                selected_run_id,
                "output/work/calibration_log.jsonl",
                CalibrationLogEntry,
            )
            trigger_calibrations = await self._read_jsonl(
                selected_run_id,
                "output/work/trigger_calibrations.jsonl",
                TriggerCalibrationRecord,
            )
            trigger_state = await self._read_json(
                selected_run_id,
                "output/work/trigger_calibration_state.json",
                TriggerCalibrationState,
            )
            wave_state = await self._read_json(
                selected_run_id, "output/work/wave_state.json", WaveState
            )
            reviewed_coverage = await self._read_json(
                selected_run_id, "output/work/coverage_map.json", CoverageMap
            )
            if reviewed_coverage.ticker.upper() != normalized_ticker:
                raise ValueError("Final Review coverage_map ticker mismatch")
            reviewed_coverage_paths = {
                (
                    gap.shell_id,
                    gap.expectation_id,
                    gap.gap_id,
                    path.path_id,
                    path.status,
                    tuple(path.policy_ids),
                )
                for gap in reviewed_coverage.gaps
                for path in gap.paths
            }
            worklist_coverage_paths = {
                (
                    item.shell_id,
                    item.expectation_id,
                    item.gap_id,
                    item.path_id,
                    item.status,
                    tuple(item.policy_ids),
                )
                for item in worklist
            }
            coverage_consistency_warnings = (
                []
                if reviewed_coverage_paths == worklist_coverage_paths
                else [
                    "Final Review coverage_map did not match the final Worklist and was "
                    "rebuilt deterministically."
                ]
            )
            policies = await self._read_policy_drafts(selected_run_id)
            validation = validate_initial_artifacts(
                expected_gap_refs=prepared.expected_gap_refs,
                worklist=worklist,
                calibration_log=calibration_log,
                policies=policies,
                trigger_calibrations=trigger_calibrations,
                trigger_state=trigger_state,
                wave_state=wave_state,
            )
            if not validation.valid:
                raise ValueError(
                    "D3 deterministic structural validation failed: "
                    + "; ".join(item.message for item in validation.blocking_findings)
                )
            if (
                prepared.document2_ref.publication_state is PublicationState.PARTIAL
                or prepared.warnings
                or review.issue_count
                or coverage_consistency_warnings
            ):
                validation = validation.model_copy(
                    update={"publication_state": PublicationState.PARTIAL}
                )
            self._complete_node(checkpoint, active_node)
            active_node = CodexD3Node.ASSEMBLE
            self._start_node(checkpoint, active_node)

            policy_set, id_map = assemble_initial_policy_set(
                ticker=normalized_ticker,
                document2_ref=prepared.document2_ref,
                event_library_ref=prepared.event_library_ref,
                drafts=policies,
                previous=previous,
                validation=validation,
                published_at=utc_now(),
            )
            canonical_worklist = [
                item.model_copy(
                    update={
                        "policy_ids": [
                            id_map.get(policy_id, policy_id) for policy_id in item.policy_ids
                        ]
                    }
                )
                for item in worklist
            ]
            coverage = build_coverage_map(
                ticker=normalized_ticker,
                worklist=canonical_worklist,
                expected_gap_refs=prepared.expected_gap_refs,
                failed_shells=prepared.failed_shells,
                warnings=[
                    *prepared.warnings,
                    *reviewed_coverage.warnings,
                    *coverage_consistency_warnings,
                    *(item.message for item in validation.findings),
                    *(item.message for item in review.issues),
                ],
            )
            self._complete_node(checkpoint, active_node)
            active_node = CodexD3Node.PUBLISH
            self._start_node(checkpoint, active_node)
            expected_base = previous.policy_set_version if previous else None
            handoff = await self._publish_policy_set(
                run_id=selected_run_id,
                policy_set=policy_set,
                coverage=coverage,
                expected_base_version=expected_base,
            )
            self._complete_node(checkpoint, active_node)
            active_node = None
            self._runtime_repository.save_bundle(
                Document3Bundle(
                    ticker=normalized_ticker,
                    run_id=selected_run_id,
                    status="published",
                    handoff=handoff,
                    artifacts=[
                        handoff.published_artifact,
                        handoff.coverage_artifact,
                        handoff.runtime_projection_artifact,
                    ],
                    updated_at=handoff.published_at,
                    published_at=handoff.published_at,
                )
            )
            unresolved = sum(item.status.value == "UNRESOLVED" for item in canonical_worklist)
            return O3RunResult(
                status=(
                    O3RunStatus.COMPLETED
                    if policy_set.publication_state is PublicationState.COMPLETE
                    else O3RunStatus.PARTIAL
                ),
                processed_gap_count=len(prepared.expected_gap_refs),
                policy_count=len(policy_set.policies),
                unresolved_path_count=unresolved,
                warning_count=len(coverage.warnings),
                policy_set_version=policy_set.policy_set_version,
            )
        except Exception as exc:
            if active_node is not None:
                self._fail_node(checkpoint, active_node)
            self._runtime_repository.save_bundle(
                Document3Bundle(
                    ticker=normalized_ticker,
                    run_id=selected_run_id,
                    status="failed",
                    error=str(exc)[:4000],
                )
            )
            raise

    async def maintain(
        self,
        *,
        ticker: str,
        event_library_version: int | None = None,
        run_id: str | None = None,
        cutoff_at: datetime | None = None,
        maintenance_feed: O3MaintenanceFeed | None = None,
    ) -> O3RunResult:
        normalized_ticker = ticker.upper()
        current, event_ref, reference_view = self._inputs.prepare_maintenance_reference(
            ticker=normalized_ticker,
            event_library_version=event_library_version,
        )
        if event_ref is None or (
            maintenance_feed is None
            and current.event_library_ref is not None
            and event_ref.version <= current.event_library_ref.version
        ):
            return O3RunResult(
                status=O3RunStatus.NOOP,
                policy_count=len(current.policies),
                policy_set_version=current.policy_set_version,
            )
        if maintenance_feed is not None:
            reference_view = maintenance_feed.reference_view_delta.reference_view_delta
        if maintenance_feed is None and not self._reference_view_has_content(reference_view):
            return O3RunResult(
                status=O3RunStatus.DEGRADED,
                policy_count=len(current.policies),
                warning_count=1,
                policy_set_version=current.policy_set_version,
            )

        selected_run_id = run_id or f"d3m-{normalized_ticker.lower()}-{uuid4().hex[:20]}"
        await self._agent.seed_maintenance(
            run_id=selected_run_id,
            policy_set_json=current.model_dump_json(indent=2),
            reference_view=reference_view,
            metadata={
                "mode": "O3_MAINTAIN",
                "ticker": normalized_ticker,
                "run_id": selected_run_id,
                "base_policy_set_version": current.policy_set_version,
                "event_library_ref": event_ref.model_dump(mode="json"),
                "runtime_feedback": (
                    None
                    if maintenance_feed is None
                    else {
                        "contract_version": maintenance_feed.contract_version,
                        "trading_date": maintenance_feed.trading_date.isoformat(),
                        "trade_record_count": len(maintenance_feed.trade_records),
                        "badcase_record_count": len(maintenance_feed.badcase_records),
                        "reference_from_version": (
                            maintenance_feed.reference_view_delta.from_library_version
                        ),
                        "reference_to_version": (
                            maintenance_feed.reference_view_delta.to_library_version
                        ),
                    }
                ),
            },
            maintenance_feed_json=(
                maintenance_feed.model_dump_json(indent=2)
                if maintenance_feed is not None
                else None
            ),
        )
        await self._agent.run_maintain(
            run_id=selected_run_id,
            ticker=normalized_ticker,
            cutoff_at=cutoff_at or utc_now(),
        )
        await self._assert_agent_write_boundary(selected_run_id)
        patch = await self._read_json(
            selected_run_id, "output/work/policy_patch.json", PolicyPatchSet
        )
        stable_upserts, _ = allocate_stable_policy_ids(
            ticker=normalized_ticker,
            drafts=patch.upsert_policies,
            previous=current.policies,
        )
        patch = patch.model_copy(update={"upsert_policies": stable_upserts})
        validation = validate_patch(
            patch=patch,
            current_policy_ids={item.policy_id for item in current.policies},
            current_version=current.policy_set_version,
        )
        if not validation.valid:
            raise ValueError(validation.blocking_findings[0].message)
        updated = apply_patch(current=current, patch=patch, published_at=utc_now())
        if updated is None:
            return O3RunResult(
                status=O3RunStatus.NOOP,
                policy_count=len(current.policies),
                policy_set_version=current.policy_set_version,
            )
        if validation.findings:
            updated = updated.model_copy(update={"publication_state": PublicationState.PARTIAL})
        coverage = CoverageMap(
            ticker=normalized_ticker,
            warnings=[item.message for item in validation.findings],
        )
        self._runtime_repository.save_bundle(
            Document3Bundle(
                ticker=normalized_ticker,
                run_id=selected_run_id,
                status="draft",
            )
        )
        try:
            handoff = await self._publish_policy_set(
                run_id=selected_run_id,
                policy_set=updated,
                coverage=coverage,
                expected_base_version=current.policy_set_version,
            )
        except Exception as exc:
            self._runtime_repository.save_bundle(
                Document3Bundle(
                    ticker=normalized_ticker,
                    run_id=selected_run_id,
                    status="failed",
                    error=str(exc)[:4000],
                )
            )
            raise
        self._runtime_repository.save_bundle(
            Document3Bundle(
                ticker=normalized_ticker,
                run_id=selected_run_id,
                status="published",
                handoff=handoff,
                artifacts=[
                    handoff.published_artifact,
                    handoff.coverage_artifact,
                    handoff.runtime_projection_artifact,
                ],
                updated_at=handoff.published_at,
                published_at=handoff.published_at,
            )
        )
        return O3RunResult(
            status=(
                O3RunStatus.PARTIAL
                if updated.publication_state is PublicationState.PARTIAL
                else O3RunStatus.COMPLETED
            ),
            policy_count=len(updated.policies),
            warning_count=len(validation.findings),
            policy_set_version=updated.policy_set_version,
        )

    async def _create_input_manifest(self, run_id: str) -> Document3InputManifest:
        inventory = await self._agent.workspace.inventory(run_id)
        by_path = {item.relative_path: item for item in inventory.files}
        missing = [path for path in INITIALIZE_BUSINESS_INPUT_PATHS if path not in by_path]
        if missing:
            raise ValueError(f"Cannot freeze missing D3 inputs: {missing}")
        manifest = Document3InputManifest(
            files=[
                FrozenInputFile(
                    relative_path=path,
                    size_bytes=by_path[path].size_bytes,
                    sha256=by_path[path].sha256,
                )
                for path in INITIALIZE_BUSINESS_INPUT_PATHS
            ]
        )
        await self._agent.workspace.write_text(
            run_id, INPUT_MANIFEST_PATH, manifest.model_dump_json(indent=2)
        )
        return manifest

    async def _verify_input_manifest(self, run_id: str) -> Document3InputManifest:
        manifest = await self._read_json(
            run_id, INPUT_MANIFEST_PATH, Document3InputManifest
        )
        expected_paths = set(INITIALIZE_BUSINESS_INPUT_PATHS)
        manifest_paths = {item.relative_path for item in manifest.files}
        if manifest_paths != expected_paths:
            raise ValueError("D3 input manifest does not contain the exact frozen input set")
        inventory = await self._agent.workspace.inventory(run_id)
        by_path = {item.relative_path: item for item in inventory.files}
        for item in manifest.files:
            current = by_path.get(item.relative_path)
            if current is None:
                raise ValueError(f"Frozen D3 input is missing: {item.relative_path}")
            if current.size_bytes != item.size_bytes or current.sha256 != item.sha256:
                raise ValueError(f"Frozen D3 input changed: {item.relative_path}")
        return manifest

    async def _load_frozen_initialize_inputs(
        self,
        *,
        run_id: str,
        ticker: str,
        document2_run_id: str,
        requested_event_library_version: int | None,
    ) -> tuple[PreparedDocument3Inputs, Document3InitializeTask]:
        await self._verify_input_manifest(run_id)
        task = await self._read_json(
            run_id, "context/document3/task.json", Document3InitializeTask
        )
        if task.run_id != run_id or task.ticker.upper() != ticker.upper():
            raise ValueError("Frozen D3 task does not match the requested run/ticker")
        if task.document2_ref.run_id != document2_run_id:
            raise ValueError("resume document2_run_id differs from frozen task context")
        if (
            requested_event_library_version is not None
            and requested_event_library_version
            != task.requested_event_library_version
        ):
            raise ValueError(
                "resume event_library_version differs from frozen task context"
            )
        document2 = await self._read_json(
            run_id, "context/document3/document2.json", Document2Document
        )
        if document2.ticker.upper() != ticker.upper():
            raise ValueError("Frozen D2 ticker does not match D3 task")
        previous_response = await self._agent.workspace.read_text(
            run_id, "context/document3/previous_policy_set.json"
        )
        previous_content = (previous_response.content or "").strip()
        previous = (
            None
            if previous_content == "null"
            else PolicySet.model_validate_json(previous_content)
        )
        if previous is not None and previous.ticker.upper() != ticker.upper():
            raise ValueError("Frozen Previous Policy Set ticker does not match D3 task")
        reference_response = await self._agent.workspace.read_text(
            run_id, "context/document3/reference_event_view.md"
        )
        expected_gap_refs = [
            (shell.shell_id, unit.expectation_id, gap.gap_id)
            for shell in document2.shells
            for unit in shell.units
            for gap in unit.potential_gaps
        ]
        return (
            PreparedDocument3Inputs(
                ticker=ticker.upper(),
                document2=document2,
                document2_ref=task.document2_ref,
                expected_gap_refs=expected_gap_refs,
                failed_shells=task.failed_shells,
                event_library_ref=task.event_library_ref,
                reference_view=reference_response.content or "",
                previous_policy_set=previous,
                warnings=task.warnings,
            ),
            task,
        )

    async def _validate_stage_a_checkpoint(
        self,
        run_id: str,
        prepared: PreparedDocument3Inputs,
        *,
        require_pending_worklist: bool,
    ) -> None:
        worklist = await self._read_jsonl(
            run_id, "output/work/worklist.jsonl", WorklistEntry
        )
        trigger_calibrations = await self._read_jsonl(
            run_id,
            "output/work/trigger_calibrations.jsonl",
            TriggerCalibrationRecord,
        )
        trigger_state = await self._read_json(
            run_id,
            "output/work/trigger_calibration_state.json",
            TriggerCalibrationState,
        )
        report = validate_trigger_calibration_stage(
            expected_gap_refs=prepared.expected_gap_refs,
            worklist=worklist,
            trigger_calibrations=trigger_calibrations,
            trigger_state=trigger_state,
            require_pending_worklist=require_pending_worklist,
        )
        if not report.valid:
            raise ValueError(
                "D3 Trigger Calibration Stage Gate failed: "
                + "; ".join(item.message for item in report.blocking_findings)
            )

    async def _validate_compile_checkpoint(
        self, run_id: str, prepared: PreparedDocument3Inputs
    ) -> None:
        worklist = await self._read_jsonl(
            run_id, "output/work/worklist.jsonl", WorklistEntry
        )
        calibration_log = await self._read_jsonl(
            run_id, "output/work/calibration_log.jsonl", CalibrationLogEntry
        )
        trigger_calibrations = await self._read_jsonl(
            run_id,
            "output/work/trigger_calibrations.jsonl",
            TriggerCalibrationRecord,
        )
        trigger_state = await self._read_json(
            run_id,
            "output/work/trigger_calibration_state.json",
            TriggerCalibrationState,
        )
        wave_state = await self._read_json(
            run_id, "output/work/wave_state.json", WaveState
        )
        policies = await self._read_policy_drafts(run_id)
        report = validate_initial_artifacts(
            expected_gap_refs=prepared.expected_gap_refs,
            worklist=worklist,
            calibration_log=calibration_log,
            policies=policies,
            trigger_calibrations=trigger_calibrations,
            trigger_state=trigger_state,
            wave_state=wave_state,
        )
        if not report.valid:
            raise ValueError(
                "D3 Policy Compile checkpoint failed: "
                + "; ".join(item.message for item in report.blocking_findings)
            )

    def _start_node(self, checkpoint: WorkflowCheckpoint, node: CodexD3Node) -> None:
        checkpoint.current_nodes = list(dict.fromkeys([*checkpoint.current_nodes, node]))
        checkpoint.failed_nodes = [item for item in checkpoint.failed_nodes if item != node]
        checkpoint.updated_at = utc_now()
        self._runtime_repository.save_checkpoint(checkpoint)

    def _complete_node(self, checkpoint: WorkflowCheckpoint, node: CodexD3Node) -> None:
        checkpoint.completed_nodes = list(
            dict.fromkeys([*checkpoint.completed_nodes, node])
        )
        checkpoint.current_nodes = [item for item in checkpoint.current_nodes if item != node]
        checkpoint.failed_nodes = [item for item in checkpoint.failed_nodes if item != node]
        checkpoint.updated_at = utc_now()
        self._runtime_repository.save_checkpoint(checkpoint)

    def _fail_node(self, checkpoint: WorkflowCheckpoint, node: CodexD3Node) -> None:
        checkpoint.failed_nodes = list(dict.fromkeys([*checkpoint.failed_nodes, node]))
        checkpoint.current_nodes = [item for item in checkpoint.current_nodes if item != node]
        checkpoint.updated_at = utc_now()
        self._runtime_repository.save_checkpoint(checkpoint)

    def _reset_from_stage_a(self, checkpoint: WorkflowCheckpoint) -> None:
        downstream = {
            CodexD3Node.O3_TRIGGER_CALIBRATION,
            CodexD3Node.O3_POLICY_COMPILE,
            CodexD3Node.O3_FINAL_REVIEW,
            CodexD3Node.VALIDATE,
            CodexD3Node.ASSEMBLE,
            CodexD3Node.PUBLISH,
        }
        self._reset_nodes(checkpoint, downstream)

    def _reset_from_compile(self, checkpoint: WorkflowCheckpoint) -> None:
        self._reset_nodes(
            checkpoint,
            {
                CodexD3Node.O3_POLICY_COMPILE,
                CodexD3Node.O3_FINAL_REVIEW,
                CodexD3Node.VALIDATE,
                CodexD3Node.ASSEMBLE,
                CodexD3Node.PUBLISH,
            },
        )

    def _reset_final_review(self, checkpoint: WorkflowCheckpoint) -> None:
        self._reset_nodes(
            checkpoint,
            {
                CodexD3Node.O3_FINAL_REVIEW,
                CodexD3Node.VALIDATE,
                CodexD3Node.ASSEMBLE,
                CodexD3Node.PUBLISH,
            },
        )

    def _reset_nodes(
        self, checkpoint: WorkflowCheckpoint, nodes: set[CodexD3Node]
    ) -> None:
        checkpoint.completed_nodes = [
            item for item in checkpoint.completed_nodes if item not in nodes
        ]
        checkpoint.current_nodes = [
            item for item in checkpoint.current_nodes if item not in nodes
        ]
        checkpoint.failed_nodes = [
            item for item in checkpoint.failed_nodes if item not in nodes
        ]
        checkpoint.updated_at = utc_now()
        self._runtime_repository.save_checkpoint(checkpoint)

    async def _read_jsonl(self, run_id: str, path: str, model: type[ModelT]) -> list[ModelT]:
        response = await self._agent.workspace.read_text(run_id, path)
        return [
            model.model_validate_json(line)
            for line in (response.content or "").splitlines()
            if line.strip()
        ]

    async def _read_json(self, run_id: str, path: str, model: type[ModelT]) -> ModelT:
        response = await self._agent.workspace.read_text(run_id, path)
        if response.content is None:
            raise ValueError(f"workspace file has no text content: {path}")
        return model.model_validate_json(response.content)

    async def _read_policy_drafts(self, run_id: str) -> list[Policy]:
        inventory = await self._agent.workspace.inventory(run_id)
        paths = sorted(
            item.relative_path
            for item in inventory.files
            if item.relative_path.startswith("output/work/policies/")
            and item.relative_path.endswith(".json")
        )
        return [await self._read_json(run_id, path, Policy) for path in paths]

    async def _assert_agent_write_boundary(
        self, run_id: str, *, initialize: bool = False
    ) -> None:
        inventory = await self._agent.workspace.inventory(run_id)
        allowed_initialize_context = {
            *INITIALIZE_BUSINESS_INPUT_PATHS,
            INPUT_MANIFEST_PATH,
            "context/document3/AGENTS.md",
            "context/document3/agent.md",
            "context/document3/foundation.md",
            "context/document3/initialize_trigger_calibration.md",
            "context/document3/initialize_policy_compile.md",
            "context/document3/initialize_final_review.md",
            "context/document3/policy_set.schema.json",
            "context/document3/trigger_calibration_record.schema.json",
            "context/document3/trigger_calibration_state.schema.json",
            f"context/document3/{CodexD3Node.O3_TRIGGER_CALIBRATION.value}.output_schema.json",
            f"context/document3/{CodexD3Node.O3_POLICY_COMPILE.value}.output_schema.json",
            f"context/document3/{CodexD3Node.O3_FINAL_REVIEW.value}.output_schema.json",
        }
        deterministic_release_paths = {
            "output/final/document3.json",
            "output/final/coverage_map.json",
            "output/final/runtime_projection.json",
            "output/final/document3.md",
            "artifacts/document3/document3.json",
            "artifacts/document3/coverage_map.json",
            "artifacts/document3/runtime_projection.json",
            "artifacts/document3/document3.md",
        }
        unauthorized = [
            item.relative_path
            for item in inventory.files
            if not item.relative_path.startswith("output/work/")
            # Worker-owned attempt telemetry and job snapshots live beside the
            # agent workspace; they are not D3 business outputs.
            and not item.relative_path.startswith("attempts/")
            and not item.relative_path.startswith("audit/jobs/")
            # Data MCP writes an immutable, attempt-scoped tool catalog before
            # the SDK turn starts. It is runtime-owned context, not an agent
            # business output, but it must remain visible to boundary checks.
            and not item.relative_path.startswith("context/data_tool_catalog/")
            and item.relative_path not in deterministic_release_paths
            and not item.relative_path.startswith("published/")
            and not (
                initialize and item.relative_path in allowed_initialize_context
            )
            and not (
                not initialize
                and item.relative_path.startswith("context/document3/")
            )
        ]
        if unauthorized:
            raise ValueError(f"D3 agent wrote outside its boundary: {unauthorized[:10]}")

    @staticmethod
    def _reference_view_has_content(value: str) -> bool:
        content = re.sub(r"[#|\-\s]", "", value)
        return len(content) >= 20

    async def _publish_policy_set(
        self,
        *,
        run_id: str,
        policy_set: PolicySet,
        coverage: CoverageMap,
        expected_base_version: int | None,
    ) -> Document3Handoff:
        projection = project_policy_set(policy_set)
        payloads = {
            "output/final/document3.json": policy_set.model_dump_json(indent=2) + "\n",
            "output/final/coverage_map.json": coverage.model_dump_json(indent=2) + "\n",
            "output/final/runtime_projection.json": projection.model_dump_json(indent=2) + "\n",
            "output/final/document3.md": self._render_markdown(policy_set),
        }
        for path, content in payloads.items():
            await self._agent.workspace.write_text(run_id, path, content)
        release_payloads = {
            "artifacts/document3/document3.json": payloads["output/final/document3.json"],
            "artifacts/document3/coverage_map.json": payloads["output/final/coverage_map.json"],
            "artifacts/document3/runtime_projection.json": payloads[
                "output/final/runtime_projection.json"
            ],
            "artifacts/document3/document3.md": payloads["output/final/document3.md"],
        }
        metadata = {
            path: await self._agent.workspace.write_text(run_id, path, content)
            for path, content in release_payloads.items()
        }
        await self._agent.workspace.publish(run_id, list(release_payloads))
        artifacts: dict[str, ArtifactRef] = {}
        kinds = {
            "artifacts/document3/document3.json": ArtifactKind.BUNDLE,
            "artifacts/document3/coverage_map.json": ArtifactKind.REPORT,
            "artifacts/document3/runtime_projection.json": ArtifactKind.REPORT,
        }
        for path, kind in kinds.items():
            item = metadata[path]
            artifact = ArtifactRef(
                workflow_version=CODEX_DOCUMENT3_WORKFLOW_VERSION,
                research_lane=ResearchLane.DOCUMENT3,
                artifact_id=uuid4().hex,
                run_id=run_id,
                node=CodexD3Node.PUBLISH,
                attempt_id="d3-publish-01",
                kind=kind,
                relative_path=path,
                sha256=item.sha256,
                size_bytes=item.size_bytes,
                content_type="application/json",
                published=True,
            )
            self._runtime_repository.save_artifact(artifact)
            await self._save_published_content(artifact, release_payloads[path])
            artifacts[path] = artifact

        self._policy_repository.publish(policy_set, expected_base_version=expected_base_version)
        return Document3Handoff(
            ticker=policy_set.ticker,
            run_id=run_id,
            policy_set_version=policy_set.policy_set_version,
            publication_state=policy_set.publication_state,
            published_artifact=artifacts["artifacts/document3/document3.json"],
            coverage_artifact=artifacts["artifacts/document3/coverage_map.json"],
            runtime_projection_artifact=artifacts["artifacts/document3/runtime_projection.json"],
            published_at=policy_set.published_at,
        )

    async def _save_published_content(self, artifact: ArtifactRef, content: str) -> None:
        encoded = content.encode("utf-8")
        artifact_kind: Literal["report", "bundle", "manifest"] = (
            "bundle" if artifact.kind is ArtifactKind.BUNDLE else "report"
        )
        if len(encoded) <= 2 * 1024 * 1024:
            document = PublishedDocument(
                artifact_id=artifact.artifact_id,
                run_id=artifact.run_id,
                artifact_kind=artifact_kind,
                sha256=artifact.sha256,
                size_bytes=len(encoded),
                content_type=artifact.content_type,
                content_text=content,
                published_at=artifact.created_at,
            )
        else:
            if self._published_storage is None:
                raise ValueError("D3 published artifact exceeds inline limit without storage")
            storage_path = (
                f"codex/document3/{artifact.run_id}/{artifact.artifact_id}/"
                f"{artifact.relative_path.rsplit('/', 1)[-1]}"
            )
            await self._published_storage.put(storage_path, encoded, artifact.content_type)
            document = PublishedDocument(
                artifact_id=artifact.artifact_id,
                run_id=artifact.run_id,
                artifact_kind=artifact_kind,
                sha256=artifact.sha256,
                size_bytes=len(encoded),
                content_type=artifact.content_type,
                storage_path=storage_path,
                published_at=artifact.created_at,
            )
        self._runtime_repository.save_published_document(document)

    @staticmethod
    def _render_markdown(policy_set: PolicySet) -> str:
        lines = [
            f"# {policy_set.ticker} Monitoring Execution Policies V{policy_set.policy_set_version}",
            "",
            f"Publication state: {policy_set.publication_state.value}",
            "",
        ]
        for policy in policy_set.policies:
            lines.extend(
                [
                    f"## {policy.title} ({policy.policy_id})",
                    "",
                    f"- Direction: {policy.decision.value}",
                    f"- Match scope: {policy.match_scope}",
                    f"- Activation: {policy.activation_summary}",
                    "",
                ]
            )
            for condition in policy.activation_conditions:
                lines.append(f"- {condition.condition_id}: {condition.criterion}")
            lines.append("")
        return "\n".join(lines)
