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
    DeltaBatch,
    FrozenRuntimeSnapshot,
    FrozenViewManifest,
    PublicationResult,
)
from doxagent.event_library.service import EventLibraryService
from doxagent.event_library.validator import (
    BundleValidationOutcome,
    ValidationIssue,
    ValidationSeverity,
)
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
        self.worker = worker
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
        )
        await self._upload_tree(run_id, frozen_root, frozen_root.relative_to(local_run_root))
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
                str(metadata["failed_attempt_id"])
                if metadata.get("failed_attempt_id")
                else None
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
            await self._seed_attempt(
                run_id=run_id,
                manifest=manifest,
                attempt_id=attempt_id,
                stage=phase["stage"],
                skill_asset=phase["skill_asset"],
                assigned_delta_ids=phase["delta_ids"],
                prior_attempt_paths=phase["prior_attempt_paths"],
                mode=mode,
            )
            request = self._worker_request(
                run_id=run_id,
                ticker=manifest.ticker,
                attempt_id=attempt_id,
                cutoff_at=cutoff_at,
                thread_id=thread_id,
            )
            job = await self.worker.run(request)
            if job.thread_id:
                thread_id = job.thread_id
            if job.status != "succeeded" or not job.final_response:
                self._save_run(
                    run_id=run_id,
                    manifest=manifest,
                    stage=phase["stage"],
                    thread_id=thread_id,
                    completed=completed,
                    wave_count=len(self._plan_waves(batch)),
                    metadata={
                        "error_code": job.error_code,
                        "error_message": job.error_message,
                        "failed_attempt_id": attempt_id,
                    },
                )
                raise StructuredOutputInvalid(job.error_message or "O2 worker failed")
            result = O2RunResult.model_validate_json(job.final_response)
            expected_final = phase is phases[-1]
            if expected_final and result.status != "BUNDLE_READY":
                raise StructuredOutputInvalid("final O2 turn did not return BUNDLE_READY")
            if not expected_final and result.status == "FAILED":
                raise StructuredOutputInvalid("O2 initialization phase reported FAILED")
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
            latest_bundle_prefix = (
                f"attempts/{phases[-1]['attempt_id']}/output/revision_bundle"
            )
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
        bundle_dir, outcome, thread_id = await self._validate_with_repairs(
            run_id=run_id,
            manifest=manifest,
            bundle_dir=bundle_dir,
            bundle_remote_prefix=latest_bundle_prefix,
            cutoff_at=cutoff_at,
            thread_id=thread_id,
            completed=completed,
            mode=mode,
        )
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
                    "prior_attempt_paths": [
                        "attempts/o2-known-index-map/output/work"
                    ],
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
                    items_by_id[item]
                    for item in package.member_delta_ids
                    if item in items_by_id
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
                last_tokens = sum(
                    max(1, len(item.model_dump_json()) // 4) for item in waves[-1]
                )
                if (
                    len(waves[-1]) + len(chunk) <= self.wave_size
                    and last_tokens + chunk_tokens <= self.wave_token_budget
                ):
                    waves[-1].extend(chunk)
                    continue
            waves.append(list(chunk))
        return waves

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
    ) -> None:
        prefix = f"attempts/{attempt_id}/input"
        task = {
            "workflow": "codex_event_library_v1",
            "mode": mode,
            "stage": stage.value,
            "attempt_id": attempt_id,
            "required_input_order": [
                "AGENTS.md",
                "agent.md",
                "skill.md",
                "task.json",
                "context.json",
                "output_schema.json",
            ],
            "frozen_view_manifest": (
                f"context/event_library/{manifest.frozen_view_id}/manifest.json"
            ),
            "assigned_delta_ids": assigned_delta_ids,
            "prior_attempt_paths": prior_attempt_paths,
            "known_index_scope": "FULL" if mode == "INCREMENTAL" else None,
            "event_detail_access": (
                "FORBIDDEN"
                if stage is EventLibraryRunStage.BUILD_CANDIDATE_MAP
                else (
                    "STABLE_IDS_FROM_PRIOR_CANDIDATE_MAP_ONLY"
                    if mode == "INCREMENTAL"
                    else "INITIALIZATION"
                )
            ),
            "work_path": f"attempts/{attempt_id}/output/work",
            "output_bundle_path": f"attempts/{attempt_id}/output/revision_bundle",
            "previous_failure": previous_failure,
        }
        assets = {
            "AGENTS.md": (self.prompt_root / "AGENTS.md").read_text(encoding="utf-8"),
            "agent.md": (self.prompt_root / "agents" / "o2.md").read_text(encoding="utf-8"),
            "skill.md": (self.prompt_root / skill_asset).read_text(encoding="utf-8"),
            "task.json": _json_text(task),
            "context.json": manifest.model_dump_json(indent=2) + "\n",
            "output_schema.json": _json_text(O2_RUN_RESULT_SCHEMA),
        }
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
                f"Event Library O2 attempt {attempt_id}. Read the six files under "
                f"attempts/{attempt_id}/input in task.json order, perform only that stage, "
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
    ) -> tuple[Path, BundleValidationOutcome, str | None]:
        loaded = RevisionBundleIO.load_tolerant(bundle_dir)
        outcome = self._validate_loaded(loaded)
        for repair_number in range(1, self.max_repairs + 1):
            if outcome.publishable:
                return bundle_dir, outcome, thread_id
            attempt_id = f"o2-repair-{repair_number:03d}"
            error = "; ".join(f"{item.code}: {item.message}" for item in outcome.issues)
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
            if result.status != "BUNDLE_READY" or not result.bundle_path:
                raise StructuredOutputInvalid("O2 repair did not return a Bundle")
            completed.append(attempt_id)
            bundle_remote_prefix = result.bundle_path.rstrip("/")
            bundle_dir = await self._download_tree(
                run_id=run_id,
                remote_prefix=bundle_remote_prefix,
                local_root=(
                    self.local_workspace_root / run_id / "downloads" / f"repair-{repair_number}"
                ),
            )
            loaded = RevisionBundleIO.load_tolerant(bundle_dir)
            outcome = self._validate_loaded(loaded)
        if not outcome.publishable:
            raise ValueError("O2 Revision Bundle failed deterministic validation after repairs")
        return bundle_dir, outcome, thread_id

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
        result, outcome = self.service.importer.import_tolerant_and_publish(loaded)
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

    def _validate_loaded(self, loaded: TolerantBundleLoadResult) -> BundleValidationOutcome:
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
        )

    async def _upload_tree(
        self, run_id: str, local_root: Path, remote_root: Path
    ) -> None:
        inventory = await self.workspace.inventory(run_id)
        existing = {item.relative_path for item in inventory.files}
        for path in sorted(item for item in local_root.rglob("*") if item.is_file()):
            relative = (remote_root / path.relative_to(local_root)).as_posix()
            content = path.read_text(encoding="utf-8")
            if relative in existing:
                current = await self.workspace.read_text(run_id, relative)
                if current.content != content:
                    raise ValueError(f"immutable workspace file mismatch: {relative}")
                continue
            await self.workspace.write_text(run_id, relative, content)

    async def _download_tree(
        self, *, run_id: str, remote_prefix: str, local_root: Path
    ) -> Path:
        if local_root.exists():
            shutil.rmtree(local_root)
        local_root.mkdir(parents=True)
        prefix = remote_prefix.rstrip("/") + "/"
        inventory = await self.workspace.inventory(run_id)
        paths = [
            item.relative_path
            for item in inventory.files
            if item.relative_path.startswith(prefix)
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
