"""Reproduce the audited zero-attempt starvation, real storage lock and stale-send race."""

import asyncio
import sqlite3
import time
from datetime import datetime, timedelta

import pytest

from doxagent.trade_execution.executor import Executor
from doxagent.trade_execution.recovery import ENTRY_QUEUE_EXPIRED
from doxagent.trade_execution.worker import run_worker, scheduler_health
from tests.test_trade_execution import admit, run_job
from tests.test_trade_execution import setup as _setup_fixture

setup = _setup_fixture


def test_stale_zero_attempt_entry_is_terminal_after_restart_and_duplicate_intake(setup):
    clock, repo, revision, executor, broker = setup
    intent = admit(repo, revision)
    clock.advance(301)
    asyncio.run(executor.step("trade:A:entry"))
    assert repo.require("executions", "trade:A")["entry_reason"] == ENTRY_QUEUE_EXPIRED
    assert repo.require("jobs", "trade:A:entry")["entry_failures"] == 0
    repo.admit(intent)
    asyncio.run(Executor(repo, broker_factory=lambda *a, **k: broker).step("trade:A:entry"))
    assert not broker.sent and broker.connect_calls == 0


def test_age_includes_delivery_delay_not_just_admission(setup):
    clock, repo, revision, executor, broker = setup
    intent = admit(repo, revision)
    intent["intent_id"] = "trade:delayed"
    intent["released_at"] = (clock() - timedelta(seconds=301)).isoformat()
    repo.admit(intent)
    asyncio.run(executor.step("trade:delayed:entry"))
    assert repo.require("executions", "trade:delayed")["entry_reason"] == ENTRY_QUEUE_EXPIRED
    assert not broker.sent


def test_freshness_crossing_inside_atomic_send_barrier_never_submits(setup, monkeypatch):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    mark = repo.mark_submitting

    def race(attempt, **kwargs):
        clock.advance(300)
        return mark(attempt, **kwargs)

    monkeypatch.setattr(repo, "mark_submitting", race)
    asyncio.run(executor.step("trade:A:entry"))
    assert not broker.sent
    assert repo.attempts("trade:A:entry")[0]["state"] == "NOT_SENT"
    assert repo.require("executions", "trade:A")["entry_reason"] == ENTRY_QUEUE_EXPIRED


@pytest.mark.parametrize("partial", [False, True])
def test_expired_sent_order_keeps_reconciliation_and_partial_exit(setup, partial):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    broker.mode = "partial" if partial else "unfilled"
    asyncio.run(executor.step("trade:A:entry"))
    clock.advance(301)
    broker.mode = "unfilled"
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    assert len(broker.sent) == 1 and len(broker.cancels) == 1
    assert repo.require("jobs", "trade:A:entry")["state"] == "DONE"
    if partial:
        assert repo.require("lots", "trade:A")["remaining_qty"] == 100
        assert repo.require("jobs", "trade:A:exit")["state"] == "READY"
    else:
        assert repo.require("executions", "trade:A")["entry_reason"] == ENTRY_QUEUE_EXPIRED


def test_real_sqlite_write_lock_defers_without_consuming_broker_failures(setup):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    # Force an actual ledger write before any broker operation.
    clock.advance(301)
    blocker = sqlite3.connect(repo.journal.path)
    blocker.execute("BEGIN IMMEDIATE")
    started = time.monotonic()
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            asyncio.run(executor.step("trade:A:entry"))
        assert time.monotonic() - started < 2
        assert executor.recovery.state("DU_TEST") == {}
        assert repo.require("jobs", "trade:A:entry")["failures"] == 0
        assert not broker.sent
    finally:
        blocker.rollback()
        blocker.close()
    asyncio.run(executor.step("trade:A:entry"))
    assert repo.require("executions", "trade:A")["entry_reason"] == ENTRY_QUEUE_EXPIRED


@pytest.mark.parametrize(
    "lane", ["heartbeat", "connections", "entry_limits", "exit_recovery", "events", "cycles"]
)
def test_due_exit_advances_even_when_maintenance_lane_fails(setup, monkeypatch, lane):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    lot = repo.require("lots", "trade:A")
    clock.value = datetime.fromisoformat(lot["scheduled_exit"])
    failures = []

    def blocked(*args, **kwargs):
        failures.append(1)
        raise sqlite3.OperationalError("database is locked")

    targets = {
        "heartbeat": ("doxagent.trade_execution.worker.scheduler_health", None),
        "connections": ("doxagent.trade_execution.worker.maintain_connections", None),
        "entry_limits": (executor, "enforce_entry_failure_limits"),
        "exit_recovery": (repo, "recover_failed_exits"),
        "events": (executor, "drain_events"),
        "cycles": (executor, "capture_ready_cycles"),
    }
    target, attr = targets[lane]
    if attr:
        monkeypatch.setattr(target, attr, blocked)
    else:
        monkeypatch.setattr(target, blocked)
    # Isolate the dispatcher regression: already-ready owned EXIT must reach step.
    advanced = []

    async def advance(job_id):
        advanced.append(job_id)

    monkeypatch.setattr(executor, "step", advance)
    asyncio.run(run_worker(executor, once=True))
    assert failures and "trade:A:exit" in advanced


def test_scheduler_reports_overdue_zero_attempt_exit(setup, caplog):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    job = repo.require("jobs", "trade:A:exit")
    clock.value = datetime.fromisoformat(job["due_at"]) + timedelta(seconds=65)
    scheduler_health(executor)
    health = repo.journal.get("execution_scheduler", "health")
    assert health["overdue_exits"][0]["attempts"] == 0
    assert health["overdue_exits"][0]["job_id"] == job["id"]
    assert "execution_jobs_stalled" in caplog.text


def test_one_job_exception_does_not_drop_other_ticker(setup, monkeypatch):
    clock, repo, revision, executor, broker = setup
    intent = admit(repo, revision)
    repo.admit({**intent, "intent_id": "trade:B", "ticker": "BE"})
    advanced = []

    async def advance(job_id):
        if job_id == "trade:A:entry":
            raise sqlite3.OperationalError("database is locked")
        advanced.append(job_id)

    monkeypatch.setattr(executor, "step", advance)
    asyncio.run(run_worker(executor, once=True))
    assert advanced == ["trade:B:entry"]


def test_full_owned_exit_still_fills_with_persistently_broken_heartbeat(setup, monkeypatch):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    clock.value = datetime.fromisoformat(repo.require("lots", "trade:A")["scheduled_exit"])

    def blocked(*args):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("doxagent.trade_execution.worker.scheduler_health", blocked)
    for _ in range(4):
        asyncio.run(run_worker(executor, once=True))
        clock.advance(6)
        if repo.require("jobs", "trade:A:exit")["state"] == "DONE":
            break
    assert repo.require("jobs", "trade:A:exit")["result"] == "EXIT_FILLED"
    assert repo.require("lots", "trade:A")["remaining_qty"] == 0
    assert len([a for a in broker.sent if a["job_id"] == "trade:A:exit"]) == 1
    assert broker.positions[9939] == "50"  # Manual baseline untouched.


def test_storage_contention_is_never_an_account_outage(setup):
    _, _, _, executor, _ = setup
    for _ in range(4):
        executor.recovery.fail("DU_TEST", sqlite3.OperationalError("database is locked"))
    assert executor.recovery.state("DU_TEST") == {}


def test_idle_exit_recovery_never_requests_sqlite_write_lock(setup):
    _, repo, _, _, _ = setup
    blocker = sqlite3.connect(repo.journal.path)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        assert repo.recover_failed_exits() == 0
    finally:
        blocker.rollback()
        blocker.close()
