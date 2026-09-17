"""Offline elastic-dispatch/recovery tests: no Codex executable or model calls."""

import asyncio
import json
import threading
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from doxagent.codex_runtime.schema import CodexAgentRole, CodexD1Node, ResearchLane
from doxagent.codex_worker.capsules import CapsuleError
from doxagent.codex_worker.io_budget import DiskBudget
from doxagent.codex_worker.job_store import JobStore
from doxagent.codex_worker.jobs import QueueHighWatermark, WorkerJobManager, _execution_lane
from doxagent.codex_worker.result_receipt import commit, receipt_path
from doxagent.codex_worker.schema import WorkerJob, WorkerRunRequest
from doxagent.codex_worker.sdk_runtime import WorkerTurnResult, _SdkTurnHandle
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.mcp.resource_budget import tool_budget


def request(index=0, **updates):
    return WorkerRunRequest(
        run_id=f"run-{index}",
        ticker="MU",
        node=CodexD1Node.C1,
        agent_role=CodexAgentRole.C1,
        attempt_id=f"attempt-{index}",
        prompt="offline",
        output_schema={},
        idempotency_key=f"job-{index}",
        **updates,
    )


class Runtime:
    def __init__(self, *, failing=False, quarantine=False):
        self.started = []
        self.gate = asyncio.Event()
        self.clean_gate = asyncio.Event()
        self.clean_gate.set()
        self.cleaning = asyncio.Event()
        self.failing, self.quarantine = failing, quarantine
        self.current = self.peak = 0

    async def start(self, req, cwd):
        self.started.append(req)
        self.current += 1
        self.peak = max(self.peak, self.current)
        return self

    async def run(self):
        await self.gate.wait()
        if self.failing:
            raise CapsuleError("WORKER_PROCESS_EXITED", "offline simulated crash")
        return WorkerTurnResult("thread", "turn", "completed", "{}")

    async def interrupt(self):
        pass

    async def release(self, req):
        self.cleaning.set()
        await self.clean_gate.wait()
        if self.quarantine:
            raise RuntimeError("offline stuck process")
        self.current -= 1


class SlowStartRuntime(Runtime):
    def __init__(self):
        super().__init__()
        self.start_gate = asyncio.Event()

    async def start(self, req, cwd):
        await self.start_gate.wait()
        return await super().start(req, cwd)


async def settle():
    for _ in range(12):
        await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_burst_20_has_no_fixed_active_capacity_and_queue_is_durable(tmp_path):
    runtime = Runtime()
    manager = WorkerJobManager(
        runtime,
        LocalWorkspaceStore(tmp_path),
        # Freeze the automatic ramp so this test can advance each launch wave
        # deterministically without depending on Windows filesystem timing.
        launch_interval_seconds=60,
    )
    try:
        jobs = await asyncio.gather(*(manager.submit(request(i)) for i in range(20)))
        await settle()
        assert runtime.peak == 3
        assert len(runtime.started) == 3
        assert sum(manager.get(j.job_id).status == "queued" for j in jobs) == 17
        assert manager.store.request(jobs[-1].job_id).attempt_id == "attempt-19"
        for _ in range(20):
            manager._next_launch_at = 0
            manager._schedule()
            await settle()
            if len(runtime.started) == 20:
                break
        assert len(runtime.started) == 20
        assert runtime.peak == 20
        runtime.gate.set()
        for _ in range(40):
            await settle()
            manager._schedule()
            if all(manager.get(j.job_id).status == "succeeded" for j in jobs):
                break
        assert all(manager.get(j.job_id).status == "succeeded" for j in jobs)
        assert runtime.peak == 20
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_queued_jobs_do_not_create_tasks_while_cold_starts_are_full(tmp_path):
    runtime = SlowStartRuntime()
    manager = WorkerJobManager(
        runtime,
        LocalWorkspaceStore(tmp_path),
        launch_interval_seconds=0.05,
        startup_concurrency=3,
    )
    try:
        jobs = await asyncio.gather(*(manager.submit(request(i)) for i in range(10)))
        await settle()
        assert len(manager._tasks) == 3
        assert len(manager._starting) == 3
        assert sum(manager.get(job.job_id).status == "queued" for job in jobs) == 7
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_same_thread_serial_and_queue_full_no_dispatch(tmp_path):
    runtime = Runtime()
    manager = WorkerJobManager(
        runtime,
        LocalWorkspaceStore(tmp_path),
        queue_high_watermark=2,
    )
    try:
        await manager.submit(request(0, thread_id="shared"))
        await settle()
        await manager.submit(request(1, thread_id="shared"))
        await manager.submit(request(2, thread_id="shared"))
        with pytest.raises(QueueHighWatermark):
            await manager.submit(request(3))
        await settle()
        assert len(runtime.started) == 1
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_native_subagent_does_not_reserve_global_weight(tmp_path):
    runtime = Runtime()
    manager = WorkerJobManager(runtime, LocalWorkspaceStore(tmp_path), subagents=1)
    try:
        await manager.submit(request(0, allow_subagents=True, max_subagents=2))
        await manager.submit(request(1))
        manager._next_launch_at = 0
        manager._schedule()
        await settle()
        assert len(runtime.started) == 2
        assert runtime.started[0].max_subagents == 1
        assert len(manager._active) == 2
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_pressure_pauses_background_but_realtime_keeps_launching(tmp_path):
    safety = tmp_path / "safety.json"
    safety.write_text(
        json.dumps({"level": "PRESSURE", "observed_at": time.time(), "reasons": ["psi"]}),
        encoding="utf-8",
    )
    runtime = Runtime()
    manager = WorkerJobManager(
        runtime, LocalWorkspaceStore(tmp_path / "worker"), safety_state_path=safety
    )
    try:
        background = await manager.submit(request(0, execution_lane="background"))
        await settle()
        assert runtime.started == []
        assert manager.get(background.job_id).wait_reason == "SAFETY_PRESSURE"

        realtime = await manager.submit(request(1, execution_lane="realtime"))
        await settle()
        assert [item.run_id for item in runtime.started] == ["run-1"]
        assert manager.get(realtime.job_id).status == "running"

        safety.write_text(
            json.dumps({"level": "NORMAL", "observed_at": time.time(), "reasons": []}),
            encoding="utf-8",
        )
        manager._next_launch_at = 0
        manager._schedule()
        await settle()
        assert {item.run_id for item in runtime.started} == {"run-0", "run-1"}
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_aged_background_gets_one_position_in_realtime_wave(tmp_path):
    runtime = Runtime()
    manager = WorkerJobManager(
        runtime,
        LocalWorkspaceStore(tmp_path),
        launch_interval_seconds=60,
        background_aging_seconds=1,
    )
    try:
        await manager.submit(request(0, execution_lane="realtime"))
        realtime = [
            await manager.submit(request(i, execution_lane="realtime")) for i in range(1, 4)
        ]
        background = await manager.submit(request(9, execution_lane="background"))
        old = manager.get(background.job_id).model_copy(
            update={"created_at": manager.get(background.job_id).created_at - timedelta(seconds=5)}
        )
        manager.store.save(old)
        manager._next_launch_at = 0
        manager._schedule()
        await settle()
        assert background.job_id in manager._tasks
        assert sum(job.job_id in manager._tasks for job in realtime) == 2
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_cancellation_cleanup_quarantine_does_not_block_other_execution(tmp_path):
    runtime = Runtime(quarantine=True)
    manager = WorkerJobManager(runtime, LocalWorkspaceStore(tmp_path))
    try:
        first = await manager.submit(request(0))
        await manager.submit(request(1))
        await settle()
        runtime.clean_gate.clear()
        cancelled = asyncio.create_task(manager.cancel(first.job_id))
        await runtime.cleaning.wait()
        manager._next_launch_at = 0
        manager._schedule()
        await settle()
        assert len(runtime.started) == 2
        assert manager.get(first.job_id).execution_phase == "CLEANING"
        runtime.clean_gate.set()
        await cancelled
        manager._schedule()
        assert len(runtime.started) == 2
        assert manager.get(first.job_id).execution_phase == "CLEANUP_QUARANTINED"
    finally:
        runtime.clean_gate.set()
        await manager.close()


@pytest.mark.asyncio
async def test_immediate_cancel_does_not_leak_slot(tmp_path):
    manager = WorkerJobManager(Runtime(), LocalWorkspaceStore(tmp_path))
    try:
        job = await manager.submit(request())
        await manager.cancel(job.job_id)
        assert not manager._active
        assert manager.get(job.job_id).status == "cancelled"
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_infrastructure_budget_two_then_manual_and_no_new_request(tmp_path):
    runtime = Runtime(failing=True)
    runtime.gate.set()
    manager = WorkerJobManager(runtime, LocalWorkspaceStore(tmp_path))
    try:
        job = await manager.submit(request())
        for expected in (1, 2, 2):
            await asyncio.gather(*list(manager._tasks.values()))
            await settle()
            current = manager.get(job.job_id)
            assert current.infra_recovery_count == expected
            if len(runtime.started) < 3:
                assert current.status == "queued"
                assert current.finished_at is None
                assert current.execution_phase == "QUEUED"
                manager.store.save(current.model_copy(update={"next_retry_at": 0}))
                manager._next_launch_at = 0
                manager._schedule()
        assert manager.get(job.job_id).error_code == "WORKER_INFRA_RECOVERY_EXHAUSTED"
        assert len({r.idempotency_key for r in runtime.started}) == 1
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_queued_restart_preserves_budget_and_adopts_committed_result(tmp_path):
    workspace = LocalWorkspaceStore(tmp_path)
    safety = tmp_path / "safety.json"
    import time

    safety.write_text(
        '{"level":"CRITICAL","observed_at":' + str(time.time()) + ',"reasons":[]}'
    )
    manager = WorkerJobManager(Runtime(), workspace, safety_state_path=safety)
    req = request()
    job = await manager.submit(req)
    await manager.close()
    restarted = WorkerJobManager(Runtime(), workspace)
    assert restarted.get(job.job_id).status == "queued"
    assert restarted.get(job.job_id).infra_recovery_count == 0
    commit(
        receipt_path(tmp_path, req),
        {
            "status": "completed",
            "thread_id": "saved",
            "turn_id": "saved-turn",
            "final_response": "{}",
        },
    )
    recovered = WorkerJobManager(Runtime(), workspace)
    assert recovered.get(job.job_id).status == "succeeded"
    assert recovered.get(job.job_id).thread_id == "saved"
    await restarted.close()
    await recovered.close()


def test_resource_aware_queue_honors_non_runtime_fairness_turn(tmp_path, monkeypatch):
    store = JobStore(tmp_path)
    runtime_request = request(1, research_lane=ResearchLane.PERSISTENT_RUNTIME)
    init_request = request(2)
    runtime_job = WorkerJob(
        job_id="runtime", run_id=runtime_request.run_id,
        attempt_id=runtime_request.attempt_id, status="queued",
    )
    init_job = WorkerJob(
        job_id="init", run_id=init_request.run_id,
        attempt_id=init_request.attempt_id, status="queued",
    )
    store.save(runtime_job, runtime_request)
    store.save(init_job, init_request)

    assert store.queued(lane="realtime") == ["runtime"]
    assert store.queued(lane="background") == ["init"]


def test_maintenance_is_background_and_runtime_case_is_realtime():
    child = request(3).model_copy(
        update={
            "run_id": "init-rklb-9631e4071e75470a97313eafbbdc51aa-o2-rklb-c8b8aac84957f2e1"
        }
    )
    assert _execution_lane(child) == "background"
    runtime = request(4, research_lane=ResearchLane.PERSISTENT_RUNTIME)
    assert _execution_lane(runtime) == "realtime"
    maintenance = runtime.model_copy(update={"run_id": "runtime-maintain-MU"})
    assert _execution_lane(maintenance) == "background"


@pytest.mark.asyncio
async def test_disk_cancel_retains_real_thread_slot():
    disk = DiskBudget()
    running, release = threading.Event(), threading.Event()

    def work():
        running.set()
        release.wait(5)

    first = asyncio.create_task(disk.run(work))
    try:
        await asyncio.to_thread(running.wait, 2)
        first.cancel()
        second = asyncio.create_task(disk.run(lambda: "done"))
        await settle()
        assert not first.done() and not second.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert await second == "done"
    finally:
        release.set()


@pytest.mark.asyncio
async def test_mcp_shared_lock_slots_do_not_block_controls(tmp_path, monkeypatch):
    monkeypatch.setenv("DOXAGENT_MCP_BUDGET_ROOT", str(tmp_path))
    release = asyncio.Event()
    occupied = 0
    peak = 0

    async def tool():
        nonlocal occupied, peak
        async with tool_budget():
            occupied += 1
            peak = max(peak, occupied)
            await release.wait()
            occupied -= 1

    tasks = [asyncio.create_task(tool()) for _ in range(12)]
    await settle()
    assert occupied == peak == 4
    release.set()
    await asyncio.gather(*tasks)
    assert peak == 4


@pytest.mark.asyncio
async def test_sdk_stream_keeps_bounded_tail_and_full_compact_audit(tmp_path):
    from openai_codex.generated.v2_all import (
        ItemCompletedNotification,
        ThreadItem,
        Turn,
        TurnCompletedNotification,
    )

    class Handle:
        id = "turn"

        async def stream(self):
            for index in range(300):
                item = ThreadItem.model_validate(
                    {"type": "webSearch", "id": str(index), "query": "offline", "action": None}
                )
                yield SimpleNamespace(
                    payload=ItemCompletedNotification.model_construct(
                        thread_id="thread", turn_id="turn", item=item
                    )
                )
            yield SimpleNamespace(
                payload=TurnCompletedNotification.model_construct(
                    thread_id="thread",
                    turn=Turn.model_validate(
                        {"id": "turn", "status": "completed", "items": [], "error": None}
                    ),
                )
            )

    path = tmp_path / "audit.jsonl"
    result = await _SdkTurnHandle(thread_id="thread", handle=Handle(), audit_path=path).run()
    assert result.status == "completed"
    assert len(result.telemetry.events) == 256
    assert result.telemetry.events[-1].sequence == 299
    assert len(path.read_text().splitlines()) == 300


@pytest.mark.asyncio
async def test_readiness_is_not_blocked_by_active_execution_count(tmp_path):
    runtime = Runtime()

    async def probe():
        return {"authenticated": True, "models": ["offline"]}

    runtime.probe = probe
    manager = WorkerJobManager(runtime, LocalWorkspaceStore(tmp_path))
    try:
        await manager.submit(request(0))
        await manager.submit(request(1))
        assert (await manager.probe())["authenticated"] is True
    finally:
        await manager.close()


def test_server_resource_envelope():
    import yaml

    root = Path(__file__).resolve().parents[1]
    overlay = yaml.safe_load((root / "deploy/docker-compose.server.yml").read_text())
    services = overlay["services"]
    def mib(value):
        text = str(value).lower()
        return int(float(text[:-1]) * (1024 if text.endswith("g") else 1))

    for service in services.values():
        assert mib(service["memswap_limit"]) >= mib(service["mem_limit"])
        assert 0 < service["pids_limit"] <= 1024
    assert mib(services["codex-worker"]["mem_limit"]) == 8192
    assert "DOXAGENT_CODEX_WORKER_CAPACITY" not in services["codex-worker"]["environment"]
    assert services["codex-worker"]["environment"]["DOXAGENT_CODEX_LAUNCH_WAVE_SIZE"] == "3"
    assert services["v2-initialization"]["environment"]["DOXAGENT_CODEX_D2_MAX_CONCURRENCY"] == "8"


@pytest.mark.asyncio
async def test_capsule_thread_affinity_and_idle_reclaim_without_sdk(tmp_path, monkeypatch):
    from doxagent.codex_worker import capsules

    class FakeCapsule:
        def __init__(self, root):
            self.busy, self.healthy = True, True
            self.thread_id, self.idle_at = None, 0
            self.closed = False

        async def launch(self):
            pass

        async def close(self):
            self.closed = True

    monkeypatch.setattr(capsules, "Capsule", FakeCapsule)
    runtime = capsules.CapsuleRuntime(tmp_path)
    req = request(thread_id="thread", max_subagents=0)
    first = await runtime._take("thread")
    first.thread_id = "thread"
    runtime.active[runtime.key(req)] = first
    await runtime.release(req)
    assert await runtime._take("thread") is first
    runtime.active[runtime.key(req)] = first
    await runtime.release(req)
    assert await runtime.reap_idle(all_idle=True) == 1
    assert first.closed and not runtime.pool


@pytest.mark.asyncio
async def test_http_reconnect_reuses_job_and_request_identity(monkeypatch):
    import json

    import httpx

    from doxagent.codex_runtime.client import HttpCodexWorkerClient

    posts, gets = [], []

    async def transport(req):
        if req.method == "POST":
            posts.append(json.loads(req.content))
            if len(posts) == 1:
                return httpx.Response(503)
            return httpx.Response(
                200,
                json={
                    "job_id": "job",
                    "run_id": "run-0",
                    "attempt_id": "attempt-0",
                    "status": "queued",
                },
            )
        gets.append(str(req.url.path))
        if len(gets) == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "job_id": "job",
                "run_id": "run-0",
                "attempt_id": "attempt-0",
                "status": "succeeded",
            },
        )

    original_sleep = asyncio.sleep

    async def fast_sleep(_seconds):
        await original_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    client = HttpCodexWorkerClient("http://127.0.0.1", "offline")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport)
    )
    try:
        assert (await client.run(request())).status == "succeeded"
        assert len(posts) == 2 and posts[0] == posts[1]
        assert gets == ["/v1/jobs/job", "/v1/jobs/job"]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_infrastructure_exhaustion_does_not_spend_parent_business_retry(tmp_path):
    from datetime import UTC, datetime

    from doxagent.codex_runtime.errors import InfrastructureRecoveryExhausted
    from doxagent.ticker_initialization import InitializationRepository, InitializationWorker
    from doxagent.ticker_initialization.catalog import default_plan

    repo = InitializationRepository(tmp_path / "control.db")
    run = repo.submit("MU", datetime.now(UTC), default_plan())
    calls = []

    class Adapter:
        async def reconcile(self, context):
            return None

        async def execute(self, context):
            calls.append(context.node.key)
            raise InfrastructureRecoveryExhausted("offline infrastructure exhausted")

    result = await InitializationWorker(repo, lambda _: Adapter()).run_once()
    assert result.status == "FAILED" and result.manual_resume_required
    assert calls and len(calls) == len(set(calls))
    assert all(n.ordinal <= 1 for n in repo.nodes(run.initialization_id))
