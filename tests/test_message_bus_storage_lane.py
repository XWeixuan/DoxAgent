"""Network deadlines must not include synchronous owner-store execution."""

import asyncio
import threading
import time
from contextlib import contextmanager
from datetime import timedelta

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
