import asyncio
import sqlite3
from datetime import datetime

import pytest

from doxagent.trade_execution.recovery import AccountRecovery
from doxagent.trade_execution.worker import maintain_connections
from tests.test_trade_execution import admit, run_job
from tests.test_trade_execution import setup as _setup_fixture

setup = _setup_fixture


def test_outage_backoff_shared_by_jobs_and_maintenance_survives_restart(setup, monkeypatch):
    clock, repo, revision, executor, broker = setup
    intent = admit(repo, revision)
    repo.admit({**intent, "intent_id": "trade:B", "ticker": "BE"})
    monkeypatch.setattr(
        "doxagent.trade_execution.worker.active_profile_revisions", lambda _: [revision]
    )
    calls = []

    def down():
        calls.append(clock())
        raise ConnectionError("IBKR_HANDSHAKE_TIMEOUT")

    broker.connect = down

    async def scenario():
        for expected in [2, 5, 10, 30, 60, 60]:
            before = len(calls)
            await executor.step("trade:A:entry")
            await executor.step("trade:B:entry")
            maintain_connections(executor)
            assert len(calls) == before + 1
            assert executor.recovery.delay("DU_TEST") == expected
            assert AccountRecovery(repo.journal).delay("DU_TEST") == expected
            clock.advance(expected - 1)
            await executor.step("trade:B:entry")
            assert len(calls) == before + 1
            clock.advance(1)

    asyncio.run(scenario())
    assert not broker.sent


def test_transient_exit_recovers_without_new_generation_or_manual_resume(setup):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    clock.value = datetime.fromisoformat(repo.require("lots", "trade:A")["scheduled_exit"])
    original = broker.connect
    broker.connect = lambda: (_ for _ in ()).throw(ConnectionError("IBKR_RECONNECT_BACKOFF"))
    for _ in range(6):
        asyncio.run(executor.step("trade:A:exit"))
        clock.advance(executor.recovery.delay("DU_TEST"))
    assert repo.require("jobs", "trade:A:exit")["state"] != "FAILED"
    assert not repo.attempts("trade:A:exit")
    broker.connect = original
    asyncio.run(run_job(executor, repo, clock, "trade:A:exit"))
    assert repo.require("lots", "trade:A")["remaining_qty"] == 0
    assert len(repo.attempts("trade:A:exit")) == 1
    assert repo.require("jobs", "trade:A:exit")["generation"] == 0


def test_only_transient_overdue_failed_exit_is_recovered(setup):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    job = repo.require("jobs", "trade:A:exit")
    repo.finish(job, "IBKR_RECONNECT_BACKOFF")
    assert repo.recover_failed_exits() == 0
    clock.value = datetime.fromisoformat(repo.require("lots", "trade:A")["scheduled_exit"])
    assert repo.recover_failed_exits() == 1
    assert repo.recover_failed_exits() == 0
    assert repo.require("jobs", job["id"])["generation"] == job["generation"]
    repo.finish(repo.require("jobs", job["id"]), "POSITION_ATTRIBUTION_GAP")
    assert repo.recover_failed_exits() == 0


def test_busy_event_is_not_skipped_and_replay_does_not_duplicate_fill(setup, monkeypatch):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    asyncio.run(executor.step("trade:A:entry"))
    cursor = repo.journal.get("trade_execution", "event_cursor", 0)
    original = executor.apply_event
    monkeypatch.setattr(
        executor,
        "apply_event",
        lambda _: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")),
    )
    with pytest.raises(sqlite3.OperationalError):
        executor.drain_events()
    assert repo.journal.get("trade_execution", "event_cursor", 0) == cursor
    monkeypatch.setattr(executor, "apply_event", original)
    executor.drain_events()
    executor.drain_events()
    assert repo.require("lots", "trade:A")["remaining_qty"] == 198
    assert len(repo.fills("trade:A:entry")) == 1


def test_read_queries_do_not_need_runtime_writer_lock(setup):
    _, repo, revision, _, _ = setup
    admit(repo, revision)
    with repo.journal.transaction():
        assert repo.get("executions", "trade:A")
        assert repo.jobs()
        assert repo.profile(revision)
        assert repo.attempts("trade:A:entry") == []


def test_sync_single_flight_and_forced_refresh(setup):
    clock, repo, revision, executor, broker = setup
    calls = []
    original = broker.sync

    def sync():
        calls.append(1)
        return original()

    broker.sync = sync

    async def scenario():
        await asyncio.gather(*(executor._sync(broker, "DU_TEST") for _ in range(5)))
        assert len(calls) == 1
        await executor._sync(broker, "DU_TEST", force=True)
        assert len(calls) == 2
        clock.advance(2)
        await executor._sync(broker, "DU_TEST")
        assert len(calls) == 3

    asyncio.run(scenario())
