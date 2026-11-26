import asyncio
import json
from datetime import timedelta
from decimal import Decimal

import pytest

from doxagent.revenue_audit.projection import account_key
from doxagent.revenue_audit.service import RevenueService
from doxagent.trade_execution import groups
from doxagent.trade_execution.capital import CapitalLedger
from doxagent.trade_execution.manual_finance import attach
from doxagent.v2_read.metrics import Metrics
from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.projector import ProjectionWorker
from doxagent.v2_read.projectors import DomainProjectors
from doxagent.v2_read.repository import ReadStore
from tests.test_trade_execution import run_job, setup  # noqa: F401
from tests.test_trade_execution_shared_equity import admit_ticker, setup_shared


@pytest.mark.parametrize("direction,sign", [("LONG", 1), ("SHORT", -1)])
def test_verified_manual_close_financials_without_reclosing_lot(setup, tmp_path, direction, sign):  # noqa: F811
    clock, repo, revision, executor, broker = setup
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    with store.connect(write=True) as db:
        db.execute(
            "INSERT INTO read_meta VALUES('revenue_audit_v1',?)",
            (
                json.dumps(
                    dict(
                        since="2026-09-01T00:00:00+00:00",
                        opening={
                            "PAPER": dict(capital="20000", account_key=account_key("DU_TEST"))
                        },
                    )
                ),
            ),
        )
    store.ingest(
        "fixture",
        "provenance",
        [
            dict(
                kind="business_provenance", ticker="MU", id="case-a", data={"basis": "TEST_FIXTURE"}
            )
        ],
    )
    source = SourceOutbox(repo.journal.path, "runtime")
    source.migrate()
    worker = ProjectionWorker(store, [source], DomainProjectors(store))
    repo.admit(
        dict(
            intent_id="trade:A",
            case_id="case-a",
            ticker="MU",
            status="READY",
            released_at=clock().isoformat(),
            expires_at=(clock() + timedelta(hours=12)).isoformat(),
            trade={"decision": direction},
            audit_context=dict(
                version=1,
                decision_at=clock().isoformat(),
                account="DU_TEST",
                environment="PAPER",
                title="manual close",
            ),
            execution_pin=dict(revision=revision, profile=repo.profile_payload(revision)),
        )
    )
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    qty = repo.require("lots", "trade:A")["remaining_qty"]
    entry = repo.fills("trade:A:entry")[0]
    repo.event("DU_TEST", "fees", dict(exec_id=entry["exec_id"], currency="USD", commission="1"))
    executor.drain_events()
    fill = dict(
        account="DU_TEST",
        con_id=9939,
        exec_id="EXTERNAL.01",
        quantity=str(qty),
        price="110",
        side="SELL" if sign == 1 else "BUY",
        time=clock().isoformat(),
    )
    repo.journal.set(
        "execution_fees",
        "DU_TEST:EXTERNAL.01",
        dict(exec_id="EXTERNAL.01", commission="1", currency="USD"),
    )
    repo.adjust_lot(
        "trade:A",
        adjustment_id="manual",
        quantity_delta=-qty,
        effective_at=fill["time"],
        reason="verified user close",
    )
    repo.finish(repo.require("jobs", "trade:A:exit"), "MANUALLY_CLOSED_BROKER_VERIFIED")
    groups.advance(repo, "trade:A")
    before = repo.require("lots", "trade:A")
    book_before = CapitalLedger(repo).book("DU_TEST")
    for field, bad in (
        ("account", "OTHER"),
        ("quantity", str(qty + 1)),
        ("con_id", 999999),
        ("side", "BUY" if sign == 1 else "SELL"),
    ):
        with pytest.raises(ValueError):
            attach(repo, "manual", {**fill, field: bad}, actor="user", evidence="broker")
    attach(repo, "manual", fill, actor="user", evidence="verified broker fill")
    attach(repo, "manual", fill, actor="user", evidence="verified broker fill")
    assert repo.require("lots", "trade:A") == before
    assert not repo.fills("trade:A:exit")
    book = CapitalLedger(repo).book("DU_TEST")
    expected = Decimal(qty) * 10 * sign - 2
    assert book["realized"] == expected
    assert book["cash"] == 20000 + expected
    assert book["cash"] != book_before["cash"]
    assert not book["provisional"]
    with pytest.raises(ValueError):
        attach(
            repo, "manual", {**fill, "price": "111"}, actor="user", evidence="verified broker fill"
        )
    for _ in range(15):
        worker.tick(limit=500)
        with store.connect(write=True) as db:
            db.execute("UPDATE gaps SET next_attempt_at=''")
        worker.repair()
    with store.connect() as db:
        assert db.execute("SELECT reason FROM gaps").fetchall() == []
        seq = store.highwater(db)
    record = store.get("revenue_record", "PAPER", "trade:A")
    assert record["completed"], record
    assert record["legs"][0]["qty"] == "0"
    assert Decimal(record["legs"][0]["realized"]) == expected
    assert Metrics(store).value("paper_realized_net_pnl", ["MU"], seq, days=None) == expected

    class NoQuotes:
        def batch(self, requests):
            assert not requests  # Closed lot has no phantom valuation request.
            return {}

    service = RevenueService(store, repo.journal, market=NoQuotes(), clock=clock)
    frame = service.capture("test")["frame_id"]
    result = service.report("test", frame, "PAPER")
    assert Decimal(result["kpis"]["strategy_value"]) == 20000 + expected
    assert result["items"][0]["completed"]
    assert len(broker.sent) == 1


@pytest.mark.parametrize("limit", [1, 2, 3, 4, 8])
def test_margin_slots_no_idle_slot_borrowing_and_shared_ceiling(tmp_path, limit):
    clock, repo, revision, executor, broker = setup_shared(tmp_path)
    ledger = CapitalLedger(repo)
    ledger.allocate("DU_TEST", "4000", "funding4000", "user allocation")
    ledger.set_ticker_limit("DU_TEST", limit, "test")
    for i in range(limit + 1):
        ticker = "T" + str(i)
        broker.symbols[ticker] = 100000 + i
        broker.prices[ticker] = Decimal(10)
        admit_ticker(repo, revision, ticker)
        asyncio.run(run_job(executor, repo, clock, f"trade:{ticker}:entry"))
        with repo.transaction(write=False) as db:
            active = ledger.groups(db, "DU_TEST")
        assert sum(Decimal(g["budget_usd"]) for g in active) <= 4000
        assert all(Decimal(g["budget_usd"]) <= Decimal(4000) / limit for g in active)
    assert len(broker.sent) == limit
    assert all(
        repo.require("executions", f"trade:T{i}")["budget_ticker_limit"] == limit
        for i in range(limit)
    )


def test_budget_changes_preserve_existing_group_and_pin_new_limit(tmp_path):
    clock, repo, revision, executor, broker = setup_shared(tmp_path)
    ledger = CapitalLedger(repo)
    ledger.allocate("DU_TEST", "4000", "funding", "test")
    ledger.set_ticker_limit("DU_TEST", 1, "old limit")
    admit_ticker(repo, revision, "MU")
    asyncio.run(run_job(executor, repo, clock, "trade:MU:entry"))
    old = repo.require("executions", "trade:MU")
    ledger.allocate("DU_TEST", "8000", "funding2", "test")
    ledger.set_ticker_limit("DU_TEST", 2, "new limit")
    admit_ticker(repo, revision, "BE")
    asyncio.run(run_job(executor, repo, clock, "trade:BE:entry"))
    assert repo.require("executions", "trade:MU")["budget_usd"] == old["budget_usd"]
    assert Decimal(repo.require("executions", "trade:BE")["budget_usd"]) <= 4000
    ledger.set_ticker_limit("DU_TEST", 1, "reduce")
    admit_ticker(repo, revision, "ONDS")
    asyncio.run(run_job(executor, repo, clock, "trade:ONDS:entry"))
    assert len(broker.sent) == 2


def test_margin_two_legs_share_slot_budget_with_integer_hedge(tmp_path):
    from doxagent.trade_execution.planning import TradingConfig
    from tests.test_trade_groups_capital import open_trade

    clock, repo, revision, executor, broker = setup_shared(tmp_path)
    ledger = CapitalLedger(repo)
    ledger.allocate("DU_TEST", "4000", "funding", "test")
    ledger.set_ticker_limit("DU_TEST", 2, "two slots")
    broker.prices.update(MU=Decimal("228.17"), BE=Decimal("63.59"))
    config = TradingConfig(hedge_enabled=True, hedge_ratio=Decimal(".35"), margin_index_etf="BE")
    open_trade(repo, revision, "MU", "trade:pair", config, direction="SHORT")
    for suffix in (":entry", ":hedge:entry"):
        asyncio.run(run_job(executor, repo, clock, "trade:pair" + suffix))
    group = repo.require("executions", "trade:pair")
    assert Decimal(group["budget_usd"]) == 2000
    main, hedge = broker.sent
    assert main["quantity"] >= 1 and hedge["quantity"] >= 1
    main_value = Decimal(main["quantity"]) * broker.prices["MU"]
    hedge_value = Decimal(hedge["quantity"]) * broker.prices["BE"]
    assert main_value + hedge_value <= Decimal(group["nominal_budget_usd"])
    assert Decimal(".25") <= hedge_value / main_value <= Decimal(".45")
    assert main["side"] == "SELL" and hedge["side"] == "BUY"
    assert group["fee_reserve_usd"] == "2"


def test_stale_cycle_cannot_override_reduced_capital_at_atomic_grant(tmp_path):
    _, repo, revision, _, _ = setup_shared(tmp_path)
    ledger = CapitalLedger(repo)
    ledger.allocate("DU_TEST", "4000", "initial4000", "test")
    ledger.set_ticker_limit("DU_TEST", 2, "test")
    admit_ticker(repo, revision, "MU")
    ledger.allocate("DU_TEST", "3000", "reduced", "changed during quote")
    with repo.transaction(write=False) as db:
        capacity = ledger.capacity(
            db, repo.require("executions", "trade:MU"), repo.profile(revision), Decimal(4000), {}
        )
    assert capacity == 1500
