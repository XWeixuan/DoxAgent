from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from doxagent.codex_worker.schema import (
    WorkerJob,
    WorkerRunRequest,
    WorkspaceFileResponse,
    WorkspaceInventory,
)
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.event_library.contracts import (
    CanonicalAssertionState,
    FrozenRuntimeAtomic,
    FrozenRuntimeSnapshot,
)
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.event_library.service import EventLibraryService
from doxagent.workflows.codex_event_library.remote_runner import (
    RemoteEventLibraryInitializer,
    _resolve_phase_attempts,
)
from doxagent.workflows.codex_event_library.schema import (
    O2_RUN_RESULT_SCHEMA,
    EventLibraryRunStage,
    O2RunResult,
)


def test_o2_response_schema_is_strict_provider_compatible() -> None:
    properties = O2_RUN_RESULT_SCHEMA["properties"]
    assert O2_RUN_RESULT_SCHEMA["additionalProperties"] is False
    assert set(O2_RUN_RESULT_SCHEMA["required"]) == set(properties)
    assert "default" not in json.dumps(O2_RUN_RESULT_SCHEMA)


def test_failed_phase_resumes_as_new_immutable_attempt_and_repairs_dependencies() -> None:
    phases = [
        {
            "attempt_id": "o2-survey",
            "stage": EventLibraryRunStage.SURVEY,
            "skill_asset": "skills/initialize-survey.md",
            "delta_ids": ["D1"],
            "prior_attempt_paths": [],
        },
        {
            "attempt_id": "o2-wave-001",
            "stage": EventLibraryRunStage.LOCAL_RECONSTRUCTION,
            "skill_asset": "skills/initialize-wave.md",
            "delta_ids": ["D1"],
            "prior_attempt_paths": ["attempts/o2-survey/output/work"],
        },
    ]
    resolved = _resolve_phase_attempts(
        phases, completed=[], failed_attempt_id="o2-survey"
    )
    assert resolved[0]["attempt_id"] == "o2-survey-retry-001"
    assert resolved[1]["prior_attempt_paths"] == [
        "attempts/o2-survey-retry-001/output/work"
    ]


class AsyncLocalWorkspace:
    def __init__(self, root: Path) -> None:
        self.store = LocalWorkspaceStore(root)

    async def write_text(
        self, run_id: str, relative_path: str, content: str
    ) -> WorkspaceFileResponse:
        return self.store.write_text(run_id, relative_path, content)

    async def read_text(self, run_id: str, relative_path: str) -> WorkspaceFileResponse:
        return self.store.read_text(run_id, relative_path)

    async def inventory(self, run_id: str) -> WorkspaceInventory:
        return self.store.inventory(run_id)


class FakeO2Worker:
    def __init__(self, workspace: AsyncLocalWorkspace, batch_id: str) -> None:
        self.workspace = workspace
        self.batch_id = batch_id
        self.requests: list[WorkerRunRequest] = []

    async def run(self, request: WorkerRunRequest) -> WorkerJob:
        self.requests.append(request)
        thread_id = request.thread_id or "o2-thread-1"
        if request.attempt_id == "o2-global-reconciliation":
            root = f"attempts/{request.attempt_id}/output/revision_bundle"
            event = {
                "event_id": "T1",
                "ticker": "AMD",
                "title": "AMD announced two product updates",
                "event_type": "COMPANY_DISCLOSURE",
                "occurred_at": "2026-08-23",
                "occurrence_time_precision": "DAY",
                "status": "ACTIVE",
                "canonical_summary": "AMD announced two related product updates.",
                "known_event_summary": "AMD announced product A and product B on August 23.",
                "is_important": True,
                "include_in_reference_view": True,
                "related_event_ids": [],
                "supersedes_event_id": None,
                "derived_from_event_ids": [],
                "price_analysis": None,
                "facts": [
                    {
                        "fact_id": "TF1",
                        "proposition": "AMD announced product A.",
                        "assertion_state": "ACTUAL",
                        "subject_time": "2026-08-23",
                        "entities": ["AMD"],
                        "consumes_delta_ids": ["D1"],
                    },
                    {
                        "fact_id": "TF2",
                        "proposition": "AMD announced product B.",
                        "assertion_state": "ACTUAL",
                        "subject_time": "2026-08-23",
                        "entities": ["AMD"],
                        "consumes_delta_ids": ["D2"],
                    },
                ],
            }
            files = {
                f"{root}/manifest.json": {
                    "contract_version": "event-library-foundation-v1",
                    "run_id": request.run_id,
                    "ticker": "AMD",
                    "base_library_version": 0,
                    "delta_batch_ids": [self.batch_id],
                    "event_revisions": ["events/T1.json"],
                },
                f"{root}/events/T1.json": event,
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
            output = O2RunResult(
                status="BUNDLE_READY",
                stage=EventLibraryRunStage.GLOBAL_RECONCILIATION,
                bundle_path=root,
                base_library_version=0,
                delta_coverage={"total": 2, "resolved": 2, "pending": 0},
            )
        else:
            output = O2RunResult(
                status="PENDING",
                stage=(
                    EventLibraryRunStage.SURVEY
                    if request.attempt_id == "o2-survey"
                    else EventLibraryRunStage.LOCAL_RECONSTRUCTION
                ),
                base_library_version=0,
                delta_coverage={"total": 2, "resolved": 0, "pending": 2},
            )
        return WorkerJob(
            job_id=f"job-{request.attempt_id}",
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            status="succeeded",
            thread_id=thread_id,
            final_response=output.model_dump_json(),
        )

    async def cancel(self, job_id: str) -> WorkerJob | None:
        return None


@pytest.mark.asyncio
async def test_wave_initialization_reuses_thread_publishes_and_resumes_without_model(
    tmp_path: Path,
) -> None:
    repository = EventLibraryRepository(tmp_path / "event_library.sqlite3")
    service = EventLibraryService(repository)
    snapshot = FrozenRuntimeSnapshot(
        snapshot_id="runtime-amd-v1",
        runtime_scope="cdecr:US:AMD",
        epoch_id="epoch-amd-v1",
        market="US",
        ticker="AMD",
        as_of=datetime(2026, 8, 24, tzinfo=UTC),
        atomics=[
            FrozenRuntimeAtomic(
                runtime_atomic_id="A1",
                version=1,
                proposition="AMD announced product A.",
                time="2026-08-23",
                assertion_state=CanonicalAssertionState.ACTUAL,
                entities=["AMD"],
            ),
            FrozenRuntimeAtomic(
                runtime_atomic_id="A2",
                version=1,
                proposition="AMD announced product B.",
                time="2026-08-23",
                assertion_state=CanonicalAssertionState.ACTUAL,
                entities=["AMD"],
            ),
        ],
    )
    batch = service.delta_compiler.compile(snapshot)
    workspace = AsyncLocalWorkspace(tmp_path / "remote")
    worker = FakeO2Worker(workspace, batch.batch_id)
    initializer = RemoteEventLibraryInitializer(
        worker=worker,
        workspace=workspace,  # type: ignore[arg-type]
        service=service,
        local_workspace_root=tmp_path / "local",
        prompt_root=Path("prompts/codex_v2/event_library"),
        wave_size=1,
    )
    _, publication, outcome, exports = await initializer.run(
        snapshot=snapshot,
        run_id="amd-initialize-v1",
        cutoff_at=snapshot.as_of,
        export_dir=tmp_path / "exports",
    )
    assert publication is not None and publication.published_library_version == 1
    assert outcome is not None and outcome.publishable
    assert len(repository.published_events("AMD")) == 1
    assert len(repository.published_events("AMD")[0].facts) == 2
    assert {request.thread_id for request in worker.requests[1:]} == {"o2-thread-1"}
    assert len(worker.requests) == 4
    assert all(path.exists() for path in exports.values())

    _, repeated, _, _ = await initializer.run(
        snapshot=snapshot,
        run_id="amd-initialize-v1",
        cutoff_at=snapshot.as_of,
        export_dir=tmp_path / "exports-repeat",
    )
    assert repeated is not None and repeated.published_library_version == 1
    assert len(worker.requests) == 4
