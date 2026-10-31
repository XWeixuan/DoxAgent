"""Audit badcases: callback identity and asynchronous owned-quantity races."""

import asyncio
import threading
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from doxagent.trade_execution.ibkr_session import IbkrSession
from doxagent.trade_execution.worker import maintain_connections, recover_idle_accounts
from tests.test_trade_execution import admit, profile, run_job
from tests.test_trade_execution import setup as setup_fixture

setup = setup_fixture


def correct(repo, executor, broker, quantity):
    old = repo.fills("trade:A:entry")[0]
    broker.positions[9939] = str(int(broker.positions[9939]) + quantity - int(old["quantity"]))
    repo.event(
        old["account"],
        "fill",
        {
            **old,
            "exec_id": old["exec_id"].rsplit(".", 1)[0] + ".02",
            "quantity": str(quantity),
            "cum_qty": str(quantity),
        },
    )
    executor.drain_events()


def opened(setup):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    return clock, repo, revision, executor, broker


def test_working_exit_is_cancelled_immediately_when_correction_reduces_owned(setup):
    clock, repo, _, executor, broker = opened(setup)
    clock.value = datetime.fromisoformat(repo.require("lots", "trade:A")["scheduled_exit"])
    broker.mode = "unfilled"
    asyncio.run(executor.step("trade:A:exit"))
    original = broker.sent[-1]["quantity"]
    correct(repo, executor, broker, original - 8)
    clock.advance(1)
    asyncio.run(executor.step("trade:A:exit"))
    assert len(broker.cancels) == 1
    assert len(broker.sent) == 2


def test_quote_race_cannot_send_stale_exit_or_leave_done_prepared(setup):
    clock, repo, _, executor, broker = opened(setup)
    clock.value = datetime.fromisoformat(repo.require("lots", "trade:A")["scheduled_exit"])
    entered, release = threading.Event(), threading.Event()
    original = broker.quote

    def quote(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    broker.quote = quote

    async def scenario():
        task = asyncio.create_task(executor.step("trade:A:exit"))
        assert await asyncio.to_thread(entered.wait, 5)
        correct(repo, executor, broker, 0)
        release.set()
        await task

    asyncio.run(scenario())
    assert len(broker.sent) == 1
    assert all(a["state"] == "NOT_SENT" for a in repo.attempts("trade:A:exit"))
    result = repo.require("executions", "trade:A")
    assert result["filled_qty"] == 0 and result["entry_result"] == "FAILED"


def test_future_exit_does_not_own_recovery_probe(setup):
    clock, repo, revision, executor, broker = opened(setup)
    executor.recovery.fail("DU_TEST", TimeoutError("IBKR_HANDSHAKE_TIMEOUT"))
    clock.advance(120)
    before = broker.connect_calls
    with patch("doxagent.trade_execution.worker.active_profile_revisions", lambda _: [revision]):
        maintain_connections(executor)
        asyncio.run(recover_idle_accounts(executor))
    assert broker.connect_calls > before
    assert not executor.recovery.state("DU_TEST")


def test_market_rule_cache_never_swallows_order_rejection():
    events = []
    session = IbkrSession(profile(), event_sink=lambda k, v: events.append((k, v)))
    app = session._make_app()
    session.app = SimpleNamespace(
        reqMarketRule=lambda n: app.marketRule(n, [SimpleNamespace(lowEdge=0, increment=0.01)])
    )
    session.market_rules(
        {"exchange": "SMART", "valid_exchanges": ["SMART"], "market_rule_ids": ["26"]}
    )
    app.error(26, 0, 201, "insufficient funds")
    assert events[-1][0] == "order" and events[-1][1]["status"] == "Rejected"
    assert session.rule_requests[26]["error"] is None


def test_what_if_delay_rechecks_quote_age_before_send(setup):
    clock, repo, revision, executor, broker = setup
    stamp = [1000.0]
    calls = []

    def quote(*args, **kwargs):
        return {
            "bid": 100,
            "ask": 100,
            "market_data_type": 1,
            "bid_received_at": stamp[0],
            "ask_received_at": stamp[0],
        }

    def preview(*args, **kwargs):
        calls.append(1)
        stamp[0] += 10
        return {"status": "VALIDATED", "rows": [{"initial_margin_change": "500"}]}

    broker.quote = quote
    broker.what_if = preview
    admit(repo, revision)
    with patch("doxagent.trade_execution.executor.time.time", lambda: stamp[0]):
        asyncio.run(executor.step("trade:A:entry"))
    assert calls == [1] and not broker.sent
    assert repo.attempts("trade:A:entry")[0]["state"] == "NOT_SENT"
    assert repo.require("jobs", "trade:A:entry")["error"] == "FRESH_QUOTE_REQUIRED"


def test_what_if_session_boundary_replans_instead_of_sending_old_session(setup):
    from datetime import UTC

    clock, repo, revision, executor, broker = setup

    def preview(*args, **kwargs):
        clock.value = datetime(2026, 9, 8, 20, tzinfo=UTC)
        return {"status": "VALIDATED", "rows": [{"initial_margin_change": "500"}]}

    broker.what_if = preview
    admit(repo, revision)
    asyncio.run(executor.step("trade:A:entry"))
    assert not broker.sent
    assert repo.attempts("trade:A:entry")[0]["state"] == "NOT_SENT"
    assert repo.require("jobs", "trade:A:entry")["error"] == "FRESH_QUOTE_REQUIRED"


def test_actual_exit_overfill_after_zero_correction_is_kept_as_gap(setup):
    clock, repo, _, executor, broker = opened(setup)
    broker.mode = "unfilled"
    asyncio.run(executor.step("trade:A:exit"))
    exit_attempt = repo.attempts("trade:A:exit")[0]
    correct(repo, executor, broker, 0)
    broker.fill(exit_attempt, exit_attempt["quantity"])
    executor.drain_events()
    assert len(repo.fills("trade:A:exit")) == 1
    lot = repo.require("lots", "trade:A")
    assert lot["remaining_qty"] == 0 and lot["unattributed_qty"] == exit_attempt["quantity"]
    assert any(
        v.get("error") == "EXIT_OVERFILL_AFTER_CORRECTION"
        for v in repo.journal.values("execution_event_gaps")
    )
    assert repo.require("executions", "trade:A")["group_state"] != "CLOSED"


def test_corrected_positive_entry_result_matches_effective_quantity(setup):
    _, repo, _, executor, broker = opened(setup)
    correct(repo, executor, broker, 190)
    execution = repo.require("executions", "trade:A")
    job = repo.require("jobs", "trade:A:entry")
    assert execution["filled_qty"] == 190
    assert execution["entry_result"] == job["result"] == "PARTIAL_FILLED"


def test_startup_cleanup_voids_old_terminal_job_prepared_attempt(setup):
    _, repo, _, executor, broker = opened(setup)
    job = repo.require("jobs", "trade:A:exit")
    data = repo.attempts("trade:A:entry")[0]
    prepared = repo.prepare_attempt(job, data, broker.next_id, broker.profile.client_id)
    repo.update_job(job, state="DONE")
    assert repo.cleanup_prepared() == 1
    assert repo.require("attempts", prepared["id"])["state"] == "NOT_SENT"
    assert len(broker.sent) == 1
