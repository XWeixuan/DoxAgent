"""Offline admission/pressure/recovery tests: no Codex executable or model calls."""

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from doxagent.codex_runtime.schema import CodexAgentRole, CodexD1Node
from doxagent.codex_worker.capsules import CapsuleError
from doxagent.codex_worker.io_budget import DiskBudget
from doxagent.codex_worker.jobs import CapacityBusy, WorkerJobManager
from doxagent.codex_worker.pressure import PressureController, PressureSample
from doxagent.codex_worker.result_receipt import commit, receipt_path
from doxagent.codex_worker.schema import WorkerRunRequest
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


async def settle():
    for _ in range(12):
        await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_burst_20_is_two_not_serial_and_queue_is_durable(tmp_path):
    runtime = Runtime()
    manager = WorkerJobManager(runtime, LocalWorkspaceStore(tmp_path))
    try:
        jobs = await asyncio.gather(*(manager.submit(request(i)) for i in range(20)))
        await settle()
        assert runtime.peak == 2
        assert len(runtime.started) == 2
        assert sum(manager.get(j.job_id).status == "queued" for j in jobs) == 18
        assert manager.store.request(jobs[-1].job_id).attempt_id == "attempt-19"
        runtime.gate.set()
        for _ in range(40):
            await settle()
            manager._schedule()
            if all(manager.get(j.job_id).status == "succeeded" for j in jobs):
                break
        assert all(manager.get(j.job_id).status == "succeeded" for j in jobs)
        assert runtime.peak == 2
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_same_thread_serial_and_queue_full_no_dispatch(tmp_path):
    runtime = Runtime()
    manager = WorkerJobManager(runtime, LocalWorkspaceStore(tmp_path), queue_limit=2)
    try:
        await manager.submit(request(0, thread_id="shared"))
        await settle()
        await manager.submit(request(1, thread_id="shared"))
        await manager.submit(request(2, thread_id="shared"))
        with pytest.raises(CapacityBusy):
            await manager.submit(request(3))
        await settle()
        assert len(runtime.started) == 1
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_native_subagent_reserves_two_slots_and_default_is_off(tmp_path):
    runtime = Runtime()
    manager = WorkerJobManager(runtime, LocalWorkspaceStore(tmp_path), subagents=1)
    try:
        await manager.submit(request(0, allow_subagents=True, max_subagents=2))
        await manager.submit(request(1))
        await settle()
        assert len(runtime.started) == 1
        assert runtime.started[0].max_subagents == 1
        assert sum(w for _, w in manager._active.values()) == 2
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_cancellation_holds_slot_until_cleanup_and_quarantine(tmp_path):
    runtime = Runtime(quarantine=True)
    manager = WorkerJobManager(runtime, LocalWorkspaceStore(tmp_path), capacity=1)
    try:
        first = await manager.submit(request(0))
        await manager.submit(request(1))
        await settle()
        runtime.clean_gate.clear()
        cancelled = asyncio.create_task(manager.cancel(first.job_id))
        await runtime.cleaning.wait()
        manager._schedule()
        assert len(runtime.started) == 1
        assert manager.get(first.job_id).execution_phase == "CLEANING"
        runtime.clean_gate.set()
        await cancelled
        manager._schedule()
        assert len(runtime.started) == 1
        assert manager.get(first.job_id).execution_phase == "CLEANUP_QUARANTINED"
    finally:
        runtime.clean_gate.set()
        await manager.close()


@pytest.mark.asyncio
async def test_immediate_cancel_does_not_leak_slot(tmp_path):
    manager = WorkerJobManager(Runtime(), LocalWorkspaceStore(tmp_path), capacity=1)
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
                manager._schedule()
        assert manager.get(job.job_id).error_code == "WORKER_INFRA_RECOVERY_EXHAUSTED"
        assert len({r.idempotency_key for r in runtime.started}) == 1
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_queued_restart_preserves_budget_and_adopts_committed_result(tmp_path):
    workspace = LocalWorkspaceStore(tmp_path)
    manager = WorkerJobManager(Runtime(), workspace)
    manager.pressure.paused = True
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


def test_pressure_hysteresis_and_missing_metrics():
    controller = PressureController()
    gib = 1024**3
    controller.update(PressureSample(memory=int(2.9 * gib), available=2 * gib), now=0)
    assert controller.paused and not controller.extreme
    good = PressureSample(memory=2 * gib, available=2 * gib, full=0)
    controller.update(good, now=1)
    controller.update(good, now=30)
    assert controller.paused
    controller.update(good, now=31)
    assert not controller.paused
    controller.update(PressureSample(), now=32)
    assert not controller.paused
    controller.update(PressureSample(memory=int(3.3 * gib)), now=33)
    assert controller.paused and controller.extreme


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
async def test_readiness_does_not_spawn_third_sdk(tmp_path):
    runtime = Runtime()

    async def probe():
        pytest.fail("third SDK must not launch")

    runtime.probe = probe
    manager = WorkerJobManager(runtime, LocalWorkspaceStore(tmp_path))
    try:
        await manager.submit(request(0))
        await manager.submit(request(1))
        assert (await manager.probe())["provider_probe"] == "deferred_capacity"
    finally:
        await manager.close()


def test_server_resource_envelope():
    import yaml

    root = Path(__file__).resolve().parents[1]
    overlay = yaml.safe_load((root / "deploy/docker-compose.server.yml").read_text())
    services = overlay["services"]
    total = 0
    for name, service in services.items():
        assert service["mem_limit"] == service["memswap_limit"]
        assert 0 < service["cpus"] <= 2.75
        assert 0 < service["pids_limit"] <= 512
        if name != "v2-migrate":
            total += int(service["mem_limit"].removesuffix("m"))
    assert total == 6464
    assert services["codex-worker"]["environment"]["DOXAGENT_CODEX_WORKER_CAPACITY"] == "2"
    assert services["v2-initialization"]["environment"]["DOXAGENT_CODEX_D2_MAX_CONCURRENCY"] == "2"


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
