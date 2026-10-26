import asyncio
from types import SimpleNamespace

import pytest

from doxagent.codex_worker.startup_admission import StartupAdmission, startup_scope


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_body", [False, True])
async def test_cancel_during_release_notification_never_strands_permit(cancel_body):
    from doxagent.codex_worker.startup_admission import PriorityGate

    gate = PriorityGate()
    entered, leave = asyncio.Event(), asyncio.Event()

    async def work():
        async with gate.acquire(label="original"):
            entered.set()
            await leave.wait()

    owner = asyncio.create_task(work())
    await entered.wait()
    assert gate.snapshot()["owner_alive"] and gate.active
    await gate.condition.acquire()
    try:
        if cancel_body:
            owner.cancel()
        else:
            leave.set()
        await asyncio.sleep(0)
        # Cancel while context exit is waiting to reacquire its notification lock.
        owner.cancel()
        await asyncio.gather(owner, return_exceptions=True)
        assert not gate.active and not gate.snapshot()["owner_alive"]
    finally:
        gate.condition.release()
    async with gate.acquire(timeout=0.1, label="next"):
        assert gate.active and gate.snapshot()["owner_label"] == "next"
    assert not gate.active and not gate.waiters


@pytest.mark.asyncio
async def test_cancelled_waiter_cannot_release_live_owners_permit():
    from doxagent.codex_worker.startup_admission import PriorityGate

    gate = PriorityGate()
    async with gate.acquire(label="live-owner"):

        async def waiter():
            async with gate.acquire():
                pytest.fail("live owner lost exclusive admission")

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert gate.active and gate.snapshot()["owner_label"] == "live-owner"
        assert not gate.waiters


@pytest.mark.asyncio
async def test_twenty_cold_starts_serialize_and_bound_waiter_is_promoted():
    admission = StartupAdmission()
    active = maximum = 0
    order = []
    bound = False
    entered, release = asyncio.Event(), asyncio.Event()

    async def work(index):
        nonlocal active, maximum
        priority = (lambda: 0 if bound else 1) if index == 19 else 1
        with startup_scope(key=str(index), priority=priority):
            async with admission.boot():
                active += 1
                maximum = max(maximum, active)
                order.append(index)
                if index == 0:
                    entered.set()
                    await release.wait()
                await asyncio.sleep(0)
                active -= 1

    tasks = [asyncio.create_task(work(i)) for i in range(20)]
    await entered.wait()
    bound = True
    release.set()
    await asyncio.gather(*tasks)
    assert maximum == 1 and order[:2] == [0, 19]


@pytest.mark.asyncio
async def test_critical_memory_still_blocks_bound_physical_boot():
    snapshot = SimpleNamespace(level="CRITICAL")
    admission = StartupAdmission(safety=SimpleNamespace(read=lambda: snapshot))
    entered = asyncio.Event()

    async def work():
        with startup_scope(key="bound", priority=0):
            async with admission.boot():
                entered.set()

    task = asyncio.create_task(work())
    await asyncio.sleep(0.02)
    assert not entered.is_set() and not admission.cold.active
    snapshot.level = "NORMAL"
    await asyncio.wait_for(task, 1)
    assert entered.is_set()


@pytest.mark.asyncio
async def test_waiting_cold_does_not_consume_handshake_or_block_hot_capsule(tmp_path):
    from doxagent.codex_worker.capsules import CapsuleRuntime

    runtime = CapsuleRuntime(tmp_path)
    resident = SimpleNamespace(thread_id="operations", healthy=True, busy=False, process=None)
    runtime.pool.append(resident)
    async with runtime.admission.boot():
        waiters = [asyncio.create_task(runtime._take(None)) for _ in range(3)]
        await asyncio.sleep(0.01)
        assert await asyncio.wait_for(runtime._take("operations"), 0.1) is resident
        assert runtime.admission.handshake._value == 3
        for task in waiters:
            task.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)
    assert not runtime.admission.cold.waiters


def test_cpu_io_pressure_pauses_speculation_with_hysteresis_and_fail_open(monkeypatch):
    import doxagent.codex_worker.startup_admission as module

    clock = [100.0]
    pressure = {"cpu": 85.0, "io": 0.0}
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        module.Path, "read_text", lambda path: f"some avg10={pressure[path.name]} avg60=0\n"
    )
    admission = StartupAdmission()
    assert not admission.allow_speculation()
    pressure["cpu"] = 20.0
    clock[0] += 2
    assert not admission.allow_speculation()
    clock[0] += 11
    assert admission.allow_speculation()
    pressure["io"] = 30.0
    clock[0] += 2
    assert not admission.allow_speculation()
    monkeypatch.setattr(module.Path, "read_text", lambda path: (_ for _ in ()).throw(OSError()))
    clock[0] += 2
    assert admission.allow_speculation()


@pytest.mark.asyncio
async def test_pressure_keeps_handoff_and_one_ready_per_profile(tmp_path):
    import time

    from doxagent.codex_worker.capsules import CapsuleRuntime

    runtime = CapsuleRuntime(tmp_path)
    closed = []

    def capsule(identity, profile=None, lease=0):
        async def close():
            closed.append(identity)

        return SimpleNamespace(
            identity=identity,
            ready_profile=profile,
            ready_slot=bool(profile),
            handoff_until=lease,
            busy=False,
            thread_id=identity,
            healthy=True,
            prepared=False,
            idle_at=0,
            close=close,
        )

    runtime.pool = [
        capsule("luna1", "luna"),
        capsule("luna2", "luna"),
        capsule("sol", "sol"),
        capsule("handoff", lease=time.monotonic() + 90),
    ]
    await runtime.reap_idle(pressure=True)
    assert closed == ["luna2"]
    await runtime.reap_idle(all_idle=True)
    assert len(closed) == 4


@pytest.mark.asyncio
async def test_dead_idle_shell_is_not_warm_inventory_and_is_reaped(tmp_path):
    from doxagent.codex_worker.capsules import CapsuleRuntime

    closed = []

    async def close():
        closed.append(True)

    runtime = CapsuleRuntime(tmp_path)
    runtime.pool = [
        SimpleNamespace(
            identity="dead",
            busy=False,
            healthy=True,
            thread_id=None,
            process=SimpleNamespace(returncode=1),
            close=close,
        )
    ]
    assert await runtime._reserve(None) is None
    assert await runtime.reap_idle() == 1 and closed and not runtime.pool


@pytest.mark.asyncio
async def test_successful_trade_handoff_survives_idle_rule_until_operations_release(tmp_path):
    import time

    from doxagent.codex_worker.capsules import CapsuleRuntime
    from tests.test_w3_thread_lifecycle import request

    closed = []

    async def close():
        closed.append(True)

    capsule = SimpleNamespace(
        identity="resident",
        healthy=True,
        process=None,
        busy=True,
        thread_id="child",
        ready_slot=False,
        prepared=False,
        idle_at=0,
        last_result_status="completed",
        close=close,
    )
    runtime = CapsuleRuntime(tmp_path)
    runtime.pool = [capsule]
    trade = request().model_copy(
        update={"task_phase": "trade_decision", "thread_id": "child", "max_subagents": 0}
    )
    runtime.active[runtime.key(trade)] = capsule
    await runtime.release(trade)
    assert capsule.handoff_until > time.monotonic() + 89
    capsule.idle_at -= 60
    assert await runtime.reap_idle() == 0
    assert await runtime._take("child") is capsule
    operations = trade.model_copy(update={"task_phase": "post_decision_operations"})
    runtime.active[runtime.key(operations)] = capsule
    await runtime.release(operations)
    assert capsule.handoff_until == 0
    capsule.idle_at -= 60
    assert await runtime.reap_idle() == 1 and closed
