from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from doxagent.codex_worker.schema import WorkerJob, WorkerRunRequest
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.event_library.bundle_io import RevisionBundleIO
from doxagent.event_library.contracts import (
    CanonicalAssertionState,
    FrozenRuntimeAtomic,
    FrozenRuntimeSnapshot,
)
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.event_library.service import EventLibraryService
from doxagent.workflows.codex_event_library.remote_runner import (
    RemoteEventLibraryInitializer,
)
from doxagent.workflows.codex_event_library.schema import EventLibraryRunStage, O2RunResult
from tests.test_codex_event_library_initialization import AsyncLocalWorkspace

FIXTURE = Path("tests/fixtures/event_library/mu_v1")


def _publish_v1(repository: EventLibraryRepository, root: Path) -> None:
    service = EventLibraryService(repository)
    snapshot = FrozenRuntimeSnapshot.model_validate_json(
        (FIXTURE / "runtime_snapshot.json").read_text(encoding="utf-8")
    )
    batch = service.delta_compiler.compile(snapshot)
    bundle_path = root / "v1-bundle"
    shutil.copytree(FIXTURE / "revision_bundle", bundle_path)
    manifest_path = bundle_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["delta_batch_ids"] = [batch.batch_id]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    service.importer.import_and_publish(RevisionBundleIO.load(bundle_path))


class IncrementalWorker:
    def __init__(
        self,
        workspace: AsyncLocalWorkspace,
        repository: EventLibraryRepository,
        batch_id: str,
    ) -> None:
        self.workspace = workspace
        self.repository = repository
        self.batch_id = batch_id
        self.requests: list[WorkerRunRequest] = []

    async def run(self, request: WorkerRunRequest) -> WorkerJob:
        self.requests.append(request)
        thread_id = request.thread_id or "o2-incremental-thread"
        stage = {
            "o2-known-index-map": EventLibraryRunStage.BUILD_CANDIDATE_MAP,
            "o2-incremental-edit": EventLibraryRunStage.RECONSTRUCT_AND_EDIT,
            "o2-reference-review": EventLibraryRunStage.REFERENCE_REVIEW,
        }[request.attempt_id]
        if request.attempt_id == "o2-reference-review":
            root = f"attempts/{request.attempt_id}/output/revision_bundle"
            current = self.repository.get_event("MU", "E1", 1)
            assert current is not None
            event = current.model_dump(mode="json")
            event["facts"] = [
                {**fact.model_dump(mode="json"), "consumes_delta_ids": []}
                for fact in current.facts
            ] + [
                {
                    "fact_id": "TF1",
                    "proposition": "Micron added an incremental operating update.",
                    "assertion_state": "ACTUAL",
                    "subject_time": "2026-08-24",
                    "consumes_delta_ids": ["D1"],
                }
            ]
            files: dict[str, Any] = {
                f"{root}/manifest.json": {
                    "contract_version": "event-library-foundation-v1",
                    "run_id": request.run_id,
                    "ticker": "MU",
                    "base_library_version": 1,
                    "delta_batch_ids": [self.batch_id],
                    "event_revisions": ["events/E1.json"],
                },
                f"{root}/events/E1.json": event,
                f"{root}/retirements.json": [],
            }
            for path, payload in files.items():
                await self.workspace.write_text(
                    request.run_id,
                    path,
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                )
            await self.workspace.write_text(
                request.run_id, f"{root}/residual_delta_resolutions.jsonl", ""
            )
            result = O2RunResult(
                status="BUNDLE_READY",
                stage=stage,
                bundle_path=root,
                base_library_version=1,
                delta_coverage={"total": 1, "resolved": 1, "pending": 0},
                validation="PASS",
            )
        else:
            result = O2RunResult(
                status="PENDING",
                stage=stage,
                base_library_version=1,
                delta_coverage={"total": 1, "resolved": 0, "pending": 1},
            )
        return WorkerJob(
            job_id=f"job-{request.attempt_id}",
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            status="succeeded",
            thread_id=thread_id,
            final_response=result.model_dump_json(),
        )

    async def cancel(self, job_id: str) -> WorkerJob | None:
        return None


class FailOnceAfterPromotion(RemoteEventLibraryInitializer):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.fail_once = True

    async def _publish(self, **kwargs: Any) -> Any:
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("simulated power loss after Bundle promotion")
        return await super()._publish(**kwargs)


@pytest.mark.asyncio
async def test_incremental_o2_uses_index_then_detail_and_recovers_after_promotion(
    tmp_path: Path,
) -> None:
    repository = EventLibraryRepository(tmp_path / "event_library.sqlite3")
    _publish_v1(repository, tmp_path)
    service = EventLibraryService(repository)
    snapshot = FrozenRuntimeSnapshot(
        snapshot_id="runtime-mu-incremental-v2",
        runtime_scope="cdecr:US:MU",
        epoch_id="epoch-mu-incremental-v2",
        market="US",
        ticker="MU",
        as_of=datetime(2026, 8, 24, 23, 59, tzinfo=UTC),
        atomics=[
            FrozenRuntimeAtomic(
                runtime_atomic_id="a10-incremental-update",
                version=1,
                proposition="Micron added an incremental operating update.",
                time="2026-08-24",
                assertion_state=CanonicalAssertionState.ACTUAL,
                entities=["Micron"],
            )
        ],
    )
    batch = service.delta_compiler.compile(snapshot)
    workspace = AsyncLocalWorkspace(tmp_path / "remote")
    worker = IncrementalWorker(workspace, repository, batch.batch_id)
    maintainer = FailOnceAfterPromotion(
        worker=worker,
        workspace=workspace,
        service=service,
        local_workspace_root=tmp_path / "local",
        prompt_root=Path("prompts/codex_v2/event_library"),
    )
    with pytest.raises(RuntimeError, match="power loss"):
        await maintainer.run(
            snapshot=snapshot,
            run_id="mu-incremental-v2",
            cutoff_at=snapshot.as_of,
            export_dir=tmp_path / "exports",
            mode="INCREMENTAL",
        )
    assert repository.published_version("MU") == 1
    assert len(worker.requests) == 3
    assert worker.requests[0].thread_id is None
    assert {request.thread_id for request in worker.requests[1:]} == {
        "o2-incremental-thread"
    }
    remote = LocalWorkspaceStore(tmp_path / "remote")
    inventory = remote.inventory("mu-incremental-v2")
    paths = {item.relative_path for item in inventory.files}
    frozen_index = next(path for path in paths if path.endswith("known_event_index.md"))
    assert "E1 |" in remote.read_text("mu-incremental-v2", frozen_index).content
    assert any(path.endswith("events/E1.json") for path in paths)

    _, publication, outcome, _ = await maintainer.run(
        snapshot=snapshot,
        run_id="mu-incremental-v2",
        cutoff_at=snapshot.as_of,
        export_dir=tmp_path / "exports-resumed",
        mode="INCREMENTAL",
    )
    assert publication is not None and publication.published_library_version == 2
    assert outcome is not None and outcome.publishable
    assert len(worker.requests) == 3
    revised = repository.get_event("MU", "E1", 2)
    assert revised is not None and len(revised.facts) == 5

    _, repeated, _, _ = await maintainer.run(
        snapshot=snapshot,
        run_id="mu-incremental-v2",
        cutoff_at=snapshot.as_of,
        export_dir=tmp_path / "exports-repeated",
        mode="INCREMENTAL",
    )
    assert repeated is not None and repeated.published_library_version == 2
    assert len(worker.requests) == 3
