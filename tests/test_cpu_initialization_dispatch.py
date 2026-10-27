import asyncio
from collections import deque
from types import SimpleNamespace

import pytest

from doxagent.ticker_initialization.cli import _run_parallel_loop


@pytest.mark.asyncio
async def test_single_reader_keeps_sixteen_slots_and_refills_before_other_tasks_finish():
    queue = deque(range(17))
    started = set()
    cleaned = set()
    releases = [asyncio.Event() for _ in range(17)]

    def claim(*args, **kwargs):
        return queue.popleft() if queue else None

    async def run_claimed(lease):
        started.add(lease)
        try:
            await releases[lease].wait()
        finally:
            cleaned.add(lease)

    repo = SimpleNamespace(claim=claim)
    workers = [
        SimpleNamespace(repository=repo, owner=str(i), lease_seconds=60, run_claimed=run_claimed)
        for i in range(16)
    ]
    task = asyncio.create_task(_run_parallel_loop(workers))
    try:
        async with asyncio.timeout(2):
            while len(started) < 16:
                await asyncio.sleep(0)
            assert 16 not in started
            releases[0].set()
            while 16 not in started:
                await asyncio.sleep(0)
        assert len(cleaned) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert cleaned == set(range(17))


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
async def test_idle_and_deferred_claims_do_not_scale_with_worker_count(deferred):
    claims = []

    def claim(*args, **kwargs):
        claims.append(1)
        return "lease" if deferred else None

    async def run_claimed(lease):
        return SimpleNamespace(status="RUNNING", model_dump_json=lambda: "{}")

    worker = SimpleNamespace(
        repository=SimpleNamespace(claim=claim),
        owner="owner",
        lease_seconds=60,
        run_claimed=run_claimed,
    )
    task = asyncio.create_task(_run_parallel_loop([worker] * 16))
    try:
        await asyncio.sleep(0.1)
        # Deferred execution may fill the initial wave before its tasks complete;
        # the next wave must wait, and idle must have exactly one reader.
        assert len(claims) == (16 if deferred else 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

@pytest.mark.asyncio
async def test_claim_lock_does_not_cancel_running_work_or_block_event_loop():
    import sqlite3
    import threading
    started = asyncio.Event()
    blocked = threading.Event()
    release = threading.Event()
    steps = iter(['lease', 'block', 'locked', None])
    def claim(*args, **kwargs):
        step = next(steps, None)
        if step == 'block':
            blocked.set()
            release.wait(timeout=2)
            return None
        if step == 'locked':
            raise sqlite3.OperationalError('database is locked')
        return step
    async def run_claimed(lease):
        started.set()
        await asyncio.Event().wait()
    worker = SimpleNamespace(repository=SimpleNamespace(claim=claim), owner='x', lease_seconds=60, run_claimed=run_claimed)
    task = asyncio.create_task(_run_parallel_loop([worker, worker]))
    try:
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(asyncio.to_thread(blocked.wait, 1), 2)
        await asyncio.wait_for(asyncio.sleep(0.05), 0.5)
        assert not task.done()
        release.set()
        await asyncio.sleep(0.8)
        assert not task.done()
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['locked', 'lease'])
async def test_one_execution_failure_does_not_cancel_other_tickers(failure):
    import sqlite3
    from doxagent.ticker_initialization.schema import LeaseLost
    queue = deque(['healthy', 'failing'])
    started = asyncio.Event()
    release = asyncio.Event()
    def claim(*args, **kwargs):
        return queue.popleft() if queue else None
    async def run_claimed(lease):
        if lease == 'failing':
            await started.wait()
            if failure == 'locked':
                raise sqlite3.OperationalError('database is locked')
            raise LeaseLost('lease expired')
        started.set()
        await release.wait()
    worker = SimpleNamespace(repository=SimpleNamespace(claim=claim), owner='x', lease_seconds=60, run_claimed=run_claimed)
    task = asyncio.create_task(_run_parallel_loop([worker, worker]))
    try:
        await asyncio.wait_for(started.wait(), 2)
        await asyncio.sleep(0.1)
        assert not task.done()
        release.set()
        await asyncio.sleep(0.1)
        assert not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

@pytest.mark.asyncio
async def test_heartbeat_retries_lock_without_blocking_event_loop():
    import sqlite3
    from doxagent.ticker_initialization.service import InitializationWorker
    calls = []
    def heartbeat(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError('database is locked')
    w = SimpleNamespace(lease_seconds=0.03, repository=SimpleNamespace(heartbeat=heartbeat))
    task = asyncio.create_task(InitializationWorker._heartbeat(w, object()))
    try:
        await asyncio.sleep(1.2)
        assert len(calls) >= 2
        assert not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
