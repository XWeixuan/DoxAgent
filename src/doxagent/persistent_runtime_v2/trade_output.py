"""Atomic Runtime trade output and optional external delivery; no implicit orders."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Any, Protocol

from doxagent.semantic_clock import expires_at, semantic_day
from doxagent.v2_control.repository import control_in, output_permission

from .fencing import assert_write_lease
from .journal import RuntimeJournal, encode
from .schema import PolicyActivationRecord, RuntimeCase, TradeRecord


class TradeExecutionAdapter(Protocol):
    async def readiness(self) -> dict[str, Any]: ...
    async def submit(self, intent: dict[str, Any]) -> dict[str, Any]: ...
    async def reconcile(self, intent_id: str) -> dict[str, Any]: ...


class AccountStateProvider(Protocol):
    async def snapshot(self, account_ref: str) -> dict[str, Any]: ...


class LocalTradeSink:
    def __init__(self, journal: RuntimeJournal) -> None:
        self.journal = journal

    async def readiness(self) -> dict[str, Any]:
        return {"ready": True, "mode": "runtime_output_only"}

    async def submit(self, intent: dict[str, Any]) -> dict[str, Any]:
        receipt = {"intent_id": intent["intent_id"], "status": "OUTPUT_RECORDED"}
        self.journal.set("trade_receipts", intent["intent_id"], receipt)
        return receipt

    async def reconcile(self, intent_id: str) -> dict[str, Any]:
        return dict(
            self.journal.get(
                "trade_receipts", intent_id, {"intent_id": intent_id, "status": "NOT_FOUND"}
            )
        )


class IBKRPaperAdapter:
    """Unconnected extension boundary. Never invent account/contract/order parameters."""

    async def readiness(self) -> dict[str, Any]:
        return {"ready": False, "mode": "ibkr_paper", "reason": "ACCOUNT_NOT_CONFIGURED"}

    async def submit(self, intent: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("IBKR paper execution is not configured")

    async def reconcile(self, intent_id: str) -> dict[str, Any]:
        return {"intent_id": intent_id, "status": "UNKNOWN", "reason": "NOT_CONNECTED"}


class TradeOutputService:
    def __init__(self, journal: RuntimeJournal) -> None:
        self.journal = journal

    def record(
        self,
        case: RuntimeCase,
        trade: TradeRecord,
        *,
        release_day: date | None = None,
        selection_id: str | None = None,
    ) -> str:
        if case.runtime_mode == "CLOSED" and selection_id is None:
            identity = f"candidate:{case.case_id}"
            value = {
                "candidate_id": identity,
                "case_id": case.case_id,
                "ticker": case.ticker,
                "closed_cycle_id": case.closed_cycle_id,
                "sweep_id": case.sweep_id,
                "semantic_day": case.trading_date.isoformat(),
                "status": "PENDING",
                "created_at": self.journal.clock().isoformat(),
                "trade": trade.model_dump(mode="json"),
            }
            with self.journal.transaction() as db:
                assert_write_lease(db)
                reason, _ = output_permission(db, case.ticker, case.case_id)
                value["origin_trade_eligible"] = reason is None
                value["trade_disposition"] = reason
                db.execute(
                    "INSERT OR IGNORE INTO runtime_values VALUES('candidates',?,?)",
                    (identity, encode(value)),
                )
            return "CANDIDATE"
        day = release_day or case.trading_date
        identity = f"trade:{selection_id or case.case_id}"
        with self.journal.transaction() as db:
            assert_write_lease(db)
            old = db.execute(
                "SELECT payload FROM runtime_values WHERE namespace='trade_intents' AND key=?",
                (identity,),
            ).fetchone()
            if old:
                return str(json.loads(old[0])["status"])
            reason, execution_pin = output_permission(db, case.ticker, case.case_id)
            if reason:
                analysis = {
                    "case_id": case.case_id,
                    "ticker": case.ticker,
                    "trade_disposition": reason,
                    "trade": trade.model_dump(mode="json"),
                    "recorded_at": self.journal.clock().isoformat(),
                }
                db.execute(
                    "INSERT OR IGNORE INTO runtime_values VALUES('analysis_trade_decisions',?,?)",
                    (identity, encode(analysis)),
                )
                return reason
            expired = (case.trade_expired and selection_id is None) or (
                semantic_day(self.journal.clock()) != day
            )
            status = "EXPIRED_SEMANTIC_DAY" if expired else "READY"
            if status == "READY" and selection_id:
                prior = db.execute(
                    "SELECT 1 FROM runtime_values WHERE namespace='trade_intents' "
                    "AND json_extract(payload,'$.ticker')=? "
                    "AND json_extract(payload,'$.release_semantic_day')=? "
                    "AND json_extract(payload,'$.trade.decision')=? "
                    "AND json_extract(payload,'$.status') NOT IN "
                    "('EXPIRED_SEMANTIC_DAY','DUPLICATE_POLICY','DUPLICATE_REALTIME_OUTPUT') LIMIT 1",
                    (case.ticker, day.isoformat(), trade.decision.value),
                ).fetchone()
                if prior:
                    status = "DUPLICATE_REALTIME_OUTPUT"
            if status == "READY" and trade.executed_policy_id:
                claim = PolicyActivationRecord(
                    case_id=case.case_id,
                    source_message_id=case.source.source_message_id,
                    ticker=case.ticker,
                    policy_id=trade.executed_policy_id,
                    activation_revision=trade.activation_revision or "",
                    policy_set_version=trade.policy_set_version,
                    matched_condition_ids=trade.matched_condition_ids,
                )
                row = db.execute(
                    "SELECT case_id FROM runtime_v2_policy_activations WHERE "
                    "ticker=? AND policy_id=? AND activation_revision=?",
                    (case.ticker, claim.policy_id, claim.activation_revision),
                ).fetchone()
                if row and row[0] != case.case_id:
                    status = "DUPLICATE_POLICY"
                else:
                    db.execute(
                        "INSERT OR IGNORE INTO runtime_v2_policy_activations VALUES"
                        "(?,?,?,?,?,?,?,?)",
                        (
                            case.ticker,
                            claim.policy_id,
                            claim.activation_revision,
                            case.case_id,
                            claim.source_message_id,
                            claim.policy_set_version,
                            claim.model_dump_json(),
                            self.journal.clock().isoformat(),
                        ),
                    )
            if status == "READY":
                db.execute(
                    "INSERT OR IGNORE INTO runtime_v2_trade_records VALUES(?,?,?,?,?,?)",
                    (
                        case.case_id,
                        case.ticker,
                        day.isoformat(),
                        trade.daily_status.value,
                        trade.model_copy(update={"trading_date": day}).model_dump_json(),
                        self.journal.clock().isoformat(),
                    ),
                )
            intent = {
                "intent_id": identity,
                "ticker": case.ticker,
                "case_id": case.case_id,
                "selection_id": selection_id,
                "release_semantic_day": day.isoformat(),
                "expires_at": expires_at(day).isoformat(),
                "status": status,
                "trade": trade.model_dump(mode="json"),
                "submitted": False,
                "released_at": self.journal.clock().isoformat(),
            }
            profile = db.execute(
                "SELECT payload FROM runtime_values WHERE namespace='trade_execution' AND key='active'"
            ).fetchone()
            if execution_pin and status == "READY":
                intent["execution_pin"] = execution_pin
            elif profile and status == "READY" and control_in(db, case.ticker) is None:
                intent["execution_pin"] = json.loads(profile[0])
            db.execute(
                "INSERT INTO runtime_values VALUES('trade_intents',?,?)", (identity, encode(intent))
            )
        return status

    async def deliver(self, adapter: TradeExecutionAdapter, *, limit: int = 20) -> int:
        count = 0
        with self.journal.transaction() as db:
            rows = db.execute(
                "SELECT v.payload FROM runtime_values v LEFT JOIN runtime_tasks t "
                "ON t.id='delivery:' || v.key WHERE v.namespace='trade_intents' "
                "AND json_extract(v.payload,'$.status') IN ('READY','UNKNOWN') "
                "AND (t.id IS NULL OR (t.status IN ('PENDING','RUNNING') "
                "AND t.due_at<=? AND (t.lease_until IS NULL OR t.lease_until<=?))) "
                "ORDER BY coalesce(t.due_at,''),v.key LIMIT ?",
                (self.journal.clock().isoformat(), self.journal.clock().isoformat(), limit),
            ).fetchall()
        for row in rows:
            intent = json.loads(row[0])
            if count >= limit:
                break
            if intent["status"] not in {"READY", "UNKNOWN"}:
                continue
            identity = intent["intent_id"]
            task_id = f"delivery:{identity}"
            if self.journal.get_task(task_id) is None:
                self.journal.put_task(
                    task_id, intent["ticker"], "DELIVERY", {"intent_id": identity}
                )
            task = self.journal.claim(task_id, seconds=60)
            if task is None:
                continue
            try:
                receipt = await asyncio.wait_for(adapter.reconcile(identity), timeout=30)
                if receipt["status"] == "NOT_FOUND":
                    if intent["expires_at"] <= self.journal.clock().isoformat():
                        intent["status"] = "EXPIRED_SEMANTIC_DAY"
                        self.journal.set("trade_intents", identity, intent)
                        self.journal.finish(task, status=intent["status"])
                        continue
                    if not (await asyncio.wait_for(adapter.readiness(), timeout=30))["ready"]:
                        raise RuntimeError("execution adapter not ready")
                    intent["submitted"] = True
                    intent["status"] = "UNKNOWN"
                    self.journal.set("trade_intents", identity, intent)
                    receipt = await asyncio.wait_for(adapter.submit(intent), timeout=30)
                if receipt["status"] == "UNKNOWN":
                    raise RuntimeError("execution receipt unknown; reconciliation required")
                self.journal.set("trade_receipts", identity, receipt)
                intent["status"] = receipt["status"]
                self.journal.set("trade_intents", identity, intent)
                self.journal.finish(task, receipt=receipt)
                count += 1
            except Exception as exc:
                self.journal.fail(task, exc)
        return count
