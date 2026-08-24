"""Foundation orchestration from frozen Runtime snapshot through atomic publication."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from doxagent.event_library.bundle_io import RevisionBundleIO
from doxagent.event_library.contracts import (
    DeltaBatch,
    FrozenRuntimeSnapshot,
    FrozenViewManifest,
    PublicationResult,
)
from doxagent.event_library.service import EventLibraryService
from doxagent.event_library.validator import BundleValidationOutcome
from doxagent.workflows.codex_event_library.runner import EventLibraryAgentRunner
from doxagent.workflows.codex_event_library.schema import EventLibraryRunStage


class EventLibraryFoundationOrchestrator:
    def __init__(
        self, *, service: EventLibraryService, agent_runner: EventLibraryAgentRunner
    ) -> None:
        self.service = service
        self.agent_runner = agent_runner

    def prepare(
        self,
        *,
        snapshot: FrozenRuntimeSnapshot,
        run_id: str,
        attempt_id: str,
        mode: Literal["INITIALIZE", "INCREMENTAL"],
    ) -> tuple[DeltaBatch, Path | None, FrozenViewManifest | None]:
        batch = self.service.delta_compiler.compile(snapshot)
        if not batch.items:
            return batch, None, None
        run_root = self.agent_runner.workspace.ensure_run(run_id)
        frozen_root, manifest = self.service.views.materialize_frozen_view(
            run_root=run_root,
            run_id=run_id,
            mode=mode,
            batches=[batch],
            as_of=snapshot.as_of,
        )
        self.agent_runner.prepare_attempt(
            run_id=run_id,
            attempt_id=attempt_id,
            manifest=manifest,
        )
        return batch, frozen_root, manifest

    def import_promoted_or_reviewed_bundle(
        self,
        *,
        run_id: str,
        bundle_path: str | Path,
        export_dir: str | Path,
    ) -> tuple[PublicationResult, BundleValidationOutcome, dict[str, Path]]:
        _, first_outcome = self.agent_runner.validate_and_promote(
            run_id=run_id, bundle_path=bundle_path
        )
        assert first_outcome.normalized_bundle is not None
        # Import the original Bundle so the repository persists both the raw and
        # normalized hashes. This makes a degraded retry idempotent as well.
        source_bundle = RevisionBundleIO.load(bundle_path)
        result, outcome = self.service.importer.import_and_publish(
            source_bundle
        )
        exports = self.service.views.export_published(
            ticker=result.ticker,
            output_dir=export_dir,
            version=result.published_library_version,
        )
        state = self.agent_runner.load_state(run_id)
        self.agent_runner.save_state(
            state.model_copy(
                update={
                    "stage": EventLibraryRunStage.PUBLISHED,
                    "validator_status": outcome.status.value,
                }
            )
        )
        return result, outcome, exports
