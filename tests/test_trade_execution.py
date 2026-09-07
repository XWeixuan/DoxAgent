import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.trade_execution.acceptance import create_suite, tick_suites
from doxagent.trade_execution.executor import Executor
from doxagent.trade_execution.repository import ExecutionRepository
from doxagent.trade_execution.schema import ExecutionProfile, Strategy, account_fuse
from doxagent.trade_execution.sessions import Sessions
from doxagent.trade_execution.strategy import order_type, price_and_quantity, round_price
from doxagent.trade_execution.worker import WriterLock


class Clock:
    value = datetime(2026, 9, 8, 14, tzinfo=UTC)

    def __call__(self):
        return self.value

    def advance(self, seconds=6):
        self.value += timedelta(seconds=seconds)


def profile(**changes):
    return ExecutionProfile(
        profile_id="paper",
        environment="PAPER",
        account_mode="PAPER",
        host="127.0.0.1",
        port=7497,
        client_id=82,
        expected_account_id="DU_TEST",
        short_enabled=True,
        **changes,
    )


def admit(repo, revision, identity="trade:A", side="LONG"):
    p = repo.profile(revision)
    value = {
        "intent_id": identity,
        "ticker": "MU",
        "status": "READY",
        "expires_at": (repo.journal.clock() + timedelta(hours=12)).isoformat(),
        "trade": {"decision": side},
        "execution_pin": {"revision": revision, "profile": p.model_dump(mode="json")},
    }
    repo.admit(value)
    return value


class Broker:
    def __init__(self, p, *, event_sink=None, write_guard=None):
        self.profile = p
        self.sink = event_sink
        self.next_id = 1
        self.sent = []
        self.cancels = []
        self.positions = {9939: "50"}  # Manual baseline must never be sold by a lot exit.
        self.mode = "fill"
        self.fill_serial = 0
        self.cumulative = {}
        self.quote_calls = 0
        self.quote_price = 100
        self.clock = None

    def connect(self):
        pass

    def close(self):
        pass

    def sync(self):
        return {"positions": dict(self.positions), "at": self.clock().isoformat(), "complete": True}

    def contract(self, ticker, exchange="SMART"):
        return {
            "con_id": 9939,
            "symbol": ticker,
            "currency": "USD",
            "exchange": exchange,
            "time_zone": "US/Eastern",
            "trading_hours": "20260908:0400-20260908:2000",
        }

    def market_rules(self, contract):
        return [{"low_edge": "0", "increment": "0.01"}]

    def quote(self, contract, side, **kwargs):
        self.quote_calls += 1
        if self.mode == "no_quote":
            raise ValueError("QUOTE_UNAVAILABLE")
        return {"bid": self.quote_price, "ask": self.quote_price, "market_data_type": 1}

    def order_event(self, a, status):
        self.sink(
            "order",
            {
                "order_id": a["order_id"],
                "order_ref": a["order_ref"],
                "client_id": a["client_id"],
                "status": status,
                "filled": str(self.cumulative.get(a["id"], 0)),
            },
        )

    def fill(self, a, quantity, price=100):
        self.fill_serial += 1
        total = self.cumulative.get(a["id"], 0) + quantity
        self.cumulative[a["id"]] = total
        self.positions[9939] = str(
            int(self.positions[9939]) + quantity * (1 if a["side"] == "BUY" else -1)
        )
        self.sink(
            "fill",
            {
                "account": a["account"],
                "client_id": a["client_id"],
                "order_id": a["order_id"],
                "order_ref": a["order_ref"],
                "con_id": 9939,
                "exec_id": f"EXEC{self.fill_serial:04d}.01",
                "quantity": str(quantity),
                "price": str(price),
                "side": a["side"],
                "time": self.clock().isoformat(),
                "cum_qty": str(total),
            },
        )

    def submit(self, a):
        self.sent.append(a)
        self.next_id = a["order_id"] + 1
        self.order_event(a, "Submitted")
        if self.mode == "fill" or a["order_type"] == "MKT":
            self.fill(a, a["quantity"], self.quote_price)
            self.order_event(a, "Filled")
        elif self.mode == "partial":
            self.fill(a, 100, 99.8)

    def cancel(self, a):
        self.cancels.append(a)
        if self.mode == "stuck_cancel":
            return
        if self.mode == "partial":
            self.fill(a, 30, 100)
            self.quote_price = 105
        self.order_event(a, "Cancelled")


@pytest.fixture
def setup(tmp_path):
    clock = Clock()
    repo = ExecutionRepository(RuntimeJournal(tmp_path / "runtime.db", clock=clock))
    revision = repo.import_profile(profile())
    brokers = []

    def factory(p, **kwargs):
        broker = Broker(p, **kwargs)
        broker.clock = clock
        brokers.append(broker)
        return broker

    executor = Executor(repo, broker_factory=factory)
    broker = executor.broker(revision)
    return clock, repo, revision, executor, broker


async def run_job(executor, repo, clock, job, limit=20):
    for _ in range(limit):
        await executor.step(job)
        clock.advance()
        if repo.get("jobs", job)["state"] in {"DONE", "FAILED"}:
            break


def test_price_rules_retry_matrix_and_sizing():
    rules = [{"low_edge": "0", "increment": "0.01"}, {"low_edge": "100", "increment": "0.05"}]
    assert round_price(Decimal("99.999"), "BUY", rules) == Decimal("100.00")
    assert round_price(Decimal("100.023"), "BUY", rules) == Decimal("100.05")
    assert round_price(Decimal("98.763"), "SELL", rules) == Decimal("98.76")
    result = price_and_quantity(
        side="BUY",
        kind="LMT",
        retry=True,
        quote={"ask": 104},
        rules=rules[:1],
        strategy=Strategy(),
        remaining_notional=Decimal("10020"),
    )
    assert result["limit_price"] == "106.08" and result["quantity"] == 94
    assert order_type([], "RTH") == "LMT"
    assert order_type([{"order_type": "LMT"}], "RTH") == "MKT"
    assert order_type([{"order_type": "LMT"}] * 2, "RTH") is None
    assert order_type([{"order_type": "LMT"}] * 2, "EXTENDED") == "LMT"
    assert order_type([{"order_type": "LMT"}] * 3, "EXTENDED") is None
    assert order_type([{"order_type": "MKT"}], "EXTENDED") is None


def test_calendar_exit_holiday_early_close_and_cutoff(setup):
    _, repo, _, _, _ = setup
    sessions = Sessions(repo.journal)
    assert sessions.exit_at(datetime(2026, 9, 4, 22, tzinfo=UTC)) == datetime(
        2026, 9, 8, 19, 30, tzinfo=UTC
    )
    assert sessions.exit_at(datetime(2026, 9, 8, 19, 30, tzinfo=UTC)) == datetime(
        2026, 9, 9, 19, 30, tzinfo=UTC
    )
    assert sessions.exit_at(datetime(2026, 11, 27, 15, tzinfo=UTC)) == datetime(
        2026, 11, 27, 17, 30, tzinfo=UTC
    )


def test_long_fill_and_exit_only_owned_lot(setup):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    lot = repo.get("lots", "trade:A")
    assert lot["remaining_qty"] == 198
    assert repo.get("executions", "trade:A")["entry_result"] == "FILLED"
    assert len(broker.sent) == 1  # Price improvement does not initiate extra top-up.
    clock.value = datetime.fromisoformat(lot["scheduled_exit"])
    asyncio.run(run_job(executor, repo, clock, "trade:A:exit"))
    assert broker.sent[-1]["side"] == "SELL" and broker.sent[-1]["quantity"] == 198
    assert broker.positions[9939] == "50"
    assert repo.get("lots", "trade:A")["remaining_qty"] == 0


def test_partial_cancel_race_reprices_remaining_budget(setup):
    clock, repo, revision, executor, broker = setup
    broker.mode = "partial"
    admit(repo, revision)
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    assert len(broker.sent) == 2 and len(broker.cancels) == 1
    assert broker.sent[-1]["order_type"] == "MKT"
    assert broker.sent[-1]["quantity"] == 66  # (20000 - 9980 - 3000) / fresh 105
    assert repo.get("lots", "trade:A")["remaining_qty"] == 196


def test_cancel_unknown_never_submits_next_order(setup):
    clock, repo, revision, executor, broker = setup
    broker.mode = "stuck_cancel"
    admit(repo, revision)
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry", limit=9))
    assert len(broker.sent) == 1
    assert repo.get("jobs", "trade:A:entry")["state"] == "RECONCILE_REQUIRED"
    assert repo.journal.values("execution_gaps")


def test_no_quote_does_not_block_another_job_or_create_orders(setup):
    clock, repo, revision, executor, broker = setup
    broker.mode = "no_quote"
    admit(repo, revision)
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    assert repo.get("executions", "trade:A")["entry_result"] == "FAILED"
    assert not broker.sent
    broker.mode = "fill"
    admit(repo, revision, "trade:B")
    asyncio.run(run_job(executor, repo, clock, "trade:B:entry"))
    assert repo.get("executions", "trade:B")["entry_result"] == "FILLED"


def test_fifo_and_duplicate_fills_do_not_close_manual_position(setup):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    admit(repo, revision, "trade:B", "SHORT")
    asyncio.run(run_job(executor, repo, clock, "trade:B:entry"))
    assert broker.sent[-1]["quantity"] == 202
    assert repo.get("lots", "trade:A")["remaining_qty"] == 0
    assert repo.get("lots", "trade:B")["remaining_qty"] == 4
    assert repo.get("jobs", "trade:A:exit")["state"] == "DONE"
    fill = repo.fills("trade:B:entry")[0]
    repo.event(broker.profile.expected_account_id, "fill", fill)
    executor.drain_events()
    assert repo.get("lots", "trade:B")["remaining_qty"] == 4
    asyncio.run(run_job(executor, repo, clock, "trade:B:exit"))
    assert broker.positions[9939] == "50"


def test_hot_switch_does_not_move_existing_execution(setup):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    live = ExecutionProfile(
        profile_id="live",
        environment="LIVE",
        account_mode="LIVE_CASH",
        port=7496,
        client_id=81,
        expected_account_id="U_TEST",
    )
    live_rev = repo.import_profile(live)
    repo.activate(live_rev, "test switch")
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    assert broker.sent[0]["account"] == "DU_TEST"
    admit(repo, live_rev, "trade:B", "SHORT")
    asyncio.run(executor.step("trade:B:entry"))
    assert repo.get("executions", "trade:B")["entry_result"] == "DIRECTION_DISABLED"
    with pytest.raises(ValueError, match="ACCOUNT_MISMATCH"):
        account_fuse(profile(), ["U_TEST"])


def test_non_rth_three_attempts_then_stop(setup):
    clock, repo, revision, executor, broker = setup
    clock.value = datetime(2026, 9, 8, 21, tzinfo=UTC)
    broker.mode = "unfilled"
    admit(repo, revision)
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry", limit=24))
    assert len(broker.sent) == 3 and len(broker.cancels) == 3
    assert [a["limit_price"] for a in broker.sent] == ["101.00", "102.00", "102.00"]
    assert all(a["order_type"] == "LMT" for a in broker.sent)
    assert repo.get("executions", "trade:A")["entry_result"] == "FAILED"


def test_acceptance_waits_and_never_uses_live(setup):
    clock, repo, revision, executor, broker = setup
    create_suite(repo, identity="rth", revision=revision, arm=False)
    asyncio.run(tick_suites(executor))
    assert not repo.get("executions", "acceptance:rth")
    clock.value = datetime(2026, 9, 6, 15, tzinfo=UTC)
    create_suite(repo, identity="weekend", revision=revision, arm=True)
    asyncio.run(tick_suites(executor))
    assert repo.get("acceptance_runs", "weekend")["state"] == "WAIT_MARKET"
    assert not broker.sent


def test_writer_singleton(tmp_path):
    with WriterLock(tmp_path / "runtime.db") as first:
        first.assert_owned()
        with pytest.raises(RuntimeError, match="EXECUTOR_ALREADY_RUNNING"):
            with WriterLock(tmp_path / "runtime.db"):
                pass
    with WriterLock(tmp_path / "runtime.db") as second:
        second.assert_owned()
