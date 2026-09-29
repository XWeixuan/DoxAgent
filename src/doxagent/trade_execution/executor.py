"""One bounded state transition per job; no new attempt before final fill reconciliation."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from decimal import Decimal
from typing import Any

from .ibkr_session import IbkrSession
from .portfolio import account_funds, margin_after
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
        self.account_locks: dict[str, asyncio.Lock] = {}

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
        elif row["kind"] == "error":
            self.repository.apply_error(row["account"], value)
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
                elif failures >= 3 and current["leg"] != "ENTRY":
                    repo.finish(current, str(exc))
                else:
                    execution = repo.require("executions", current["execution_id"])
                    if current["leg"] == "ENTRY" and self.journal.clock() >= datetime.fromisoformat(
                        execution["intent"]["expires_at"]
                    ):
                        repo.finish(current, "EXPIRED_SEMANTIC_DAY")
                        return
                    repo.update_job(
                        current,
                        state="WAIT_QUOTE",
                        failures=failures,
                        delay=min(30, failures),
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
        cycle_ended = bool(
            entry
            and job.get("cycle_id")
            and strategy.capital_model == "SHARED_CYCLE"
            and job["cycle_id"] != self.sessions.cycle_id(now, strategy.exit_offset_minutes)
        )
        expired = entry and (
            now >= datetime.fromisoformat(execution["intent"]["expires_at"]) or cycle_ended
        )
        active = next((a for a in reversed(attempts) if a["state"] != "SETTLED"), None)
        if active:
            await self._advance_order(
                job,
                active,
                broker,
                expired,
                "CYCLE_CLOSED" if cycle_ended else "EXPIRED_SEMANTIC_DAY",
            )
            return
        if expired:
            repo.finish(job, "CYCLE_CLOSED" if cycle_ended else "EXPIRED_SEMANTIC_DAY")
            return
        cycle = None
        if entry and strategy.capital_model == "SHARED_CYCLE":
            try:
                cycle = repo.cycle_for_entry(
                    job["account"],
                    profile.environment,
                    self.sessions.cycle_id(now, strategy.exit_offset_minutes),
                    sync,
                    strategy.min_entry_notional_ratio,
                )
            except ValueError as exc:
                repo.update_job(job, state="WAIT_QUOTE", delay=10, error=str(exc))
                return
            if cycle is None:
                repo.update_job(job, state="WAIT_QUOTE", delay=10, error="WAIT_CYCLE_EQUITY")
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
        kind = order_type(
            attempts,
            session,
            job["leg"],
            legacy=strategy.capital_model == "LEGACY_PER_INTENT",
        )
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
        remaining = (
            max(
                Decimal(0),
                strategy.target_notional_usd
                - sum((decimal(f["quantity"]) * decimal(f["price"]) for f in fills), Decimal(0)),
            )
            if entry and strategy.capital_model == "LEGACY_PER_INTENT"
            else decimal(cycle["equity"])
            if entry and cycle
            else None
        )
        parameters = price_and_quantity(
            side=side,
            kind=kind,
            retry=bool(attempts),
            session=session,
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
        if entry and cycle:
            async with self.account_locks.setdefault(job["account"], asyncio.Lock()):
                sync = await self._sync(broker, job["account"])
                self._position_check(job, contract, sync)
                side_stamp = quote.get(("ask" if side == "BUY" else "bid") + "_at")
                if side_stamp and time.time() - side_stamp > strategy.quote_max_age_seconds:
                    repo.update_job(
                        job, state="WAIT_QUOTE", delay=0, error="FRESH_QUOTE_UNAVAILABLE"
                    )
                    return
                # A submit with no broker acknowledgement can already consume funds.
                with self.journal.transaction() as db:
                    unknown = db.execute(
                        "SELECT 1 FROM te_attempts a JOIN te_jobs j ON a.job_id=j.id "
                        "WHERE a.account=? AND j.leg='ENTRY' "
                        "AND a.state IN ('SUBMITTING','WORKING') "
                        "AND json_extract(a.payload,'$.broker_seen') IS NOT 1 LIMIT 1",
                        (job["account"],),
                    ).fetchone()
                if unknown:
                    repo.update_job(
                        job, state="RECONCILE_REQUIRED", delay=10, error="BROKER_SUBMIT_UNCONFIRMED"
                    )
                    return
                what_if_id = repo.allocate_order_id(
                    job["account"], broker.profile.client_id, broker.next_id
                )
                attempt, reason = repo.reserve_attempt(
                    job, data, cycle, what_if_id + 1, broker.profile.client_id, sync["at"]
                )
                if attempt is None:
                    if reason == "PORTFOLIO_CAPACITY_EXHAUSTED":
                        repo.finish(job, reason)
                    else:
                        repo.update_job(job, state="WAIT_QUOTE", delay=10, error=reason)
                    return
                try:
                    funds = account_funds(sync)
                    preview = await asyncio.to_thread(
                        broker.what_if,
                        contract,
                        what_if_id,
                        side=side,
                        price=attempt["limit_price"],
                        quantity=attempt["quantity"],
                        order_type=kind,
                        session=session,
                    )
                    after, look_ahead = margin_after(funds, preview, decimal(cycle["equity"]))
                    repo.event(
                        job["account"],
                        "margin_decision",
                        {
                            "attempt_id": attempt["id"],
                            "what_if": preview,
                            "available_after": str(after),
                            "look_ahead_after": str(look_ahead),
                        },
                    )
                except Exception as exc:
                    repo.void_prepared_attempt(attempt)
                    detail = str(exc).lower()
                    reason = (
                        "INSUFFICIENT_MARGIN"
                        if any(
                            part in detail
                            for part in (
                                "insufficient_margin",
                                "insufficient funds",
                                "equity with loan",
                                "margin requirement",
                            )
                        )
                        else "MARGIN_CHECK_UNAVAILABLE"
                    )
                    repo.event(
                        job["account"],
                        "margin_decision",
                        {
                            "attempt_id": attempt["id"],
                            "reason": reason,
                            "detail": str(exc),
                        },
                    )
                    if reason == "INSUFFICIENT_MARGIN":
                        repo.finish(job, reason)
                    else:
                        repo.update_job(
                            repo.require("jobs", job["id"]),
                            state="WAIT_QUOTE",
                            delay=10,
                            error=reason,
                        )
                    return
                await self._submit_prepared(repo, job, execution, broker, strategy, attempt, entry)
                await self._sync(broker, job["account"])
            return
        next_id = repo.allocate_order_id(job["account"], broker.profile.client_id, broker.next_id)
        attempt = repo.prepare_attempt(job, data, next_id, broker.profile.client_id)
        await self._submit_prepared(repo, job, execution, broker, strategy, attempt, entry)

    async def _submit_prepared(
        self,
        repo: ExecutionRepository,
        job: Any,
        execution: Any,
        broker: Any,
        strategy: Any,
        attempt: Any,
        entry: bool,
    ) -> None:
        if attempt.get("cycle_id") and attempt["cycle_id"] != self.sessions.cycle_id(
            self.journal.clock(), strategy.exit_offset_minutes
        ):
            repo.void_prepared_attempt(attempt)
            repo.update_job(
                repo.require("jobs", job["id"]),
                state="WAIT_QUOTE",
                delay=1,
                error="WAIT_CYCLE_EQUITY",
            )
            return
        # No await between final expiry check and persisted submit intent.
        if entry and self.journal.clock() >= datetime.fromisoformat(
            execution["intent"]["expires_at"]
        ):
            repo.void_prepared_attempt(attempt)
            repo.finish(job, "EXPIRED_SEMANTIC_DAY")
            return
        attempt = repo.update_attempt(
            attempt, state="SUBMITTING", sent_at=self.journal.clock().isoformat()
        )
        repo.update_job(
            repo.require("jobs", job["id"]),
            state="ACTIVE",
            failures=0,
            delay=strategy.order_wait_seconds,
        )
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

    async def _advance_order(
        self,
        job: Any,
        attempt: Any,
        broker: Any,
        expired: Any,
        expired_reason: str = "EXPIRED_SEMANTIC_DAY",
    ) -> Any:
        repo = self.repository
        now = self.journal.clock()
        if attempt["state"] == "PREPARED":
            # Proven pre-send crash: regenerate from fresh quotes without spending an attempt.
            repo.void_prepared_attempt(attempt)
            repo.update_job(repo.require("jobs", job["id"]), state="READY")
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
            rejection = attempt.get("rejection")
            errors = attempt.get("broker_errors", [])
            if rejection:
                detail = str((rejection.get("error") or {}).get("message", "")).lower()
                if any(
                    word in detail for word in ("margin", "insufficient funds", "equity with loan")
                ):
                    reason = "INSUFFICIENT_MARGIN"
                elif attempt.get("precaution_rejection") or any(
                    item.get("code") == 10329 for item in errors
                ):
                    reason = "ORDER_PRECAUTION_REJECTED"
                else:
                    reason = "BROKER_REJECTED"
                repo.finish(job, reason)
            elif status == "Filled" or filled == attempt["quantity"]:
                repo.finish(job, "ORDER_FILLED", complete=True)
            elif status in {"Inactive", "Rejected"} or expired:
                repo.finish(job, "BROKER_REJECTED" if not expired else expired_reason)
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
