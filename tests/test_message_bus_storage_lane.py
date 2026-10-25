"""Network deadlines must not include synchronous owner-store execution."""

import asyncio
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import timedelta
from types import SimpleNamespace

from doxagent.message_bus_v2.schema import PollResult, utc_now
from doxagent.message_bus_v2.storage_lane import StorageLane
from tests.fixtures.message_bus_v2 import _bus, _input


def test_storage_lane_yields_and_serializes_writers():
    async def scenario():
        lane = StorageLane()
        active = 0
        peak = 0
        ticks = 0
        owner_thread = threading.get_ident()

        def write():
            nonlocal active, peak
            assert threading.get_ident() != owner_thread
            active += 1
            peak = max(peak, active)
            time.sleep(0.04)
            active -= 1

        tasks = [asyncio.create_task(lane.call(write)) for _ in range(3)]
        while any(not task.done() for task in tasks):
            await asyncio.sleep(0.005)
            ticks += 1
        await asyncio.gather(*tasks)
        assert peak == 1
        assert ticks >= 8

    asyncio.run(scenario())


def test_cancelled_storage_commit_drains_before_next_writer():
    async def scenario():
        lane = StorageLane()
        started, release = threading.Event(), threading.Event()
        events = []

        def write():
            started.set()
            assert release.wait(2)
            events.append("commit")

        first = asyncio.create_task(lane.call(write))
        while not started.is_set():
            await asyncio.sleep(0.001)
        first.cancel()
        second = asyncio.create_task(lane.call(lambda: events.append("next")))
        await asyncio.sleep(0.02)
        first.cancel()
        await asyncio.sleep(0.02)
        assert not first.done() and not second.done()
        release.set()
        await asyncio.gather(first, second, return_exceptions=True)
        assert first.cancelled()
        assert events == ["commit", "next"]

    asyncio.run(scenario())


def test_historical_feed_admission_batches_transactions_and_retains_evidence(tmp_path):
    repo, service = _bus(tmp_path / "bus.db")
    service.start_ticker("MU")
    binding = repo.list_bindings(ticker="MU", active_only=True)[0]
    source = repo.get_source(binding.source_id)
    messages = [_input(str(i), published_at=utc_now() - timedelta(days=3)) for i in range(1000)]
    transaction = repo.transaction
    calls = []

    @contextmanager
    def counted():
        calls.append(threading.get_ident())
        with transaction() as db:
            yield db

    repo.transaction = counted
    main_thread = threading.get_ident()
    result = asyncio.run(
        service.accept_poll_result(
            source=source, binding=binding, result=PollResult(messages=messages)
        )
    )
    assert result.filtered_count == 1000
    assert result.published_count == 0
    assert len(calls) < 20  # Not two write transactions/connections per item.
    assert all(identity != main_thread for identity in calls)
    with repo._connect() as db:
        assert db.execute("SELECT count(*) FROM message_first_seen").fetchone()[0] == 1000
        assert db.execute("SELECT count(*) FROM message_admission_results").fetchone()[0] == 1000


def test_batch_first_seen_never_refreshes_unknown_or_updated_timestamp(tmp_path):
    repo, _ = _bus(tmp_path / "bus.db")
    old = _input("same", published_at=utc_now() - timedelta(days=2))
    repo.resolve_first_seen_many([old], "binding")
    unknown = old.model_copy(
        update={"published_at": utc_now(), "publication_time_basis": "UNKNOWN_FIRST_SEEN"}
    )
    updated = old.model_copy(
        update={
            "published_at": utc_now(),
            "metadata": {"publication_time_origin": "UPDATED_FALLBACK"},
        }
    )
    resolved = repo.resolve_first_seen_many([unknown, updated], "binding")
    assert [item.published_at for item in resolved] == [old.published_at] * 2


def test_ibkr_cancel_drains_socket_operation_before_adapter_close():
    from doxagent.message_bus_v2.ibkr_news import IbkrNewsAdapter

    async def scenario():
        started, release = threading.Event(), threading.Event()
        events = []

        def poll(*args):
            started.set()
            assert release.wait(2)
            events.append("poll_finished")
            return [], {}

        @asynccontextmanager
        async def permit():
            yield

        gateway = SimpleNamespace(poll=poll, close=lambda: events.append("closed"))
        adapter = IbkrNewsAdapter(SimpleNamespace(), gateway=gateway)
        task = asyncio.create_task(
            adapter.poll(
                SimpleNamespace(requested_at=utc_now(), ticker="MU", request_permit=permit)
            )
        )
        while not started.is_set():
            await asyncio.sleep(0.001)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        adapter.close()
        assert events == ["poll_finished", "closed"]

    asyncio.run(scenario())


def test_writer_queue_keeps_priority_fairness_and_survives_cancelled_waiter():
    async def scenario():
        lane = StorageLane()
        entered, release = threading.Event(), threading.Event()
        events = []

        def blocked():
            entered.set()
            assert release.wait(5)

        first = asyncio.create_task(lane.call(blocked))
        while not entered.is_set():
            await asyncio.sleep(.001)
        cancelled = asyncio.create_task(lane.call(lambda: events.append('cancelled')))
        ordinary = [asyncio.create_task(lane.call(lambda i=i: events.append(f'ordinary{i}'))) for i in range(2)]
        urgent = [asyncio.create_task(lane.call(lambda i=i: events.append(f'urgent{i}'), _lane_priority=0)) for i in range(20)]
        await asyncio.sleep(.01)
        cancelled.cancel()
        await asyncio.gather(cancelled, return_exceptions=True)
        release.set()
        await asyncio.wait_for(asyncio.gather(first, *ordinary, *urgent), 5)
        assert events[:8] == [f'urgent{i}' for i in range(8)]
        assert events[8] == 'ordinary0' and events[17] == 'ordinary1'
        assert 'cancelled' not in events
        assert len(events) == 22
        assert await lane.call(lambda: 'still-usable') == 'still-usable'

    asyncio.run(scenario())


def test_cancelled_queued_future_is_not_granted_before_its_finally_runs():
    async def scenario():
        lane = StorageLane()
        async with lane._lane().writer(1):
            waiter = asyncio.create_task(lane.call(lambda: 'must-not-run'))
            await asyncio.sleep(0)
            waiter.cancel()
            # Release synchronously before the cancelled task gets a turn.
        await asyncio.gather(waiter, return_exceptions=True)
        assert waiter.cancelled()
        assert await lane.call(lambda: 'healthy') == 'healthy'
    asyncio.run(scenario())
