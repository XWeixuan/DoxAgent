"""Real Codex Worker orchestration for initialization and incremental maintenance."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypedDict

from doxagent.codex_runtime.client import CodexWorkerClient, WorkspaceClient
from doxagent.codex_runtime.errors import StructuredOutputInvalid
from doxagent.codex_runtime.schema import (
    CODEX_EVENT_LIBRARY_WORKFLOW_VERSION,
    CodexEventLibraryAgentRole,
    CodexEventLibraryNode,
    ResearchLane,
)
from doxagent.codex_worker.schema import WorkerRunRequest
from doxagent.event_library.bundle_io import RevisionBundleIO, TolerantBundleLoadResult
from doxagent.event_library.contracts import (
    REFERENCE_REVIEW_POLICY_VERSION,
    CandidateMap,
    DateResolutionLedgerEntry,
    DeltaBatch,
    FrozenRuntimeSnapshot,
    FrozenViewManifest,
    PublicationResult,
    ReferenceReviewCandidate,
    ReferenceViewDecisionLedgerEntry,
    SurveyDeltaCatalog,
    WaveIndex,
)
from doxagent.event_library.reference_review import classify_review, occurrence_anchor
from doxagent.event_library.service import EventLibraryService
from doxagent.event_library.validator import (
    BundleValidationContext,
    BundleValidationOutcome,
    ValidationIssue,
    ValidationSeverity,
)
from doxagent.ticker_initialization.substeps import DurableWorker, durable
from doxagent.workflows.codex_event_library.context import build_attempt_assets
from doxagent.workflows.codex_event_library.schema import (
    O2_RUN_RESULT_SCHEMA,
    EventLibraryRunStage,
    O2RunResult,
)


class _O2Phase(TypedDict):
    attempt_id: str
    stage: EventLibraryRunStage
    skill_asset: str
    delta_ids: list[str]
    prior_attempt_paths: list[str]


class RemoteEventLibraryInitializer:
    """Run SURVEY, deterministic waves, reconciliation, validation, and V1 publish."""

    def __init__(
        self,
        *,
        worker: CodexWorkerClient,
        workspace: WorkspaceClient,
        service: EventLibraryService,
        local_workspace_root: str | Path,
        prompt_root: str | Path = "prompts/codex_v2/event_library",
        model: str = "gpt-5.6-luna",
        model_provider: str | None = None,
        effort: Literal["low", "medium", "high", "xhigh", "max"] = "max",
        timeout_seconds: int = 3600,
        wave_size: int = 100,
        wave_token_budget: int = 18_000,
        max_repairs: int = 2,
    ) -> None:
        if wave_size < 1:
            raise ValueError("wave_size must be positive")
        self.worker = DurableWorker(worker)
        self.workspace = workspace
        self.service = service
        self.local_workspace_root = Path(local_workspace_root).resolve()
        self.prompt_root = Path(prompt_root)
        self.model = model
        self.model_provider = model_provider
        self.effort = effort
        self.timeout_seconds = timeout_seconds
        self.wave_size = wave_size
        self.wave_token_budget = wave_token_budget
        self.max_repairs = max_repairs

    async def run(
        self,
        *,
        snapshot: FrozenRuntimeSnapshot,
        run_id: str,
        cutoff_at: datetime,
        export_dir: str | Path,
        mode: Literal["INITIALIZE", "INCREMENTAL"] = "INITIALIZE",
        upstream_context_manifest: dict[str, Any] | None = None,
    ) -> tuple[
        DeltaBatch,
        PublicationResult | None,
        BundleValidationOutcome | None,
        dict[str, Path],
    ]:
        batch = self.service.delta_compiler.compile(snapshot)
        review_candidates = self.service.repository.due_reference_review_candidates(
            ticker=snapshot.ticker, as_of=snapshot.as_of
        )
        if not batch.items and not review_candidates:
            return batch, None, None, {}
        d1_reports = await self._load_and_verify_d1_reports(upstream_context_manifest)
        local_run_root = self.local_workspace_root / run_id
        local_run_root.mkdir(parents=True, exist_ok=True)
        frozen_root, manifest = self.service.views.materialize_frozen_view(
            run_root=local_run_root,
            run_id=run_id,
            mode=mode,
            batches=[batch],
            as_of=snapshot.as_of,
            reference_review_candidates=review_candidates,
            upstream_context_manifest=upstream_context_manifest,
            upstream_d1_reports=d1_reports,
        )
        # Event Details are capability-scoped per attempt and therefore are not
        # uploaded as a globally readable Frozen View subtree.
        await self._upload_tree(
            run_id,
            frozen_root,
            frozen_root.relative_to(local_run_root),
            exclude_relative_prefixes=("events/",),
        )
        existing = self.service.repository.get_maintenance_run(run_id)
        completed = []
        thread_id = None
        failed_attempt_id: str | None = None
        if existing is not None:
            if (
                str(existing["frozen_view_id"]) != manifest.frozen_view_id
                or int(existing["base_version"]) != manifest.base_library_version
            ):
                metadata = dict(existing.get("metadata_json") or {})
                can_reseed_before_model_content = (
                    not existing.get("thread_id")
                    and not existing.get("bundle_path")
                    and not metadata.get("completed_attempt_ids")
                )
                if not can_reseed_before_model_content:
                    raise ValueError("resume state does not match the frozen maintenance input")
            metadata = dict(existing.get("metadata_json") or {})
            completed = [str(item) for item in metadata.get("completed_attempt_ids", [])]
            failed_attempt_id = (
                str(metadata["failed_attempt_id"]) if metadata.get("failed_attempt_id") else None
            )
            thread_id = str(existing["thread_id"]) if existing.get("thread_id") else None
            promoted = existing.get("bundle_path")
            if promoted and existing.get("validator_status") in {"PASS", "PARTIAL"}:
                bundle_dir = await self._download_tree(
                    run_id=run_id,
                    remote_prefix=str(promoted),
                    local_root=local_run_root / "resume" / "promoted_bundle",
                )
                return await self._publish(
                    run_id=run_id,
                    bundle_dir=bundle_dir,
                    batch=batch,
                    manifest=manifest,
                    export_dir=export_dir,
                    thread_id=thread_id,
                    completed=completed,
                )
        self._save_run(
            run_id=run_id,
            manifest=manifest,
            stage=(
                EventLibraryRunStage.PREPARE
                if mode == "INITIALIZE"
                else EventLibraryRunStage.PREPARE_DELTA
            ),
            thread_id=thread_id,
            completed=completed,
            wave_count=len(self._plan_waves(batch)),
        )

        phases = _resolve_phase_attempts(
            self._phases(
                batch,
                mode=mode,
                review_only=not batch.items and bool(review_candidates),
            ),
            completed=completed,
            failed_attempt_id=(
                failed_attempt_id
                or _infer_unfinished_attempt(
                    [item.relative_path for item in (await self.workspace.inventory(run_id)).files],
                    completed=completed,
                )
            ),
        )
        completed_phase_ids = {_base_attempt_id(item) for item in completed}
        latest_bundle_prefix: str | None = None
        for phase in phases:
            attempt_id = phase["attempt_id"]
            if _base_attempt_id(attempt_id) in completed_phase_ids:
                continue
            if mode == "INCREMENTAL":
                pre_stage = {
                    EventLibraryRunStage.BUILD_CANDIDATE_MAP: (
                        EventLibraryRunStage.READ_FULL_INDEX
                    ),
                    EventLibraryRunStage.RECONSTRUCT_AND_EDIT: (
                        EventLibraryRunStage.LOAD_EVENT_DETAILS
                    ),
                }.get(phase["stage"])
                if pre_stage is not None:
                    self._save_run(
                        run_id=run_id,
                        manifest=manifest,
                        stage=pre_stage,
                        thread_id=thread_id,
                        completed=completed,
                        wave_count=len(self._plan_waves(batch)),
                    )
            candidate_detail_ids = await self._candidate_detail_ids(
                run_id=run_id,
                phase=phase,
                manifest=manifest,
            )
            review_ids = (
                [item.event_id for item in review_candidates]
                if phase["stage"] is EventLibraryRunStage.REFERENCE_REVIEW
                else []
            )
            allowed_detail_ids = sorted(set(candidate_detail_ids) | set(review_ids))
            expected_final = phase is phases[-1]
            try:
                result, thread_id = await self._execute_phase(
                    run_id=run_id,
                    phase=phase,
                    manifest=manifest,
                    cutoff_at=cutoff_at,
                    thread_id=thread_id,
                    mode=mode,
                    frozen_root=frozen_root,
                    allowed_detail_ids=allowed_detail_ids,
                    review_candidates=review_candidates,
                    expected_final=expected_final,
                )
            except Exception as exc:
                self._save_run(
                    run_id=run_id,
                    manifest=manifest,
                    stage=EventLibraryRunStage.FAILED,
                    thread_id=thread_id,
                    completed=completed,
                    wave_count=len(self._plan_waves(batch)),
                    metadata={
                        "failed_attempt_id": attempt_id,
                        "error_code": "INVALID_O2_RUN_RESULT",
                        "error_message": str(exc),
                    },
                )
                raise StructuredOutputInvalid(f"invalid O2 run result: {exc}") from exc
            completed.append(attempt_id)
            completed_phase_ids.add(_base_attempt_id(attempt_id))
            if result.bundle_path:
                latest_bundle_prefix = result.bundle_path.rstrip("/")
            self._save_run(
                run_id=run_id,
                manifest=manifest,
                stage=phase["stage"],
                thread_id=thread_id,
                completed=completed,
                wave_count=len(self._plan_waves(batch)),
            )

        if latest_bundle_prefix is None:
            latest_bundle_prefix = f"attempts/{phases[-1]['attempt_id']}/output/revision_bundle"
        bundle_dir = await self._download_tree(
            run_id=run_id,
            remote_prefix=latest_bundle_prefix,
            local_root=local_run_root / "downloads" / "revision_bundle",
        )
        self._save_run(
            run_id=run_id,
            manifest=manifest,
            stage=EventLibraryRunStage.BUNDLE_VALIDATE,
            thread_id=thread_id,
            completed=completed,
            wave_count=len(self._plan_waves(batch)),
        )
        try:
            bundle_dir, outcome, thread_id = await self._validate_with_repairs(
                run_id=run_id,
                manifest=manifest,
                bundle_dir=bundle_dir,
                bundle_remote_prefix=latest_bundle_prefix,
                cutoff_at=cutoff_at,
                thread_id=thread_id,
                completed=completed,
                mode=mode,
                reported_result=result,
            )
        except Exception as exc:
            self._save_run(
                run_id=run_id,
                manifest=manifest,
                stage=EventLibraryRunStage.FAILED,
                thread_id=thread_id,
                completed=completed,
                wave_count=len(self._plan_waves(batch)),
                metadata={
                    "error_code": "BUNDLE_VALIDATION_FAILED",
                    "error_message": str(exc),
                },
            )
            raise
        digest = _directory_hash(bundle_dir)
        promoted_prefix = f"artifacts/event_library/revision_bundles/{digest[:24]}"
        await self._upload_tree(run_id, bundle_dir, Path(promoted_prefix))
        self._save_run(
            run_id=run_id,
            manifest=manifest,
            stage=EventLibraryRunStage.ARTIFACT_PROMOTED,
            thread_id=thread_id,
            completed=completed,
            wave_count=len(self._plan_waves(batch)),
            bundle_path=promoted_prefix,
            bundle_hash=digest,
            validator_status=outcome.status.value,
        )
        return await self._publish(
            run_id=run_id,
            bundle_dir=bundle_dir,
            batch=batch,
            manifest=manifest,
            export_dir=export_dir,
            thread_id=thread_id,
            completed=completed,
            existing_outcome=outcome,
        )

    def _phases(
        self,
        batch: DeltaBatch,
        *,
        mode: Literal["INITIALIZE", "INCREMENTAL"] = "INITIALIZE",
        review_only: bool = False,
    ) -> list[_O2Phase]:
        if review_only:
            return [
                {
                    "attempt_id": "o2-reference-review",
                    "stage": EventLibraryRunStage.REFERENCE_REVIEW,
                    "skill_asset": "skills/incremental-reference-review.md",
                    "delta_ids": [],
                    "prior_attempt_paths": [],
                }
            ]
        if mode == "INCREMENTAL":
            delta_ids = [item.delta_id for item in batch.items]
            return [
                {
                    "attempt_id": "o2-known-index-map",
                    "stage": EventLibraryRunStage.BUILD_CANDIDATE_MAP,
                    "skill_asset": "skills/incremental-index-map.md",
                    "delta_ids": delta_ids,
                    "prior_attempt_paths": [],
                },
                {
                    "attempt_id": "o2-incremental-edit",
                    "stage": EventLibraryRunStage.RECONSTRUCT_AND_EDIT,
                    "skill_asset": "skills/incremental-edit.md",
                    "delta_ids": delta_ids,
                    "prior_attempt_paths": ["attempts/o2-known-index-map/output/work"],
                },
                {
                    "attempt_id": "o2-reference-review",
                    "stage": EventLibraryRunStage.REFERENCE_REVIEW,
                    "skill_asset": "skills/incremental-reference-review.md",
                    "delta_ids": delta_ids,
                    "prior_attempt_paths": [
                        "attempts/o2-known-index-map/output/work",
                        "attempts/o2-incremental-edit/output/work",
                    ],
                },
            ]
        phases: list[_O2Phase] = [
            {
                "attempt_id": "o2-survey",
                "stage": EventLibraryRunStage.SURVEY,
                "skill_asset": "skills/initialize-survey.md",
                "delta_ids": [item.delta_id for item in batch.items],
                "prior_attempt_paths": [],
            }
        ]
        prior = ["attempts/o2-survey/output/work"]
        for index, wave in enumerate(self._plan_waves(batch), start=1):
            attempt_id = f"o2-wave-{index:03d}"
            phases.append(
                {
                    "attempt_id": attempt_id,
                    "stage": EventLibraryRunStage.LOCAL_RECONSTRUCTION,
                    "skill_asset": "skills/initialize-wave.md",
                    "delta_ids": [item.delta_id for item in wave],
                    "prior_attempt_paths": list(prior),
                }
            )
            prior.append(f"attempts/{attempt_id}/output/work")
        phases.append(
            {
                "attempt_id": "o2-global-reconciliation",
                "stage": EventLibraryRunStage.GLOBAL_RECONCILIATION,
                "skill_asset": "skills/initialize-reconcile.md",
                "delta_ids": [item.delta_id for item in batch.items],
                "prior_attempt_paths": prior,
            }
        )
        return phases

    def _plan_waves(self, batch: DeltaBatch) -> list[list[Any]]:
        """Assign every Atomic Delta once, keeping its primary Package together."""

        items_by_id = {item.delta_id: item for item in batch.items}
        package_members: dict[str, list[Any]] = {}
        if batch.runtime_packages:
            for package in sorted(batch.runtime_packages, key=lambda item: item.runtime_hint_id):
                package_members[package.runtime_hint_id] = [
                    items_by_id[item] for item in package.member_delta_ids if item in items_by_id
                ]
        else:
            # Legacy v1 batches are upgraded in-memory without mutating their immutable DB row.
            for hint in sorted(batch.runtime_hints, key=lambda item: item.runtime_hint_id):
                package_members[hint.runtime_hint_id] = [
                    item for item in batch.items if hint.runtime_hint_id in item.runtime_hint_ids
                ]
        assigned: set[str] = set()
        groups: list[list[Any]] = []
        for _hint_id, members in package_members.items():
            primary = [item for item in members if item.delta_id not in assigned]
            if primary:
                groups.append(primary)
                assigned.update(item.delta_id for item in primary)
        ungrouped = [item for item in batch.items if item.delta_id not in assigned]
        if ungrouped:
            groups.append(ungrouped)
        chunks: list[list[Any]] = []
        for group in groups:
            ordered = sorted(
                group,
                key=lambda item: (item.time, tuple(item.entities), int(item.delta_id[1:])),
            )
            current: list[Any] = []
            estimated_tokens = 0
            for item in ordered:
                item_tokens = max(1, len(item.model_dump_json()) // 4)
                if current and (
                    len(current) >= self.wave_size
                    or estimated_tokens + item_tokens > self.wave_token_budget
                ):
                    chunks.append(current)
                    current = []
                    estimated_tokens = 0
                current.append(item)
                estimated_tokens += item_tokens
            if current:
                chunks.append(current)
        waves: list[list[Any]] = []
        for chunk in chunks:
            chunk_tokens = sum(max(1, len(item.model_dump_json()) // 4) for item in chunk)
            if waves:
                last_tokens = sum(max(1, len(item.model_dump_json()) // 4) for item in waves[-1])
                if (
                    len(waves[-1]) + len(chunk) <= self.wave_size
                    and last_tokens + chunk_tokens <= self.wave_token_budget
                ):
                    waves[-1].extend(chunk)
                    continue
            waves.append(list(chunk))
        return waves

    @durable("o2")
    async def _execute_phase(
        self,
        *,
        run_id: str,
        phase: _O2Phase,
        manifest: FrozenViewManifest,
        cutoff_at: datetime,
        thread_id: str | None,
        mode: Literal["INITIALIZE", "INCREMENTAL"],
        frozen_root: Path,
        allowed_detail_ids: list[str],
        review_candidates: list[ReferenceReviewCandidate],
        expected_final: bool,
    ) -> tuple[O2RunResult, str | None]:
        await self._seed_attempt(
            run_id=run_id,
            manifest=manifest,
            attempt_id=phase["attempt_id"],
            stage=phase["stage"],
            skill_asset=phase["skill_asset"],
            assigned_delta_ids=phase["delta_ids"],
            prior_attempt_paths=phase["prior_attempt_paths"],
            mode=mode,
            frozen_root=frozen_root,
            allowed_event_detail_ids=allowed_detail_ids,
            review_candidates=review_candidates,
        )
        request = self._worker_request(
            run_id=run_id,
            ticker=manifest.ticker,
            attempt_id=phase["attempt_id"],
            cutoff_at=cutoff_at,
            thread_id=thread_id,
        )
        job = await self.worker.run(request)
        if job.status != "succeeded" or not job.final_response:
            raise StructuredOutputInvalid(job.error_message or "O2 worker failed")
        result = O2RunResult.model_validate_json(job.final_response)
        self._validate_phase_result(
            result=result,
            phase=phase,
            manifest=manifest,
            expected_final=expected_final,
        )
        await self._validate_phase_artifacts(run_id=run_id, phase=phase)
        return result, job.thread_id or thread_id

    async def _seed_attempt(
        self,
        *,
        run_id: str,
        manifest: FrozenViewManifest,
        attempt_id: str,
        stage: EventLibraryRunStage,
        skill_asset: str,
        assigned_delta_ids: list[str],
        prior_attempt_paths: list[str],
        previous_failure: str | None = None,
        mode: Literal["INITIALIZE", "INCREMENTAL"] = "INITIALIZE",
        frozen_root: Path | None = None,
        allowed_event_detail_ids: list[str] | None = None,
        review_candidates: list[ReferenceReviewCandidate] | None = None,
    ) -> None:
        prefix = f"attempts/{attempt_id}/input"
        allowed = sorted(set(allowed_event_detail_ids or []))
        detail_paths: dict[str, str] = {}
        if allowed:
            if frozen_root is None:
                raise ValueError("allowed Event Details require a local Frozen View")
            for event_id in allowed:
                source = frozen_root / "events" / f"{event_id}.json"
                if not source.is_file():
                    raise ValueError(f"allowed Event Detail does not exist: {event_id}")
                relative = f"{prefix}/event_details/{event_id}.json"
                detail_paths[event_id] = relative
                await self._write_immutable(run_id, relative, source.read_text(encoding="utf-8"))
        task_metadata = {
            "required_frozen_paths": self._required_frozen_paths(
                manifest=manifest,
                stage=stage,
                prior_attempt_paths=prior_attempt_paths,
            ),
            "allowed_event_detail_ids": allowed,
            "event_detail_paths": detail_paths,
            "known_index_scope": "FULL" if mode == "INCREMENTAL" else None,
            "reference_review_policy": (
                self._reference_review_policy()
                if stage is EventLibraryRunStage.REFERENCE_REVIEW
                else None
            ),
            "deterministic_review_fields_by_event": (
                self._deterministic_review_fields(
                    manifest=manifest,
                    candidates=review_candidates or [],
                )
                if stage is EventLibraryRunStage.REFERENCE_REVIEW
                else {}
            ),
            "review_only_bundle_requires_empty_delta_batch_ids": (
                stage is EventLibraryRunStage.REFERENCE_REVIEW and not assigned_delta_ids
            ),
        }
        if stage is EventLibraryRunStage.REFERENCE_REVIEW and not assigned_delta_ids:
            task_metadata["required_bundle_identity"] = {
                "run_id": manifest.run_id,
                "ticker": manifest.ticker,
                "base_library_version": manifest.base_library_version,
                "delta_batch_ids": [],
            }
        assets = build_attempt_assets(
            prompt_root=self.prompt_root,
            manifest=manifest,
            attempt_id=attempt_id,
            stage=stage.value,
            skill_asset=skill_asset,
            assigned_delta_ids=assigned_delta_ids,
            prior_attempt_paths=prior_attempt_paths,
            previous_failure=previous_failure,
            task_metadata=task_metadata,
        )
        # Remote Worker uses the provider-strict schema variant.
        assets["output_schema.json"] = _json_text(O2_RUN_RESULT_SCHEMA)
        inventory = await self.workspace.inventory(run_id)
        existing = {item.relative_path: item for item in inventory.files}
        for name, content in assets.items():
            relative = f"{prefix}/{name}"
            item = existing.get(relative)
            if item is not None:
                current = await self.workspace.read_text(run_id, relative)
                if current.content != content:
                    raise ValueError(f"immutable O2 input mismatch on resume: {relative}")
                continue
            await self.workspace.write_text(run_id, relative, content)

    async def _write_immutable(self, run_id: str, relative: str, content: str) -> None:
        inventory = await self.workspace.inventory(run_id)
        existing = {item.relative_path for item in inventory.files}
        if relative in existing:
            current = await self.workspace.read_text(run_id, relative)
            if current.content != content:
                raise ValueError(f"immutable O2 input mismatch on resume: {relative}")
            return
        await self.workspace.write_text(run_id, relative, content)

    async def _candidate_detail_ids(
        self,
        *,
        run_id: str,
        phase: _O2Phase,
        manifest: FrozenViewManifest,
    ) -> list[str]:
        if phase["stage"] is EventLibraryRunStage.BUILD_CANDIDATE_MAP:
            return []
        candidate_paths = [
            path.rstrip("/") + "/candidate_map.json"
            for path in phase["prior_attempt_paths"]
            if "known-index-map" in path
        ]
        if not candidate_paths:
            return []
        response = await self.workspace.read_text(run_id, candidate_paths[0])
        if response.content is None:
            raise ValueError("Candidate Map has no readable content")
        candidate_map = CandidateMap.model_validate_json(response.content)
        assigned: list[str] = []
        detail_ids: set[str] = set()
        for entry in candidate_map.root.values():
            assigned.extend(entry.delta_ids)
            detail_ids.update(entry.detail_event_ids)
            if not set(entry.same_occurrence_event_ids + entry.related_event_ids).issubset(
                set(entry.detail_event_ids)
            ):
                raise ValueError("Candidate Map candidates must be included in detail_event_ids")
        if len(assigned) != len(set(assigned)):
            raise ValueError("Candidate Map accounts for a Delta more than once")
        known = {
            item.event_id
            for item in self.service.repository.published_events(
                manifest.ticker, manifest.base_library_version
            )
        }
        unknown = detail_ids - known
        if unknown:
            raise ValueError(f"Candidate Map requested unknown Event Details: {sorted(unknown)}")
        return sorted(detail_ids)

    async def _validate_phase_artifacts(self, *, run_id: str, phase: _O2Phase) -> None:
        expected = set(phase["delta_ids"])
        if phase["stage"] is EventLibraryRunStage.SURVEY:
            response = await self.workspace.read_text(
                run_id,
                f"attempts/{phase['attempt_id']}/output/work/delta_catalog.json",
            )
            catalog = SurveyDeltaCatalog.model_validate_json(response.content or "")
            if set(catalog.root) != expected:
                raise ValueError("Survey Delta catalog must account for every assigned D# once")
        elif phase["stage"] is EventLibraryRunStage.LOCAL_RECONSTRUCTION:
            response = await self.workspace.read_text(
                run_id,
                f"attempts/{phase['attempt_id']}/output/work/wave_index.json",
            )
            index = WaveIndex.model_validate_json(response.content or "")
            assigned = [item for entry in index.entries for item in entry.assigned_delta_ids]
            if len(assigned) != len(set(assigned)) or set(assigned) != expected:
                raise ValueError("Wave index must account for every assigned D# once")
        elif phase["stage"] is EventLibraryRunStage.BUILD_CANDIDATE_MAP:
            response = await self.workspace.read_text(
                run_id,
                f"attempts/{phase['attempt_id']}/output/work/candidate_map.json",
            )
            candidate_map = CandidateMap.model_validate_json(response.content or "")
            assigned = [item for entry in candidate_map.root.values() for item in entry.delta_ids]
            if len(assigned) != len(set(assigned)) or set(assigned) != expected:
                raise ValueError("Candidate Map must account for every assigned D# once")
        elif phase["stage"] in {
            EventLibraryRunStage.GLOBAL_RECONCILIATION,
            EventLibraryRunStage.REFERENCE_REVIEW,
        }:
            attempt_root = f"attempts/{phase['attempt_id']}/output"
            for filename, model in (
                ("date_resolution_ledger.jsonl", DateResolutionLedgerEntry),
                (
                    "reference_view_decision_ledger.jsonl",
                    ReferenceViewDecisionLedgerEntry,
                ),
            ):
                work = await self.workspace.read_text(run_id, f"{attempt_root}/work/{filename}")
                bundled = await self.workspace.read_text(
                    run_id, f"{attempt_root}/revision_bundle/{filename}"
                )
                if work.content is None or bundled.content is None:
                    raise ValueError(f"Final O2 phase must write synchronized {filename}")
                work_rows = [
                    model.model_validate_json(line).model_dump(mode="json")
                    for line in work.content.splitlines()
                    if line.strip()
                ]
                bundle_rows = [
                    model.model_validate_json(line).model_dump(mode="json")
                    for line in bundled.content.splitlines()
                    if line.strip()
                ]
                if work_rows != bundle_rows:
                    raise ValueError(f"work and Revision Bundle {filename} differ")

    @staticmethod
    def _required_frozen_paths(
        *,
        manifest: FrozenViewManifest,
        stage: EventLibraryRunStage,
        prior_attempt_paths: list[str],
    ) -> list[str]:
        prefix = f"context/event_library/{manifest.frozen_view_id}/"
        common = [
            prefix + "manifest.json",
            prefix + manifest.pending_atomics_path,
            prefix + manifest.runtime_packages_path,
            prefix + manifest.package_index_path,
            prefix + "schemas/schema_index.json",
        ]
        if stage in {
            EventLibraryRunStage.BUILD_CANDIDATE_MAP,
            EventLibraryRunStage.RECONSTRUCT_AND_EDIT,
            EventLibraryRunStage.REFERENCE_REVIEW,
        }:
            common.append(prefix + manifest.known_event_index_path)
        if stage is EventLibraryRunStage.REFERENCE_REVIEW:
            common.append(prefix + manifest.reference_review_candidates_path)
        schema_by_stage = {
            EventLibraryRunStage.SURVEY: [
                "schemas/survey_delta_catalog.schema.json",
            ],
            EventLibraryRunStage.LOCAL_RECONSTRUCTION: [
                "schemas/canonical_event_revision.schema.json",
                "schemas/wave_index.schema.json",
            ],
            EventLibraryRunStage.GLOBAL_RECONCILIATION: [
                "schemas/canonical_event_revision.schema.json",
                "schemas/revision_bundle_manifest.schema.json",
                "schemas/event_retirement.schema.json",
                "schemas/residual_delta_resolution.schema.json",
                "schemas/date_resolution_ledger.schema.json",
                "schemas/reference_view_decision_ledger.schema.json",
                "examples/bundle/manifest.json",
                "examples/bundle/events/T1.json",
                "examples/bundle/events/E7.json",
                "examples/bundle/retirements.json",
                "examples/bundle/residual_delta_resolutions.jsonl",
                "examples/bundle/reference_review_decisions.jsonl",
                "examples/bundle/date_resolution_ledger.jsonl",
                "examples/bundle/reference_view_decision_ledger.jsonl",
            ],
            EventLibraryRunStage.BUILD_CANDIDATE_MAP: [
                "schemas/candidate_map.schema.json",
            ],
            EventLibraryRunStage.RECONSTRUCT_AND_EDIT: [
                "schemas/canonical_event_revision.schema.json",
                "schemas/event_retirement.schema.json",
                "schemas/residual_delta_resolution.schema.json",
                "schemas/date_resolution_ledger.schema.json",
                "schemas/reference_view_decision_ledger.schema.json",
            ],
            EventLibraryRunStage.REFERENCE_REVIEW: [
                "schemas/canonical_event_revision.schema.json",
                "schemas/revision_bundle_manifest.schema.json",
                "schemas/reference_review_decision.schema.json",
                "schemas/date_resolution_ledger.schema.json",
                "schemas/reference_view_decision_ledger.schema.json",
            ],
            EventLibraryRunStage.BUNDLE_VALIDATE: [
                "schemas/canonical_event_revision.schema.json",
                "schemas/revision_bundle_manifest.schema.json",
                "schemas/event_retirement.schema.json",
                "schemas/residual_delta_resolution.schema.json",
                "schemas/reference_review_decision.schema.json",
                "schemas/date_resolution_ledger.schema.json",
                "schemas/reference_view_decision_ledger.schema.json",
                "examples/bundle/manifest.json",
                "examples/bundle/events/T1.json",
                "examples/bundle/events/E7.json",
                "examples/bundle/retirements.json",
                "examples/bundle/residual_delta_resolutions.jsonl",
                "examples/bundle/reference_review_decisions.jsonl",
                "examples/bundle/date_resolution_ledger.jsonl",
                "examples/bundle/reference_view_decision_ledger.jsonl",
            ],
        }
        common.extend(prefix + path for path in schema_by_stage.get(stage, []))
        if manifest.upstream_context_manifest_path:
            common.append(prefix + manifest.upstream_context_manifest_path)
        if manifest.upstream_d1_artifact_manifest_path:
            common.append(prefix + manifest.upstream_d1_artifact_manifest_path)
            common.extend(prefix + path for path in manifest.upstream_d1_report_paths.values())
        return [*common, *prior_attempt_paths]

    @staticmethod
    def _reference_review_policy() -> dict[str, Any]:
        return {
            "version": REFERENCE_REVIEW_POLICY_VERSION,
            "periodic_review_days": 10,
            "explicit_review_age_days": 30,
            "included_recheck_days": 7,
            "clock_owner": "DETERMINISTIC_PROGRAM",
            "model_owned_fields": [
                "is_important",
                "include_in_reference_view",
                "reference_view_basis",
                "note",
            ],
        }

    @staticmethod
    def _deterministic_review_fields(
        *,
        manifest: FrozenViewManifest,
        candidates: list[ReferenceReviewCandidate],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for candidate in candidates:
            anchor = candidate.occurrence_anchor or occurrence_anchor(candidate.occurred_at)
            included_mode, included_reason, included_next = classify_review(
                anchor=anchor,
                as_of=manifest.as_of,
                include_in_reference_view=True,
                last_reviewed_at=candidate.last_reviewed_at,
            )
            excluded_mode, excluded_reason, excluded_next = classify_review(
                anchor=anchor,
                as_of=manifest.as_of,
                include_in_reference_view=False,
                last_reviewed_at=candidate.last_reviewed_at,
            )
            result[candidate.event_id] = {
                "reviewed_at": manifest.as_of.isoformat(),
                "prior_is_important": candidate.is_important,
                "prior_include_in_reference_view": candidate.include_in_reference_view,
                "changed_rule": "new flags differ from either prior flag",
                "outcome_by_include_in_reference_view": {
                    "true": {
                        "review_mode": included_mode.value,
                        "candidate_reason": included_reason.value,
                        "next_review_at": (
                            None if included_next is None else included_next.isoformat()
                        ),
                    },
                    "false": {
                        "review_mode": excluded_mode.value,
                        "candidate_reason": excluded_reason.value,
                        "next_review_at": (
                            None if excluded_next is None else excluded_next.isoformat()
                        ),
                    },
                },
            }
        return result

    @staticmethod
    def _validate_phase_result(
        *,
        result: O2RunResult,
        phase: _O2Phase,
        manifest: FrozenViewManifest,
        expected_final: bool,
    ) -> None:
        if result.stage is not phase["stage"]:
            raise ValueError(f"stage must be {phase['stage'].value}")
        if result.base_library_version != manifest.base_library_version:
            raise ValueError("base_library_version does not match Frozen View")
        if result.validation != "NOT_RUN":
            raise ValueError("model stage validation must be NOT_RUN")
        expected_total = len(phase["delta_ids"])
        if result.delta_coverage.total != expected_total:
            raise ValueError(f"delta coverage total must be {expected_total}")
        expected_path = f"attempts/{phase['attempt_id']}/output/revision_bundle"
        if expected_final:
            if result.status != "BUNDLE_READY":
                raise ValueError("final O2 phase must return BUNDLE_READY")
            if (result.bundle_path or "").rstrip("/") != expected_path:
                raise ValueError("final Bundle path must be the current attempt output")
        else:
            if result.status != "PENDING":
                raise ValueError("intermediate O2 phase must return PENDING")
            if result.bundle_path is not None:
                raise ValueError("intermediate O2 phase must not return a Bundle path")

    async def _load_and_verify_d1_reports(
        self, manifest: dict[str, Any] | None
    ) -> dict[str, str] | None:
        if manifest is None:
            return None
        d1_run_id = str(manifest.get("d1_run_id") or "")
        published_at = manifest.get("d1_published_at")
        refs = manifest.get("research_artifacts")
        if not d1_run_id or not published_at or not isinstance(refs, dict):
            raise ValueError("O2 upstream context is missing Published D1 identity")
        reports: dict[str, str] = {}
        for role in ("c1", "c3", "c5"):
            ref = refs.get(role)
            if not isinstance(ref, dict):
                raise ValueError(f"O2 upstream context is missing D1 {role.upper()}")
            if ref.get("run_id") != d1_run_id or not ref.get("published"):
                raise ValueError(
                    f"D1 {role.upper()} ArtifactRef is not Published for the declared run"
                )
            relative = str(ref.get("relative_path") or "")
            response = await self.workspace.read_text(d1_run_id, relative)
            if response.content is None:
                raise ValueError(f"D1 {role.upper()} report body is unreadable")
            encoded = response.content.encode("utf-8")
            if len(encoded) != int(ref.get("size_bytes", -1)):
                raise ValueError(f"D1 {role.upper()} report size does not match ArtifactRef")
            if hashlib.sha256(encoded).hexdigest() != str(ref.get("sha256")):
                raise ValueError(f"D1 {role.upper()} report hash does not match ArtifactRef")
            reports[role] = response.content
        return reports

    def _worker_request(
        self,
        *,
        run_id: str,
        ticker: str,
        attempt_id: str,
        cutoff_at: datetime,
        thread_id: str | None,
    ) -> WorkerRunRequest:
        return WorkerRunRequest(
            workflow_version=CODEX_EVENT_LIBRARY_WORKFLOW_VERSION,
            research_lane=ResearchLane.EVENT_LIBRARY,
            run_id=run_id,
            ticker=ticker,
            node=CodexEventLibraryNode.O2_MAINTAIN,
            agent_role=CodexEventLibraryAgentRole.O2,
            attempt_id=attempt_id,
            cutoff_at=cutoff_at,
            prompt=(
                f"Event Library O2 attempt {attempt_id}. Read "
                f"attempts/{attempt_id}/input/task.json first, then read the five "
                "content files in content_input_order, perform only that stage, "
                "write the required workspace artifacts, and return exactly one JSON object "
                "matching output_schema.json."
            ),
            output_schema=O2_RUN_RESULT_SCHEMA,
            thread_id=thread_id,
            model=self.model,
            model_provider=self.model_provider,
            effort=self.effort,
            timeout_seconds=self.timeout_seconds,
            allow_subagents=False,
            max_subagents=0,
        )

    async def _validate_with_repairs(
        self,
        *,
        run_id: str,
        manifest: FrozenViewManifest,
        bundle_dir: Path,
        bundle_remote_prefix: str,
        cutoff_at: datetime,
        thread_id: str | None,
        completed: list[str],
        mode: Literal["INITIALIZE", "INCREMENTAL"],
        reported_result: O2RunResult,
    ) -> tuple[Path, BundleValidationOutcome, str | None]:
        loaded = RevisionBundleIO.load_tolerant(bundle_dir)
        outcome = self._validate_loaded(loaded, manifest=manifest)
        for repair_number in range(1, self.max_repairs + 1):
            if outcome.publishable:
                self._require_exact_bundle_coverage(reported_result, outcome)
                return bundle_dir, outcome, thread_id
            attempt_id = f"o2-repair-{repair_number:03d}"
            error = "; ".join(f"{item.code}: {item.message}" for item in outcome.issues)
            result, thread_id = await self._execute_repair(
                run_id=run_id,
                manifest=manifest,
                attempt_id=attempt_id,
                bundle_remote_prefix=bundle_remote_prefix,
                error=error,
                mode=mode,
                cutoff_at=cutoff_at,
                thread_id=thread_id,
                final_repair=repair_number == self.max_repairs,
            )
            completed.append(attempt_id)
            assert result.bundle_path is not None
            bundle_remote_prefix = result.bundle_path.rstrip("/")
            bundle_dir = await self._download_tree(
                run_id=run_id,
                remote_prefix=bundle_remote_prefix,
                local_root=(
                    self.local_workspace_root / run_id / "downloads" / f"repair-{repair_number}"
                ),
            )
            loaded = RevisionBundleIO.load_tolerant(bundle_dir)
            outcome = self._validate_loaded(loaded, manifest=manifest)
            reported_result = result
            if outcome.publishable:
                self._require_exact_bundle_coverage(result, outcome)
        if not outcome.publishable:
            raise ValueError("O2 Revision Bundle failed deterministic validation after repairs")
        return bundle_dir, outcome, thread_id

    @durable("o2_repair")
    async def _execute_repair(
        self,
        *,
        run_id: str,
        manifest: FrozenViewManifest,
        attempt_id: str,
        bundle_remote_prefix: str,
        error: str,
        mode: Literal["INITIALIZE", "INCREMENTAL"],
        cutoff_at: datetime,
        thread_id: str | None,
        final_repair: bool,
    ) -> tuple[O2RunResult, str | None]:
        await self._seed_attempt(
            run_id=run_id,
            manifest=manifest,
            attempt_id=attempt_id,
            stage=EventLibraryRunStage.BUNDLE_VALIDATE,
            skill_asset="skills/revision-bundle.md",
            assigned_delta_ids=[],
            prior_attempt_paths=[
                bundle_remote_prefix,
                (
                    "attempts/o2-global-reconciliation/output"
                    if mode == "INITIALIZE"
                    else "attempts/o2-reference-review/output"
                ),
            ],
            previous_failure=error,
            mode=mode,
        )
        job = await self.worker.run(
            self._worker_request(
                run_id=run_id,
                ticker=manifest.ticker,
                attempt_id=attempt_id,
                cutoff_at=cutoff_at,
                thread_id=thread_id,
            )
        )
        if job.thread_id:
            thread_id = job.thread_id
        if job.status != "succeeded" or not job.final_response:
            raise StructuredOutputInvalid(job.error_message or "O2 repair failed")
        result = O2RunResult.model_validate_json(job.final_response)
        expected_path = f"attempts/{attempt_id}/output/revision_bundle"
        if (
            result.status != "BUNDLE_READY"
            or result.stage is not EventLibraryRunStage.BUNDLE_VALIDATE
            or result.base_library_version != manifest.base_library_version
            or result.validation != "NOT_RUN"
            or (result.bundle_path or "").rstrip("/") != expected_path
        ):
            raise StructuredOutputInvalid(
                "O2 repair result must identify the current repair Bundle, Frozen base, "
                "BUNDLE_VALIDATE stage, and validation=NOT_RUN"
            )
        assert result.bundle_path is not None
        bundle_remote_prefix = result.bundle_path.rstrip("/")
        bundle_dir = await self._download_tree(
            run_id=run_id,
            remote_prefix=bundle_remote_prefix,
            local_root=(self.local_workspace_root / run_id / "downloads" / attempt_id),
        )
        loaded = RevisionBundleIO.load_tolerant(bundle_dir)
        outcome = self._validate_loaded(loaded, manifest=manifest)
        if outcome.publishable:
            self._require_exact_bundle_coverage(result, outcome)
        elif final_repair:
            raise ValueError("O2 final repair has no publishable bundle")
        return result, thread_id

    @staticmethod
    def _require_exact_bundle_coverage(
        result: O2RunResult, outcome: BundleValidationOutcome
    ) -> None:
        actual = (
            result.delta_coverage.total,
            result.delta_coverage.resolved,
            result.delta_coverage.pending,
        )
        expected = (
            outcome.total_delta_count,
            outcome.resolved_delta_count,
            outcome.pending_delta_count,
        )
        if actual != expected:
            raise StructuredOutputInvalid(
                "O2 final Delta coverage does not match the deterministic Bundle coverage"
            )

    async def _publish(
        self,
        *,
        run_id: str,
        bundle_dir: Path,
        batch: DeltaBatch,
        manifest: FrozenViewManifest,
        export_dir: str | Path,
        thread_id: str | None,
        completed: list[str],
        existing_outcome: BundleValidationOutcome | None = None,
    ) -> tuple[
        DeltaBatch,
        PublicationResult,
        BundleValidationOutcome,
        dict[str, Path],
    ]:
        loaded = RevisionBundleIO.load_tolerant(bundle_dir)
        self._save_run(
            run_id=run_id,
            manifest=manifest,
            stage=EventLibraryRunStage.IMPORT_WORKING,
            thread_id=thread_id,
            completed=completed,
            wave_count=len(self._plan_waves(batch)),
        )
        validation_context = self._validation_context(
            manifest, review_only=manifest.pending_delta_count == 0
        )
        result, outcome = self.service.importer.import_tolerant_and_publish(
            loaded, context=validation_context
        )
        if existing_outcome is not None and outcome.status != existing_outcome.status:
            raise RuntimeError("Bundle validation changed between promotion and import")
        self._save_run(
            run_id=run_id,
            manifest=manifest,
            stage=EventLibraryRunStage.PUBLISH_VN,
            thread_id=thread_id,
            completed=completed,
            wave_count=len(self._plan_waves(batch)),
            validator_status=outcome.status.value,
            metadata={"published_library_version": result.published_library_version},
        )
        exports = self.service.views.export_published(
            ticker=result.ticker,
            output_dir=export_dir,
            version=result.published_library_version,
        )
        published_prefix = f"published/event_library/v{result.published_library_version}"
        for _name, path in exports.items():
            await self.workspace.write_text(
                run_id,
                f"{published_prefix}/{path.name}",
                path.read_text(encoding="utf-8"),
            )
        self._save_run(
            run_id=run_id,
            manifest=manifest,
            stage=EventLibraryRunStage.PUBLISHED,
            thread_id=thread_id,
            completed=completed,
            wave_count=len(self._plan_waves(batch)),
            validator_status=outcome.status.value,
            metadata={"published_library_version": result.published_library_version},
        )
        return batch, result, outcome, exports

    def _validate_loaded(
        self,
        loaded: TolerantBundleLoadResult,
        *,
        manifest: FrozenViewManifest,
    ) -> BundleValidationOutcome:
        issues = [
            ValidationIssue(
                code=item.code,
                severity=ValidationSeverity.WARNING,
                message=item.message,
                item_id=item.item_id,
            )
            for item in loaded.issues
        ]
        return self.service.validator.validate(
            loaded.bundle,
            initial_issues=issues,
            force_pending_delta_ids=loaded.invalid_delta_ids,
            context=self._validation_context(
                manifest, review_only=manifest.pending_delta_count == 0
            ),
        )

    def _validation_context(
        self, manifest: FrozenViewManifest, *, review_only: bool
    ) -> BundleValidationContext:
        candidates = self.service.repository.due_reference_review_candidates(
            ticker=manifest.ticker, as_of=manifest.as_of
        )
        return BundleValidationContext(
            run_id=manifest.run_id,
            ticker=manifest.ticker,
            base_library_version=manifest.base_library_version,
            delta_batch_ids=list(manifest.delta_batch_ids),
            frozen_as_of=manifest.as_of,
            mode=manifest.mode,
            review_only=review_only,
            required_contract_version="event-library-maintenance-v3",
            deterministic_review_fields_by_event=self._deterministic_review_fields(
                manifest=manifest, candidates=candidates
            ),
        )

    async def _upload_tree(
        self,
        run_id: str,
        local_root: Path,
        remote_root: Path,
        *,
        exclude_relative_prefixes: tuple[str, ...] = (),
    ) -> None:
        inventory = await self.workspace.inventory(run_id)
        existing = {item.relative_path for item in inventory.files}
        for path in sorted(item for item in local_root.rglob("*") if item.is_file()):
            local_relative = path.relative_to(local_root).as_posix()
            if any(local_relative.startswith(prefix) for prefix in exclude_relative_prefixes):
                continue
            relative = (remote_root / path.relative_to(local_root)).as_posix()
            content = path.read_text(encoding="utf-8")
            if relative in existing:
                current = await self.workspace.read_text(run_id, relative)
                if current.content != content:
                    raise ValueError(f"immutable workspace file mismatch: {relative}")
                continue
            await self.workspace.write_text(run_id, relative, content)

    async def _download_tree(self, *, run_id: str, remote_prefix: str, local_root: Path) -> Path:
        if local_root.exists():
            shutil.rmtree(local_root)
        local_root.mkdir(parents=True)
        prefix = remote_prefix.rstrip("/") + "/"
        inventory = await self.workspace.inventory(run_id)
        paths = [
            item.relative_path for item in inventory.files if item.relative_path.startswith(prefix)
        ]
        if not paths:
            raise FileNotFoundError(f"remote Bundle is empty: {remote_prefix}")
        for relative in sorted(paths):
            response = await self.workspace.read_text(run_id, relative)
            if response.content is None:
                raise ValueError(f"remote workspace file has no text content: {relative}")
            target = local_root / Path(relative).relative_to(Path(remote_prefix))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(response.content, encoding="utf-8")
        return local_root

    def _save_run(
        self,
        *,
        run_id: str,
        manifest: FrozenViewManifest,
        stage: EventLibraryRunStage,
        thread_id: str | None,
        completed: list[str],
        wave_count: int,
        bundle_path: str | None = None,
        bundle_hash: str | None = None,
        validator_status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.service.repository.save_maintenance_run(
            run_id=run_id,
            ticker=manifest.ticker,
            stage=stage.value,
            thread_id=thread_id,
            frozen_view_id=manifest.frozen_view_id,
            base_version=manifest.base_library_version,
            bundle_path=bundle_path,
            bundle_hash=bundle_hash,
            validator_status=validator_status,
            metadata={
                "completed_attempt_ids": completed,
                "wave_count": wave_count,
                "model": self.model,
                "model_provider": self.model_provider,
                "effort": self.effort,
                "mode": manifest.mode,
                **(metadata or {}),
            },
        )


_RETRY_ATTEMPT = re.compile(r"^(?P<base>.+)-retry-(?P<number>0*[1-9]\d*)$")


def _base_attempt_id(attempt_id: str) -> str:
    match = _RETRY_ATTEMPT.fullmatch(attempt_id)
    return match.group("base") if match is not None else attempt_id


def _resolve_phase_attempts(
    phases: list[_O2Phase],
    *,
    completed: list[str],
    failed_attempt_id: str | None,
) -> list[_O2Phase]:
    """Allocate a new immutable attempt after failure and repair dependency paths."""

    actual_by_base = {_base_attempt_id(item): item for item in completed}
    if failed_attempt_id is not None:
        base = _base_attempt_id(failed_attempt_id)
        match = _RETRY_ATTEMPT.fullmatch(failed_attempt_id)
        retry_number = int(match.group("number")) + 1 if match is not None else 1
        actual_by_base[base] = f"{base}-retry-{retry_number:03d}"

    resolved: list[_O2Phase] = []
    for phase in phases:
        base = phase["attempt_id"]
        actual = actual_by_base.get(base, base)
        prior_paths = list(phase["prior_attempt_paths"])
        for prior_base, prior_actual in actual_by_base.items():
            prior_paths = [
                path.replace(f"attempts/{prior_base}/", f"attempts/{prior_actual}/")
                for path in prior_paths
            ]
        resolved.append(
            {
                "attempt_id": actual,
                "stage": phase["stage"],
                "skill_asset": phase["skill_asset"],
                "delta_ids": list(phase["delta_ids"]),
                "prior_attempt_paths": prior_paths,
            }
        )
    return resolved


def _infer_unfinished_attempt(paths: list[str], *, completed: list[str]) -> str | None:
    """Recover a lost failed-attempt pointer without mutating immutable inputs."""

    completed_set = set(completed)
    attempts = {
        parts[1]
        for path in paths
        if len(parts := path.replace("\\", "/").split("/")) >= 4
        and parts[0] == "attempts"
        and parts[2] == "input"
        and parts[3] == "task.json"
        and parts[1] not in completed_set
    }
    if not attempts:
        return None

    def order(attempt_id: str) -> tuple[str, int]:
        match = _RETRY_ATTEMPT.fullmatch(attempt_id)
        return (
            _base_attempt_id(attempt_id),
            int(match.group("number")) if match is not None else 0,
        )

    # Earliest unfinished phase is the only safe continuation point; within that
    # phase use its highest immutable retry number.
    first_base = sorted({_base_attempt_id(item) for item in attempts})[0]
    return max((item for item in attempts if _base_attempt_id(item) == first_base), key=order)


def _wave_count(item_count: int, wave_size: int) -> int:
    return (item_count + wave_size - 1) // wave_size


def _directory_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
