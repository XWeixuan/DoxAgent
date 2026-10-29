"""Causal exit decisions and real execution-ledger integration; no broker network."""

import asyncio
import threading
import time
from datetime import timedelta

import pytest

from doxagent.exit_strategy.engine import StopState, observe
from doxagent.exit_strategy.market import PriceWindow, quote_quality
from doxagent.exit_strategy.repository import ExitRepository
from doxagent.exit_strategy.runner import ExitRunner
from doxagent.exit_strategy.schema import ExitPolicy
from doxagent.v2_read.outbox import SourceOutbox
from tests.test_trade_execution import admit, run_job
from tests.test_trade_execution import setup as execution_setup

setup = execution_setup


def observation(state, t, pnl=1000, *, valid=True, atr=10, generation=1):
    return observe(
        state,
        ExitPolicy(),
        utc=100000 + t,
        mono=t,
        pnl=pnl,
        notional=10000,
        atr=atr,
        valid=valid,
        generation=generation,
    )


def test_causal_median_activation_and_monotonic_line():
    state = StopState()
    for t, p in enumerate([0, 2000, 0, 0]):
        observation(state, t * 5, p)
    assert state.stop_net is None  # one anomalous quote is not an MFE
    for t in range(20, 36, 5):
        observation(state, t, 1000)
    assert state.mfe == 1000 and state.stop_net == 500
    observation(state, 40, 900, atr=1000)
    assert state.stop_net == 500  # ATR growth must not loosen the stop


def test_positive_cost_adjusted_line_and_five_percent_threshold():
    s = StopState()
    for t in range(0, 20, 5):
        observation(s, t, 499)
    assert s.activated_at is None
    for t in range(20, 40, 5):
        observation(s, t, 600, atr=300)
    assert s.activated_at is None
    observation(s, 40, 600, atr=10)
    assert s.stop_net == 300


def test_confirmation_six_valid_five_breaches_latest_and_actual_delay():
    s = StopState()
    for t in range(0, 15, 5):
        observation(s, t)
    assert s.activated_at == 100010
    for t in range(15, 40, 5):
        assert not observation(s, t, 400)
    assert not observation(s, 40, 600)  # latest does not breach despite 5/6
    assert observation(s, 45, 400)
    assert not observation(s, 50, 400)  # latched; never duplicate the request


@pytest.mark.parametrize("fault", ["invalid", "gap", "generation", "restart"])
def test_confirmation_never_spans_a_data_or_process_gap(fault):
    s = StopState()
    for t in range(0, 15, 5):
        observation(s, t)
    for t in range(15, 35, 5):
        observation(s, t, 400)
    if fault == "restart":
        s = StopState.restore(s.durable())
    for t in range(35, 51, 5):
        if fault == "gap" and t == 35:
            continue
        assert not observation(
            s,
            t,
            400,
            valid=not (fault == "invalid" and t == 35),
            generation=2 if fault == "generation" else 1,
        )
    assert s.stop_net == 500


def test_atr_completed_minutes_current_quantities_and_joint_prices():
    p = ExitPolicy()
    window = PriceWindow()
    for minute in range(21):
        for i in range(12):
            window.add(
                minute * 60 + i * 5, [100 + minute + (i % 2), 200 + 2 * minute + 2 * (i % 2)]
            )
    assert window.atr([2, -1], 1260, p) == 0  # perfect hedge; separate leg ATR sum is wrong
    assert window.atr([2, 0], 1260, p) > 0
    assert window.atr([4, 0], 1260, p) == 2 * window.atr([2, 0], 1260, p)
    assert window.atr([2, 0], 1200, p) is None  # current minute excluded
    original = window.atr([2, 0], 1260, p)
    window.add(1260, [90000, 1])
    assert window.atr([2, 0], 1261, p) == original
    for frame in window.frames[:12]:
        frame[1][0] = float("nan")
    window.invalidate()
    assert window.atr([2, -1], 1260, p) is None


def test_seed_never_uses_incomplete_or_future_bar():
    window = PriceWindow()
    window.seed([dict(date=i * 60, high=101, low=99, close=100) for i in range(30)], 1260)
    assert len(window.seeds) == 21
    assert window.atr([100], 1260, ExitPolicy()) == 200
    window.add(10000, [100])
    assert window.atr([100], 10000, ExitPolicy()) is None


def test_quality_size_only_freshness_and_invalid_quotes():
    q = dict(
        bid=100,
        ask=100.1,
        bid_size=1,
        ask_size=2,
        market_data_type=1,
        bid_received_mono=10,
        ask_received_mono=10,
    )
    p = ExitPolicy()
    assert quote_quality(q, 15, p) == "OK"
    assert quote_quality(q, 16, p) == "STALE"
    assert quote_quality({**q, "bid_pending": True}, 11, p) == "INVALID_SIZE"
    assert quote_quality({**q, "ask": 99}, 11, p) == "INVALID_PRICE"
    assert quote_quality({**q, "ask": float("nan")}, 11, p) == "INVALID_PRICE"
    assert quote_quality({**q, "ask": 102}, 11, p) == "WIDE_SPREAD"
    assert quote_quality({**q, "market_data_type": 3}, 11, p) == "QUOTE_GAP"


def enable(repo):
    store = ExitRepository(repo.journal)
    revision = store.import_policy(ExitPolicy())
    store.bind("DU_TEST", "PAPER", revision, reason="test")
    return store, revision


def opened(setup, identity="trade:A", side="LONG"):
    clock, repo, revision, executor, broker = setup
    store, policy = enable(repo)
    admit(repo, revision, identity=identity, side=side)
    asyncio.run(run_job(executor, repo, clock, identity + ":entry"))
    return store, policy


def test_pin_first_actual_fill_not_admission_and_legacy_is_unchanged(setup):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision, identity="trade:legacy")
    store, policy = enable(repo)
    admit(repo, revision)
    assert store.positions() == []
    assert repo.require("executions", "trade:A")["exit_policy_revision"] == policy
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    value = store.positions()[0]
    assert value["legs"][0]["quantity"] == repo.require("lots", "trade:A")["remaining_qty"]
    assert value["notional"] > 0 and value["legs"][0]["cash"] < 0
    assert repo.require("executions", "trade:legacy")["exit_policy_revision"] is None
    store.bind("DU_TEST", "PAPER", None, reason="off")
    assert repo.require("executions", "trade:A")["exit_policy_revision"] == policy
    assert "ts_positions" not in SourceOutbox(repo.journal.path, "runtime").migrate(dry_run=True)


def test_atomic_request_same_exit_job_version_race_and_dedup(setup):
    store, _ = opened(setup)
    clock, repo, revision, executor, broker = setup
    value = store.positions()[0]
    with pytest.raises(ValueError, match="VERSION_CHANGED"):
        store.request_exit("trade:A", "TRAILING_PROFIT", expected_version=100)
    value.update(mfe=1000, stop_net=500, request_reason="TRAILING_PROFIT", evidence={"test": True})
    saved = store.save_batch([value])
    assert "trade:A" in saved
    exit_job = repo.require("jobs", "trade:A:exit")
    assert exit_job["due_at"] == clock().isoformat() and exit_job["generation"] == 0
    assert store.positions()[0]["phase"] == "EXIT_REQUESTED"
    store.request_exit("trade:A", "TRAILING_PROFIT")
    with repo.journal.transaction(write=False) as db:
        assert (
            db.execute("SELECT count(*) FROM te_events WHERE kind='exit_decision'").fetchone()[0]
            == 1
        )
    asyncio.run(run_job(executor, repo, clock, "trade:A:exit"))
    assert repo.require("lots", "trade:A")["remaining_qty"] == 0
    assert int(broker.positions[9939]) == 50  # manual baseline untouched


def test_explicit_group_requests_all_legs_and_refreshes_after_fifo(setup):
    clock, repo, revision, executor, broker = setup
    store, _ = opened(setup)
    admit(repo, revision, identity="trade:B")
    asyncio.run(run_job(executor, repo, clock, "trade:B:entry"))
    store.group("group:AB", ["trade:A", "trade:B"], main_lot="trade:A", reason="explicit fixture")
    assert [v["position_id"] for v in store.positions()] == ["group:AB"]
    store.request_exit("group:AB", "TRAILING_PROFIT")
    assert repo.require("jobs", "trade:A:exit")["exit_requested_at"]
    assert repo.require("jobs", "trade:B:exit")["exit_requested_at"]
    asyncio.run(run_job(executor, repo, clock, "trade:A:exit"))
    group = store.positions()[0]
    assert group["legs"][0]["quantity"] == 0
    assert group["legs"][1]["quantity"] > 0
    assert group["pending_reason"] == "RESIDUAL_GROUP_EXIT"


def test_transient_early_exit_recovery_before_scheduled_time(setup):
    store, _ = opened(setup)
    clock, repo, _, _, _ = setup
    store.request_exit("trade:A", "TRAILING_PROFIT")
    job = repo.require("jobs", "trade:A:exit")
    assert repo.require("lots", "trade:A")["scheduled_exit"] > clock().isoformat()
    repo.update_job(job, state="FAILED", reason="IBKR_DISCONNECTED")
    assert repo.recover_failed_exits() == 1
    assert repo.require("jobs", job["id"])["generation"] == 0


def test_entry_remaining_cancelled_after_exit_trigger(setup):
    clock, repo, revision, executor, broker = setup
    store, _ = enable(repo)
    admit(repo, revision)
    broker.mode = "partial"
    asyncio.run(executor.step("trade:A:entry"))
    executor.drain_events()
    store.request_exit("trade:A", "TRAILING_PROFIT")
    clock.advance()
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    assert len(broker.sent) == 1 and broker.cancels
    assert repo.require("jobs", "trade:A:entry")["reason"] == "POSITION_EXIT_REQUESTED"


def test_runner_real_ledger_quote_stop_and_restart_state(setup):
    store, _ = opened(setup)
    clock, repo, _, executor, broker = setup

    async def run():
        runner = ExitRunner(executor)
        value = store.positions()[0]
        ident = value["position_id"]
        key = ident + ":" + ident
        contract = broker.contract("MU")
        runner.contexts[key] = (broker, contract)
        runner.seeded.add(ident)
        window = PriceWindow()
        end = int(clock().timestamp()) // 60 * 60
        window.seed(
            [dict(date=end - 60 * i, high=100.1, low=99.9, close=100) for i in range(30, 0, -1)],
            end,
        )
        runner.windows[ident] = window
        current = {}
        broker.quote_snapshot = lambda c: current
        broker.release_quote = lambda c, o: None
        initial = clock.value
        for i in range(12):
            clock.value = initial + timedelta(seconds=5 * i)
            price = 110 if i < 4 else 104
            current.update(
                bid=price,
                ask=price + 0.01,
                bid_size=100,
                ask_size=100,
                market_data_type=1,
                bid_received_mono=100 + 5 * i,
                ask_received_mono=100 + 5 * i,
                generation=(1, 1),
            )
            await runner.tick(mono=100 + 5 * i)
        v = store.positions()[0]
        assert v["phase"] == "EXIT_REQUESTED" and v["exit_reason"] == "TRAILING_PROFIT"
        assert v["mfe"] > 500 and v["stop_net"] > 0
        assert len(repo.require("executions", ident)["exit_strategy"]["confirmation"]) == 6
        await runner.close()

    asyncio.run(run())


def test_core_twenty_positions_budget():
    states = [StopState() for _ in range(20)]
    began = time.perf_counter()
    for i in range(200):
        for s in states:
            observation(s, i * 5, 1000 + i)
    assert time.perf_counter() - began < 1  # generous cross-platform regression bound


def test_book_update_cas_never_overwrites_new_fill(setup):
    store, _ = opened(setup)
    clock, repo, _, _, _ = setup
    old = store.positions()[0]
    store.pause(old["position_id"], True, "concurrent update")
    old.update(mfe=999999, stop_net=500000)
    assert store.save_batch([old]) == set()
    assert store.positions()[0].get("mfe", 0) == 0
    assert store.positions()[0]["paused"]


def test_exit_request_transaction_rolls_back_state_if_job_is_missing(setup):
    store, _ = opened(setup)
    _, repo, _, _, _ = setup
    before = store.positions()[0]
    with repo.transaction() as db:
        db.execute("DELETE FROM te_jobs WHERE id='trade:A:exit'")
    value = dict(before, mfe=1000, request_reason="TRAILING_PROFIT")
    assert store.save_batch([value]) == set()
    after = store.positions()[0]
    assert after["state_version"] == before["state_version"]
    assert after["phase"] == "TRACKING" and not after.get("exit_requested_at")


def test_next_exit_round_only_after_old_attempt_is_settled_and_cooldown(setup):
    store, _ = opened(setup)
    clock, repo, _, executor, broker = setup
    store.request_exit("trade:A", "TRAILING_PROFIT")
    broker.mode = "wait"
    asyncio.run(executor.step("trade:A:exit"))
    attempts = repo.attempts("trade:A:exit")
    attempt = attempts[-1]
    executor.drain_events()
    repo.finish(repo.require("jobs", "trade:A:exit"), "RETRY_BUDGET_EXHAUSTED")
    clock.advance(61)
    assert repo.recover_failed_exits() == 0  # working/unknown order cannot be retried
    repo.update_attempt(
        attempt, state="SETTLED", broker_status="Cancelled", settled_at=clock().isoformat()
    )
    assert repo.recover_failed_exits() == 0
    clock.advance(61)
    assert repo.recover_failed_exits() == 1
    assert repo.require("jobs", "trade:A:exit")["generation"] == 1


def test_realized_fifo_profit_stays_in_whole_trade_net_pnl(setup):
    from doxagent.exit_strategy.runner import net_pnl

    clock, repo, revision, executor, broker = setup
    store, _ = opened(setup)
    initial = store.positions()[0]
    quantity = initial["legs"][0]["quantity"]
    broker.quote_price = 110
    admit(repo, revision, identity="trade:opposite", side="SHORT")
    asyncio.run(run_job(executor, repo, clock, "trade:opposite:entry"))
    value = next(v for v in store.positions() if v["position_id"] == "trade:A")
    remaining = value["legs"][0]["quantity"]
    assert 0 <= remaining < quantity
    pnl = net_pnl(value, [110], ExitPolicy(), clock())
    assert pnl > quantity * 9  # shrinking quantity does not erase realized gains


def test_fee_callback_refreshes_net_cost_without_changing_policy(setup):
    store, policy = opened(setup)
    clock, repo, _, executor, _ = setup
    before = store.positions()[0]
    with repo.transaction(write=False) as db:
        fill_id = db.execute("SELECT exec_id FROM te_fills LIMIT 1").fetchone()[0]
    repo.event("DU_TEST", "fees", dict(exec_id=fill_id, commission="7", currency="USD"))
    executor.drain_events()
    after = store.positions()[0]
    assert after["legs"][0]["fees"] == 7
    assert after["book_version"] != before["book_version"]
    assert after["policy_revision"] == policy


def test_strategy_summary_is_valid_wire_and_hot_table_not_captured(setup):
    from doxagent.api_v2.dto import validate

    store, _ = opened(setup)
    _, repo, _, _, _ = setup
    store.request_exit("trade:A", "HARD_RISK")
    summary = repo.require("executions", "trade:A")["exit_strategy"]
    assert validate("ExitStrategySummary", summary) == summary
    assert summary["phase"] == "EXIT_REQUESTED"
    assert "ts_positions" not in SourceOutbox(repo.journal.path, "runtime").migrate(dry_run=True)


def test_one_bad_position_cannot_stop_sibling_updates_or_exits(setup):
    clock, repo, revision, executor, broker = setup
    store, _ = opened(setup)
    admit(repo, revision, identity="trade:B")
    asyncio.run(run_job(executor, repo, clock, "trade:B:entry"))
    with repo.transaction() as db:
        db.execute("DELETE FROM te_jobs WHERE id='trade:A:exit'")
    updates = store.positions()
    for value in updates:
        value["request_reason"] = "HARD_RISK"
    assert store.save_batch(updates) == {"trade:B"}
    assert repo.require("jobs", "trade:B:exit")["exit_requested_at"]
    bad = next(v for v in store.positions() if v["position_id"] == "trade:A")
    bad["policy_revision"] = "xp_missing"
    assert store.save_batch([bad]) == {"trade:A"}

    async def check():
        runner = ExitRunner(executor)
        try:
            await runner.tick(mono=100)
            a = next(v for v in store.positions() if v["position_id"] == "trade:A")
            assert a["data_health"] == "STATE_GAP"
            assert repo.require("jobs", "trade:B:exit")["exit_requested_at"]
        finally:
            await runner.close()

    asyncio.run(check())


def test_late_fill_after_flat_reopens_exit_obligation_not_trailing_trade(setup):
    clock, repo, _, executor, broker = setup
    store, _ = opened(setup)
    store.request_exit("trade:A", "HARD_RISK")
    asyncio.run(run_job(executor, repo, clock, "trade:A:exit"))

    async def close_projection():
        runner = ExitRunner(executor)
        try:
            await runner.tick(mono=100)
        finally:
            await runner.close()

    asyncio.run(close_projection())
    assert store.positions() == []
    entry_attempt = repo.attempts("trade:A:entry")[-1]
    broker.fill(entry_attempt, 1, 100)
    executor.drain_events()
    position = store.positions()[0]
    assert position["phase"] == "EXIT_REQUESTED"
    assert position["legs"][0]["quantity"] == 1
    asyncio.run(run_job(executor, repo, clock, "trade:A:exit"))
    assert int(broker.positions[9939]) == 50


def test_new_quote_received_during_db_read_is_not_misclassified_future_stale(setup):
    store, _ = opened(setup)
    _, _, _, executor, broker = setup

    async def check():
        runner = ExitRunner(executor)
        ident = store.positions()[0]["position_id"]
        runner.seeded.add(ident)
        runner.contexts[ident + ":" + ident] = (broker, broker.contract("MU"))

        def current(c):
            stamp = time.monotonic()
            return dict(
                bid=100,
                ask=100.01,
                bid_size=1,
                ask_size=1,
                market_data_type=1,
                bid_received_mono=stamp,
                ask_received_mono=stamp,
                generation=(1, 1),
            )

        broker.quote_snapshot = current
        broker.release_quote = lambda c, o: None
        try:
            await runner.tick()  # real monotonic clock, unlike deterministic replay tests
            value = store.positions()[0]
            assert value["valid_observations"] == 1
            assert value["invalid_observations"] == 0
        finally:
            await runner.close()

    asyncio.run(check())


def test_partial_fifo_reduction_of_group_leg_requests_whole_group_exit(setup):
    clock, repo, revision, executor, broker = setup
    store, _ = opened(setup)
    admit(repo, revision, identity="trade:B")
    asyncio.run(run_job(executor, repo, clock, "trade:B:entry"))
    store.group("group:AB", ["trade:A", "trade:B"], main_lot="trade:A", reason="test")
    broker.quote_price = 110
    admit(repo, revision, identity="trade:C", side="SHORT")
    asyncio.run(run_job(executor, repo, clock, "trade:C:entry"))
    value = next(v for v in store.positions() if v["position_id"] == "group:AB")
    assert all(leg["quantity"] > 0 for leg in value["legs"])
    assert value["pending_reason"] == "RESIDUAL_GROUP_EXIT"


def test_slow_subscription_release_does_not_block_observation_loop(setup):
    store, _ = opened(setup)
    clock, repo, _, executor, broker = setup
    store.request_exit("trade:A", "HARD_RISK")
    asyncio.run(run_job(executor, repo, clock, "trade:A:exit"))
    released = threading.Event()
    broker.release_quote = lambda c, o: released.wait(5)

    async def check():
        runner = ExitRunner(executor)
        runner.contexts["trade:A:trade:A"] = (broker, broker.contract("MU"))
        try:
            await asyncio.wait_for(runner.tick(mono=100), timeout=0.5)
            assert store.positions() == []
        finally:
            released.set()
            await runner.close()

    asyncio.run(check())


def test_failed_trigger_commit_retries_without_losing_new_tighter_line(setup):
    import sqlite3

    store, _ = opened(setup)
    clock, _, _, executor, broker = setup
    value = store.positions()[0]
    value.update(phase="ARMED", mfe=1000, stop_net=300, activated_at=clock().timestamp() - 100)
    store.save_batch([value])

    async def check():
        runner = ExitRunner(executor)
        ident = value["position_id"]
        runner.contexts[ident + ":" + ident] = (broker, broker.contract("MU"))
        runner.seeded.add(ident)
        window = PriceWindow()
        index = [0]
        window.atr = lambda *a: 200 if index[0] >= 6 else 280
        runner.windows[ident] = window
        current = {}
        broker.quote_snapshot = lambda c: current
        broker.release_quote = lambda c, o: None
        save = runner.repository.save_batch
        failed = [False]

        def commit(updates):
            if any(v.get("request_reason") for v in updates) and not failed[0]:
                failed[0] = True
                raise sqlite3.OperationalError("database is locked")
            return save(updates)

        runner.repository.save_batch = commit
        initial = clock.value
        qty = value["legs"][0]["quantity"]
        try:
            for i, pnl in enumerate([290, 290, 290, 290, 290, 600, 450, 450]):
                index[0] = i
                clock.value = initial + timedelta(seconds=5 * i)
                price = 100 + (pnl + 2) / qty
                current.update(
                    bid=price,
                    ask=price + 0.001,
                    bid_size=1,
                    ask_size=1,
                    bid_received_mono=100 + i * 5,
                    ask_received_mono=100 + i * 5,
                    market_data_type=1,
                    generation=(1, 1),
                )
                try:
                    await runner.tick(mono=100 + i * 5)
                except sqlite3.OperationalError:
                    assert i == 6
            result = store.positions()[0]
            assert failed[0] and result["phase"] == "EXIT_REQUESTED"
            assert result["stop_net"] == 500
            assert result["exit_evidence"]["stop_net"] == 500
        finally:
            await runner.close()

    asyncio.run(check())
