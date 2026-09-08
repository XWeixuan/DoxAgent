import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from doxagent.persistent_runtime_v2.trade_output import LocalTradeSink, TradeOutputService
from doxagent.v2_control.repository import ControlError, ControlRepository, control_in
from tests.test_persistent_runtime_v2 import _source
from tests.test_runtime_orchestration_execution import runtime_at


def start(control, mode="MESSAGE_MONITORING", key="start-0001"):
    state = control.get("MU")
    op = control.submit(
        "MU",
        "START",
        actor="developer",
        key=key,
        body={"monitor_mode": mode},
        expected=str(state["revision"]) if state else None,
    )
    control.settle(op["id"], activation_id="test-active")
    return op


def test_monitoring_trade_is_analysis_without_consumption(tmp_path):
    runtime, journal = runtime_at(tmp_path, [datetime(2026, 9, 8, 12, tzinfo=UTC)])
    control = ControlRepository(journal)
    control.migrate()
    start(control)
    try:
        case = runtime.execute_message(_source())
        runtime.process_pending_effects()
        assert case.w2_final.policy_ids
        assert not journal.values("trade_intents")
        assert not runtime.repository.list_consumed_policy_revisions("MU")
        assert journal.values("analysis_trade_decisions")[0]["trade_disposition"] == "ANALYSIS_ONLY"
    finally:
        runtime.close()


def test_pause_idempotency_and_remove_readd_fence(tmp_path):
    runtime, journal = runtime_at(tmp_path, [datetime(2026, 9, 8, 12, tzinfo=UTC)])
    control = ControlRepository(journal)
    control.migrate()
    start(control)
    try:
        control.admit("old-case", "MU")
        revision = str(control.get("MU")["revision"])
        op = control.submit(
            "MU", "REMOVE", actor="developer", key="remove-0001", body={}, expected=revision
        )
        with pytest.raises(ControlError, match="ANALYSIS_PAUSED"):
            control.dispatch("next", "MU", case_id="old-case")
        control.settle(op["id"])
        start(control, key="start-0002")
        with pytest.raises(ControlError, match="ANALYSIS_REMOVED"):
            control.dispatch("next", "MU", case_id="old-case")
        assert (
            control.submit(
                "MU", "REMOVE", actor="developer", key="remove-0001", body={}, expected=revision
            )["id"]
            == op["id"]
        )
        assert not control.get("MU")["removed"]
    finally:
        runtime.close()


def test_released_intent_delivers_when_all_tickers_paused(tmp_path):
    now = [datetime(2026, 9, 8, 12, tzinfo=UTC)]
    runtime, journal = runtime_at(tmp_path, now)
    try:
        runtime.execute_message(_source())
        runtime.process_pending_effects()
        intent = journal.values("trade_intents")[0]
        control = ControlRepository(journal)
        control.migrate()
        start(control)
        op = control.submit(
            "MU",
            "PAUSE",
            actor="developer",
            key="pause-0001",
            body={},
            expected=str(control.get("MU")["revision"]),
        )
        control.settle(op["id"])
        assert asyncio.run(TradeOutputService(journal).deliver(LocalTradeSink(journal))) == 1
        assert journal.get("trade_intents", intent["intent_id"])["status"] == "OUTPUT_RECORDED"
    finally:
        runtime.close()


def test_monitoring_backlog_keeps_original_eligibility(tmp_path):
    now = [datetime(2026, 9, 8, 12, tzinfo=UTC)]
    runtime, journal = runtime_at(tmp_path, now)
    control = ControlRepository(journal)
    control.migrate()
    start(control)
    try:
        control.admit("old", "MU")
        now[0] += timedelta(minutes=1)
        # Only eligibility is exercised here; actual profile validation has separate tests.
        with journal.transaction() as db:
            state = control_in(db, "MU")
            state.update(mode="LIVE_TRADING", mode_effective_at=now[0].isoformat())
            control._save(db, state, "fixture-mode")
        control.admit("backlog", "MU", now[0] - timedelta(seconds=1))
        with journal.transaction() as db:
            for identity in ("old", "backlog"):
                value = json.loads(
                    db.execute(
                        "SELECT payload FROM v2_analysis_admission WHERE identity=?", (identity,)
                    ).fetchone()[0]
                )
                assert value["trade_eligible"] is False
    finally:
        runtime.close()
