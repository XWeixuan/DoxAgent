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
