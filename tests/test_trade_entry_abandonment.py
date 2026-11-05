import asyncio

import pytest

from doxagent.trade_execution.executor import ENTRY_ABANDONED, Executor
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


def test_account_maintenance_failures_do_not_charge_waiting_entries(setup, monkeypatch):
    clock, repo, revision, executor, broker = setup
    intent = admit(repo, revision)
    repo.admit({**intent, "intent_id": "trade:B", "ticker": "BE"})
    monkeypatch.setattr(
        "doxagent.trade_execution.worker.active_profile_revisions", lambda _: [revision]
    )
    broker.connect = lambda: (_ for _ in ()).throw(ConnectionError("IBKR_HANDSHAKE_TIMEOUT"))
    for _ in range(3):
        # Actual maintenance probes, not failures of either ENTRY task.
        executor.recovery.fail("DU_TEST", ConnectionError("IBKR_HANDSHAKE_TIMEOUT"))
        clock.advance(executor.recovery.delay("DU_TEST"))
    executor.enforce_entry_failure_limits()
    for identity in ["trade:A", "trade:B"]:
        assert repo.require("executions", identity)["entry_result"] is None
        assert repo.require("jobs", identity + ":entry").get("entry_failures", 0) == 0
    assert not broker.sent


@pytest.mark.parametrize("historical", [3, 490])
def test_new_entry_does_not_inherit_account_history_at_step_or_send(setup, historical):
    from datetime import timedelta

    clock, repo, revision, executor, broker = setup
    executor.recovery.fail("DU_TEST", ConnectionError("old outage"))
    value = {
        "failures": historical,
        "error": "old outage",
        "retry_at": (clock() + timedelta(seconds=60)).isoformat(),
    }
    executor.recovery.states["DU_TEST"] = value
    repo.journal.set("execution_recovery", "DU_TEST", value)
    admit(repo, revision)
    asyncio.run(executor.step("trade:A:entry"))
    job = repo.require("jobs", "trade:A:entry")
    assert job["state"] == "READY" and job.get("entry_failures", 0) == 0
    assert broker.connect_calls == 0 and not broker.sent
    clock.advance(60)
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    assert repo.require("executions", "trade:A")["entry_result"] == "FILLED"
    assert len(broker.sent) == 1


def test_account_cooldown_race_blocks_send_without_spending_entry_budget(setup, monkeypatch):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    original = repo.mark_submitting

    def raced(attempt, **kwargs):
        executor.recovery.fail("DU_TEST", ConnectionError("new account failure"))
        return original(attempt, **kwargs)

    monkeypatch.setattr(repo, "mark_submitting", raced)
    asyncio.run(executor.step("trade:A:entry"))
    assert not broker.sent
    assert repo.attempts("trade:A:entry")[0]["state"] == "NOT_SENT"
    job = repo.require("jobs", "trade:A:entry")
    assert job.get("entry_failures", 0) == 0 and not job.get("entry_abandoned_at")


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
    broker.healthy = False  # A broken connection cannot keep the streaming observer healthy.
    for _ in range(3):
        asyncio.run(executor.step("trade:A:entry"))
        clock.advance(executor.recovery.delay("DU_TEST"))
    job = repo.require("jobs", "trade:A:entry")
    assert job["state"] == "RECONCILE_REQUIRED" and job["entry_abandoned_at"]
    assert repo.require("executions", "trade:A")["entry_result"] is None
    broker.sync = original
    broker.healthy = True
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
    broker.healthy = False  # A broken connection cannot keep the streaming observer healthy.
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
