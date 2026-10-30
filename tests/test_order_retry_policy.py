"""Requested order policy against the real executor and durable cancel reconciliation."""

import asyncio
import time
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from doxagent.trade_execution.executor import Executor
from doxagent.trade_execution.strategy import entry_book_ready, order_type, price_and_quantity
from tests.test_trade_execution import admit, profile, run_job
from tests.test_trade_execution import setup as setup_fixture

setup = setup_fixture


def stream(broker, clock, *, bid=100, ask=100):
    book = {"bid": bid, "ask": ask}
    samples, releases = [], []

    def snapshot(contract):
        samples.append(clock())
        return {
            "market_data_type": 1,
            "bid_size": "100",
            "ask_size": "100",
            "bid_received_at": time.time(),
            "ask_received_at": time.time(),
            **book,
        }

    def quote(contract, side, **kwargs):
        value = snapshot(contract)
        if not value.get("ask" if side == "BUY" else "bid"):
            raise ValueError("QUOTE_UNAVAILABLE")
        return value

    broker.subscribe_quote = lambda *args, **kwargs: None
    broker.quote_snapshot = snapshot
    broker.release_quote = lambda contract, owner: releases.append(owner)
    broker.quote = quote
    return book, samples, releases


def tick(executor, clock, seconds=0, job="trade:A:entry"):
    clock.advance(seconds)
    asyncio.run(executor.step(job))


@pytest.mark.parametrize("session", ["RTH", "EXTENDED", "OVERNIGHT"])
@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_all_prices_are_half_percent_of_this_attempt_and_exit_is_unbounded(session, side):
    for retry in (False, True):
        result = price_and_quantity(
            side=side,
            kind="LMT",
            retry=retry,
            session=session,
            quote={"ask": "104", "bid": "104"},
            rules=[{"low_edge": "0", "increment": ".01"}],
            strategy=profile().strategy,
            remaining_notional=Decimal(20000),
        )
        assert result["limit_price"] == ("104.52" if side == "BUY" else "103.48")
    for count in range(8):
        attempts = [{"order_type": "LMT"}] * count
        assert order_type(attempts, session, "ENTRY") == ("LMT" if count < 3 else None)
        assert order_type(attempts, session, "EXIT") == "LMT"


def test_book_is_strictly_under_three_percent_and_both_sides_are_fresh(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 500)
    value = dict(
        bid="98.5",
        ask="101.5",
        bid_size="1",
        ask_size="1",
        bid_received_at=500,
        ask_received_at=500,
        market_data_type=1,
    )
    assert not entry_book_ready(value, 5)  # Exactly 3% fails.
    value["bid"] = "98.51"
    assert entry_book_ready(value, 5)
    for mutation in (
        {"bid": None},
        {"ask": 0},
        {"bid_size": "0"},
        {"ask_pending": True},
        {"bid_received_at": 494},
        {"market_data_type": 2},
        {"bid": "103"},
        {"ask": "NaN"},
    ):
        assert not entry_book_ready({**value, **mutation}, 5)


@pytest.mark.parametrize("hour", [8, 21, 1])
def test_non_rth_window_submits_as_soon_as_book_passes_and_order_wait_is_separate(setup, hour):
    clock, repo, revision, executor, broker = setup
    clock.value = datetime(2026, 9, 8, hour, tzinfo=UTC)
    if hour == 1:
        # The regular session calendar provides the overnight venue contract.
        broker.contract = lambda ticker, exchange="SMART": dict(
            con_id=9939,
            symbol=ticker,
            currency="USD",
            exchange=exchange,
            time_zone="US/Eastern",
            trading_hours="20260907:2000-20260908:0400",
        )
    book, _, _ = stream(broker, clock, bid=95, ask=100)
    broker.mode = "unfilled"
    admit(repo, revision)
    tick(executor, clock)
    start = repo.require("jobs", "trade:A:entry")["entry_book_started_at"]
    assert not broker.sent
    tick(executor, clock, 20)
    assert not broker.sent
    book["bid"] = 99
    tick(executor, clock, 0.2)
    assert len(broker.sent) == 1
    assert repo.require("jobs", "trade:A:entry")["entry_book_started_at"] == start
    assert (
        datetime.fromisoformat(broker.sent[0]["sent_at"]) - datetime.fromisoformat(start)
    ).total_seconds() == 20.2
    tick(executor, clock, 4.9)
    assert not broker.cancels
    tick(executor, clock, 0.1)
    assert len(broker.cancels) == 1


@pytest.mark.parametrize("bid,ask", [(None, 100), (95, 100)])
def test_thirty_second_timeout_persists_across_restart_without_spending_order_or_failure_budget(
    setup, bid, ask
):
    clock, repo, revision, executor, broker = setup
    clock.value = datetime(2026, 9, 8, 21, tzinfo=UTC)
    stream(broker, clock, bid=bid, ask=ask)
    admit(repo, revision)
    tick(executor, clock)
    start = repo.require("jobs", "trade:A:entry")["entry_book_started_at"]
    executor.close()
    executor = Executor(repo, broker_factory=lambda *args, **kwargs: broker)
    tick(executor, clock, 29.9)
    assert not broker.sent
    tick(executor, clock, 0.1)
    job = repo.require("jobs", "trade:A:entry")
    assert job["state"] == "DONE" and job["reason"] == "ENTRY_BOOK_TIMEOUT"
    assert job["entry_book_started_at"] == start
    assert not repo.attempts(job["id"]) and not broker.sent
    assert not job.get("entry_failures")
    assert not executor.quote_watches


def test_waiting_book_reads_stream_without_repeated_account_snapshots(setup):
    clock, repo, revision, executor, broker = setup
    clock.value = datetime(2026, 9, 8, 21, tzinfo=UTC)
    book, _, releases = stream(broker, clock, bid=95, ask=100)
    sync, calls = broker.sync, []

    def counted():
        calls.append(1)
        return sync()

    broker.sync = counted
    admit(repo, revision)
    tick(executor, clock)
    initial = len(calls)
    for _ in range(4):
        tick(executor, clock, 0.2)
    assert len(calls) == initial and not broker.sent
    book["bid"] = 99
    tick(executor, clock, 0.2)
    assert len(broker.sent) == 1
    tick(executor, clock, 1)
    tick(executor, clock, 0.2)
    assert releases and not executor.quote_watches


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_entry_early_cancel_needs_two_seconds_then_reprices_single_sided_retry(setup, direction):
    clock, repo, revision, executor, broker = setup
    clock.value = datetime(2026, 9, 8, 21, tzinfo=UTC)
    book, _, _ = stream(broker, clock)
    broker.mode = "unfilled"
    sync = broker.sync
    sync_calls = []

    def counted_sync():
        sync_calls.append(clock())
        return sync()

    broker.sync = counted_sync
    admit(repo, revision, side=direction)
    tick(executor, clock)
    assert len(broker.sent) == 1
    before_monitor = len(sync_calls)
    passed = repo.require("jobs", "trade:A:entry")["entry_book_passed_at"]
    if direction == "LONG":
        book.update(ask=101, bid=None)
    else:
        book.update(bid=99, ask=None)
    tick(executor, clock, 1)
    tick(executor, clock, 1)
    assert not broker.cancels
    assert len(sync_calls) == before_monitor
    tick(executor, clock, 1)
    assert len(broker.cancels) == 1
    assert repo.attempts("trade:A:entry")[0]["cancel_reason"] == "PRICE_OUTSIDE_LIMIT"
    # A cancel request alone cannot spend the next opportunity.
    assert len(broker.sent) == 1
    for _ in range(4):
        tick(executor, clock, 0.2)
    assert len(broker.sent) == 2
    assert broker.sent[-1]["limit_price"] == ("101.51" if direction == "LONG" else "98.50")
    assert repo.require("jobs", "trade:A:entry")["entry_book_passed_at"] == passed


def test_rth_skips_bilateral_gate_and_unconfirmed_early_cancel_cannot_duplicate(setup):
    clock, repo, revision, executor, broker = setup
    book, _, _ = stream(broker, clock, bid=None)
    broker.mode = "stuck_cancel"
    admit(repo, revision)
    tick(executor, clock)
    assert broker.sent and not repo.require("jobs", "trade:A:entry").get("entry_book_started_at")
    book["ask"] = 102
    for _ in range(12):
        tick(executor, clock, 1)
    assert len(broker.sent) == 1 and broker.cancels


def test_invalid_or_returned_inside_quote_breaks_the_early_cancel_streak(setup):
    clock, repo, revision, executor, broker = setup
    book, _, _ = stream(broker, clock)
    broker.mode = "unfilled"
    admit(repo, revision)
    tick(executor, clock)
    for ask in (102, None, 102, 100, 102):
        book["ask"] = ask
        tick(executor, clock, 0.9)
        assert not broker.cancels
    tick(executor, clock, 0.5)
    assert repo.attempts("trade:A:entry")[0]["cancel_reason"] == "ORDER_WAIT_ELAPSED"


def test_entry_three_chances_all_lmt_and_partial_fill_keeps_exit_duty(setup):
    clock, repo, revision, executor, broker = setup
    stream(broker, clock)
    broker.mode = "unfilled"
    admit(repo, revision)
    tick(executor, clock)
    broker.fill(broker.sent[0], 1)
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry", limit=30))
    assert len(broker.sent) == 3 and all(a["order_type"] == "LMT" for a in broker.sent)
    assert repo.require("executions", "trade:A")["entry_result"] == "PARTIAL_FILLED"
    assert repo.require("lots", "trade:A")["remaining_qty"] == 1
    broker.mode = "fill"
    clock.value = datetime.fromisoformat(repo.require("lots", "trade:A")["scheduled_exit"])
    asyncio.run(run_job(executor, repo, clock, "trade:A:exit"))
    assert (
        broker.sent[-1]["quantity"] == 1 and repo.require("lots", "trade:A")["remaining_qty"] == 0
    )


def test_exit_continues_past_three_lmt_orders_and_only_sends_remaining_quantity(setup):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    initial = repo.require("lots", "trade:A")["remaining_qty"]
    clock.value = datetime.fromisoformat(repo.require("lots", "trade:A")["scheduled_exit"])
    broker.mode = "unfilled"
    seen = set()
    for _ in range(55):
        tick(executor, clock, 1, "trade:A:exit")
        exits = [a for a in broker.sent if a["job_id"].endswith(":exit")]
        if exits and exits[-1]["id"] not in seen:
            seen.add(exits[-1]["id"])
            broker.fill(exits[-1], 1)
        if len(exits) >= 5:
            break
    assert len(exits) >= 5 and all(a["order_type"] == "LMT" for a in exits)
    assert [a["quantity"] for a in exits] == list(range(initial, initial - len(exits), -1))
    assert repo.require("jobs", "trade:A:exit")["state"] not in {"DONE", "FAILED"}
    broker.mode = "fill"
    asyncio.run(run_job(executor, repo, clock, "trade:A:exit", limit=30))
    assert repo.require("lots", "trade:A")["remaining_qty"] == 0
    assert broker.positions[9939] == "50"
