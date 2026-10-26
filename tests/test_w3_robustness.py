"""Fault boundaries: no real model, broker, or production state."""

import asyncio
import json
import os
import subprocess
import sys
import time
from contextlib import closing
from types import SimpleNamespace

import pytest

from doxagent.codex_worker.capsules import Capsule, CapsuleError, CapsuleRuntime
from doxagent.codex_worker.job_store import JobStore
from doxagent.codex_worker.jobs import WorkerJobManager
from doxagent.codex_worker.schema import WorkerJob
from doxagent.codex_worker.startup_admission import PriorityGate, StartupAdmission
from doxagent.codex_worker.thread_lifecycle import (
    AppServerThreads,
    LifecycleCleanupError,
    WorkerThreadLifecycle,
)
from doxagent.codex_worker.w3_ready import W3ReadySlots
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from tests.test_worker_resource_budget import Runtime, request


async def until(predicate, timeout=3):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancels", [2, 4])
async def test_gate_waiters_repeated_cancel_have_no_orphan_lock_or_overlap(cancels):
    for _ in range(20):
        gate, entered, leave = PriorityGate(), asyncio.Event(), asyncio.Event()
        active = maximum = 0

        async def work(index, gate=gate, entered=entered, leave=leave):
            nonlocal active, maximum
            async with gate.acquire(timeout=0.3):
                active += 1
                maximum = max(maximum, active)
                try:
                    if index == 0:
                        entered.set()
                        await leave.wait()
                    await asyncio.sleep(0)
                finally:
                    active -= 1

        tasks = [asyncio.create_task(work(i)) for i in range(18)]
        await entered.wait()
        for _ in range(cancels):
            for task in tasks[1::2]:
                task.cancel()
            await asyncio.sleep(0)
        leave.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 1)
        assert all(r is None or isinstance(r, asyncio.CancelledError) for r in results)
        assert maximum == 1 and not gate.active and not gate.waiters
        assert not gate.condition.locked()
        async with gate.acquire(timeout=0.03):
            pass


@pytest.mark.asyncio
async def test_admission_initial_condition_lock_is_inside_timeout():
    gate = PriorityGate()
    await gate.condition.acquire()
    try:
        with pytest.raises(TimeoutError):
            async with gate.acquire(timeout=0.02):
                pytest.fail("initial lock was not acquired")
        assert not gate.waiters and not gate.active
    finally:
        gate.condition.release()


@pytest.mark.asyncio
async def test_event_consumer_backpressure_and_cancel_never_hold_notification_lock(tmp_path):
    manager = WorkerJobManager(SimpleNamespace(), LocalWorkspaceStore(tmp_path))
    manager.EVENT_HEARTBEAT_SECONDS = 0.01
    manager.store.save(WorkerJob(job_id="job", run_id="run", attempt_id="one", status="running"))
    stream = manager.events("job")
    try:
        assert (await anext(stream)).event_type == "heartbeat"
        assert not manager._condition.locked()
        await asyncio.wait_for(manager._emit("job", "next", {}), 0.2)
        # Consume persisted event, then repeatedly cancel a subscriber waiting for more.
        assert (await anext(stream)).event_type == "next"
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await asyncio.wait_for(manager._emit("job", "other-job-can-finish", {}), 0.2)
        assert not manager._condition.locked()
    finally:
        await stream.aclose()
        await manager.close()


def blocked_capsule(tmp_path, monkeypatch):
    draining, exited = asyncio.Event(), asyncio.Event()
    terminations = []

    async def drain():
        draining.set()
        await asyncio.Event().wait()

    async def wait():
        await exited.wait()

    process = SimpleNamespace(
        pid=123456789,
        returncode=None,
        stdin=SimpleNamespace(write=lambda _: None, drain=drain),
        wait=wait,
    )
    capsule = Capsule(tmp_path)
    capsule.GRACEFUL_CLOSE_SECONDS = 0.02
    capsule.process = process

    async def terminate(proc, identity):
        assert proc is process and identity == capsule.identity
        terminations.append(identity)
        process.returncode = 0
        exited.set()

    monkeypatch.setattr("doxagent.codex_worker.capsules.terminate_owned", terminate)
    return capsule, draining, terminations


@pytest.mark.asyncio
async def test_close_is_single_owned_bounded_and_survives_repeated_cancellation(
    tmp_path, monkeypatch
):
    capsule, draining, terminations = blocked_capsule(tmp_path, monkeypatch)
    callers = [asyncio.create_task(capsule.close()) for _ in range(3)]
    await draining.wait()
    for _ in range(3):
        callers[0].cancel()
        await asyncio.sleep(0)
    results = await asyncio.wait_for(asyncio.gather(*callers, return_exceptions=True), 0.5)
    assert isinstance(results[0], asyncio.CancelledError)
    assert results[1:] == [None, None]
    assert capsule.cleanup_state == "CLOSED" and len(terminations) == 1
    await capsule.close()
    assert len(terminations) == 1


@pytest.mark.asyncio
async def test_failed_boot_keeps_cold_until_owned_cleanup_finishes(tmp_path, monkeypatch):
    capsule, draining, terminations = blocked_capsule(tmp_path, monkeypatch)
    runtime = CapsuleRuntime(tmp_path)

    async def launch():
        raise TimeoutError("synthetic boot")

    capsule.launch = launch
    monkeypatch.setattr("doxagent.codex_worker.capsules.Capsule", lambda _: capsule)
    boot = asyncio.create_task(runtime._take(None))
    await draining.wait()
    assert runtime.admission.cold.active
    boot.cancel()
    await asyncio.sleep(0)
    boot.cancel()
    await asyncio.gather(boot, return_exceptions=True)
    assert terminations and capsule.process.returncode == 0
    assert not runtime.pool and not runtime.admission.cold.active
    assert not runtime.admission.quarantined


@pytest.mark.asyncio
async def test_cleanup_deadline_quarantines_and_later_reconciles_original_owner(
    tmp_path, monkeypatch
):
    capsule, _, _ = blocked_capsule(tmp_path, monkeypatch)
    capsule.CLEANUP_TIMEOUT_SECONDS = 0.12

    async def stuck(*_):
        await asyncio.Event().wait()

    monkeypatch.setattr("doxagent.codex_worker.capsules.terminate_owned", stuck)
    runtime = CapsuleRuntime(tmp_path)
    runtime.pool.append(capsule)
    started = time.monotonic()
    with pytest.raises(CapsuleError):
        await runtime._dispose(capsule, cold=True)
    assert time.monotonic() - started < 1
    assert capsule.cleanup_state == "QUARANTINED"
    assert capsule.identity in runtime.admission.quarantined
    assert not runtime.live(capsule)

    async def ended(*_):
        capsule.process.returncode = 0

    monkeypatch.setattr("doxagent.codex_worker.capsules.terminate_owned", ended)
    capsule.cleanup_retry_at = 0
    await runtime.reap_idle()
    assert not runtime.pool and not runtime.admission.quarantined


@pytest.mark.asyncio
async def test_one_reap_failure_does_not_strand_rest_of_batch(tmp_path):
    runtime, closed = CapsuleRuntime(tmp_path), []

    def item(index):
        async def close():
            closed.append(index)
            if index == 0:
                raise OSError("one close fault")

        return SimpleNamespace(
            identity=str(index),
            thread_id=str(index),
            busy=False,
            healthy=True,
            process=None,
            close=close,
        )

    runtime.pool = [item(i) for i in range(3)]
    with pytest.raises(OSError):
        await runtime.reap_idle(all_idle=True)
    assert closed == [0, 1, 2]
    assert len(runtime.pool) == 1 and runtime.pool[0].cleanup_state == "QUARANTINED"


@pytest.mark.asyncio
async def test_cancelled_batch_releases_unattempted_reservations(tmp_path):
    runtime, entered, release = CapsuleRuntime(tmp_path), asyncio.Event(), asyncio.Event()

    async def close():
        entered.set()
        await release.wait()

    first = SimpleNamespace(
        identity="one", thread_id="one", busy=False, healthy=True, process=None, close=close
    )
    second = SimpleNamespace(
        identity="two", thread_id="two", busy=False, healthy=True, process=None, close=close
    )
    runtime.pool = [first, second]
    batch = asyncio.create_task(runtime.reap_idle(all_idle=True))
    await entered.wait()
    batch.cancel()
    await asyncio.gather(batch, return_exceptions=True)
    assert not second.busy and first.cleanup_state == "QUARANTINED"


@pytest.mark.asyncio
async def test_dispatcher_recovers_without_new_http_request_or_duplicate_job(tmp_path):
    class Flaky(Runtime):
        recoveries = 0

        async def recover(self):
            self.recoveries += 1
            if self.recoveries == 1:
                raise OSError("temporary recovery read fault")

    runtime = Flaky()
    runtime.gate.set()
    manager = WorkerJobManager(runtime, LocalWorkspaceStore(tmp_path))
    job = await manager.submit(request())
    try:
        await until(lambda: manager.get(job.job_id).status == "succeeded")
        assert runtime.recoveries == 2 and len(runtime.started) == 1
        assert manager.dispatcher_status()["ready"]
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_dispatcher_tick_fault_and_observation_gc_failure_are_isolated(tmp_path, monkeypatch):
    runtime = Runtime()
    runtime.gate.set()
    manager = WorkerJobManager(runtime, LocalWorkspaceStore(tmp_path))
    original = manager._schedule
    calls = 0

    def schedule():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("one scheduling read fault")
        return original()

    def maintenance(*_):
        raise OSError("optional GC failure")

    monkeypatch.setattr(manager, "_schedule", schedule)
    monkeypatch.setattr("doxagent.codex_worker.w3_turn_probe.maintenance", maintenance)
    manager.start()
    try:
        await until(lambda: manager.dispatcher_status()["ready"])
        job = await manager.submit(request())
        await until(lambda: manager.get(job.job_id).status == "succeeded")
        assert calls >= 2 and len(runtime.started) == 1
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_repeated_job_cancel_waits_finalizer_and_does_not_repeat_turn(tmp_path):
    runtime = Runtime()
    runtime.clean_gate.clear()
    manager = WorkerJobManager(runtime, LocalWorkspaceStore(tmp_path))
    job = await manager.submit(request())
    try:
        await until(lambda: bool(runtime.started))
        owner = manager._tasks[job.job_id]
        owner.cancel()
        await runtime.cleaning.wait()
        owner.cancel()
        await asyncio.sleep(0)
        owner.cancel()
        runtime.clean_gate.set()
        await asyncio.gather(owner, return_exceptions=True)
        assert manager.get(job.job_id).status == "cancelled"
        assert not manager._active and len(runtime.started) == 1
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_finalization_write_fault_retries_settlement_not_model(tmp_path, monkeypatch):
    runtime = Runtime()
    runtime.gate.set()
    manager = WorkerJobManager(runtime, LocalWorkspaceStore(tmp_path))
    original = manager._update
    failed = False

    async def update(job_id, **values):
        nonlocal failed
        await original(job_id, **values)
        if values.get("execution_phase") == "SETTLED" and not failed:
            failed = True
            raise OSError("lost mirror/event acknowledgement after SQLite commit")

    monkeypatch.setattr(manager, "_update", update)
    job = await manager.submit(request())
    try:
        await until(lambda: failed)
        await until(lambda: not manager._active)
        assert manager.get(job.job_id).status == "succeeded"
        assert len(runtime.started) == 1 and not manager._pending_finish
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_terminal_ack_before_settlement_retry_releases_admission(tmp_path, monkeypatch):
    runtime = Runtime()
    runtime.gate.set()
    manager = WorkerJobManager(runtime, LocalWorkspaceStore(tmp_path))
    original, failed = manager._update, False

    async def update(job_id, **values):
        nonlocal failed
        await original(job_id, **values)
        if values.get("execution_phase") == "SETTLED" and not failed:
            failed = True
            manager.store.acknowledge(job_id)
            raise OSError("ACK raced with lost terminal event acknowledgement")

    monkeypatch.setattr(manager, "_update", update)
    job = await manager.submit(request(task_phase="w3_template"))
    try:
        await until(lambda: failed)
        await until(lambda: not manager._active)
        assert manager.store.request(job.job_id) is None
        assert manager.get(job.job_id).status == "succeeded"
        assert len(runtime.started) == 1 and not manager._pending_finish
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_settlement_retry_does_not_spend_second_infrastructure_retry(tmp_path, monkeypatch):
    runtime = Runtime(failing=True)
    runtime.gate.set()
    manager = WorkerJobManager(runtime, LocalWorkspaceStore(tmp_path))
    original, failed = manager._update, False

    async def update(job_id, **values):
        nonlocal failed
        await original(job_id, **values)
        if values.get("wait_reason") == "INFRASTRUCTURE_RECOVERY" and not failed:
            failed = True
            raise OSError("lost mirror acknowledgement after retry committed")

    monkeypatch.setattr(manager, "_update", update)
    job = await manager.submit(request())
    try:
        await until(lambda: failed)
        await until(lambda: job.job_id not in manager._tasks)
        # Even if the recovery deadline becomes due during a storage outage,
        # the unacknowledged finalizer owns this job until settlement succeeds.
        await original(job.job_id, next_retry_at=0)
        manager._next_launch_at = 0
        manager._schedule()
        assert job.job_id not in manager._tasks
        await until(lambda: not manager._active)
        current = manager.get(job.job_id)
        assert current.status == "queued" and current.infra_recovery_count == 1
        assert len(runtime.started) == 1 and not manager._pending_finish
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_ack_does_not_lose_quarantined_physical_owner(tmp_path):
    class Quarantined(Runtime):
        key = staticmethod(CapsuleRuntime.key)
        gone = False

        def cleanup_complete_for(self, req):
            return self.gone

        def cleanup_complete_key(self, key):
            assert key == self.key(request(task_phase="w3_template"))
            return self.gone

    runtime = Quarantined(quarantine=True)
    runtime.gate.set()
    manager = WorkerJobManager(runtime, LocalWorkspaceStore(tmp_path))
    job = await manager.submit(request(task_phase="w3_template"))
    try:
        await until(lambda: manager.get(job.job_id).execution_phase == "CLEANUP_QUARANTINED")
        assert job.job_id in manager._active
        manager.store.acknowledge(job.job_id)
        await manager._reconcile_cleanups()
        assert job.job_id in manager._active
        runtime.gone = True
        await manager._reconcile_cleanups()
        assert not manager._active and not manager._cleanup_quarantines
        assert len(runtime.started) == 1
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_helper_close_retains_process_and_is_single_owned(tmp_path, monkeypatch):
    helper = AppServerThreads()
    helper.root = tmp_path
    closing, finish = asyncio.Event(), asyncio.Event()
    proc = SimpleNamespace(pid=123456789, poll=lambda: None)
    client = SimpleNamespace()

    class Client:
        _sync = SimpleNamespace(_proc=proc)

        async def close(self):
            self._sync._proc = None  # SDK clears its public process reference.
            closing.set()
            await finish.wait()

    client = Client()
    helper.client = client
    helper.clients.add(client)
    helper.identities[client] = dict(identity="owned-helper", sdk_ready=True)
    killed = []

    async def terminate(process, identity, *, kill_group):
        assert process.process is proc and identity == "owned-helper"
        assert kill_group is False
        killed.append(identity)

    monkeypatch.setattr("doxagent.codex_worker.process_tree.terminate_owned", terminate)
    callers = [asyncio.create_task(helper._close_client(client)) for _ in range(3)]
    await closing.wait()
    for _ in range(3):
        callers[0].cancel()
        await asyncio.sleep(0)
    finish.set()
    results = await asyncio.wait_for(asyncio.gather(*callers, return_exceptions=True), 1)
    assert isinstance(results[0], asyncio.CancelledError)
    assert results[1:] == [None, None] and killed == ["owned-helper"]
    assert not helper.clients and not helper.identities and helper.client is None


@pytest.mark.asyncio
@pytest.mark.parametrize("sdk_ready", [False, True])
async def test_helper_quarantine_preserves_owner_until_confirmed_gone(
    tmp_path, monkeypatch, sdk_ready
):
    helper = AppServerThreads()
    helper.root, helper.admission = tmp_path, StartupAdmission()
    gone = False
    proc = SimpleNamespace(pid=123456789, poll=lambda: 0 if gone else None)

    class Client:
        _sync = SimpleNamespace(_proc=proc)

        async def close(self):
            self._sync._proc = None

    client = Client()
    helper.client = client
    helper.clients.add(client)
    helper.identities[client] = dict(identity="quarantined-helper", sdk_ready=sdk_ready)

    async def fail(*_, **__):
        raise OSError("owned process has not exited")

    monkeypatch.setattr("doxagent.codex_worker.process_tree.terminate_owned", fail)
    monkeypatch.setattr(
        "doxagent.codex_worker.process_tree.owned_processes",
        lambda _: {} if gone else {proc.pid: ("ticks", 0)},
    )
    with pytest.raises(LifecycleCleanupError):
        await helper._close_client(client)
    assert helper.quarantined and client in helper.identities
    assert bool(helper.admission.quarantined) is (not sdk_ready)
    await helper._reconcile_quarantine()
    assert helper.quarantined
    gone, helper._quarantine_check_at = True, 0
    await helper._reconcile_quarantine()
    assert not helper.quarantined and not helper.clients and not helper.admission.quarantined


@pytest.mark.asyncio
async def test_real_private_process_close_leaves_unrelated_sibling_alive(tmp_path):
    capsule = Capsule(tmp_path)
    capsule.GRACEFUL_CLOSE_SECONDS = 0.05
    options = dict(
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name != "nt",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    capsule.process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time;time.sleep(60)",
        env={**os.environ, "DOXAGENT_CAPSULE_ID": capsule.identity},
        **options,
    )
    sibling = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time;time.sleep(60)",
        env={**os.environ, "DOXAGENT_CAPSULE_ID": "independent-sibling"},
        **options,
    )
    try:
        await asyncio.wait_for(capsule.close(), 15)
        assert capsule.cleanup_state == "CLOSED" and capsule.process.returncode is not None
        assert sibling.returncode is None
    finally:
        if capsule.process.returncode is None:
            capsule.process.kill()
        await capsule.process.wait()
        if sibling.returncode is None:
            sibling.kill()
        await sibling.wait()


@pytest.mark.asyncio
async def test_popen_wait_is_cancellable_without_leaving_wait_thread():
    from doxagent.codex_worker.process_tree import PopenProcess

    process = SimpleNamespace(
        poll=lambda: None, wait=lambda: pytest.fail("blocking Popen.wait must not run")
    )
    adapted = PopenProcess(process)
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.02):
            await adapted.wait()
    process.poll = lambda: 0
    assert await adapted.wait() == 0


@pytest.mark.asyncio
async def test_start_replaces_finished_dispatcher_once(tmp_path):
    manager = WorkerJobManager(Runtime(), LocalWorkspaceStore(tmp_path))
    manager.start()
    try:
        await until(lambda: manager.dispatcher_status()["ready"])
        old = manager._pump
        old.cancel()
        await asyncio.gather(old, return_exceptions=True)
        assert not manager.dispatcher_status()["ready"]
        manager.start()
        replacement = manager._pump
        manager.start()
        assert replacement is not old and manager._pump is replacement
        await until(lambda: manager.dispatcher_status()["ready"])
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_helper_quarantine_reconciles_without_new_lifecycle_request(tmp_path, monkeypatch):
    manager = WorkerJobManager(Runtime(), LocalWorkspaceStore(tmp_path))
    helper = manager.thread_lifecycle.transport
    helper.admission = StartupAdmission()

    class Client:
        _sync = SimpleNamespace(_proc=SimpleNamespace(pid=123456789, poll=lambda: 0))

        async def close(self):
            pass

    client = Client()
    helper.client = client
    helper.clients.add(client)
    helper.identities[client] = dict(identity="finished-helper", sdk_ready=False)

    async def fail(*_, **__):
        raise OSError("last kill acknowledgement was lost")

    monkeypatch.setattr("doxagent.codex_worker.process_tree.terminate_owned", fail)
    monkeypatch.setattr("doxagent.codex_worker.process_tree.owned_processes", lambda _: {})
    with pytest.raises(LifecycleCleanupError):
        await helper._close_client(client)
    assert helper.quarantined and helper.admission.quarantined
    manager.start()
    try:
        await until(lambda: not helper.quarantined)
        assert not helper.admission.quarantined and not helper.clients
        assert not manager._runtime.started
    finally:
        await manager.close()


class Threads:
    def __init__(self):
        self.calls = []

    async def request(self, method, params):
        self.calls.append((method, params))
        if method == "thread/list":
            return {"data": []}
        if method == "thread/read":
            return {"thread": {"id": params["threadId"], "status": {"type": "idle"}}}
        if method == "thread/delete":
            return {}
        raise AssertionError(method)

    async def close(self):
        pass


def retirement(tmp_path):
    workspaces = LocalWorkspaceStore(tmp_path)
    jobs = SimpleNamespace(store=JobStore(tmp_path), _runtime=SimpleNamespace(), generation="new")
    transport = Threads()
    jobs.thread_lifecycle = WorkerThreadLifecycle(jobs, workspaces, transport)
    with closing(jobs.store.connect()) as db, db:
        db.execute(
            "INSERT INTO w3_thread_registry VALUES('parent','TEMPLATE',NULL,'mother','ACTIVE')"
        )
    slots = W3ReadySlots(jobs, workspaces, SimpleNamespace(), SimpleNamespace())
    return slots, jobs, workspaces, transport


def add_retiring(jobs, workspaces, identity, dispatched, *, child=None, receipt=False):
    run = "w3-ready-" + identity
    cwd = workspaces.ensure_run(run)
    if receipt:
        (cwd / ".w3-fork-receipt.json").write_text(
            json.dumps(dict(run_id=run, parent_id="parent", thread_id=child))
        )
    with closing(jobs.store.connect()) as db, db:
        db.execute(
            "INSERT INTO w3_ready_slots(id,scope,config,state,run_id,created_at) "
            "VALUES(?,?,'{}','RETIRING',?,?)",
            (identity, "scope", run, "2026-10-26T00:00:00Z"),
        )
        db.execute(
            "INSERT INTO w3_thread_operations(operation_key,request,thread_id,status,"
            "fork_dispatched_at) VALUES(?,?,?,'CREATING',?)",
            (
                "w3-slot-fork:" + identity,
                json.dumps(dict(run_id=run, parent_thread_id="parent")),
                child,
                dispatched,
            ),
        )


@pytest.mark.asyncio
async def test_retirement_preview_fair_scan_and_unsubmitted_original_inventory(tmp_path):
    slots, jobs, workspaces, transport = retirement(tmp_path)
    for i in range(5):
        add_retiring(jobs, workspaces, str(i), None if i < 4 else "")
    preview = await slots.retire(dry_run=True)
    assert len(preview) == 4 and not transport.calls
    assert workspaces._run_root("w3-ready-0").is_dir()
    await slots.retire()
    await slots.retire()
    with closing(jobs.store.connect()) as db:
        assert (
            db.execute("SELECT state FROM w3_ready_slots WHERE id='4'").fetchone()[0] == "RETIRED"
        )
        assert (
            db.execute("SELECT count(*) FROM w3_ready_slots WHERE state='RETIRING'").fetchone()[0]
            == 4
        )
    assert jobs.thread_lifecycle.operation("w3-slot-fork:4")["status"] == "FAILED"
    assert not workspaces._run_root("w3-ready-4").exists()
    assert all(
        method == "thread/list" and params["useStateDbOnly"] for method, params in transport.calls
    )


@pytest.mark.asyncio
async def test_retirement_adopts_only_original_receipt_child_without_fork(tmp_path):
    slots, jobs, workspaces, transport = retirement(tmp_path)
    add_retiring(jobs, workspaces, "one", "sent", child="child", receipt=True)
    await slots.retire()
    row = jobs.thread_lifecycle._row("child")
    assert row["status"] == "DELETED" and row["run_id"] == "w3-ready-one"
    assert [m for m, _ in transport.calls] == ["thread/read", "thread/delete"]
    assert not workspaces._run_root("w3-ready-one").exists()


@pytest.mark.asyncio
async def test_retirement_live_owner_and_conflicting_receipt_are_preserved(tmp_path):
    slots, jobs, workspaces, transport = retirement(tmp_path)
    add_retiring(jobs, workspaces, "one", "sent", child="child", receipt=True)
    key = "w3-slot-fork:one"
    owner = asyncio.create_task(asyncio.Event().wait())
    jobs.thread_lifecycle.preparations[key] = owner
    await slots.retire()
    assert not transport.calls
    owner.cancel()
    await asyncio.gather(owner, return_exceptions=True)
    jobs.thread_lifecycle.preparations.pop(key)
    path = workspaces._run_root("w3-ready-one") / ".w3-fork-receipt.json"
    path.write_text(
        json.dumps(dict(run_id="w3-ready-one", parent_id="parent", thread_id="different"))
    )
    await slots.retire()
    assert not transport.calls and path.exists()
    assert jobs.thread_lifecycle._row("child") is None
