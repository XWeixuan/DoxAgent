import asyncio

import pytest

from doxagent.trade_execution.executor import ENTRY_ABANDONED, Executor
from doxagent.trade_execution.worker import maintain_connections
from tests.test_trade_execution import admit, run_job
from tests.test_trade_execution import setup as _setup_fixture

setup = _setup_fixture


@pytest.mark.parametrize("failures", [3, 337, 712])
def test_existing_exhausted_entry_fails_before_any_api_even_after_restart(setup, failures):
    clock, repo, revision, executor, broker = setup
    intent = admit(repo, revision)
    repo.update_job(repo.require("jobs", "trade:A:entry"), failures=failures)
    restarted = Executor(repo, broker_factory=lambda *a, **kw: broker)
    asyncio.run(restarted.step("trade:A:entry"))
    repo.admit(intent)  # Repeated durable intake cannot resurrect it.
    asyncio.run(restarted.step("trade:A:entry"))
    assert broker.connect_calls == 0 and not broker.sent
    execution = repo.require("executions", "trade:A")
    assert execution["entry_result"] == "FAILED"
    assert execution["entry_reason"] == ENTRY_ABANDONED
    assert repo.require("jobs", "trade:A:entry")["entry_abandoned_at"]


def test_third_connection_failure_permanently_abandons_entry(setup):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    original = broker.connect
    broker.connect = lambda: (_ for _ in ()).throw(ConnectionError("IBKR_HANDSHAKE_TIMEOUT"))
    for delay in [2, 5, 10]:
        asyncio.run(executor.step("trade:A:entry"))
        assert executor.recovery.delay("DU_TEST") == delay
        clock.advance(delay)
    broker.connect = original
    executor.recovery.success(
        "DU_TEST"
    )  # Even a later successful account recovery cannot revive it.
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    assert not broker.sent
    assert repo.require("executions", "trade:A")["entry_reason"] == ENTRY_ABANDONED


def test_shared_connection_failures_stop_not_due_entries_without_api_burst(setup, monkeypatch):
    clock, repo, revision, executor, broker = setup
    intent = admit(repo, revision)
    repo.admit({**intent, "intent_id": "trade:B", "ticker": "BE"})
    repo.update_job(repo.require("jobs", "trade:B:entry"), delay=3600)
    monkeypatch.setattr(
        "doxagent.trade_execution.worker.active_profile_revisions", lambda _: [revision]
    )
    calls = []

    def down():
        calls.append(1)
        raise ConnectionError("IBKR_HANDSHAKE_TIMEOUT")

    broker.connect = down
    for delay in [2, 5, 10]:
        maintain_connections(executor)
        # Maintenance defers to account recovery after the first failure.
        if len(calls) < 3 and executor.recovery.delay("DU_TEST") == 0:
            asyncio.run(executor.step("trade:A:entry"))
        clock.advance(delay)
    # Complete the third actual probe if maintenance merely observed the cooldown.
    while len(calls) < 3:
        asyncio.run(executor.step("trade:A:entry"))
        clock.advance(executor.recovery.delay("DU_TEST"))
    assert len(calls) == 3
    for identity in ["trade:A", "trade:B"]:
        assert repo.require("executions", identity)["entry_result"] == "FAILED"
        assert repo.require("jobs", identity + ":entry")["entry_abandoned_at"]
    assert not broker.sent


def test_cooldown_polls_do_not_consume_three_failures(setup):
    _, repo, revision, executor, broker = setup
    admit(repo, revision)
    broker.connect = lambda: (_ for _ in ()).throw(ConnectionError("IBKR_HANDSHAKE_TIMEOUT"))

    async def scenario():
        for _ in range(20):
            await executor.step("trade:A:entry")

    asyncio.run(scenario())
    assert repo.require("jobs", "trade:A:entry")["entry_failures"] == 1
    assert repo.require("executions", "trade:A")["entry_result"] is None


@pytest.mark.parametrize("mode", ["unfilled", "partial"])
def test_exhausted_sent_entry_only_reconciles_and_cancels_no_top_up(setup, mode):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    broker.mode = mode
    asyncio.run(executor.step("trade:A:entry"))
    original = broker.sync
    broker.sync = lambda: (_ for _ in ()).throw(TimeoutError("BROKER_ORDER_SYNC_INCOMPLETE"))
    for _ in range(3):
        asyncio.run(executor.step("trade:A:entry"))
        clock.advance(executor.recovery.delay("DU_TEST"))
    job = repo.require("jobs", "trade:A:entry")
    assert job["state"] == "RECONCILE_REQUIRED" and job["entry_abandoned_at"]
    assert repo.require("executions", "trade:A")["entry_result"] is None
    broker.sync = original
    broker.mode = "unfilled"
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    assert len(broker.sent) == 1 and len(broker.cancels) == 1
    execution = repo.require("executions", "trade:A")
    assert execution["entry_result"] == ("PARTIAL_FILLED" if mode == "partial" else "FAILED")
    if mode == "partial":
        assert repo.require("lots", "trade:A")["remaining_qty"] == 100
        assert repo.require("jobs", "trade:A:exit")["state"] == "READY"
    else:
        assert execution["entry_reason"] == ENTRY_ABANDONED


def test_prepared_order_is_voided_at_final_send_guard(setup, monkeypatch):
    _, repo, revision, executor, broker = setup
    admit(repo, revision)
    original = executor._submit_prepared

    async def failure_before_send(*args):
        repo.update_job(repo.require("jobs", "trade:A:entry"), entry_failures=3)
        await original(*args)

    monkeypatch.setattr(executor, "_submit_prepared", failure_before_send)
    asyncio.run(executor.step("trade:A:entry"))
    assert not broker.sent
    assert repo.attempts("trade:A:entry")[0]["state"] == "NOT_SENT"
    assert repo.require("executions", "trade:A")["entry_reason"] == ENTRY_ABANDONED


def test_entry_failure_counter_survives_a_successful_submit(setup):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    repo.update_job(repo.require("jobs", "trade:A:entry"), entry_failures=2)
    broker.mode = "unfilled"
    asyncio.run(executor.step("trade:A:entry"))
    assert repo.require("jobs", "trade:A:entry")["entry_failures"] == 2
    broker.sync = lambda: (_ for _ in ()).throw(TimeoutError("BROKER_ORDER_SYNC_INCOMPLETE"))
    clock.advance()
    asyncio.run(executor.step("trade:A:entry"))
    assert repo.require("jobs", "trade:A:entry")["entry_abandoned_at"]
    assert len(broker.sent) == 1


def test_failure_between_preflight_and_atomic_send_is_blocked(setup, monkeypatch):
    _, repo, revision, executor, broker = setup
    admit(repo, revision)
    original = repo.mark_submitting

    def raced(attempt, **kwargs):
        repo.update_job(repo.require("jobs", "trade:A:entry"), entry_failures=3)
        return original(attempt, **kwargs)

    monkeypatch.setattr(repo, "mark_submitting", raced)
    asyncio.run(executor.step("trade:A:entry"))
    assert not broker.sent
    assert repo.attempts("trade:A:entry")[0]["state"] == "NOT_SENT"
    assert repo.require("executions", "trade:A")["entry_reason"] == ENTRY_ABANDONED
