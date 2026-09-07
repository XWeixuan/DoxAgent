"""One bounded state transition per job; no new attempt before final fill reconciliation."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from .ibkr_session import IbkrSession
from .repository import TERMINAL_ORDERS, ExecutionRepository
from .sessions import Sessions
from .strategy import decimal, order_type, price_and_quantity


class Executor:
    def __init__(
        self,
        repository: ExecutionRepository,
        *,
        broker_factory: Any = None,
        write_guard: Any = None,
    ) -> None:
        self.repository = repository
        self.journal = repository.journal
        self.sessions = Sessions(self.journal)
        self.broker_factory = broker_factory or IbkrSession
        self.write_guard = write_guard
        self.brokers: dict[tuple[Any, ...], Any] = {}
        self.locks: dict[tuple[Any, ...], asyncio.Lock] = {}

    def broker(self, revision: Any) -> Any:
        profile = self.repository.profile(revision)
        endpoint = self.journal.get("execution_endpoints", profile.expected_account_id)
        if endpoint:
            profile = profile.model_copy(update=endpoint["endpoint"])
        # Different strategy revisions share the same socket/client identity.
        key = (profile.expected_account_id, profile.host, profile.port, profile.client_id)
        if key not in self.brokers:
            self.brokers[key] = self.broker_factory(
                profile,
                event_sink=lambda kind, value: self.repository.event(
                    profile.expected_account_id, kind, value
                ),
                write_guard=self.write_guard,
            )
        return self.brokers[key]

    def drain_events(self) -> Any:
        cursor = self.journal.get("trade_execution", "event_cursor", 0)
        with self.journal.transaction() as db:
            rows = db.execute(
                "SELECT * FROM te_events WHERE id>? ORDER BY id LIMIT 500", (cursor,)
            ).fetchall()
        for row in rows:
            value = json.loads(row["payload"])
            try:
                self.apply_event(row)
            except Exception as exc:
                self.journal.set(
                    "execution_event_gaps",
                    str(row["id"]),
                    {
                        "event_id": row["id"],
                        "account": row["account"],
                        "con_id": value.get("con_id"),
                        "error": str(exc),
                    },
                )
            self.journal.set("trade_execution", "event_cursor", row["id"])

    def apply_event(self, row: Any) -> None:
        value = json.loads(row["payload"])
        if row["kind"] == "order":
            self.repository.apply_order(row["account"], value)
        elif row["kind"] == "fill":
            # Resolve offset from the pinned execution rather than the current config.
            with self.journal.transaction() as db:
                match = db.execute(
                    "SELECT j.execution_id FROM te_attempts a "
                    "JOIN te_jobs j ON a.job_id=j.id WHERE a.account=? "
                    "AND ((a.order_id=? AND a.client_id=?) OR a.order_ref=?)",
                    (
                        row["account"],
                        value.get("order_id", -1),
                        value.get("client_id", -1),
                        value.get("order_ref", ""),
                    ),
                ).fetchone()
            if match:
                execution = self.repository.require("executions", match[0])
                strategy = self.repository.profile(execution["profile_revision"]).strategy
                exit_at = self.sessions.exit_at(
                    datetime.fromisoformat(value["time"]), strategy.exit_offset_minutes
                ).isoformat()
                self.repository.apply_fill(value, exit_at)
        elif row["kind"] == "fees":
            self.journal.set("execution_fees", row["account"] + ":" + value["exec_id"], value)

    async def step(self, job_id: Any) -> Any:
        repo = self.repository
        job = repo.get("jobs", job_id)
        if not job or job["state"] in {"DONE", "FAILED"}:
            return
        scope = (job["account"], job["ticker"])
        lock = self.locks.setdefault(scope, asyncio.Lock())
        if lock.locked():
            return
        async with lock:
            try:
                await self._step(job)
            except Exception as exc:
                current = repo.require("jobs", job_id)
                failures = current.get("failures", 0) + 1
                attempts = repo.attempts(job_id)
                outstanding = any(a["state"] not in {"SETTLED", "NOT_SENT"} for a in attempts)
                if failures >= 3:
                    repo.gap(
                        current,
                        "RECONCILE_REQUIRED" if outstanding else "EXECUTION_UNAVAILABLE",
                        str(exc),
                    )
                if outstanding:
                    repo.update_job(
                        current,
                        state="RECONCILE_REQUIRED",
                        failures=failures,
                        delay=30 if failures >= 3 else failures,
                        error=str(exc),
                    )
                elif failures >= 3:
                    repo.finish(current, str(exc))
                else:
                    repo.update_job(
                        current,
                        state="WAIT_QUOTE",
                        failures=failures,
                        delay=failures,
                        error=str(exc),
                    )

    async def _sync(self, broker: Any, account: Any) -> Any:
        value = await asyncio.to_thread(broker.sync)
        with self.journal.transaction() as db:
            watermark = db.execute("SELECT COALESCE(MAX(id),0) FROM te_events").fetchone()[0]
        while self.journal.get("trade_execution", "event_cursor", 0) < watermark:
            self.drain_events()
            await asyncio.sleep(0)
        with self.journal.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO te_sync_state VALUES(?,?)", (account, json.dumps(value))
            )
        return value

    async def _step(self, job: Any) -> Any:
        repo = self.repository
        execution = repo.require("executions", job["execution_id"])
        profile = repo.profile(execution["profile_revision"])
        strategy = profile.strategy
        entry = job["leg"] == "ENTRY"
        direction = execution["intent"]["trade"]["decision"]
        attempts = [
            a
            for a in repo.attempts(job["id"])
            if a["generation"] == job.get("generation", 0) and a["state"] != "NOT_SENT"
        ]
        if (
            not attempts
            and entry
            and not (profile.long_enabled if direction == "LONG" else profile.short_enabled)
        ):
            repo.finish(job, "DIRECTION_DISABLED", disabled=True)
            return
        if (
            not attempts
            and entry
            and self.journal.clock() >= datetime.fromisoformat(execution["intent"]["expires_at"])
        ):
            repo.finish(job, "EXPIRED_SEMANTIC_DAY")
            return
        broker = self.broker(execution["profile_revision"])
        await asyncio.to_thread(broker.connect)
        sync = await self._sync(broker, job["account"])
        job = repo.require("jobs", job["id"])
        attempts = [
            a
            for a in repo.attempts(job["id"])
            if a["generation"] == job.get("generation", 0) and a["state"] != "NOT_SENT"
        ]
        now = self.journal.clock()
        expired = entry and now >= datetime.fromisoformat(execution["intent"]["expires_at"])
        active = next((a for a in reversed(attempts) if a["state"] != "SETTLED"), None)
        if active:
            await self._advance_order(job, active, broker, expired)
            return
        if expired:
            repo.finish(job, "EXPIRED_SEMANTIC_DAY")
            return
        if len(attempts) >= 3 or any(a["order_type"] == "MKT" for a in attempts):
            repo.finish(job, "RETRY_BUDGET_EXHAUSTED")
            return
        if job["state"] == "DONE":
            return
        lot = repo.get("lots", job["execution_id"])
        if not entry and (not lot or lot["remaining_qty"] == 0):
            repo.finish(job, "NO_OWNED_POSITION", complete=True)
            return
        # An unresolved order for this contract must settle before an exit or another entry.
        with self.journal.transaction() as db:
            busy = db.execute(
                "SELECT 1 FROM te_attempts a JOIN te_jobs j ON a.job_id=j.id "
                "WHERE j.account=? AND j.ticker=? AND a.state NOT IN ('SETTLED','NOT_SENT')",
                (job["account"], job["ticker"]),
            ).fetchone()
        if busy:
            repo.update_job(job, delay=1)
            return
        venue = self.sessions.venue(now)
        contract = await asyncio.to_thread(broker.contract, job["ticker"], venue)
        session = self.sessions.classify(now, contract)
        if session == "CLOSED":
            alternate = "OVERNIGHT" if venue == "SMART" else "SMART"
            try:
                other = await asyncio.to_thread(broker.contract, job["ticker"], alternate)
                other_session = self.sessions.classify(now, other)
                if other_session != "CLOSED":
                    venue, contract, session = alternate, other, other_session
            except (ValueError, RuntimeError, TimeoutError):
                pass  # Unsupported alternate venue does not make a closed market tradable.
        if session == "CLOSED":
            repo.update_job(job, state="WAIT_SESSION", delay=30)
            return
        for gap in self.journal.values("execution_event_gaps"):
            if gap["account"] == job["account"] and gap.get("con_id") == contract["con_id"]:
                raise ValueError("UNRESOLVED_FILL_GAP")
        self._position_check(job, contract, sync)
        kind = order_type(attempts, session)
        if kind is None:
            repo.finish(job, "RETRY_BUDGET_EXHAUSTED")
            return
        if entry:
            side = "BUY" if direction == "LONG" else "SELL"
        else:
            assert lot is not None
            side = "SELL" if lot["sign"] > 0 else "BUY"
        quote_broker = (
            self.broker(profile.quote_profile_revision)
            if profile.quote_profile_revision
            else broker
        )
        quote_contract = await asyncio.to_thread(quote_broker.contract, job["ticker"], venue)
        if quote_contract["con_id"] != contract["con_id"]:
            raise ValueError("QUOTE_CONTRACT_MISMATCH")
        rules = await asyncio.to_thread(broker.market_rules, contract) if kind == "LMT" else []
        quote = await asyncio.to_thread(quote_broker.quote, quote_contract, side, strategy=strategy)
        # A quote request may straddle the RTH boundary. Recompute instead of sending old MKT rules.
        if self.sessions.classify(self.journal.clock(), contract) != session:
            repo.update_job(job, delay=0)
            return
        fills = repo.fills(job["id"])
        remaining = max(
            Decimal(0),
            strategy.target_notional_usd
            - sum((decimal(f["quantity"]) * decimal(f["price"]) for f in fills), Decimal(0)),
        )
        parameters = price_and_quantity(
            side=side,
            kind=kind,
            retry=bool(attempts),
            quote=quote,
            rules=rules,
            strategy=strategy,
            remaining_notional=remaining if entry else None,
            remaining_qty=lot["remaining_qty"] if lot else 0,
        )
        if parameters["quantity"] <= 0:
            repo.finish(job, "BELOW_ONE_SHARE", complete=bool(fills))
            return
        data = {
            **parameters,
            "contract": contract,
            "session": session,
            "side": side,
            "order_type": kind,
            "quote": quote,
            "number": len(attempts),
            "generation": job.get("generation", 0),
        }
        next_id = repo.allocate_order_id(job["account"], broker.profile.client_id, broker.next_id)
        attempt = repo.prepare_attempt(job, data, next_id, broker.profile.client_id)
        # No await between final expiry check and persisted submit intent.
        if entry and self.journal.clock() >= datetime.fromisoformat(
            execution["intent"]["expires_at"]
        ):
            repo.update_attempt(attempt, state="NOT_SENT")
            repo.finish(job, "EXPIRED_SEMANTIC_DAY")
            return
        attempt = repo.update_attempt(
            attempt, state="SUBMITTING", sent_at=self.journal.clock().isoformat()
        )
        repo.update_job(job, state="ACTIVE", failures=0, delay=strategy.order_wait_seconds)
        await asyncio.to_thread(broker.submit, attempt)
        repo.update_attempt(attempt, state="WORKING")

    def _position_check(self, job: Any, contract: Any, sync: Any) -> Any:
        key = f"{job['account']}:{contract['con_id']}"
        actual = decimal(
            sync["positions"].get(
                contract["con_id"], sync["positions"].get(str(contract["con_id"]), 0)
            )
        )
        with self.journal.transaction() as db:
            fills = self.repository._effective_fills(db, job["account"], contract["con_id"])
            adjustments = [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM te_lot_adjustments WHERE account=? AND con_id=?",
                    (job["account"], contract["con_id"]),
                )
            ]
        owned = sum(
            (decimal(f["quantity"]) * (1 if f["side"] == "BUY" else -1) for f in fills), Decimal(0)
        )
        baseline = self.journal.get("execution_position_baselines", key)
        owned += sum(
            (decimal(item["quantity_delta"]) * item["sign"] for item in adjustments), Decimal(0)
        )
        if baseline is None:
            if fills:
                raise ValueError("POSITION_BASELINE_MISSING")
            self.journal.set(
                "execution_position_baselines", key, {"quantity": str(actual), "at": sync["at"]}
            )
        elif decimal(baseline["quantity"]) + owned != actual:
            raise ValueError("POSITION_ATTRIBUTION_GAP")

    async def _advance_order(self, job: Any, attempt: Any, broker: Any, expired: Any) -> Any:
        repo = self.repository
        now = self.journal.clock()
        if attempt["state"] == "PREPARED":
            # Proven pre-send crash: regenerate from fresh quotes without spending an attempt.
            repo.update_attempt(attempt, state="NOT_SENT")
            repo.update_job(job, state="READY")
            return
        if not attempt.get("broker_seen"):
            repo.gap(
                job, "BROKER_HISTORY_GAP", "Submit outcome unknown; no blind order resubmission"
            )
            repo.update_job(job, state="RECONCILE_REQUIRED", delay=30)
            return
        status = attempt.get("broker_status")
        fills = [f for f in repo.fills(job["id"]) if f["attempt_id"] == attempt["id"]]
        filled = sum(int(decimal(f["quantity"])) for f in fills)
        if status in TERMINAL_ORDERS:
            # Obtain fills again after observing terminal status, including cancellation races.
            if not attempt.get("terminal_observed_at"):
                repo.update_attempt(attempt, terminal_observed_at=now.isoformat())
                repo.update_job(job, state="ACTIVE", delay=0.1)
                return
            if (decimal(attempt.get("broker_filled", 0)) != filled and status != "Rejected") or (
                status == "Filled" and filled != attempt["quantity"]
            ):
                repo.gap(
                    job,
                    "FILL_RECONCILIATION_GAP",
                    "Order cumulative quantity differs from execution ledger",
                )
                repo.update_job(job, state="RECONCILE_REQUIRED", delay=5)
                return
            repo.update_attempt(attempt, state="SETTLED", settled_at=now.isoformat())
            if status == "Filled" or filled == attempt["quantity"]:
                repo.finish(job, "ORDER_FILLED", complete=True)
            elif status in {"Inactive", "Rejected"} or expired:
                repo.finish(job, "BROKER_REJECTED" if not expired else "EXPIRED_SEMANTIC_DAY")
            else:
                repo.update_job(job, state="READY", failures=0)
            return
        elapsed = (now - datetime.fromisoformat(attempt["sent_at"])).total_seconds()
        profile = repo.profile(repo.require("executions", job["execution_id"])["profile_revision"])
        if elapsed < profile.strategy.order_wait_seconds and not expired:
            repo.update_job(
                job, state="ACTIVE", delay=profile.strategy.order_wait_seconds - elapsed
            )
            return
        if not attempt.get("cancel_sent_at"):
            repo.update_attempt(attempt, state="CANCEL_PENDING", cancel_sent_at=now.isoformat())
            await asyncio.to_thread(broker.cancel, attempt)
            repo.update_job(job, state="ACTIVE", delay=0.2)
        else:
            # Repeat cancellation of this known order after reconnect; never create another.
            elapsed_cancel = (
                now - datetime.fromisoformat(attempt["cancel_sent_at"])
            ).total_seconds()
            if elapsed_cancel > 5:
                repo.gap(job, "CANCEL_UNCONFIRMED", "Awaiting broker terminal state")
                await asyncio.to_thread(broker.cancel, attempt)
                repo.update_job(job, state="RECONCILE_REQUIRED", delay=30)
            else:
                repo.update_job(job, state="ACTIVE", delay=0.2)

    def close(self) -> Any:
        for broker in self.brokers.values():
            broker.close()
