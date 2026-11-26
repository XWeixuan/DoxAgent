"""Regression coverage for the 850-Delta COIN survey deadline incident."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime

import pytest

from doxagent.codex_worker.schema import WorkerJob
from doxagent.event_library.contracts import FrozenRuntimeAtomic, FrozenRuntimeSnapshot
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.event_library.service import EventLibraryService
from doxagent.ticker_initialization import (
    InitializationRepository,
    InitializationWorker,
    NodeResult,
    NodeSpec,
)
from doxagent.workflows.codex_event_library.remote_runner import RemoteEventLibraryInitializer
from tests.test_codex_event_library_initialization import AsyncLocalWorkspace


class SurveyWorker:
    def __init__(self, workspace):
        self.workspace = workspace
        self.requests = []
        self.jobs = {}
        self.tasks = []
        self.cancel_part = None
        self.omit_id = None
        self.omit_date_id = None
        self.empty_occurrence = False

    async def run(self, request):
        self.requests.append(request)
        task = json.loads((await self.workspace.read_text(
            request.run_id, f"attempts/{request.attempt_id}/input/task.json"
        )).content)
        self.tasks.append(task)
        if request.attempt_id == self.cancel_part:
            self.cancel_part = None
            raise asyncio.CancelledError()
        survey = task["metadata"]["survey"]
        if survey["mode"] == "PART":
            content = (await self.workspace.read_text(
                request.run_id, survey["runtime_context_path"]
            )).content
            assert hashlib.sha256(content.encode()).hexdigest() == survey["runtime_context_sha256"]
            projection = json.loads(content)
            atomics = projection["ungrouped_atomics"] + [
                atomic for group in projection["package_groups"] for atomic in group["atomics"]
            ]
            assert {a["delta_id"] for a in atomics} == set(task["assigned_delta_ids"])
            assert all(a["proposition"] and "occurrence_date_candidates" in a for a in atomics)
        else:
            covered = []
            for path in survey["part_paths"]:
                catalog = json.loads((await self.workspace.read_text(
                    request.run_id, f"{path}/delta_catalog.json"
                )).content)
                covered.extend(catalog)
            assert len(covered) == len(set(covered)) == len(task["assigned_delta_ids"])
            assert set(covered) == set(task["assigned_delta_ids"])
        assigned = [d for d in task["assigned_delta_ids"] if d != self.omit_id]
        # The same occurrence intentionally spans every pass. Only the global
        # model pass adjudicates its shared identity; the planner never merges it.
        outputs = {
            "delta_catalog.json": json.dumps({d: "shared-occurrence" for d in assigned}),
            "occurrence_ledger.md": (
                "" if self.empty_occurrence else
                "Shared disclosure; compare across passes: " + ",".join(assigned)
            ),
            "date_resolution_ledger.jsonl": "\n".join(json.dumps({
                "delta_id": d, "semantic_role": "EVENT_OCCURRENCE", "status": "UNRESOLVED",
            }) for d in assigned if d != self.omit_date_id),
        }
        for name, content in outputs.items():
            await self.workspace.write_text(request.run_id, f"{task['work_path']}/{name}", content)
        job = WorkerJob(
            job_id=f"job-{request.attempt_id}", run_id=request.run_id,
            attempt_id=request.attempt_id, status="succeeded", thread_id="survey-thread",
            final_response=json.dumps({"base_library_version": 0}),
        )
        self.jobs[(request.run_id, request.attempt_id, request.idempotency_key)] = job
        return job

    async def lookup_job(self, request):
        return self.jobs.get((request.run_id, request.attempt_id, request.idempotency_key))


def setup_survey(tmp_path, count, **options):
    service = EventLibraryService(EventLibraryRepository(tmp_path / "events.sqlite3"))
    snapshot = FrozenRuntimeSnapshot(
        snapshot_id="frozen-coin", runtime_scope="cdecr:US:COIN", epoch_id="frozen-epoch",
        ticker="COIN", market="US", as_of=datetime(2026, 11, 24, tzinfo=UTC),
        atomics=[FrozenRuntimeAtomic(
            runtime_atomic_id=f"A{i}", version=1, proposition=f"Coinbase disclosure fact {i}.",
            time="2026-11-23", entities=["Coinbase"], assertion_state="ACTUAL",
        ) for i in range(1, count + 1)],
    )
    batch = service.delta_compiler.compile(snapshot)
    frozen_root, manifest = service.views.materialize_frozen_view(
        run_root=tmp_path / "local" / "original-run", run_id="original-run",
        mode="INITIALIZE", batches=[batch], as_of=snapshot.as_of,
    )
    workspace = AsyncLocalWorkspace(tmp_path / "remote")
    worker = SurveyWorker(workspace)
    runner = RemoteEventLibraryInitializer(
        worker=worker, workspace=workspace, service=service,
        local_workspace_root=tmp_path / "local", timeout_seconds=1800, **options,
    )
    arguments = dict(
        run_id=manifest.run_id, phase=runner._phases(batch)[0], manifest=manifest,
        cutoff_at=snapshot.as_of, thread_id=None, mode="INITIALIZE", frozen_root=frozen_root,
        allowed_detail_ids=[], review_candidates=[], expected_final=False, batch=batch,
    )
    return runner, worker, arguments


@pytest.mark.asyncio
async def test_850_delta_survey_is_bounded_and_globally_synthesized(tmp_path):
    runner, worker, arguments = setup_survey(tmp_path, 850)
    original_batch = arguments["batch"].model_dump_json()
    original_manifest = arguments["manifest"].model_dump_json()
    await runner._execute_phase(**arguments)
    parts = worker.tasks[:-1]
    assert len(parts) == 9
    assert max(len(t["assigned_delta_ids"]) for t in parts) <= 100
    assert sum(len(t["assigned_delta_ids"]) for t in parts) == 850
    synthesis = worker.tasks[-1]
    assert synthesis["attempt_id"] == "o2-survey"
    assert synthesis["metadata"]["survey"]["mode"] == "SYNTHESIS"
    assert len(synthesis["assigned_delta_ids"]) == 850
    assert all(r.thread_id is None and r.run_id == "original-run" for r in worker.requests)
    assert all((r.model, r.effort) == ("gpt-6-luna", "max") for r in worker.requests)
    assert all(r.cutoff_at == arguments["cutoff_at"] for r in worker.requests)
    assert [r.timeout_seconds for r in worker.requests] == [1800] * 9 + [7200]
    assert arguments["batch"].model_dump_json() == original_batch
    assert arguments["manifest"].model_dump_json() == original_manifest
    assert arguments["phase"]["prior_attempt_paths"] == []
    assert all(not any(p.endswith("pending_atomics.jsonl") for p in t["required_frozen_paths"])
               for t in worker.tasks)
    assert all("schemas/date_resolution_ledger.schema.json" in " ".join(t["required_frozen_paths"])
               for t in worker.tasks)


@pytest.mark.asyncio
async def test_token_budget_also_bounds_survey_passes(tmp_path):
    runner, worker, arguments = setup_survey(tmp_path, 4, wave_token_budget=200)
    await runner._execute_phase(**arguments)
    assert len(worker.tasks) > 2
    assert worker.tasks[-1]["metadata"]["survey"]["mode"] == "SYNTHESIS"


@pytest.mark.asyncio
@pytest.mark.parametrize("defect,expected_error", [
    ("omit_id", "survey catalog must cover exactly"),
    ("omit_date_id", "survey date ledger must cover exactly"),
    ("empty_occurrence", "survey occurrence ledger is empty"),
])
async def test_incomplete_pass_blocks_global_synthesis(tmp_path, defect, expected_error):
    runner, worker, arguments = setup_survey(tmp_path, 101)
    setattr(worker, defect, True if defect == "empty_occurrence" else "D1")
    with pytest.raises(ValueError, match=expected_error):
        await runner._execute_phase(**arguments)
    assert len(worker.requests) == 1


@pytest.mark.asyncio
async def test_interrupted_survey_reuses_completed_durable_pass(tmp_path):
    runner, worker, arguments = setup_survey(tmp_path, 201)
    worker.cancel_part = "o2-survey-part-002"
    upstream_calls = []

    class Adapter:
        async def reconcile(self, context):
            return await self.execute(context)

        async def execute(self, context):
            if context.node.key == "d1":
                upstream_calls.append(context.node.execution_id)
                return NodeResult()
            await runner._execute_phase(**arguments)
            return NodeResult()

    repository = InitializationRepository(tmp_path / "control.sqlite3")
    run = repository.submit("COIN", arguments["cutoff_at"], [
        NodeSpec(key="d1", block="D1"),
        NodeSpec(key="o2", block="O2", dependencies=["d1"]),
    ])
    lease = repository.claim("interrupted-owner")
    assert lease is not None
    with pytest.raises(asyncio.CancelledError):
        await InitializationWorker(repository, lambda _: Adapter()).run_claimed(lease)
    completed = repository.node(run.initialization_id, "o2.o2-survey-part-001")
    assert completed.status == "SUCCEEDED"
    repository.release_lease(lease)
    result = await InitializationWorker(repository, lambda _: Adapter()).run_once()
    assert result.status == "SUCCEEDED"
    assert [r.attempt_id for r in worker.requests].count("o2-survey-part-001") == 1
    replayed = repository.node(run.initialization_id, "o2.o2-survey-part-001")
    assert replayed.execution_id == completed.execution_id
    assert repository.get(run.initialization_id).research_cutoff_at == run.research_cutoff_at
    assert upstream_calls == [repository.node(run.initialization_id, "d1").execution_id]
    assert worker.requests[-1].attempt_id == "o2-survey"


@pytest.mark.parametrize("count,expected", [(0, 1800), (100, 1800), (101, 3600), (850, 7200)])
def test_whole_batch_stages_and_repairs_receive_bounded_workload_budget(tmp_path, count, expected):
    runner, _, arguments = setup_survey(tmp_path, 1)
    request = runner._worker_request(
        run_id="original-run", ticker="COIN", attempt_id="o2-global-reconciliation",
        cutoff_at=arguments["cutoff_at"], thread_id="existing-thread", work_item_count=count,
    )
    assert request.timeout_seconds == expected
    assert request.thread_id == "existing-thread"


@pytest.mark.asyncio
@pytest.mark.parametrize("role,status", [
    ("EVENT_OCCURRENCE", "GENUINELY_PERIOD_WIDE"),
    ("FACT_OCCURRENCE", "UNRESOLVED"),
])
async def test_survey_accepts_hypotheses_before_formal_ids_exist(tmp_path, role, status):
    from doxagent.event_library.contracts import DateResolutionLedgerEntry
    runner, _, arguments = setup_survey(tmp_path, 1)
    phase = arguments["phase"]
    row = dict(delta_id="D1", semantic_role=role, status=status)
    prefix = f"attempts/{phase['attempt_id']}/output/work"
    for name, content in {
        "delta_catalog.json": json.dumps({"D1": "hypothesis"}),
        "occurrence_ledger.md": "Unassigned occurrence hypothesis",
        "date_resolution_ledger.jsonl": json.dumps(row),
    }.items():
        await runner.workspace.write_text(arguments["run_id"], f"{prefix}/{name}", content)
    await runner._validate_survey_catalog(run_id=arguments["run_id"], phase=phase)
    with pytest.raises(ValueError):
        DateResolutionLedgerEntry.model_validate(row)
    row.update(status="RESOLVED", selected_date=None)
    await runner.workspace.write_text(arguments["run_id"],
        f"{prefix}/date_resolution_ledger.jsonl", json.dumps(row))
    with pytest.raises(ValueError, match="requires selected_date"):
        await runner._validate_survey_catalog(run_id=arguments["run_id"], phase=phase)


@pytest.mark.asyncio
async def test_validator_repair_reuses_succeeded_dispatch_without_research(tmp_path, monkeypatch):
    runner, worker, arguments = setup_survey(tmp_path, 101)
    original = runner._validate_survey_catalog
    async def old_validator(**kwargs):
        raise ValueError("formal IDs cannot exist during survey")
    monkeypatch.setattr(runner, "_validate_survey_catalog", old_validator)
    class Adapter:
        async def reconcile(self, context):
            return await self.execute(context)
        async def execute(self, context):
            if context.node.key == "d1":
                return NodeResult()
            await runner._execute_phase(**arguments)
            return NodeResult()
    repo = InitializationRepository(tmp_path / "control.sqlite3")
    run = repo.submit("COIN", arguments["cutoff_at"], [
        NodeSpec(key="d1", block="D1"),
        NodeSpec(key="o2", block="O2", dependencies=["d1"]),
    ])
    failed = await InitializationWorker(repo, lambda _: Adapter()).run_once()
    assert failed.status == "FAILED"
    upstream = repo.node(run.initialization_id, "d1").model_dump_json()
    assert len(worker.requests) == 1
    monkeypatch.setattr(runner, "_validate_survey_catalog", original)
    repo.resume(run.initialization_id, reason="repair survey-only identity validation")
    result = await InitializationWorker(repo, lambda _: Adapter()).run_once()
    assert result.status == "SUCCEEDED"
    assert sum(r.attempt_id == "o2-survey-part-001" for r in worker.requests) == 1
    recovered = repo.node(run.initialization_id, "o2.o2-survey-part-001")
    assert recovered.receipt["artifact_recovery"]["reason"] == "SUCCEEDED_DISPATCH_REVALIDATED"
    assert repo.node(run.initialization_id, "d1").model_dump_json() == upstream
    assert repo.get(run.initialization_id).research_cutoff_at == run.research_cutoff_at
