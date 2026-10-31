import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest

from doxagent.trade_execution import groups
from doxagent.trade_execution.capital import CapitalLedger
from doxagent.trade_execution.planning import TradingConfig, assets, integer_pair
from doxagent.trade_execution.schema import Strategy
from tests.test_trade_execution import profile, run_job
from tests.test_trade_execution_shared_equity import setup_shared


def open_trade(repo, revision, ticker, identity, config=None, direction="LONG"):
    return repo.admit(
        dict(
            intent_id=identity,
            ticker=ticker,
            status="READY",
            trade={"decision": direction},
            trading_config=(config or TradingConfig()).model_dump(mode="json"),
            expires_at=(repo.journal.clock() + timedelta(hours=12)).isoformat(),
            execution_pin={"revision": revision, "profile": repo.profile_payload(revision)},
        )
    )


def test_asset_direction_table():
    config = TradingConfig(
        hedge_enabled=True,
        cash_short_substitute_enabled=True,
        inverse_single_stock_etf="MUD",
        margin_index_etf="SOXX",
        cash_positive_index_etf="SOXL",
        cash_inverse_index_etf="SOXS",
    )
    for mode, signal, expected in [
        ("MARGIN", "LONG", [("MU", "BUY"), ("SOXX", "SELL")]),
        ("MARGIN", "SHORT", [("MU", "SELL"), ("SOXX", "BUY")]),
        ("CASH", "LONG", [("MU", "BUY"), ("SOXS", "BUY")]),
        ("CASH", "SHORT", [("MUD", "BUY"), ("SOXL", "BUY")]),
    ]:
        assert [
            (v["traded_ticker"], v["side"]) for v in assets("MU", signal, mode, config)
        ] == expected


@pytest.mark.parametrize("direction,main,hedge", [("LONG", "MU", "SIDX"), ("SHORT", "MUD", "LIDX")])
def test_cash_hedge_uses_two_etfs_and_buys_for_both_signal_directions(
    tmp_path, direction, main, hedge
):
    clock, repo, _, executor, _ = setup_shared(tmp_path)
    revision = repo.import_profile(profile(account_type="CASH", strategy=Strategy()))
    broker = executor.broker(revision)
    broker.symbols = {**broker.symbols, "MUD": 100001, "LIDX": 100002, "SIDX": 100003}
    broker.prices.update({name: Decimal(100) for name in ("MUD", "LIDX", "SIDX")})
    sync = broker.sync

    def cash_sync():
        value = sync()
        value["account_values"]["SettledCash"] = {"value": "100000", "currency": "USD"}
        return value

    broker.sync = cash_sync
    cfg = TradingConfig(
        hedge_enabled=True,
        cash_short_substitute_enabled=True,
        inverse_single_stock_etf="MUD",
        cash_positive_index_etf="LIDX",
        cash_inverse_index_etf="SIDX",
        margin_index_etf="OTHER",
    )
    root = "cash:hedge:" + direction
    open_trade(repo, revision, "MU", root, cfg, direction)
    for suffix in (":entry", ":hedge:entry"):
        asyncio.run(run_job(executor, repo, clock, root + suffix))
    assert [(a["contract"]["symbol"], a["side"]) for a in broker.sent] == [
        (main, "BUY"),
        (hedge, "BUY"),
    ]
    with repo.transaction() as db:
        groups.request_exit(db, root, clock().isoformat(), "TEST_CASH_HEDGE")
    for suffix in (":exit", ":hedge:exit"):
        asyncio.run(run_job(executor, repo, clock, root + suffix))
    assert [(a["contract"]["symbol"], a["side"]) for a in broker.sent[2:]] == [
        (main, "SELL"),
        (hedge, "SELL"),
    ]
    assert repo.require("executions", root)["group_state"] == "CLOSED"
    assert not any(broker.positions.values())


@pytest.mark.parametrize("missing", ["cash_positive_index_etf", "cash_inverse_index_etf"])
def test_cash_hedge_rejects_either_missing_side_even_with_margin_etf(missing):
    values = dict(
        hedge_enabled=True,
        margin_index_etf="OTHER",
        cash_positive_index_etf="LIDX",
        cash_inverse_index_etf="SIDX",
    )
    values[missing] = None
    with pytest.raises(ValueError, match="HEDGE_ASSET_MISSING"):
        assets("MU", "LONG", "CASH", TradingConfig(**values))


def test_integer_plan_matches_exhaustive_small_search():
    for budget in (20, 57, 100, 999):
        for mp in (3, 25, 60):
            for hp in (1, 17, 80):
                for raw in (".2", "1", "2"):
                    ratio = Decimal(raw)
                    cap = Decimal(budget) / (1 + ratio)
                    valid = [
                        (qm, qh)
                        for qm in range(1, int(cap // mp) + 1)
                        for qh in range(1, int((budget - cap) // hp) + 1)
                        if Decimal(".9") * ratio
                        <= Decimal(qh * hp) / Decimal(qm * mp)
                        <= Decimal("1.1") * ratio
                    ]
                    if not valid:
                        with pytest.raises(ValueError, match="NO_VALID"):
                            integer_pair(Decimal(budget), ratio, Decimal(mp), Decimal(hp))
                    else:
                        plan = integer_pair(Decimal(budget), ratio, Decimal(mp), Decimal(hp))
                        assert plan["main_quantity"] == max(qm for qm, qh in valid)
                        assert (plan["main_quantity"], plan["hedge_quantity"]) in valid


def test_main_then_hedge_and_ordered_group_exit(tmp_path):
    clock, repo, revision, executor, broker = setup_shared(tmp_path)
    broker.symbols = {**broker.symbols, "SOXX": 100001}
    broker.prices["SOXX"] = Decimal(100)
    cfg = TradingConfig(hedge_enabled=True, margin_index_etf="SOXX")
    open_trade(repo, revision, "MU", "group:A", cfg)
    assert repo.require("jobs", "group:A:hedge:entry")["state"] == "WAIT_MAIN"
    asyncio.run(executor.step("group:A:hedge:entry"))
    assert not broker.sent
    asyncio.run(run_job(executor, repo, clock, "group:A:entry"))
    assert len(broker.sent) == 1
    assert repo.require("jobs", "group:A:hedge:entry")["state"] == "READY"
    asyncio.run(run_job(executor, repo, clock, "group:A:hedge:entry"))
    assert [a["side"] for a in broker.sent] == ["BUY", "SELL"]
    assert repo.require("lots", "group:A:hedge")["remaining_qty"] > 0
    assert repo.require("jobs", "group:A:hedge:exit")["state"] == "WAIT_MAIN"
    with repo.transaction() as db:
        groups.request_exit(db, "group:A", clock().isoformat(), "MANUAL_TEST")
    asyncio.run(executor.step("group:A:hedge:exit"))
    assert len(broker.sent) == 2
    asyncio.run(run_job(executor, repo, clock, "group:A:exit"))
    asyncio.run(run_job(executor, repo, clock, "group:A:hedge:exit"))
    assert [a["contract"]["symbol"] for a in broker.sent] == ["MU", "SOXX", "MU", "SOXX"]
    assert repo.require("executions", "group:A")["group_state"] == "CLOSED"
    assert all(not n for n in broker.positions.values())


def test_hedge_failure_keeps_main_and_original_deadline(tmp_path):
    clock, repo, revision, executor, broker = setup_shared(tmp_path)
    broker.symbols = {**broker.symbols, "SOXX": 100001}
    broker.prices["SOXX"] = Decimal(100)
    open_trade(
        repo, revision, "MU", "group:A", TradingConfig(hedge_enabled=True, margin_index_etf="SOXX")
    )
    asyncio.run(run_job(executor, repo, clock, "group:A:entry"))
    broker.mode = "unfilled"
    clock.advance(301)
    asyncio.run(run_job(executor, repo, clock, "group:A:hedge:entry"))
    assert len(broker.sent) == 1
    assert repo.require("lots", "group:A")["remaining_qty"] > 0
    assert repo.require("jobs", "group:A:hedge:entry")["reason"] == "ENTRY_QUEUE_AGE_EXCEEDED"


def test_cash_two_slots_no_borrow_and_settled_cash_required(tmp_path):
    clock, repo, old, executor, _ = setup_shared(tmp_path)
    revision = repo.import_profile(profile(account_type="CASH", strategy=Strategy()))
    broker = executor.broker(revision)
    original_sync = broker.sync

    def sync():
        result = original_sync()
        result["account_values"]["SettledCash"] = {"value": "100000", "currency": "USD"}
        return result

    broker.sync = sync
    for ticker in ("MU", "BE", "ONDS"):
        open_trade(repo, revision, ticker, "cash:" + ticker)
        asyncio.run(run_job(executor, repo, clock, "cash:" + ticker + ":entry"))
    assert len(broker.sent) == 2
    assert all(Decimal(a["quantity"]) * Decimal(a["sizing_price"]) <= 10000 for a in broker.sent)
    assert not broker.previews
    assert repo.require("executions", "cash:ONDS")["entry_reason"] == "PORTFOLIO_CAPACITY_EXHAUSTED"
    # Personal NLV changes cannot expand strategy equity.
    broker.equity = Decimal(999999)
    assert CapitalLedger(repo).equity("DU_TEST", "CASH") == 20000


def test_cash_short_disabled_records_signal_without_order(tmp_path):
    clock, repo, old, executor, broker = setup_shared(tmp_path)
    revision = repo.import_profile(profile(account_type="CASH"))
    open_trade(repo, revision, "MU", "short:disabled", direction="SHORT")
    value = repo.require("executions", "short:disabled")
    assert value["intent"]["trade"]["decision"] == "SHORT"
    assert value["entry_result"] == "DIRECTION_DISABLED"
    assert not repo.get("jobs", "short:disabled:entry")
    assert not broker.sent


def test_cash_missing_settlement_only_blocks_entry_not_exit(tmp_path):
    clock, repo, old, executor, _ = setup_shared(tmp_path)
    revision = repo.import_profile(profile(account_type="CASH", strategy=Strategy()))
    broker = executor.broker(revision)
    original = broker.sync
    available = [True]

    def sync():
        value = original()
        if available[0]:
            value["account_values"]["SettledCash"] = {"value": "100000", "currency": "USD"}
        return value

    broker.sync = sync
    open_trade(repo, revision, "MU", "cash:first")
    asyncio.run(run_job(executor, repo, clock, "cash:first:entry"))
    assert repo.require("lots", "cash:first")["remaining_qty"] > 0
    available[0] = False
    asyncio.run(run_job(executor, repo, clock, "cash:first:exit"))
    assert repo.require("executions", "cash:first")["group_state"] == "CLOSED"
    open_trade(repo, revision, "BE", "cash:second")
    asyncio.run(run_job(executor, repo, clock, "cash:second:entry"))
    assert not repo.get("lots", "cash:second")


def test_cash_inverse_asset_profit_is_positive_in_capital_and_projection(tmp_path):
    from doxagent.v2_read.outbox import SourceOutbox
    from doxagent.v2_read.projector import ProjectionWorker
    from doxagent.v2_read.projectors import DomainProjectors
    from doxagent.v2_read.repository import ReadStore
    from tests.v2_backend.test_trade_outcomes import case

    clock, repo, old, executor, _ = setup_shared(tmp_path)
    revision = repo.import_profile(profile(account_type="CASH", strategy=Strategy()))
    broker = executor.broker(revision)
    original = broker.sync

    def sync():
        result = original()
        result["account_values"]["SettledCash"] = {"value": "100000", "currency": "USD"}
        return result

    broker.sync = sync
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    store.ingest(
        "fixture",
        "seed",
        [
            {
                "kind": "business_provenance",
                "ticker": "MU",
                "id": "case-1",
                "data": {"basis": "TEST_FIXTURE"},
            },
            {"kind": "case", "ticker": "MU", "id": "case-1", "data": case()},
        ],
    )
    source = SourceOutbox(repo.journal.path, "runtime")
    source.migrate()
    worker = ProjectionWorker(store, [source], DomainProjectors(store))
    repo.admit(
        dict(
            intent_id="inverse:main",
            case_id="case-1",
            ticker="MU",
            status="READY",
            released_at=clock().isoformat(),
            expires_at=(clock() + timedelta(hours=1)).isoformat(),
            trade={"decision": "SHORT"},
            trading_config=TradingConfig(
                cash_short_substitute_enabled=True, inverse_single_stock_etf="BE"
            ).model_dump(mode="json"),
            execution_pin={"revision": revision, "profile": repo.profile_payload(revision)},
        )
    )
    asyncio.run(run_job(executor, repo, clock, "inverse:main:entry"))
    assert broker.sent[0]["contract"]["symbol"] == "BE" and broker.sent[0]["side"] == "BUY"
    quantity = repo.require("lots", "inverse:main")["remaining_qty"]
    broker.prices["BE"] = Decimal(110)
    asyncio.run(run_job(executor, repo, clock, "inverse:main:exit"))
    for job in ("inverse:main:entry", "inverse:main:exit"):
        for fill in repo.fills(job):
            repo.event(
                "DU_TEST",
                "fees",
                {"exec_id": fill["exec_id"], "currency": "USD", "commission": "1"},
            )
    executor.drain_events()
    for _ in range(8):
        worker.tick(limit=500)
    assert CapitalLedger(repo).book("DU_TEST")["realized"] == quantity * 10 - 2
    with store.connect() as db:
        import json

        assert db.execute("SELECT COUNT(*) FROM gaps").fetchone()[0] == 0
        values = [
            json.loads(r[0])
            for r in db.execute("SELECT payload FROM object_current WHERE kind='realized_pnl'")
        ]
    assert sum(Decimal(v["amount"]) for v in values) == quantity * 10 - 2


def test_main_correction_during_hedge_quote_never_opens_orphan_hedge(tmp_path):
    clock, repo, revision, executor, broker = setup_shared(tmp_path)
    broker.symbols = {**broker.symbols, "SOXX": 100001}
    broker.prices["SOXX"] = Decimal(100)
    open_trade(
        repo,
        revision,
        "MU",
        "correct:main",
        TradingConfig(hedge_enabled=True, margin_index_etf="SOXX"),
    )
    asyncio.run(run_job(executor, repo, clock, "correct:main:entry"))
    original = broker.quote
    applied = [False]

    def quote(contract, *args, **kwargs):
        value = original(contract, *args, **kwargs)
        if contract["symbol"] == "SOXX" and not applied[0]:
            applied[0] = True
            fill = repo.fills("correct:main:entry")[0]
            corrected = {
                **fill,
                "exec_id": fill["exec_id"].rsplit(".", 1)[0] + ".02",
                "quantity": "0",
                "cum_qty": "0",
            }
            repo.apply_fill(corrected, fill["exit_at"])
            broker.positions[broker.symbols["MU"]] = 0
        return value

    broker.quote = quote
    asyncio.run(run_job(executor, repo, clock, "correct:main:hedge:entry"))
    assert len(broker.sent) == 1
    assert repo.require("jobs", "correct:main:hedge:entry")["state"] == "DONE"
    assert repo.require("executions", "correct:main")["entry_result"] == "FAILED"
    assert not any(a["state"] == "PREPARED" for a in repo.attempts("correct:main:hedge:entry"))


def test_cash_next_day_equity_does_not_lend_old_grant_or_float_profit(tmp_path):
    from doxagent.semantic_clock import boundary, semantic_day

    clock, repo, old, executor, _ = setup_shared(tmp_path)
    revision = repo.import_profile(profile(account_type="CASH", strategy=Strategy()))
    ledger = CapitalLedger(repo)
    ledger.allocate("DU_TEST", "4000", "cash-principal", "initial cash budget")
    broker = executor.broker(revision)
    original = broker.sync

    def sync():
        result = original()
        result["account_values"]["SettledCash"] = {"value": "100000", "currency": "USD"}
        return result

    broker.sync = sync
    open_trade(repo, revision, "MU", "carry:MU")
    asyncio.run(run_job(executor, repo, clock, "carry:MU:entry"))
    first = repo.require("executions", "carry:MU")
    assert Decimal(first["budget_usd"]) == 2000
    ledger.adjust(
        "DU_TEST", "-1000", "carry:cost", "attributable strategy cost", clock().isoformat()
    )
    assert ledger.equity("DU_TEST", "CASH") == 4000
    broker.prices["MU"] = Decimal(999)  # Floating gains cannot expand the next-day budget.
    clock.value = boundary(semantic_day(clock()) + timedelta(days=1)) + timedelta(hours=8)
    assert ledger.equity("DU_TEST", "CASH") == 2999
    open_trade(repo, revision, "BE", "carry:BE")
    asyncio.run(run_job(executor, repo, clock, "carry:BE:entry"))
    second = repo.require("executions", "carry:BE")
    assert Decimal(second["budget_usd"]) <= 999
    assert Decimal(first["budget_usd"]) + Decimal(second["budget_usd"]) <= 2999
    assert repo.require("lots", "carry:MU")["remaining_qty"] > 0


def test_cash_stale_broker_balance_retains_buy_and_both_leg_fee_debits(tmp_path):
    clock, repo, _, executor, _ = setup_shared(tmp_path)
    revision = repo.import_profile(profile(account_type="CASH", strategy=Strategy()))
    ledger = CapitalLedger(repo)
    ledger.allocate("DU_TEST", "4000", "cash-fees", "cash fee debit test")
    broker = executor.broker(revision)
    original = broker.sync

    def sync():
        result = original()
        result["account_values"]["SettledCash"] = {"value": "1500", "currency": "USD"}
        return result

    broker.sync = sync
    open_trade(repo, revision, "MU", "fee:MU")
    asyncio.run(run_job(executor, repo, clock, "fee:MU:entry"))
    asyncio.run(run_job(executor, repo, clock, "fee:MU:exit"))
    assert repo.require("executions", "fee:MU")["group_state"] == "CLOSED"
    book = ledger.book("DU_TEST")
    buys = sum(
        (
            Decimal(f["quantity"]) * Decimal(f["price"])
            for job in ("fee:MU:entry", "fee:MU:exit")
            for f in repo.fills(job)
            if f["side"] == "BUY"
        ),
        Decimal(0),
    )
    assert book["cash_debits"] == buys + 2
    with repo.transaction() as db:
        ledger.release_slots(db, "DU_TEST")
        capacity = ledger.capacity(
            db,
            {"id": "next", "ticker": "BE", "account": "DU_TEST"},
            repo.profile(revision),
            Decimal(4000),
            sync(),
        )
    assert capacity == 1500 - buys - 2


def test_archived_shared_cycle_rebases_broker_equity_to_own_capital(tmp_path):
    import json

    clock, repo, revision, executor, broker = setup_shared(tmp_path)
    ledger = CapitalLedger(repo)
    ledger.allocate("DU_TEST", "4000", "own-principal", "own budget")
    cycle = repo.cycle_for_entry(
        "DU_TEST", "PAPER", "archived-cycle", broker.sync(), Decimal(".25")
    )
    archived = {k: v for k, v in cycle.items() if k != "principal"}
    archived["equity"] = "100000"
    with repo.transaction() as db:
        db.execute(
            "UPDATE te_account_cycles SET equity=?,payload=? WHERE cycle_id=?",
            ("100000", json.dumps(archived), "archived-cycle"),
        )
    result = repo.cycle_for_entry(
        "DU_TEST", "PAPER", "archived-cycle", broker.sync(), Decimal(".25")
    )
    assert result["equity"] == "4000" and result["principal"] == "4000"


def test_shared_etf_opposite_groups_exit_their_own_quantities(tmp_path):
    clock, repo, revision, executor, broker = setup_shared(tmp_path)
    broker.symbols = {**broker.symbols, "SOXX": 100001}
    broker.prices["SOXX"] = Decimal(100)
    cfg = TradingConfig(hedge_enabled=True, margin_index_etf="SOXX")
    open_trade(repo, revision, "MU", "overlap:long", cfg, "LONG")
    for suffix in (":entry", ":hedge:entry"):
        asyncio.run(run_job(executor, repo, clock, "overlap:long" + suffix))
    first = repo.require("lots", "overlap:long:hedge")["remaining_qty"]
    open_trade(repo, revision, "BE", "overlap:short", cfg, "SHORT")
    for suffix in (":entry", ":hedge:entry"):
        asyncio.run(run_job(executor, repo, clock, "overlap:short" + suffix))
    second = repo.require("lots", "overlap:short:hedge")["remaining_qty"]
    assert first > 0 and second > 0
    assert repo.require("lots", "overlap:long:hedge")["remaining_qty"] == first
    assert int(broker.positions[100001]) == second - first
    for suffix in (":exit", ":hedge:exit"):
        asyncio.run(run_job(executor, repo, clock, "overlap:long" + suffix))
    assert repo.require("lots", "overlap:long:hedge")["remaining_qty"] == 0
    assert repo.require("lots", "overlap:short:hedge")["remaining_qty"] == second
    assert int(broker.positions[100001]) == second
    assert sum(Decimal(f["quantity"]) for f in repo.fills("overlap:long:hedge:exit")) == first
    for suffix in (":exit", ":hedge:exit"):
        asyncio.run(run_job(executor, repo, clock, "overlap:short" + suffix))
    assert int(broker.positions[100001]) == 0
    assert repo.require("executions", "overlap:short")["group_state"] == "CLOSED"
