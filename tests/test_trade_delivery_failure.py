"""A failed delivery is visible without inventing an execution or broker order."""

import asyncio
from datetime import UTC, datetime, timedelta

from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.trade_output import TradeOutputService
from doxagent.trade_execution.schema import Strategy
from doxagent.v2_read.executions import pending_intent


class RejectingAdapter:
    async def readiness(self):
        return {"ready": True}

    async def reconcile(self, intent_id):
        return {"intent_id": intent_id, "status": "NOT_FOUND"}

    async def submit(self, intent):
        Strategy.model_validate(
            {"capital_model": "SHARED_CYCLE", "target_notional_usd": "20000"}
        )


class LostReceiptAdapter(RejectingAdapter):
    accepted = False

    async def reconcile(self, intent_id):
        return {
            "intent_id": intent_id,
            "status": "EXECUTION_ACCEPTED" if self.accepted else "NOT_FOUND",
        }

    async def submit(self, intent):
        self.accepted = True
        raise TimeoutError("receipt lost after durable intake")


def make_journal(tmp_path):
    now = [datetime(2026, 9, 30, 12, tzinfo=UTC)]
    journal = RuntimeJournal(tmp_path / "runtime.db", clock=lambda: now[0])
    intent = {
        "intent_id": "trade:test",
        "case_id": "case_test",
        "ticker": "MU",
        "status": "READY",
        "trade": {"decision": "LONG"},
        "released_at": now[0].isoformat(),
        "expires_at": (now[0] + timedelta(days=1)).isoformat(),
    }
    journal.set("trade_intents", intent["intent_id"], intent)
    return journal, now, intent


def test_terminal_delivery_validation_failure_is_visible(tmp_path):
    journal, now, intent = make_journal(tmp_path)
    service = TradeOutputService(journal)
    assert asyncio.run(service.deliver(RejectingAdapter())) == 0
    assert journal.get_task("delivery:trade:test")["status"] == "PENDING"
    now[0] += timedelta(minutes=2)
    assert asyncio.run(service.deliver(RejectingAdapter())) == 0
    failed = journal.get("trade_intents", "trade:test")
    assert failed["status"] == "DELIVERY_FAILED"
    assert failed["submitted"] is True
    assert failed["delivery_failure"]["code"] == "PROFILE_VALIDATION_FAILED"
    assert "shared cycle must not set target_notional_usd" in failed["delivery_failure"]["detail"]
    assert journal.get_task("delivery:trade:test")["status"] == "FAILED"
    assert asyncio.run(service.deliver(RejectingAdapter())) == 0

    class EmptyStore:
        def get(self, *_):
            return None

    row = pending_intent(EmptyStore(), "MU", failed)[0]["data"]
    assert row["intake_status"] == "REJECTED"
    assert row["execution_state"] == "NOT_EXECUTED"
    assert row["execution_reason_codes"] == ["DELIVERY_FAILED"]
    assert "PROFILE_VALIDATION_FAILED" in row["entry_reason"]


def test_lost_receipt_reconciles_durable_execution(tmp_path):
    journal, _, _ = make_journal(tmp_path)
    assert asyncio.run(TradeOutputService(journal).deliver(LostReceiptAdapter())) == 1
    assert journal.get_task("delivery:trade:test")["status"] == "SUCCEEDED"
    assert journal.get("trade_intents", "trade:test")["status"] == "EXECUTION_ACCEPTED"
