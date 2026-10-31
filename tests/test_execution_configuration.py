import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from doxagent.ibkr_data_sources import DataSourceRouter
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.trade_execution.capital import CapitalLedger
from doxagent.trade_execution.configuration import ExecutionConfiguration
from doxagent.trade_execution.repository import ExecutionRepository
from doxagent.trade_execution.schema import ExecutionProfile
from doxagent.v2_control.repository import ControlRepository
from tests.test_trade_execution import Clock, profile
from tests.v2_backend.test_control import start


def configured(tmp_path):
    clock = Clock()
    journal = RuntimeJournal(tmp_path / "config.db", clock=clock)
    repo = ExecutionRepository(journal)
    revision = repo.import_profile(profile())
    CapitalLedger(repo).allocate("DU_TEST", "4000", "initial", "test")
    control = ControlRepository(journal)
    control.migrate()
    control.bind("*", "PAPER_TRADING", revision)
    start(control, "PAPER_TRADING")
    return clock, journal, repo, control, ExecutionConfiguration(journal), revision


def test_save_apply_and_account_pin_are_distinct(tmp_path):
    clock, journal, repo, control, service, revision = configured(tmp_path)
    service.apply(repo)
    first = service.read("MU")
    assert first["active"]["ticker_revision"] == first["revision"] == 1
    assert "execution_pin" not in first["active"]
    changed = {**first["configuration"], "trailing_profit_enabled": True}
    saved = service.save("MU", changed, first["revision"], "test")
    assert saved["active"]["ticker_revision"] == 1 and saved["revision"] == 2
    assert not saved["active"]["configuration"]["trailing_profit_enabled"]
    with pytest.raises(ValueError, match="REVISION"):
        service.save("MU", changed, 1, "conflict")
    service.apply(repo)
    assert service.read("MU")["active"]["configuration"]["trailing_profit_enabled"]
    # Existing execution keeps the original immutable pin even after a LIVE config applies.
    pin = {"revision": revision, "profile": repo.profile_payload(revision)}
    repo.admit(
        dict(
            intent_id="old",
            ticker="MU",
            status="READY",
            trade={"decision": "LONG"},
            expires_at=(clock() + timedelta(hours=1)).isoformat(),
            execution_pin=pin,
            trading_config=first["configuration"],
        )
    )
    live = repo.import_profile(
        ExecutionProfile(
            profile_id="live",
            environment="LIVE",
            account_type="CASH",
            port=4001,
            client_id=81,
            expected_account_id="U_TEST",
        )
    )
    CapitalLedger(repo).allocate("U_TEST", "4000", "live-initial", "test")
    service.save("MU", {**changed, "environment": "LIVE"}, 2, "test")
    service.apply(repo)
    assert service.read("MU")["active"]["account_revision"] == live
    assert repo.require("executions", "old")["profile_revision"] == revision
    assert repo.require("executions", "old")["account"] == "DU_TEST"
    assert control.get("MU")["mode"] == "LIVE_TRADING"


def test_unconfigured_live_draft_is_never_applied_or_routed(tmp_path):
    _, journal, repo, control, service, revision = configured(tmp_path)
    service.apply(repo)
    before = service.read("MU")
    result = service.save(
        "MU", {**before["configuration"], "environment": "LIVE"}, before["revision"], "test"
    )
    assert result["configuration"]["environment"] == "LIVE"
    service.apply(repo)
    result = service.read("MU")
    assert result["active"]["configuration"]["environment"] == "PAPER"
    assert result["error"]["error"] == "ACCOUNT_NOT_CONFIGURED"


def test_account_capital_delta_does_not_reset_realized_pnl(tmp_path):
    _, journal, repo, _, service, _ = configured(tmp_path)
    ledger = CapitalLedger(repo)
    with repo.transaction() as db:
        db.execute(
            "INSERT INTO te_capital_events VALUES(?,?,?,?,?,?)",
            ("fee", "DU_TEST", "ADJUSTMENT", "-10", journal.clock().isoformat(), "{}"),
        )
    assert ledger.book("DU_TEST")["equity"] == 3990
    ledger.allocate("DU_TEST", "4500", "new-capital", "add capital")
    assert ledger.book("DU_TEST")["realized"] == -10
    assert ledger.book("DU_TEST")["equity"] == 4490
    ledger.allocate("DU_TEST", "4500", "new-capital", "idempotent")
    assert ledger.book("DU_TEST")["principal"] == 4500


def test_data_router_sticky_failover_has_independent_capabilities():
    now = [0.0]
    bad = set()
    calls = []
    router = DataSourceRouter(
        [
            dict(name="live", host="localhost", port=1, capabilities=["news", "history"]),
            dict(name="paper", host="localhost", port=2, capabilities=["news", "history"]),
        ],
        clock=lambda: now[0],
    )

    def operation(source):
        calls.append(source["name"])
        if source["name"] in bad:
            raise ConnectionError("down")
        return source["name"]

    assert router.run("news", operation) == "live"
    bad.add("live")
    assert router.run("news", operation) == "paper"
    bad.clear()
    now[0] = 61
    assert router.run("news", operation) == "paper"
    assert router.run("history", operation) == "live"
    assert calls == ["live", "live", "paper", "paper", "live"]


def test_runtime_delay_boundary_and_closed_exception(tmp_path):
    from doxagent.persistent_runtime_v2.schema import TradeRecord
    from doxagent.persistent_runtime_v2.trade_output import TradeOutputService
    from tests.test_persistent_runtime_v2 import _source
    from tests.test_runtime_orchestration_execution import runtime_at

    at = datetime(2026, 9, 8, 14, tzinfo=UTC)
    runtime, journal = runtime_at(tmp_path, [at])
    data = json.loads(
        (
            Path(__file__).parents[1] / "eval/trade_execution/20260928_audit/ledger_redacted.json"
        ).read_text()
    )
    trade = next(
        TradeRecord.model_validate(json.loads(v["payload"])["trade"])
        for v in data["runtime_values"]
        if v["namespace"] == "trade_intents"
    ).model_copy(update={"ticker": "MU"})
    try:
        source = _source().model_copy(
            update={"message_bus_event_time": at, "published_at": at, "collected_at": at}
        )
        original = runtime.execute_message(source)
        output = TradeOutputService(journal)
        for seconds, expected in [(720, "READY"), (721, "RUNTIME_DELAY_EXCEEDED")]:
            case = original.model_copy(
                update={
                    "case_id": f"delay-{seconds}",
                    "runtime_mode": "REALTIME",
                    "source": source.model_copy(
                        update={"message_bus_event_time": at - timedelta(seconds=seconds)}
                    ),
                }
            )
            assert output.record(case, trade) == expected
        case = original.model_copy(
            update={
                "case_id": "closed",
                "runtime_mode": "CLOSED",
                "source": source.model_copy(
                    update={"message_bus_event_time": at - timedelta(hours=3)}
                ),
            }
        )
        assert output.record(case, trade, selection_id="closed-selection") in {
            "READY",
            "DUPLICATE_EVENT_TRADE",
            "DUPLICATE_REALTIME_OUTPUT",
        }
    finally:
        runtime.close()


def test_applied_ban_permanently_abandons_unsent_intent(tmp_path):
    import asyncio

    from doxagent.semantic_clock import semantic_day
    from doxagent.trade_execution.executor import Executor
    from tests.test_trade_execution import Broker, run_job

    clock, journal, repo, control, service, revision = configured(tmp_path)
    service.apply(repo)
    executor = Executor(repo, broker_factory=lambda p, **kw: Broker(p, **kw))
    broker = executor.broker(revision)
    broker.clock = clock
    old = service.read("MU")
    intent = dict(
        intent_id="ban:intent",
        ticker="MU",
        status="READY",
        released_at=clock().isoformat(),
        expires_at=(clock() + timedelta(hours=1)).isoformat(),
        trade={"decision": "LONG"},
        trading_config=old["configuration"],
        execution_pin={"revision": revision, "profile": repo.profile_payload(revision)},
    )
    repo.admit(intent)
    service.save(
        "MU",
        {**old["configuration"], "banned_semantic_dates": [semantic_day(clock()).isoformat()]},
        old["revision"],
        "test",
    )
    service.apply(repo)
    asyncio.run(run_job(executor, repo, clock, "ban:intent:entry"))
    assert not broker.sent
    assert repo.require("jobs", "ban:intent:entry")["reason"] == "BANNED_TRADE_DAY"
    latest = service.read("MU")
    service.save(
        "MU",
        {**old["configuration"], "banned_semantic_dates": []},
        latest["revision"],
        "remove date",
    )
    service.apply(repo)
    asyncio.run(executor.step("ban:intent:entry"))
    assert not broker.sent


def test_unknown_live_margin_only_draft_cannot_apply_when_account_becomes_cash(tmp_path):
    _, journal, repo, _, service, _ = configured(tmp_path)
    service.apply(repo)
    before = service.read("MU")
    draft = {
        **before["configuration"],
        "environment": "LIVE",
        "hedge_enabled": True,
        "margin_index_etf": "SIDX",
    }
    service.save("MU", draft, before["revision"], "test")
    service.apply(repo)
    assert service.read("MU")["error"]["error"] == "ACCOUNT_NOT_CONFIGURED"
    repo.import_profile(
        ExecutionProfile(
            profile_id="cash-live",
            environment="LIVE",
            account_type="CASH",
            port=4001,
            client_id=81,
            expected_account_id="U_TEST",
        )
    )
    CapitalLedger(repo).allocate("U_TEST", "4000", "cash-principal", "test")
    service.apply(repo)
    value = service.read("MU")
    assert value["accounts"]["LIVE"]["account_type"] == "CASH"
    assert value["error"]["error"] == "HEDGE_ASSET_MISSING"
    assert value["active"] == before["active"]
    assert value["active"]["configuration"]["environment"] == "PAPER"
