"""Dedicated O2 V2 workspace, Worker request, recovery, and Bundle promotion skeleton."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from doxagent.codex_runtime.schema import (
    CODEX_EVENT_LIBRARY_WORKFLOW_VERSION,
    CodexEventLibraryAgentRole,
    CodexEventLibraryNode,
    ResearchLane,
)
from doxagent.codex_worker.schema import WorkerRunRequest
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.event_library.bundle_io import RevisionBundleIO
from doxagent.event_library.contracts import FrozenViewManifest
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.event_library.validator import BundleValidationOutcome, RevisionBundleValidator
from doxagent.workflows.codex_event_library.context import EventLibraryAttemptSeeder
from doxagent.workflows.codex_event_library.schema import (
    EventLibraryRunStage,
    EventLibraryRunState,
    O2RunResult,
    PreparedO2Attempt,
)


class EventLibraryAgentRunner:
    def __init__(
        self,
        *,
        workspace: LocalWorkspaceStore,
        repository: EventLibraryRepository,
        prompt_root: str | Path = "prompts/codex_v2/event_library",
        model: str = "gpt-5.6-luna",
        model_provider: str | None = None,
        effort: Literal["low", "medium", "high", "xhigh", "max"] = "max",
        timeout_seconds: int = 1800,
    ) -> None:
        self.workspace = workspace
        self.repository = repository
        self._seeder = EventLibraryAttemptSeeder(workspace, prompt_root)
        self._validator = RevisionBundleValidator(repository)
        self.model = model
        self.model_provider = model_provider
        self.effort = effort
        self.timeout_seconds = timeout_seconds

    def prepare_attempt(
        self,
        *,
        run_id: str,
        attempt_id: str,
        manifest: FrozenViewManifest,
        previous_failure: str | None = None,
        skill_asset: str = "skills/foundation.md",
        stage: EventLibraryRunStage = EventLibraryRunStage.CANONICAL_EDIT,
        assigned_delta_ids: list[str] | None = None,
        prior_attempt_paths: list[str] | None = None,
        task_metadata: dict[str, Any] | None = None,
    ) -> PreparedO2Attempt:
        prepared = self._seeder.seed(
            run_id=run_id,
            attempt_id=attempt_id,
            manifest=manifest,
            skill_asset=skill_asset,
            previous_failure=previous_failure,
            stage=stage.value,
            assigned_delta_ids=assigned_delta_ids,
            prior_attempt_paths=prior_attempt_paths,
            task_metadata=task_metadata,
        )
        try:
            previous = self.load_state(run_id)
        except FileNotFoundError:
            previous = None
        self._save_state(
            EventLibraryRunState(
                run_id=run_id,
                attempt_id=attempt_id,
                ticker=manifest.ticker,
                stage=stage,
                frozen_view_id=manifest.frozen_view_id,
                base_library_version=manifest.base_library_version,
                thread_id=previous.thread_id if previous else None,
                completed_attempt_ids=(previous.completed_attempt_ids if previous else []),
                wave_count=previous.wave_count if previous else 0,
                model=self.model,
                model_provider=self.model_provider,
                effort=self.effort,
            )
        )
        return prepared

    def build_worker_request(
        self,
        *,
        prepared: PreparedO2Attempt,
        ticker: str,
        cutoff_at: datetime,
        thread_id: str | None = None,
    ) -> WorkerRunRequest:
        prompt = (
            f"Event Library O2 attempt: {prepared.attempt_id}. Read the six files under "
            f"attempts/{prepared.attempt_id}/input in the order declared by task.json. "
            "Follow them exactly and return one JSON object matching output_schema.json."
        )
        return WorkerRunRequest(
            workflow_version=CODEX_EVENT_LIBRARY_WORKFLOW_VERSION,
            research_lane=ResearchLane.EVENT_LIBRARY,
            run_id=prepared.run_id,
            ticker=ticker.upper(),
            node=CodexEventLibraryNode.O2_MAINTAIN,
            agent_role=CodexEventLibraryAgentRole.O2,
            attempt_id=prepared.attempt_id,
            cutoff_at=cutoff_at,
            prompt=prompt,
            output_schema=O2RunResult.model_json_schema(),
            thread_id=thread_id,
            model=self.model,
            model_provider=self.model_provider,
            effort=self.effort,
            timeout_seconds=self.timeout_seconds,
            allow_subagents=False,
            max_subagents=0,
        )

    def record_thread(self, run_id: str, thread_id: str) -> EventLibraryRunState:
        state = self.load_state(run_id)
        updated = state.model_copy(
            update={"thread_id": thread_id, "stage": EventLibraryRunStage.AGENT_EDIT}
        )
        self._save_state(updated)
        return updated

    def validate_and_promote(
        self, *, run_id: str, bundle_path: str | Path
    ) -> tuple[Path, BundleValidationOutcome]:
        bundle = RevisionBundleIO.load(bundle_path)
        outcome = self._validator.validate(bundle)
        state = self.load_state(run_id)
        if not outcome.publishable or outcome.normalized_bundle is None:
            self._save_state(
                state.model_copy(
                    update={
                        "stage": EventLibraryRunStage.BUNDLE_VALIDATE,
                        "validator_status": outcome.status.value,
                    }
                )
            )
            raise ValueError("O2 Revision Bundle failed deterministic validation")
        digest = _directory_hash(Path(bundle_path))
        run_root = self.workspace.ensure_run(run_id)
        destination = run_root / "artifacts" / "event_library" / "revision_bundles" / digest[:24]
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=f".{digest[:12]}-", dir=destination.parent))
            try:
                shutil.copytree(bundle_path, staging / "bundle")
                os.replace(staging / "bundle", destination)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        updated = state.model_copy(
            update={
                "stage": EventLibraryRunStage.ARTIFACT_PROMOTED,
                "bundle_path": destination.relative_to(run_root).as_posix(),
                "bundle_hash": digest,
                "validator_status": outcome.status.value,
            }
        )
        self._save_state(updated)
        return destination, outcome

    def load_state(self, run_id: str) -> EventLibraryRunState:
        response = self.workspace.read_text(run_id, "audit/event_library_run_state.json")
        if response.content is None:
            raise FileNotFoundError("audit/event_library_run_state.json")
        return EventLibraryRunState.model_validate_json(response.content)

    def _save_state(self, state: EventLibraryRunState) -> None:
        relative = "audit/event_library_run_state.json"
        try:
            current = self.workspace.read_text(state.run_id, relative)
        except FileNotFoundError:
            expected = None
        else:
            expected = current.sha256
        self.workspace.write_text(
            state.run_id,
            relative,
            state.model_dump_json(indent=2) + "\n",
            expected_sha256=expected,
        )
        self.repository.save_maintenance_run(
            run_id=state.run_id,
            ticker=state.ticker,
            stage=state.stage.value,
            thread_id=state.thread_id,
            frozen_view_id=state.frozen_view_id,
            base_version=state.base_library_version,
            bundle_path=state.bundle_path,
            bundle_hash=state.bundle_hash,
            validator_status=state.validator_status,
            metadata={"attempt_id": state.attempt_id},
        )

    def save_state(self, state: EventLibraryRunState) -> None:
        """Persist an explicit orchestration stage outside the Codex thread."""

        self._save_state(state)


def _directory_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
