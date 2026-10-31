import asyncio
import json
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from doxagent.persistent_runtime_v2.journal import RuntimeJournal, digest, encode
from doxagent.persistent_runtime_v2.schema import TradeRecord
from doxagent.persistent_runtime_v2.trade_output import TradeOutputService, event_trade_key
from doxagent.trade_execution.executor import Executor
from doxagent.trade_execution.repository import ExecutionRepository
from doxagent.trade_execution.schema import Strategy
from doxagent.trade_execution.strategy import order_type, price_and_quantity
from tests.test_trade_execution import Clock, profile, run_job


class PortfolioBroker:
    symbols = {"MU": 1, "BE": 2, "ONDS": 3, "OTHER": 4}

    def __init__(self, account_profile, *, event_sink=None, write_guard=None):
        self.profile = account_profile
        self.sink = event_sink
        self.next_id = 1
        self.clock = None
        self.positions = {}
        self.prices = {name: Decimal("100") for name in self.symbols}
        self.equity = Decimal("20000")
        self.available = Decimal("30000")
        self.margin_change = Decimal("500")
        self.sent = []
        self.previews = []
        self.mode = "fill"
        self.serial = 0

    def connect(self):
        pass

    def close(self):
        pass

    def sync(self):
        portfolio = [
            {
                "con_id": con_id,
                "symbol": ticker,
                "security_type": "STK",
                "currency": "USD",
                "quantity": str(self.positions.get(con_id, 0)),
                "market_value": str(self.positions.get(con_id, 0) * self.prices[ticker]),
            }
            for ticker, con_id in self.symbols.items()
            if self.positions.get(con_id, 0)
        ]
        return {
            "complete": True,
            "positions": {key: str(value) for key, value in self.positions.items()},
            "portfolio": portfolio,
            "account_values": {
                "AccountReady": "true",
                "NetLiquidation": {"value": str(self.equity), "currency": "USD"},
                "AvailableFunds": {"value": str(self.available), "currency": "USD"},
                "LookAheadAvailableFunds": {"value": str(self.available), "currency": "USD"},
            },
            "at": self.clock().isoformat(),
        }

    def contract(self, ticker, exchange="SMART"):
        day = self.clock().astimezone(ZoneInfo("America/New_York")).date().strftime("%Y%m%d")
        return {
            "con_id": self.symbols[ticker],
            "symbol": ticker,
            "currency": "USD",
            "exchange": exchange,
            "time_zone": "US/Eastern",
            "trading_hours": f"{day}:0400-{day}:2000",
        }

    def market_rules(self, contract):
        return [{"low_edge": "0", "increment": "0.01"}]

    def quote(self, contract, side, **kwargs):
        value = self.prices[contract["symbol"]]
        return {
            "bid": str(value),
            "ask": str(value),
            "market_data_type": 1,
            "bid_size": "100",
            "ask_size": "100",
            "bid_received_at": time.time(),
            "ask_received_at": time.time(),
        }

    def what_if(self, contract, order_id, **kwargs):
        self.previews.append({"contract": contract, "order_id": order_id, **kwargs})
        return {
            "status": "VALIDATED",
            "rows": [{"initial_margin_change": str(self.margin_change), "warning": ""}],
        }

    def submit(self, attempt):
        self.sent.append(attempt)
        self.next_id = attempt["order_id"] + 1
        self.sink(
            "order",
            {
                "order_id": attempt["order_id"],
                "client_id": attempt["client_id"],
                "order_ref": attempt["order_ref"],
                "status": "Submitted",
                "filled": "0",
            },
        )
        if self.mode == "reject_margin":
            error = {
                "order_id": attempt["order_id"],
                "client_id": attempt["client_id"],
                "code": 201,
                "message": "Equity with Loan Value below Initial Margin",
                "advanced_order_reject_json": '{"reason":"margin"}',
            }
            self.sink("error", error)
            self.sink(
                "order",
                {
                    "order_id": attempt["order_id"],
                    "client_id": attempt["client_id"],
                    "order_ref": attempt["order_ref"],
                    "status": "Rejected",
                    "filled": "0",
                    "error": error,
                },
            )
            self.sink(
                "order",
                {
                    "order_id": attempt["order_id"],
                    "client_id": attempt["client_id"],
                    "order_ref": attempt["order_ref"],
                    "status": "Cancelled",
                    "filled": "0",
                },
            )
            return
        if self.mode == "unfilled":
            return
        quantity = attempt["quantity"]
        self.positions[attempt["contract"]["con_id"]] = self.positions.get(
            attempt["contract"]["con_id"], 0
        ) + quantity * (1 if attempt["side"] == "BUY" else -1)
        self.serial += 1
        self.sink(
            "fill",
            {
                "account": attempt["account"],
                "client_id": attempt["client_id"],
                "order_id": attempt["order_id"],
                "order_ref": attempt["order_ref"],
                "con_id": attempt["contract"]["con_id"],
                "exec_id": f"SHARED{self.serial}.01",
                "quantity": str(quantity),
                "cum_qty": str(quantity),
                "price": str(self.prices[attempt["contract"]["symbol"]]),
                "side": attempt["side"],
                "time": self.clock().isoformat(),
            },
        )
        self.sink(
            "order",
            {
                "order_id": attempt["order_id"],
                "client_id": attempt["client_id"],
                "order_ref": attempt["order_ref"],
                "status": "Filled",
                "filled": str(quantity),
            },
        )

    def cancel(self, attempt):
        self.sink(
            "order",
            {
                "order_id": attempt["order_id"],
                "client_id": attempt["client_id"],
                "order_ref": attempt["order_ref"],
                "status": "Cancelled",
                "filled": "0",
            },
        )


def setup_shared(tmp_path):
    clock = Clock()
    repo = ExecutionRepository(RuntimeJournal(tmp_path / "shared.db", clock=clock))
    revision = repo.import_profile(profile(strategy=Strategy()))
    from doxagent.trade_execution.capital import CapitalLedger

    CapitalLedger(repo).allocate("DU_TEST", "20000", "initial", "test")
    created = []

    def factory(p, **kwargs):
        broker = PortfolioBroker(p, **kwargs)
        broker.clock = clock
        created.append(broker)
        return broker

    executor = Executor(repo, broker_factory=factory)
    return clock, repo, revision, executor, executor.broker(revision)


def admit_ticker(repo, revision, ticker, *, direction="LONG", identity=None):
    repo.admit(
        {
            "intent_id": identity or f"trade:{ticker}",
            "ticker": ticker,
            "status": "READY",
            "expires_at": (repo.journal.clock() + timedelta(hours=12)).isoformat(),
            "trade": {"decision": direction},
            "execution_pin": {
                "revision": revision,
                "profile": repo.profile_payload(revision),
            },
        }
    )


def test_shared_pool_counts_filled_positions_and_integer_minimum(tmp_path):
    clock, repo, revision, executor, broker = setup_shared(tmp_path)
    admit_ticker(repo, revision, "MU")
    asyncio.run(run_job(executor, repo, clock, "trade:MU:entry"))
    assert broker.sent[0]["quantity"] == 198
    assert broker.previews[0]["quantity"] == 198
    admit_ticker(repo, revision, "BE")
    asyncio.run(run_job(executor, repo, clock, "trade:BE:entry"))
    assert broker.sent[1]["quantity"] == 99
    admit_ticker(repo, revision, "ONDS")
    asyncio.run(run_job(executor, repo, clock, "trade:ONDS:entry"))
    assert len(broker.sent) == 2
    assert repo.require("executions", "trade:ONDS")["entry_reason"] == (
        "PORTFOLIO_CAPACITY_EXHAUSTED"
    )


def test_shared_pool_reserves_unfilled_entry_before_second_ticker(tmp_path):
    clock, repo, revision, executor, broker = setup_shared(tmp_path)
    broker.mode = "unfilled"
    admit_ticker(repo, revision, "MU")
    admit_ticker(repo, revision, "BE")

    async def start_both():
        await asyncio.gather(executor.step("trade:MU:entry"), executor.step("trade:BE:entry"))

    asyncio.run(start_both())
    jobs = [repo.require("jobs", f"trade:{ticker}:entry") for ticker in ("MU", "BE")]
    assert sum(Decimal(job.get("reserved_open_usd", "0")) for job in jobs) <= 30000
    assert len(broker.sent) == 2
    assert sorted(attempt["quantity"] for attempt in broker.sent) == [99, 198]


def test_same_origin_reverse_cannot_borrow_full_grant_or_net_old_group(tmp_path):
    clock, repo, revision, executor, broker = setup_shared(tmp_path)
    admit_ticker(repo, revision, "MU")
    asyncio.run(run_job(executor, repo, clock, "trade:MU:entry"))
    admit_ticker(repo, revision, "MU", direction="SHORT", identity="trade:MU:reverse")
    asyncio.run(run_job(executor, repo, clock, "trade:MU:reverse:entry"))
    assert len(broker.sent) == 1
    assert repo.require("lots", "trade:MU")["remaining_qty"] == 198
    assert (
        repo.require("executions", "trade:MU:reverse")["entry_reason"]
        == "PORTFOLIO_CAPACITY_EXHAUSTED"
    )


def test_personal_position_notional_does_not_replace_own_budget(tmp_path):
    clock, repo, revision, executor, broker = setup_shared(tmp_path)
    broker.positions[broker.symbols["OTHER"]] = 240
    admit_ticker(repo, revision, "MU")
    asyncio.run(run_job(executor, repo, clock, "trade:MU:entry"))
    assert broker.sent[0]["quantity"] == 198
    assert broker.positions[broker.symbols["OTHER"]] == 240


def test_rounding_and_margin_fail_without_submitting(tmp_path):
    clock, repo, revision, executor, broker = setup_shared(tmp_path)
    from doxagent.trade_execution.capital import CapitalLedger

    ledger = CapitalLedger(repo)
    ledger.allocate("DU_TEST", "24000", "increase", "test initial grant")
    admit_ticker(repo, revision, "MU")
    asyncio.run(run_job(executor, repo, clock, "trade:MU:entry"))
    ledger.allocate("DU_TEST", "20000", "decrease", "new grants use reduced allocation")
    broker.prices["ONDS"] = Decimal("4700")
    admit_ticker(repo, revision, "ONDS")
    asyncio.run(run_job(executor, repo, clock, "trade:ONDS:entry"))
    assert len(broker.sent) == 1
    assert (
        repo.require("executions", "trade:ONDS")["entry_reason"] == "PORTFOLIO_CAPACITY_EXHAUSTED"
    )

    margin_dir = tmp_path / "margin"
    margin_dir.mkdir()
    clock, repo, revision, executor, broker = setup_shared(margin_dir)
    broker.margin_change = Decimal("27000")
    admit_ticker(repo, revision, "MU")
    asyncio.run(run_job(executor, repo, clock, "trade:MU:entry"))
    assert not broker.sent
    assert repo.require("executions", "trade:MU")["entry_reason"] == "INSUFFICIENT_MARGIN"


def test_loss_reduces_next_cycle_equity_after_confirmed_exit(tmp_path):
    clock, repo, revision, executor, broker = setup_shared(tmp_path)
    admit_ticker(repo, revision, "MU")
    asyncio.run(run_job(executor, repo, clock, "trade:MU:entry"))
    first = repo.require("jobs", "trade:MU:entry")
    assert first["cycle_id"] == "margin:2026-09-08"
    clock.value = datetime.fromisoformat(repo.require("lots", "trade:MU")["scheduled_exit"])
    broker.prices["MU"] = Decimal("90")
    asyncio.run(run_job(executor, repo, clock, "trade:MU:exit"))
    assert repo.require("lots", "trade:MU")["remaining_qty"] == 0
    broker.equity = Decimal("18500")
    admit_ticker(repo, revision, "BE")
    asyncio.run(run_job(executor, repo, clock, "trade:BE:entry"))
    assert broker.sent[-1]["quantity"] == 179
    assert repo.require("jobs", "trade:BE:entry")["cycle_id"] == "margin:2026-09-09"


def test_cycle_equity_is_captured_after_exit_without_waiting_for_next_intent(tmp_path):
    clock, repo, revision, executor, broker = setup_shared(tmp_path)
    admit_ticker(repo, revision, "MU")
    asyncio.run(run_job(executor, repo, clock, "trade:MU:entry"))
    clock.value = datetime.fromisoformat(repo.require("lots", "trade:MU")["scheduled_exit"])
    broker.equity = Decimal("18500")
    asyncio.run(executor.capture_ready_cycles([revision]))
    with repo.journal.transaction() as db:
        assert not db.execute(
            "SELECT 1 FROM te_account_cycles WHERE cycle_id='margin:2026-09-09'"
        ).fetchone()
    asyncio.run(run_job(executor, repo, clock, "trade:MU:exit"))
    asyncio.run(executor.capture_ready_cycles([revision]))
    with repo.journal.transaction() as db:
        row = db.execute(
            "SELECT equity FROM te_account_cycles WHERE cycle_id='margin:2026-09-09'"
        ).fetchone()
    assert row[0] == "19998"


def test_old_cycle_unfilled_entry_is_cancelled_at_exit_cutoff(tmp_path):
    clock, repo, revision, executor, broker = setup_shared(tmp_path)
    broker.mode = "unfilled"
    admit_ticker(repo, revision, "MU")
    asyncio.run(executor.step("trade:MU:entry"))
    assert len(broker.sent) == 1
    clock.value = datetime(2026, 9, 8, 19, 30, tzinfo=ZoneInfo("UTC"))
    asyncio.run(run_job(executor, repo, clock, "trade:MU:entry"))
    assert len(broker.sent) == 1
    # The new five-minute queue-age bound expires this order before cycle close.
    assert repo.require("executions", "trade:MU")["entry_reason"] == "ENTRY_QUEUE_AGE_EXCEEDED"
    assert repo.require("jobs", "trade:MU:entry")["reserved_open_usd"] == "0"


def test_new_price_matrix():
    strategy = Strategy()
    assert order_type([{"order_type": "LMT"}], "RTH", "ENTRY") == "LMT"
    assert order_type([{"order_type": "LMT"}], "RTH", "EXIT") == "LMT"
    kwargs = {
        "side": "BUY",
        "kind": "LMT",
        "quote": {"ask": "100"},
        "rules": [{"low_edge": "0", "increment": "0.01"}],
        "strategy": strategy,
        "remaining_notional": Decimal("20000"),
    }
    assert price_and_quantity(**kwargs, retry=False, session="RTH")["limit_price"] == "100.50"
    assert price_and_quantity(**kwargs, retry=True, session="EXTENDED")["limit_price"] == "100.50"


def test_margin_rejection_survives_later_cancel_and_does_not_retry(tmp_path):
    clock, repo, revision, executor, broker = setup_shared(tmp_path)
    broker.mode = "reject_margin"
    admit_ticker(repo, revision, "BE")
    asyncio.run(run_job(executor, repo, clock, "trade:BE:entry"))
    assert len(broker.sent) == 1
    assert repo.require("executions", "trade:BE")["entry_reason"] == "INSUFFICIENT_MARGIN"
    attempt = repo.attempts("trade:BE:entry")[0]
    assert attempt["broker_status"] == "Cancelled"
    assert attempt["rejection"]["error"]["code"] == 201
    assert attempt["broker_errors"][0]["advanced_order_reject_json"]


def test_rklb_three_cases_share_one_structured_event_claim():
    evidence = (
        Path(__file__).parents[1] / "eval/trade_execution/20260928_audit/ledger_redacted.json"
    )

    rows = json.loads(evidence.read_text(encoding="utf-8"))["runtime_values"]
    suffixes = ("bd3c", "adf6", "b9dc")
    trades = [
        TradeRecord.model_validate(json.loads(row["payload"])["trade"])
        for row in rows
        if row["namespace"] == "trade_intents" and row["key"].endswith(suffixes)
    ]
    assert len(trades) == 3
    assert {event_trade_key(trade) for trade in trades} == {"RKLB:E86:F275:ACTUAL:2026-09-24"}
    assert (
        event_trade_key(
            trades[0].model_copy(
                update={
                    "w3_result": trades[0].w3_result.model_copy(update={"delta_candidates": []})
                }
            )
        )
        is None
    )


def test_structured_event_claim_blocks_second_case(tmp_path):
    from tests.test_persistent_runtime_v2 import _source
    from tests.test_runtime_orchestration_execution import runtime_at

    evidence = (
        Path(__file__).parents[1] / "eval/trade_execution/20260928_audit/ledger_redacted.json"
    )
    rows = json.loads(evidence.read_text(encoding="utf-8"))["runtime_values"]
    trade = next(
        TradeRecord.model_validate(json.loads(row["payload"])["trade"])
        for row in rows
        if row["namespace"] == "trade_intents" and row["key"].endswith("bd3c")
    ).model_copy(update={"ticker": "MU"})
    runtime, journal = runtime_at(tmp_path, [datetime(2026, 9, 8, 14, tzinfo=ZoneInfo("UTC"))])
    try:
        source = _source().model_copy(
            update={
                "message_bus_event_time": journal.clock(),
                "published_at": journal.clock(),
                "collected_at": journal.clock(),
            }
        )
        first = runtime.execute_message(source)
        second = runtime.execute_message(
            source.model_copy(update={"source_message_id": "same-event-second-story"})
        )
        output = TradeOutputService(journal)
        assert output.record(first, trade) == "READY"
        assert output.record(second, trade) == "DUPLICATE_EVENT_TRADE"
        assert len(journal.values("event_trade_claims")) == 1
    finally:
        runtime.close()


def test_schema_v1_profile_pin_remains_compatible(tmp_path):
    clock = Clock()
    journal = RuntimeJournal(tmp_path / "legacy.db", clock=clock)
    ExecutionRepository(journal)
    historical = profile().model_dump(mode="json")
    historical["strategy"]["capital_model"] = "LEGACY_PER_INTENT"
    historical["strategy"]["target_notional_usd"] = "20000"
    historical["account_mode"] = "PAPER"
    historical.pop("account_type")
    historical["strategy"].pop("min_entry_notional_ratio")
    revision = "ep_" + digest(historical)[:24]
    with journal.transaction() as db:
        db.execute("UPDATE te_meta SET value='1' WHERE key='version'")
        db.execute(
            "INSERT INTO te_profiles VALUES(?,?,?,?,?)",
            (
                revision,
                historical["profile_id"],
                historical["expected_account_id"],
                historical["environment"],
                encode(historical),
            ),
        )
    repo = ExecutionRepository(journal)
    assert repo.profile(revision).account_type == "MARGIN"
    assert "capital_model" not in repo.profile(revision).strategy.model_dump()
    assert repo.profile_payload(revision) == historical
    with pytest.raises(ValueError, match="LEGACY_ENTRY_REMOVED"):
        repo.admit(
            {
                "intent_id": "trade:historical",
                "ticker": "MU",
                "status": "READY",
                "expires_at": (clock() + timedelta(hours=12)).isoformat(),
                "trade": {"decision": "LONG"},
                "execution_pin": {"revision": revision, "profile": historical},
            }
        )
