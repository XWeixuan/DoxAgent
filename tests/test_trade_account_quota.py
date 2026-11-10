"""Account quota and LIVE/MARGIN sizing regressions using an in-memory broker."""

import asyncio
from decimal import Decimal as D

import pytest

from doxagent.trade_execution import groups
from doxagent.trade_execution.capital import CapitalLedger
from doxagent.trade_execution.configuration import ExecutionConfiguration
from doxagent.trade_execution.planning import TradingConfig
from doxagent.trade_execution.schema import ExecutionProfile, Strategy
from doxagent.v2_control.repository import ControlRepository
from tests.test_trade_execution import profile, run_job
from tests.test_trade_execution_shared_equity import setup_shared
from tests.test_trade_groups_capital import open_trade


def live_setup(tmp_path, limit=1):
    clock, repo, _, executor, _ = setup_shared(tmp_path)
    revision = repo.import_profile(
        ExecutionProfile(
            **{
                **profile(strategy=Strategy()).model_dump(),
                "environment": "LIVE",
                "expected_account_id": "U_TEST",
                "account_type": "MARGIN",
            }
        )
    )
    CapitalLedger(repo).allocate("U_TEST", "2000", "live-capital", "explicit LIVE allocation")
    CapitalLedger(repo).set_ticker_limit("U_TEST", limit, "test quota")
    broker = executor.broker(revision)
    broker.symbols = {**broker.symbols, "SOXL": 5}
    broker.prices["SOXL"] = D(100)
    return clock, repo, revision, executor, broker


@pytest.mark.parametrize("direction,sides", [("LONG", ["BUY", "SELL"]), ("SHORT", ["SELL", "BUY"])])
def test_live_one_ticker_includes_both_legs_and_keeps_slot_until_hedge_exit(
    tmp_path, direction, sides
):
    clock, repo, revision, executor, broker = live_setup(tmp_path)
    cfg = TradingConfig(environment="LIVE", hedge_enabled=True, margin_index_etf="SOXL")
    open_trade(repo, revision, "MU", "live:A", cfg, direction)
    for suffix in [":entry", ":hedge:entry"]:
        asyncio.run(run_job(executor, repo, clock, "live:A" + suffix))
    assert [a["side"] for a in broker.sent] == sides
    assert len(broker.previews) == 2
    assert D(repo.require("executions", "live:A")["budget_usd"]) == 2000
    assert repo.require("executions", "live:A")["allocation_policy_version"] == 2
    assert all(a["account"] == "U_TEST" for a in broker.sent)
    open_trade(repo, revision, "BE", "live:B", TradingConfig(environment="LIVE"))
    asyncio.run(run_job(executor, repo, clock, "live:B:entry"))
    assert len(broker.sent) == 2 and not repo.attempts("live:B:entry")
    assert repo.require("executions", "live:B")["entry_reason"] == "PORTFOLIO_CAPACITY_EXHAUSTED"
    with repo.transaction() as db:
        groups.request_exit(db, "live:A", clock().isoformat(), "TEST")
    asyncio.run(run_job(executor, repo, clock, "live:A:exit"))
    assert repo.require("lots", "live:A:hedge")["remaining_qty"] > 0
    open_trade(repo, revision, "BE", "live:C", TradingConfig(environment="LIVE"))
    asyncio.run(run_job(executor, repo, clock, "live:C:entry"))
    assert not repo.attempts("live:C:entry")
    asyncio.run(run_job(executor, repo, clock, "live:A:hedge:exit"))
    assert repo.require("executions", "live:A")["group_state"] == "CLOSED"
    open_trade(repo, revision, "BE", "live:D", TradingConfig(environment="LIVE"))
    asyncio.run(run_job(executor, repo, clock, "live:D:entry"))
    assert repo.require("lots", "live:D")["remaining_qty"] > 0
    assert [a["side"] for a in broker.sent[2:4]] == [
        "SELL" if sides[0] == "BUY" else "BUY",
        "SELL" if sides[1] == "BUY" else "BUY",
    ]


def test_live_limit_two_preserves_full_ticker_budget_and_margin_gross_cap(tmp_path):
    clock, repo, revision, executor, broker = live_setup(tmp_path, 2)
    for ticker in ["MU", "BE", "ONDS"]:
        open_trade(repo, revision, ticker, "live:" + ticker, TradingConfig(environment="LIVE"))
        asyncio.run(run_job(executor, repo, clock, "live:" + ticker + ":entry"))
    first = D(repo.require("executions", "live:MU")["budget_usd"])
    second = D(repo.require("executions", "live:BE")["budget_usd"])
    assert first == 2000  # Count limit must not divide the per-ticker budget by two.
    assert second == 1000 and first + second == D("1.5") * 2000
    assert len(broker.sent) == 2 and not repo.attempts("live:ONDS:entry")
    CapitalLedger(repo).allocate("U_TEST", "3000", "increase-capital", "explicit increase")
    cycle = repo.cycle_for_entry(
        "U_TEST",
        "LIVE",
        executor.cycle_id(repo.profile(revision)),
        broker.sync(),
        D(".25"),
        profile_revision=revision,
    )
    assert D(cycle["equity"]) == 3000
    with repo.transaction() as db:
        ledger = CapitalLedger(repo)
        assert (
            ledger.capacity(
                db,
                {"id": "new", "account": "U_TEST", "ticker": "ONDS"},
                repo.profile(revision),
                D(cycle["equity"]),
                broker.sync(),
            )
            == 0
        )
        assert (
            ledger.capacity(
                db,
                {"id": "new", "account": "U_TEST", "ticker": "MU"},
                repo.profile(revision),
                D(cycle["equity"]),
                broker.sync(),
            )
            == 1000
        )


def test_lowered_quota_counts_groups_pinned_to_old_revision_without_mutating_them(tmp_path):
    clock, repo, old, executor, broker = live_setup(tmp_path, 2)
    for ticker in ["MU", "BE"]:
        open_trade(repo, old, ticker, "old:" + ticker, TradingConfig(environment="LIVE"))
        asyncio.run(run_job(executor, repo, clock, "old:" + ticker + ":entry"))
    before = [repo.require("executions", "old:" + t) for t in ["MU", "BE"]]
    CapitalLedger(repo).set_ticker_limit("U_TEST", 1, "lower quota")
    revision = old
    with repo.transaction() as db:
        assert (
            CapitalLedger(repo).capacity(
                db,
                {"id": "new", "account": "U_TEST", "ticker": "ONDS"},
                repo.profile(revision),
                D(2000),
                broker.sync(),
            )
            == 0
        )
    assert before == [repo.require("executions", "old:" + t) for t in ["MU", "BE"]]
    assert repo.journal.get("execution_account_limits", "U_TEST")["max_concurrent_tickers"] == 1


def test_margin_profile_applies_to_future_ticker_pin_and_preserves_cash_execution(tmp_path):
    clock, repo, _, executor, broker = live_setup(tmp_path)
    ControlRepository(repo.journal).migrate()
    cash = repo.import_profile(
        repo.profile(repo.journal.get("execution_account_profiles", "LIVE")).model_copy(
            update={"account_type": "CASH"}
        )
    )
    open_trade(repo, cash, "MU", "old:cash", TradingConfig(environment="LIVE"))
    frozen = repo.require("executions", "old:cash")
    service = ExecutionConfiguration(repo.journal)
    service.save(
        "MU",
        TradingConfig(
            environment="LIVE",
            hedge_enabled=True,
            margin_index_etf="SOXL",
            cash_positive_index_etf="SOXL",
            cash_inverse_index_etf="SOXS",
        ).model_dump(mode="json"),
        0,
        "test",
    )
    service.apply(repo)
    current = repo.import_profile(repo.profile(cash).model_copy(update={"account_type": "MARGIN"}))
    service.apply(repo)
    active = repo.journal.get("execution_config_active", "MU")
    assert (
        active["account_revision"] == current
        and repo.journal.get("execution_account_limits", "U_TEST")["max_concurrent_tickers"] == 1
    )
    assert active["execution_pin"]["profile"]["account_type"] == "MARGIN"
    assert repo.require("executions", "old:cash") == frozen


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "1"])
def test_quota_requires_a_positive_integer(tmp_path, invalid):
    _, repo, _, _, _ = setup_shared(tmp_path)
    with pytest.raises(ValueError):
        CapitalLedger(repo).set_ticker_limit("DU_TEST", invalid, "test invalid")


def test_archived_margin_profile_keeps_original_rules():
    assert (
        "max_concurrent_tickers"
        not in ExecutionProfile.model_validate(profile().model_dump()).model_dump()
    )


def test_cli_updates_only_account_quota_without_broker_calls(tmp_path, monkeypatch, capsys):
    import json

    from doxagent.trade_execution.cli import main
    from doxagent.trade_execution.ibkr_session import IbkrSession

    _, repo, revision, _, _ = setup_shared(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("configuration must not connect to a broker")

    monkeypatch.setattr(IbkrSession, "connect", forbidden)
    monkeypatch.setattr(
        "sys.argv",
        [
            "executor",
            "--db",
            str(repo.journal.path),
            "set-ticker-limit",
            "--profile",
            revision,
            "--max-concurrent-tickers",
            "1",
            "--reason",
            "operator quota",
        ],
    )
    main()
    assert json.loads(capsys.readouterr().out)["max_concurrent_tickers"] == 1
    assert repo.journal.get("execution_account_limits", "DU_TEST")["max_concurrent_tickers"] == 1
    with repo.transaction(write=False) as db:
        assert db.execute("SELECT COUNT(*) FROM te_attempts").fetchone()[0] == 0


@pytest.mark.parametrize(
    "warning",
    [
        "",
        "Special margin requirements apply to this product.",
        "Equity with Loan Value is shown in the account window.",
    ],
)
def test_informational_margin_warning_does_not_reject_a_funded_preview(warning):
    from doxagent.trade_execution.portfolio import margin_after

    funds = {"AvailableFunds": D(2000), "LookAheadAvailableFunds": D(2000)}
    preview = {
        "status": "VALIDATED",
        "rows": [{"initial_margin_change": "500", "warning": warning}],
    }
    assert margin_after(funds, preview, D(2000)) == (D(1500), D(1500))


@pytest.mark.parametrize(
    "row",
    [
        {"initial_margin_change": "1700", "warning": "Special margin requirements apply."},
        {"initial_margin_change": "500", "warning": "Insufficient funds for margin requirement."},
        {
            "initial_margin_change": "500",
            "warning": "Equity with Loan Value is below Initial Margin.",
        },
        {
            "initial_margin_change": "500",
            "warning": "Equity with Loan Value must exceed Initial Margin.",
        },
        {"initial_margin_change": "500", "reject_reason": "Trading permission is required."},
    ],
)
def test_margin_warning_fix_keeps_buffer_and_explicit_rejection_gates(row):
    from doxagent.trade_execution.portfolio import margin_after

    with pytest.raises(ValueError, match="INSUFFICIENT_MARGIN|MARGIN_CHECK_UNAVAILABLE"):
        margin_after(
            {"AvailableFunds": D(2000), "LookAheadAvailableFunds": D(2000)},
            {"status": "VALIDATED", "rows": [row]},
            D(2000),
        )
