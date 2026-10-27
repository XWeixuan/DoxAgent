"""One bounded state transition per job; no new attempt before final fill reconciliation."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime
from decimal import Decimal
from typing import Any

from .ibkr_session import IbkrSession
from .portfolio import account_funds, margin_after
from .recovery import (
    ENTRY_ABANDONED,
    ENTRY_FAILURE_LIMIT,
    ENTRY_QUEUE_EXPIRED,
    AccountRecovery,
    RecoveryDeferred,
    entry_queue_expired,
    storage_busy,
    transient,
)
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
        self.broker_lock = threading.Lock()
        self.broker_factory = broker_factory or IbkrSession
        self.write_guard = write_guard
        self.brokers: dict[tuple[Any, ...], Any] = {}
        self.locks: dict[tuple[Any, ...], asyncio.Lock] = {}
        self.account_locks: dict[str, asyncio.Lock] = {}
        self.recovery = AccountRecovery(self.journal, on_failure=self.enforce_entry_failure_limits)
        self.recovery_locks: dict[str, asyncio.Lock] = {}
        self.sync_locks: dict[int, asyncio.Lock] = {}
        self.sync_cache: dict[int, tuple[Any, Any]] = {}
        self.sync_successes: dict[str, int] = {}

    def broker(self, revision: Any) -> Any:
        profile = self.repository.profile(revision)
        endpoint = self.journal.get("execution_endpoints", profile.expected_account_id)
        if endpoint:
            profile = profile.model_copy(update=endpoint["endpoint"])
        # Different strategy revisions share the same socket/client identity.
        key = (profile.expected_account_id, profile.host, profile.port, profile.client_id)
        with self.broker_lock:
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
        with self.repository.transaction(write=False) as db:
            rows = db.execute(
                "SELECT * FROM te_events WHERE id>? ORDER BY id LIMIT 500", (cursor,)
            ).fetchall()
        advanced = cursor
        for row in rows:
            value = json.loads(row["payload"])
            try:
                self.apply_event(row)
            except Exception as exc:
                if transient(exc):
                    raise  # Retry the event; contention must not advance the fill cursor.
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
            advanced = row["id"]
        if advanced != cursor:
            self.journal.set("trade_execution", "event_cursor", advanced)

    def apply_event(self, row: Any) -> None:
        value = json.loads(row["payload"])
        if row["kind"] in {"order", "fill"}:
            self.sync_cache.clear()
        if row["kind"] == "order":
            self.repository.apply_order(row["account"], value)
        elif row["kind"] == "error":
            self.repository.apply_error(row["account"], value)
        elif row["kind"] == "fill":
            # Resolve offset from the pinned execution rather than the current config.
            with self.repository.transaction(write=False) as db:
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

    def abandon_entry(self, job: dict) -> bool:
        """Stop a stale entry permanently; sent orders retain their reconciliation duty."""
        repo = self.repository
        current = repo.require("jobs", job["id"])
        if current["leg"] != "ENTRY" or current["state"] in {"DONE", "FAILED"}:
            return False
        failures = max(
            current.get("entry_failures", 0),
            current.get("failures", 0),
            self.recovery.state(current["account"]).get("failures", 0),
        )
        stale = entry_queue_expired(
            repo.require("executions", current["execution_id"]), self.journal.clock()
        )
        if failures < ENTRY_FAILURE_LIMIT and not stale and not current.get("entry_abandoned_at"):
            return False
        reason = current.get("entry_abandon_reason") or (
            ENTRY_ABANDONED if failures >= ENTRY_FAILURE_LIMIT else ENTRY_QUEUE_EXPIRED
        )
        if not current.get("entry_abandoned_at"):
            current = repo.update_job(
                current,
                entry_failures=failures,
                entry_abandon_reason=reason,
                entry_abandoned_at=self.journal.clock().isoformat(),
                error=reason,
            )
        attempts = repo.attempts(current["id"])
        for attempt in attempts:
            if attempt["state"] == "PREPARED":
                repo.void_prepared_attempt(attempt, if_prepared=True)
        outstanding = any(
            a["state"] not in {"NOT_SENT", "SETTLED"} for a in repo.attempts(current["id"])
        )
        if outstanding:
            repo.update_job(
                repo.require("jobs", current["id"]),
                state="RECONCILE_REQUIRED",
                error=reason,
                delay=self.recovery.delay(current["account"]),
            )
        else:
            repo.finish(repo.require("jobs", current["id"]), reason)
        return True

    def enforce_entry_failure_limits(self, account: str | None = None) -> None:
        # Includes jobs deferred by the shared cooldown, even when not due yet.
        with self.repository.transaction(write=False) as db:
            jobs = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM te_jobs WHERE leg='ENTRY' "
                    "AND state NOT IN ('DONE','FAILED') "
                    "AND (? IS NULL OR account=?)",
                    (account, account),
                )
            ]
        for job in jobs:
            self.abandon_entry(job)

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
                self.abandon_entry(job)
                job = repo.require("jobs", job_id)
                if job["state"] in {"DONE", "FAILED"}:
                    return
                account = job["account"]
                self.recovery.check(account)
                if self.recovery.state(account):
                    # Serialize outage probes only; healthy ticker work stays parallel.
                    async with self.recovery_locks.setdefault(account, asyncio.Lock()):
                        self.recovery.check(account)
                        before = self.sync_successes.get(account, 0)
                        await self._step(job)
                        if self.sync_successes.get(account, 0) > before:
                            self.recovery.success(account)
                else:
                    await self._step(job)
            except RecoveryDeferred:
                return
            except Exception as exc:
                if storage_busy(exc):
                    # No broker failure happened. Preserve attempt identity/cursor for retry.
                    raise
                current = repo.require("jobs", job_id)
                failures = current.get("failures", 0) + 1
                if current["leg"] == "ENTRY":
                    current = repo.update_job(
                        current,
                        failures=failures,
                        error=str(exc),
                        entry_last_error=str(exc),
                        entry_failures=max(
                            current.get("entry_failures", 0), current.get("failures", 0)
                        )
                        + 1,
                    )
                attempts = repo.attempts(job_id)
                outstanding = any(a["state"] not in {"SETTLED", "NOT_SENT"} for a in attempts)
                if transient(exc):
                    self.sync_cache.clear()
                    self.recovery.fail(job["account"], exc)
                    self.abandon_entry(current)
                    current = repo.require("jobs", job_id)
                    if current["state"] in {"DONE", "FAILED"}:
                        return
                    repo.update_job(
                        current,
                        state="RECONCILE_REQUIRED" if outstanding else "WAIT_QUOTE",
                        failures=failures,
                        delay=self.recovery.delay(job["account"]),
                        error=current.get("entry_abandon_reason", ENTRY_ABANDONED)
                        if current.get("entry_abandoned_at")
                        else str(exc),
                    )
                    return
                self.abandon_entry(current)
                current = repo.require("jobs", job_id)
                if current["state"] in {"DONE", "FAILED"}:
                    return
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
                        error=current.get("entry_abandon_reason", ENTRY_ABANDONED)
                        if current.get("entry_abandoned_at")
                        else str(exc),
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

    async def _sync(self, broker: Any, account: Any, *, force: bool = False) -> Any:
        async with self.sync_locks.setdefault(id(broker), asyncio.Lock()):
            self.recovery.check(account)
            generation = getattr(broker, "connection_generation", 0)
            cached = self.sync_cache.get(id(broker))
            if (
                not force
                and cached
                and cached[0] == generation
                and (self.journal.clock() - datetime.fromisoformat(cached[1]["at"])).total_seconds()
                < 1
            ):
                return cached[1]
            value = await self._sync_fresh(broker, account)
            self.sync_cache[id(broker)] = (getattr(broker, "connection_generation", 0), value)
            return value

    async def _sync_fresh(self, broker: Any, account: Any) -> Any:
        value = await asyncio.to_thread(broker.sync)
        with self.repository.transaction(write=False) as db:
            watermark = db.execute("SELECT COALESCE(MAX(id),0) FROM te_events").fetchone()[0]
        while self.journal.get("trade_execution", "event_cursor", 0) < watermark:
            self.drain_events()
            await asyncio.sleep(0)
        with self.repository.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO te_sync_state VALUES(?,?)", (account, json.dumps(value))
            )
        self.sync_successes[account] = self.sync_successes.get(account, 0) + 1
        return value

    async def capture_ready_cycles(self, revisions: list[str]) -> None:
        """Record the next equity as soon as all old owned positions and orders settle."""
        accounts: set[str] = set()
        for revision in revisions:
            profile = self.repository.profile(revision)
            if profile.strategy.capital_model != "SHARED_CYCLE":
                continue
            account = profile.expected_account_id
            if account in accounts:
                continue
            accounts.add(account)
            if self.recovery.state(account):
                continue
            cycle_id = self.sessions.cycle_id(
                self.journal.clock(), profile.strategy.exit_offset_minutes
            )
            with self.repository.transaction(write=False) as db:
                existing = db.execute(
                    "SELECT 1 FROM te_account_cycles WHERE account=? AND environment=? "
                    "AND cycle_id=? AND status='ACTIVE'",
                    (account, profile.environment, cycle_id),
                ).fetchone()
                outstanding = db.execute(
                    "SELECT 1 FROM te_attempts WHERE account=? AND state NOT IN "
                    "('SETTLED','NOT_SENT') LIMIT 1",
                    (account,),
                ).fetchone()
                open_lot = any(
                    decimal(json.loads(row[0]).get("remaining_qty", 0)) > 0
                    for row in db.execute("SELECT payload FROM te_lots WHERE account=?", (account,))
                )
            if existing or outstanding or open_lot:
                continue
            async with self.account_locks.setdefault(account, asyncio.Lock()):
                try:
                    broker = self.broker(revision)
                    await asyncio.to_thread(broker.connect)
                    sync = await self._sync(broker, account)
                    if cycle_id != self.sessions.cycle_id(
                        self.journal.clock(), profile.strategy.exit_offset_minutes
                    ):
                        continue
                    self.repository.cycle_for_entry(
                        account,
                        profile.environment,
                        cycle_id,
                        sync,
                        profile.strategy.min_entry_notional_ratio,
                    )
                except Exception as exc:
                    if transient(exc):
                        self.recovery.fail(account, exc)
                    self.journal.set(
                        "execution_cycle_capture",
                        account,
                        {"cycle_id": cycle_id, "error": str(exc)},
                    )

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
        sync = await self._sync(broker, job["account"], force=bool(attempts))
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
            now >= datetime.fromisoformat(execution["intent"]["expires_at"])
            or cycle_ended
            or bool(job.get("entry_abandoned_at"))
        )
        active = next((a for a in reversed(attempts) if a["state"] != "SETTLED"), None)
        if active:
            await self._advance_order(
                job,
                active,
                broker,
                expired,
                job.get("entry_abandon_reason", ENTRY_ABANDONED)
                if job.get("entry_abandoned_at")
                else "CYCLE_CLOSED"
                if cycle_ended
                else "EXPIRED_SEMANTIC_DAY",
            )
            return
        if expired:
            repo.finish(
                job,
                job.get("entry_abandon_reason", ENTRY_ABANDONED)
                if job.get("entry_abandoned_at")
                else "CYCLE_CLOSED"
                if cycle_ended
                else "EXPIRED_SEMANTIC_DAY",
            )
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
                repo.update_job(job, state="WAIT_QUOTE", delay=60, error="WAIT_CYCLE_EQUITY")
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
        with self.repository.transaction(write=False) as db:
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
                with self.repository.transaction(write=False) as db:
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
                        if transient(exc):
                            raise
                        repo.update_job(
                            repo.require("jobs", job["id"]),
                            state="WAIT_QUOTE",
                            delay=10,
                            error=reason,
                        )
                    return
                submitted = await self._submit_prepared(
                    repo, job, execution, broker, strategy, attempt, entry
                )
                if submitted:
                    await self._sync(broker, job["account"], force=True)
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
    ) -> bool:
        if entry:
            current = repo.require("jobs", job["id"])
            if current["state"] in {"DONE", "FAILED"} or self.abandon_entry(current):
                return False
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
            return False
        # No await between final expiry check and persisted submit intent.
        if entry and self.journal.clock() >= datetime.fromisoformat(
            execution["intent"]["expires_at"]
        ):
            repo.void_prepared_attempt(attempt)
            repo.finish(job, "EXPIRED_SEMANTIC_DAY")
            return False
        attempt = repo.mark_submitting(attempt, wait_seconds=strategy.order_wait_seconds)
        if attempt is None:
            if entry:
                self.abandon_entry(repo.require("jobs", job["id"]))
            return False
        self.sync_cache.clear()
        try:
            await asyncio.to_thread(broker.submit, attempt)
        finally:
            self.sync_cache.clear()
        repo.update_attempt(attempt, state="WORKING")
        return True

    def _position_check(self, job: Any, contract: Any, sync: Any) -> Any:
        key = f"{job['account']}:{contract['con_id']}"
        actual = decimal(
            sync["positions"].get(
                contract["con_id"], sync["positions"].get(str(contract["con_id"]), 0)
            )
        )
        with self.repository.transaction(write=False) as db:
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
            self.sync_cache.clear()
            await asyncio.to_thread(broker.cancel, attempt)
            repo.update_job(job, state="ACTIVE", delay=0.2)
        else:
            # Repeat cancellation of this known order after reconnect; never create another.
            elapsed_cancel = (
                now - datetime.fromisoformat(attempt["cancel_sent_at"])
            ).total_seconds()
            if elapsed_cancel > 5:
                repo.gap(job, "CANCEL_UNCONFIRMED", "Awaiting broker terminal state")
                self.sync_cache.clear()
                await asyncio.to_thread(broker.cancel, attempt)
                repo.update_job(job, state="RECONCILE_REQUIRED", delay=30)
            else:
                repo.update_job(job, state="ACTIVE", delay=0.2)

    def close(self) -> Any:
        for broker in self.brokers.values():
            broker.close()
