"""Atomic Runtime trade output and optional external delivery; no implicit orders."""

from __future__ import annotations

import asyncio
import json
from contextlib import nullcontext
from datetime import date, timedelta
from typing import Any, Protocol

from pydantic import ValidationError

from doxagent.semantic_clock import expires_at, semantic_day
from doxagent.v2_control.repository import output_permission

from . import policy_consumption
from .fencing import assert_write_lease
from .journal import RuntimeJournal, encode
from .schema import PolicyActivationRecord, RuntimeCase, TradeRecord


def event_trade_key(trade: TradeRecord) -> str | None:
    """Claim only an unambiguous known-fact state transition, not text similarity."""
    if trade.w3_trade_decision is not None:
        return None
    if trade.decision_origin.value != "W3" or not trade.w3_result:
        return None
    attributions = trade.w1_result.fact_attributions or []
    deltas = trade.w3_result.delta_candidates
    if len(attributions) != 1 or len(attributions[0].fact_ids) != 1 or len(deltas) != 1:
        return None
    delta = deltas[0]
    if delta.occurrence_date is None:
        return None
    return ":".join(
        (
            trade.ticker,
            attributions[0].event_id,
            attributions[0].fact_ids[0],
            delta.assertion_state.value,
            delta.occurrence_date.isoformat(),
        )
    )


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

    def audit_context(self, db, case, *, decision_at=None):
        """Freeze attribution at decision time; this grants no execution permission."""
        from doxagent.trade_execution.configuration import active_in, account_pin

        active = active_in(db, case.ticker) or {}
        desired = db.execute(
            "SELECT payload FROM runtime_values WHERE namespace='execution_config_desired' AND key=?",
            (case.ticker,),
        ).fetchone()
        environment = (
            (json.loads(desired[0]) if desired else active).get("configuration", {}).get("environment")
        )
        if environment is None:
            row = (
                db.execute(
                    "SELECT json_extract(payload,'$.requested_mode') FROM v2_ticker_control WHERE ticker=?",
                    (case.ticker,),
                ).fetchone()
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_ticker_control'").fetchone()
                else None
            )
            environment = {"PAPER_TRADING": "PAPER", "LIVE_TRADING": "LIVE"}.get(row[0] if row else "")
        environment = environment or "PAPER"
        pin = account_pin(db, environment) if environment else None
        return dict(
            version=1,
            decision_at=decision_at or self.journal.clock().isoformat(),
            exit_offset_minutes=(pin or {})
            .get("profile", {})
            .get("strategy", {})
            .get("exit_offset_minutes", 30),
            environment=environment,
            account=(pin or {}).get("profile", {}).get("expected_account_id"),
            title=case.source.snapshot.title,
        )


    def audit_rejected(self, case, trade, reason):
        """Record a terminal W3 rejection without claiming or releasing an intent."""
        if case.runtime_mode == "CLOSED" or trade.decision.value not in {"LONG", "SHORT"}:
            return
        with self.journal.transaction() as db:
            assert_write_lease(db)
            value = dict(
                intent_id=f"trade:{case.case_id}",
                case_id=case.case_id,
                ticker=case.ticker,
                trade_disposition=reason,
                trade=trade.model_dump(mode="json"),
                recorded_at=self.journal.clock().isoformat(),
                audit_context=self.audit_context(db, case),
            )
            db.execute(
                "INSERT OR IGNORE INTO runtime_values VALUES('analysis_trade_decisions',?,?)",
                (value["intent_id"], encode(value)),
            )

    def record(
        self,
        case: RuntimeCase,
        trade: TradeRecord,
        *,
        release_day: date | None = None,
        selection_id: str | None = None,
        db=None,
        disposition_override: str | None = None,
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
            with self.journal.transaction() if db is None else nullcontext(db) as db:
                assert_write_lease(db)
                value["audit_context"] = self.audit_context(db, case)
                reason, _ = output_permission(db, case.ticker, case.case_id)
                from doxagent.trade_execution.configuration import active_in

                active_config = active_in(db, case.ticker)
                if active_config and semantic_day(
                    self.journal.clock()
                ).isoformat() in active_config["configuration"].get("banned_semantic_dates", []):
                    reason = "BANNED_TRADE_DAY"
                value["origin_trade_eligible"] = reason is None and disposition_override is None
                value["trade_disposition"] = disposition_override or reason
                db.execute(
                    "INSERT OR IGNORE INTO runtime_values VALUES('candidates',?,?)",
                    (identity, encode(value)),
                )
            return "CANDIDATE"
        day = release_day or case.trading_date
        identity = f"trade:{selection_id or case.case_id}"
        with self.journal.transaction() if db is None else nullcontext(db) as db:
            assert_write_lease(db)
            old = db.execute(
                "SELECT payload FROM runtime_values WHERE namespace='trade_intents' AND key=?",
                (identity,),
            ).fetchone()
            if old:
                return str(json.loads(old[0])["status"])
            prepared_at = self.journal.clock()
            audit = self.audit_context(db, case, decision_at=prepared_at.isoformat())
            if selection_id:
                candidate = db.execute("SELECT payload FROM runtime_values WHERE namespace='candidates' AND key=?", (f"candidate:{case.case_id}",)).fetchone()
                if candidate:
                    original = json.loads(candidate[0])
                    audit["decision_at"] = original.get("audit_context", {}).get("decision_at", original["created_at"])
            reason, execution_pin = output_permission(db, case.ticker, case.case_id)
            if execution_pin:
                audit["environment"] = execution_pin["profile"]["environment"]
                audit["account"] = execution_pin["profile"]["expected_account_id"]
                audit["exit_offset_minutes"] = execution_pin["profile"].get("strategy", {}).get("exit_offset_minutes", 30)
            if reason:
                analysis = {
                    "audit_context": audit,
                    "intent_id": identity,
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
            status = disposition_override or ("EXPIRED_SEMANTIC_DAY" if expired else "READY")
            if (
                status == "READY"
                and case.runtime_mode == "REALTIME"
                and prepared_at - case.source.message_bus_event_time
                > timedelta(minutes=12)
            ):
                status = "RUNTIME_DELAY_EXCEEDED"
            from doxagent.trade_execution.configuration import active_in

            active_config = active_in(db, case.ticker)
            config = (active_config or {}).get("configuration", {})
            if semantic_day(prepared_at).isoformat() in config.get("banned_semantic_dates",[]):
                status = "BANNED_TRADE_DAY"
            if status == "READY" and selection_id:
                prior = db.execute(
                    "SELECT 1 FROM runtime_values WHERE namespace='trade_intents' "
                    "AND json_extract(payload,'$.ticker')=? "
                    "AND json_extract(payload,'$.release_semantic_day')=? "
                    "AND json_extract(payload,'$.trade.decision')=? "
                    "AND json_extract(payload,'$.status') NOT IN "
                    "('EXPIRED_SEMANTIC_DAY','DUPLICATE_POLICY',"
                    "'DUPLICATE_REALTIME_OUTPUT','DUPLICATE_EVENT_TRADE',"
                    "'SELECTED_CONDITION_INVALID','POLICY_MIGRATION_UNRESOLVED',"
                    "'RUNTIME_DELAY_EXCEEDED','BANNED_TRADE_DAY','EXECUTION_CONFIGURATION_NOT_APPLIED') LIMIT 1",
                    (case.ticker, day.isoformat(), trade.decision.value),
                ).fetchone()
                if prior:
                    status = "DUPLICATE_REALTIME_OUTPUT"
            policy_claim = None
            if (
                policy_consumption.POLICY_AUTO_FREEZE_ENABLED
                and status == "READY"
                and trade.executed_policy_id
                and trade.executed_condition_id is None
            ):
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
                    policy_claim = claim
            if status == "READY":
                claim_key = event_trade_key(trade)
                if claim_key:
                    inserted = db.execute(
                        "INSERT OR IGNORE INTO runtime_values VALUES('event_trade_claims',?,?)",
                        (
                            claim_key,
                            encode(
                                {
                                    "case_id": case.case_id,
                                    "intent_id": identity,
                                    "claimed_at": self.journal.clock().isoformat(),
                                }
                            ),
                        ),
                    ).rowcount
                    if not inserted:
                        status = "DUPLICATE_EVENT_TRADE"
            if status == "READY" and trade.executed_policy_id and trade.executed_condition_id:
                if not trade.executed_condition_revision or trade.usage_generation is None:
                    raise ValueError(
                        "Condition output requires revision and frozen usage generation"
                    )
                status = policy_consumption.claim_ready_in_transaction(
                    db, case=case, trade=trade, intent_id=identity, now=self.journal.clock()
                )
                if status == "POLICY_AUTHORIZATION_NOT_ADMITTED":
                    # Retry against the admitted head without persisting a transient intent.
                    return status
            if status == "READY" and policy_claim:
                db.execute(
                    "INSERT OR IGNORE INTO runtime_v2_policy_activations VALUES(?,?,?,?,?,?,?,?)",
                    (
                        case.ticker,
                        policy_claim.policy_id,
                        policy_claim.activation_revision,
                        case.case_id,
                        policy_claim.source_message_id,
                        policy_claim.policy_set_version,
                        policy_claim.model_dump_json(),
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
                "audit_context": audit,
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
                "decision_created_at":prepared_at.isoformat(),
                "runtime_delay":dict(source_at=case.source.message_bus_event_time.isoformat(),prepared_at=prepared_at.isoformat(),age_seconds=(prepared_at-case.source.message_bus_event_time).total_seconds(),limit_seconds=720) if case.runtime_mode == "REALTIME" else None,
                "suppressed_semantic_date":semantic_day(prepared_at).isoformat() if status == "BANNED_TRADE_DAY" else None,
                "suppression_policy_revision":(active_config or {}).get("applied_revision") if status == "BANNED_TRADE_DAY" else None,
                "ready_accepted_at": self.journal.clock().isoformat()
                if status == "READY"
                else None,
            }
            if execution_pin and status == "READY":
                intent["execution_pin"] = {
                    k: v
                    for k, v in execution_pin.items()
                    if k not in {"trading_config", "configuration_revision"}
                }
                intent["trading_config"] = execution_pin.get("trading_config", {})
                intent["configuration_revision"] = execution_pin.get("configuration_revision")

            db.execute(
                "INSERT INTO runtime_values VALUES('trade_intents',?,?)", (identity, encode(intent))
            )
        return status

    def record_in_transaction(self, db, case, trade, **kwargs):
        return self.record(case, trade, db=db, **kwargs)

    async def deliver(self, adapter: TradeExecutionAdapter, *, limit: int = 20) -> int:
        count = 0
        with self.journal.transaction(write=False) as db:
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
                    from doxagent.trade_execution.configuration import banned_in
                    with self.journal.transaction(write=False) as db:
                        banned = banned_in(db,intent["ticker"],self.journal.clock())
                    if banned:
                        intent.update(status="BANNED_TRADE_DAY",**banned)
                        self.journal.set("trade_intents",identity,intent)
                        self.journal.finish(task,status="BANNED_TRADE_DAY")
                        continue
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
                import sqlite3

                from doxagent.trade_execution.recovery import transient

                if isinstance(exc, sqlite3.OperationalError) and transient(exc):
                    # Keep the task recoverable; contention is not delivery failure.
                    raise
                # An exception after durable intake may have lost only its receipt.
                # Reconcile before declaring a delivery failure or admitting again.
                try:
                    recovered = await asyncio.wait_for(adapter.reconcile(identity), timeout=30)
                except Exception:
                    recovered = {"status": "UNKNOWN"}
                if recovered["status"] not in {"NOT_FOUND", "UNKNOWN"}:
                    self.journal.set("trade_receipts", identity, recovered)
                    intent["status"] = recovered["status"]
                    self.journal.set("trade_intents", identity, intent)
                    self.journal.finish(task, receipt=recovered)
                    count += 1
                    continue
                self.journal.fail(task, exc)
                failed = self.journal.get_task(task_id)
                if failed and failed["status"] == "FAILED":
                    if isinstance(exc, ValidationError):
                        detail = "; ".join(
                            f"{'.'.join(map(str, error['loc']))}: {error['msg']}"
                            for error in exc.errors()
                        )[:1000]
                        code = "PROFILE_VALIDATION_FAILED"
                    else:
                        detail = None
                        code = type(exc).__name__
                    intent["delivery_failure"] = {
                        "status": "FAILED",
                        "code": code,
                        "detail": detail,
                    }
                    if recovered["status"] == "NOT_FOUND":
                        intent["status"] = "DELIVERY_FAILED"
                    self.journal.set("trade_intents", identity, intent)
        return count
