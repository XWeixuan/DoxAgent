"""Business constraints checked independently of the planner and real broker."""

import asyncio
import json
from decimal import Decimal as D

import pytest

from doxagent.trade_execution import groups
from doxagent.trade_execution.capital import CapitalLedger
from doxagent.trade_execution.planning import TradingConfig, hedge_quantity, integer_pair
from doxagent.trade_execution.schema import Strategy
from tests.test_trade_execution import profile, run_job
from tests.test_trade_execution_shared_equity import setup_shared
from tests.test_trade_groups_capital import open_trade


@pytest.mark.parametrize(
    "b,r,mp,hp,expected",
    [
        ("473", ".29", "60.09", "57.83", (5, 2)),
        ("998", ".2", "800", "58", (1, 3)),
        ("1400", ".4", "100", "222", (9, 2)),
        ("130", ".2", "100", "30", (1, 1)),  # exact budget and upper ratio
        ("130", ".2", "100", "10", (1, 2)),
        ("150", ".01", "100", "11", (1, 1)),
        ("320", "2", "100", "210", (1, 1)),
    ],
)
def test_business_examples(b, r, mp, hp, expected):
    plan = integer_pair(*map(D, (b, r, mp, hp)))
    assert (plan["main_quantity"], plan["hedge_quantity"]) == expected
    assert "hedge_cap" not in plan
    assert plan["hedge_tolerance_pp"] == 10


@pytest.mark.parametrize("r", [".01", ".05", ".1", ".1001", ".2", ".29", ".4", ".66", "1", "2"])
def test_independent_exhaustive_search(r):
    ratio = D(r)
    for b in (20, 57, 100, 127, 400):
        for mp in (3, 25, 60):
            for hp in (1, 17, 80):
                # Enumerate the full affordable domain, filter in dollars, then rank.
                valid = [
                    (m, h)
                    for m in range(1, b // mp + 1)
                    for h in range(1, b // hp + 1)
                    if m * mp * (1 + ratio) <= b
                    and m * mp + h * hp <= b
                    and abs(h * hp - ratio * m * mp) <= D(".1") * m * mp
                ]
                if not valid:
                    with pytest.raises(ValueError, match="NO_VALID_INTEGER_COMBINATION"):
                        integer_pair(D(b), ratio, D(mp), D(hp))
                else:
                    expected = min(
                        valid, key=lambda q: (-q[0], abs(q[1] * hp - ratio * q[0] * mp), q[1])
                    )
                    plan = integer_pair(D(b), ratio, D(mp), D(hp))
                    assert (plan["main_quantity"], plan["hedge_quantity"]) == expected


def test_exact_decimal_boundaries_and_tie():
    assert hedge_quantity(D(100), D(30), D(".2"), D(30)) == 1
    assert hedge_quantity(D(100), D(10), D(".2"), D(10)) == 1
    with pytest.raises(ValueError):
        hedge_quantity(D(100), D("30.01"), D(".2"), D("30.01"))
    assert hedge_quantity(D(100), D(50), D(".3"), D(20)) == 1  # 20/40 equal distance
    assert hedge_quantity(D(990), D(210), D(".2"), D("100.01")) == 2
    # Already valid positive hedge is done, even when the exact target is nearer one more share.
    assert hedge_quantity(D(100), D(100), D(".2"), D(5), D(10)) == 0
    assert hedge_quantity(D(100), D(100), D(".05"), D(5), D(0)) == 1
    assert hedge_quantity(D(1000), D(1000), D(".2"), D(50), D(50)) == 3
    with pytest.raises(ValueError, match="HEDGE_ABOVE_RATIO_LIMIT"):
        hedge_quantity(D(100), D(100), D(".2"), D(1), D("30.01"))


def cash_group(tmp_path, identity="test:new", ratio=".29", mp="59.79", hp="57.54"):
    clock, repo, _, executor, _ = setup_shared(tmp_path)
    revision = repo.import_profile(profile(account_type="CASH", strategy=Strategy()))
    CapitalLedger(repo).allocate("DU_TEST", "950", "allocation:950", "test grant")
    broker = executor.broker(revision)
    broker.symbols = {**broker.symbols, "SOXS": 10001}
    broker.prices["MU"], broker.prices["SOXS"] = D(mp), D(hp)
    original = broker.sync

    def sync():
        result = original()
        result["account_values"]["SettledCash"] = {"value": "5000", "currency": "USD"}
        return result

    broker.sync = sync
    cfg = TradingConfig(
        hedge_enabled=True,
        hedge_ratio=D(ratio),
        cash_positive_index_etf="OTHER",
        cash_inverse_index_etf="SOXS",
    )
    open_trade(repo, revision, "MU", identity, cfg)
    return clock, repo, revision, executor, broker


def test_shared_grant_and_two_cash_slots_keep_original_capital(tmp_path):
    clock, repo, revision, executor, broker = cash_group(tmp_path, hp="57.40")
    for suffix in (":entry", ":hedge:entry"):
        asyncio.run(run_job(executor, repo, clock, "test:new" + suffix))
    value = repo.require("executions", "test:new")
    assert value["allocation_policy_version"] == 2
    assert (value["plan"]["main_quantity"], value["plan"]["hedge_quantity"]) == (5, 2)
    assert [a["quantity"] for a in broker.sent] == [5, 2]
    assert D(value["budget_usd"]) == D(value["grant_budget_usd"]) == 475
    assert D(value["fee_reserve_usd"]) == 2 and D(value["nominal_budget_usd"]) == 473
    assert D(value["actual_hedge_ratio"]) > D(".29")
    assert "planning_diagnostic" in repo.require("jobs", "test:new:hedge:entry")
    # Legal hedge above the theoretical split must not manufacture extra locked capital.
    with repo.transaction() as db:
        capacity = CapitalLedger(repo).capacity(
            db,
            {"id": "next", "ticker": "BE", "account": "DU_TEST"},
            repo.profile(revision),
            D(950),
            broker.sync(),
        )
    assert capacity == 473  # Existing CASH fee debit reservation still applies.
    with repo.transaction() as db:
        groups.request_exit(db, "test:new", clock().isoformat(), "TEST_EXIT")
    for suffix in (":exit", ":hedge:exit"):
        asyncio.run(run_job(executor, repo, clock, "test:new" + suffix))
    assert repo.require("executions", "test:new")["group_state"] == "CLOSED"
    assert all(not qty for qty in broker.positions.values())


def test_failure_diagnostics_no_attempt_and_no_revive_on_duplicate_or_restart(tmp_path):
    clock, repo, revision, executor, broker = cash_group(tmp_path, mp="800")
    intent = repo.require("executions", "test:new")["intent"]
    asyncio.run(run_job(executor, repo, clock, "test:new:entry"))
    original = repo.require("executions", "test:new")
    diagnostic = repo.require("jobs", "test:new:entry")["planning_diagnostic"]
    assert diagnostic["subreason"] == "MAIN_BELOW_ONE_SHARE"
    assert diagnostic["grant_budget_usd"] == "475" and diagnostic["fee_reserve_usd"] == "2"
    assert diagnostic["hedge_reference"]["source"] == "LIVE_QUOTE"
    assert original["group_state"] == "CLOSED" and original["entry_result"] == "FAILED"
    # Deliberately re-deliver after prices become feasible: durable terminal identity wins.
    broker.prices["MU"] = D("59.79")
    clock.advance(13 * 3600)
    repo.admit(intent)
    from doxagent.trade_execution.executor import Executor

    restarted = Executor(
        repo, broker_factory=lambda *a, **k: pytest.fail("terminal trade accessed broker")
    )
    for suffix in (":entry", ":hedge:entry"):
        asyncio.run(restarted.step("test:new" + suffix))
    groups.advance(repo, "test:new")
    assert repo.require("executions", "test:new")["entry_reason"] == original["entry_reason"]
    assert not repo.attempts("test:new:entry") and not broker.sent
    assert repo.require("jobs", "test:new:hedge:entry")["state"] == "DONE"


def test_old_group_keeps_relative_tolerance_and_old_caps(tmp_path):
    clock, repo, revision, executor, broker = cash_group(tmp_path)
    with repo.transaction() as db:
        value = repo.require("executions", "test:new")
        value.pop("allocation_policy_version")
        db.execute("UPDATE te_executions SET payload=? WHERE id=?", (json.dumps(value), "test:new"))
    asyncio.run(run_job(executor, repo, clock, "test:new:entry"))
    assert not broker.sent
    assert repo.require("executions", "test:new")["entry_reason"] == "NO_VALID_INTEGER_COMBINATION"
    with pytest.raises(ValueError):
        integer_pair(D(473), D(".29"), D("60.09"), D("57.83"), policy_version=1)
    plan = integer_pair(D(998), D(1), D(100), D(100), policy_version=1)
    assert plan == dict(main_quantity=4, hedge_quantity=4, main_cap="499", hedge_cap="499")


def test_hedge_requote_uses_shared_balance_and_actual_main(tmp_path):
    clock, repo, _, executor, broker = cash_group(tmp_path, ratio=".2", mp="100", hp="20")
    asyncio.run(run_job(executor, repo, clock, "test:new:entry"))
    main = D(repo.require("executions", "test:new")["filled_notional"])
    broker.prices["SOXS"] = D("80")  # Above the old 78.83 hard cap, within group remainder.
    asyncio.run(run_job(executor, repo, clock, "test:new:hedge:entry"))
    assert len(broker.sent) == 2 and broker.sent[1]["quantity"] == 1
    assert main + D(80) <= D(473)
    assert repo.require("jobs", "test:new:hedge:entry")["result"] == "FILLED"


def test_hedge_true_infeasibility_keeps_main_and_exit(tmp_path):
    clock, repo, _, executor, broker = cash_group(tmp_path)
    asyncio.run(run_job(executor, repo, clock, "test:new:entry"))
    broker.prices["SOXS"] = D(200)
    asyncio.run(run_job(executor, repo, clock, "test:new:hedge:entry"))
    assert len(broker.sent) == 1
    assert repo.require("jobs", "test:new:hedge:entry")["result"] == "FAILED"
    assert repo.require("lots", "test:new")["remaining_qty"] == 5
    assert repo.require("jobs", "test:new:exit")["state"] != "DONE"


def partial_hedge(broker, quantity, price, next_price=None):
    submit = broker.submit
    first = [True]

    def partial(attempt):
        if not attempt["job_id"].endswith(":hedge:entry") or not first[0]:
            return submit(attempt)
        first[0] = False
        broker.sent.append(attempt)
        con_id = attempt["contract"]["con_id"]
        broker.positions[con_id] = broker.positions.get(con_id, 0) + quantity
        broker.sink(
            "fill",
            dict(
                account=attempt["account"],
                client_id=attempt["client_id"],
                order_id=attempt["order_id"],
                order_ref=attempt["order_ref"],
                con_id=con_id,
                exec_id="PARTIAL.01",
                quantity=str(quantity),
                cum_qty=str(quantity),
                price=str(price),
                side=attempt["side"],
                time=broker.clock().isoformat(),
            ),
        )
        broker.sink(
            "order",
            dict(
                order_id=attempt["order_id"],
                client_id=attempt["client_id"],
                order_ref=attempt["order_ref"],
                status="Cancelled",
                filled=str(quantity),
            ),
        )
        if next_price:
            broker.prices["SOXS"] = D(next_price)

    broker.submit = partial


def test_partial_hedge_already_in_band_finishes_without_extra_order(tmp_path):
    clock, repo, _, executor, broker = cash_group(tmp_path, hp="57.40")
    asyncio.run(run_job(executor, repo, clock, "test:new:entry"))
    partial_hedge(broker, 1, "57.40")
    asyncio.run(run_job(executor, repo, clock, "test:new:hedge:entry"))
    assert [a["quantity"] for a in broker.sent] == [5, 2]
    assert len(repo.fills("test:new:hedge:entry")) == 1
    job = repo.require("jobs", "test:new:hedge:entry")
    assert job["result"] == "FILLED" and job["reason"] == "HEDGE_TARGET_SATISFIED"
    assert repo.require("lots", "test:new:hedge")["remaining_qty"] == 1


def test_partial_hedge_below_band_reprices_only_increment(tmp_path):
    clock, repo, _, executor, broker = cash_group(tmp_path, ratio=".66", mp="100", hp="10")
    asyncio.run(run_job(executor, repo, clock, "test:new:entry"))
    partial_hedge(broker, 2, "10", next_price="20")
    asyncio.run(run_job(executor, repo, clock, "test:new:hedge:entry"))
    assert [a["quantity"] for a in broker.sent] == [2, 13, 6]
    assert repo.require("lots", "test:new:hedge")["remaining_qty"] == 8
    value = repo.require("executions", "test:new")
    assert D(value["actual_hedge_ratio"]) == D(".7")
    assert D(value["budget_usd"]) == 475


def test_projection_retains_machine_codes_with_human_allocation_detail():
    from doxagent.v2_read.executions import allocation_detail

    execution = dict(
        allocation_diagnostics={
            "MAIN": dict(
                reason="NO_VALID_INTEGER_COMBINATION",
                subreason="INTEGER_RATIO_GAP",
                target_ratio=".29",
                ratio_lower=".19",
                ratio_upper=".39",
                nominal_budget_usd="473",
                main_price="800",
                hedge_price="58",
            )
        }
    )
    text = allocation_detail(execution)["allocation_detail"]
    assert "目标 29.00%" in text and "19.00%–39.00%" in text
    assert "$473.00" in text and "整数股" in text
    assert execution["allocation_diagnostics"]["MAIN"]["reason"] == "NO_VALID_INTEGER_COMBINATION"
    assert allocation_detail({}) == {}


def test_late_fill_correction_accounts_actual_excess_without_new_entry(tmp_path):
    clock, repo, _, executor, broker = cash_group(tmp_path, hp="57.40")
    for suffix in (":entry", ":hedge:entry"):
        asyncio.run(run_job(executor, repo, clock, "test:new" + suffix))
    original = repo.fills("test:new:entry")[0]
    correction = {
        **original,
        "exec_id": original["exec_id"].rsplit(".", 1)[0] + ".02",
        "price": "100",
    }
    repo.apply_fill(correction, original["exit_at"])
    value = repo.require("executions", "test:new")
    assert D(value["budget_usd"]) == D(475) + (D(500) + D("114.80") - D(473))
    assert D(value["grant_budget_usd"]) == 475 and D(value["nominal_budget_usd"]) == 473
    for suffix in (":entry", ":hedge:entry"):
        asyncio.run(executor.step("test:new" + suffix))
    assert len(broker.sent) == 2
    assert repo.require("jobs", "test:new:entry")["state"] == "DONE"


def test_main_price_improvement_recomputes_hedge_without_increasing_main(tmp_path):
    clock, repo, _, executor, broker = cash_group(tmp_path)
    for suffix in (":entry", ":hedge:entry"):
        asyncio.run(run_job(executor, repo, clock, "test:new" + suffix))
    value = repo.require("executions", "test:new")
    assert value["plan"]["hedge_quantity"] == 2
    assert [a["quantity"] for a in broker.sent] == [5, 1]
    assert repo.require("jobs", "test:new:hedge:entry")["main_notional_usd"] == "298.95"
