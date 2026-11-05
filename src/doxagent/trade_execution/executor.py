"""One bounded state transition per job; no new attempt before final fill reconciliation."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime
from decimal import Decimal
from typing import Any

from doxagent.semantic_clock import semantic_day

from . import groups
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
from .strategy import decimal, entry_book_ready, order_type, price_and_quantity, valid_quote_side


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
        self.broker_lock = threading.Lock()
        self.data_readers = {}
        self.locks: dict[tuple[Any, ...], asyncio.Lock] = {}
        self.account_locks: dict[str, asyncio.Lock] = {}
        self.recovery = AccountRecovery(self.journal, on_failure=self.enforce_entry_failure_limits)
        self.recovery_locks: dict[str, asyncio.Lock] = {}
        self.sync_locks: dict[int, asyncio.Lock] = {}
        self.sync_cache: dict[int, tuple[Any, Any]] = {}
        self.sync_successes: dict[str, int] = {}
        self.quote_watches = {}

    def _release_watch(self, job_id):
        watch = self.quote_watches.pop(job_id, None)
        if watch:
            reader, contract, owner = watch
            reader.release_quote(contract, owner)

    async def _observe_quote(self, job, contract, side, strategy):
        execution = self.repository.require("executions", job["execution_id"])
        reader = self.market_data(
            self.repository.profile(execution["profile_revision"]), execution["profile_revision"]
        )
        owner = "execution-watch:" + job["id"]
        try:
            if hasattr(reader, "quote_snapshot") and hasattr(reader, "subscribe_quote"):
                old = self.quote_watches.get(job["id"])
                if old and (old[0] is not reader or old[1] != contract):
                    self._release_watch(job["id"])
                self.quote_watches[job["id"]] = (reader, contract, owner)
                if hasattr(reader, "watch_quote"):
                    return await asyncio.to_thread(
                        reader.watch_quote, contract, owner, side=side, strategy=strategy
                    )
                await asyncio.to_thread(reader.subscribe_quote, contract, owner)
                return reader.quote_snapshot(contract) or {}
            # Broker test doubles and existing adapters without streaming support.
            return await asyncio.to_thread(reader.quote, contract, side, strategy=strategy)
        except (ValueError, RuntimeError, TimeoutError):
            return {}

    async def _entry_book_gate(self, job, contract, side, strategy):
        if job.get("entry_book_passed_at"):
            return True
        repo, now = self.repository, self.journal.clock()
        if not job.get("entry_book_started_at"):
            job = repo.update_job(job, entry_book_started_at=now.isoformat())
        if (now - datetime.fromisoformat(job["entry_book_started_at"])).total_seconds() >= 30:
            repo.finish(job, "ENTRY_BOOK_TIMEOUT")
            return False
        quote = await self._observe_quote(job, contract, side, strategy)
        now = self.journal.clock()
        if (now - datetime.fromisoformat(job["entry_book_started_at"])).total_seconds() >= 30:
            repo.finish(job, "ENTRY_BOOK_TIMEOUT")
            return False
        if entry_book_ready(quote, strategy.quote_max_age_seconds):
            repo.update_job(repo.require("jobs", job["id"]), entry_book_passed_at=now.isoformat())
            return True
        repo.update_job(repo.require("jobs", job["id"]), state="WAIT_QUOTE", delay=0.2)
        return False

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
            if profile.account_type == "CASH":
                self.brokers[key].want_settled_cash = True
            return self.brokers[key]

    def market_data(self, profile, revision):
        import os

        if os.environ.get("IBKR_DATA_SOURCES_JSON", "[]").strip() not in {"", "[]"}:
            from .market_data import ReadOnlyQuotes

            if "shared" not in self.data_readers:
                self.data_readers["shared"] = ReadOnlyQuotes(self, profile)
            return self.data_readers["shared"]
        return self.broker(profile.quote_profile_revision or revision)

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
            from doxagent.exit_strategy.repository import refresh_books

            with self.repository.transaction() as db:
                fill = db.execute(
                    "SELECT con_id FROM te_fills WHERE account=? AND exec_id=?",
                    (row["account"], value["exec_id"]),
                ).fetchone()
                if fill:
                    refresh_books(db, row["account"], fill[0], self.journal.clock)

    def abandon_entry(self, job: dict) -> bool:
        """Stop a stale entry permanently; sent orders retain their reconciliation duty."""
        repo = self.repository
        current = repo.require("jobs", job["id"])
        if current["leg"] != "ENTRY" or current["state"] in {"DONE", "FAILED"}:
            return False
        # Account failures drive shared cooldown, never a new job's lifetime budget.
        failures = max(
            current.get("entry_failures", 0),
            current.get("failures", 0),
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
        self.repository.cleanup_prepared()

    async def step(self, job_id: Any) -> Any:
        repo = self.repository
        job = repo.get("jobs", job_id)
        if not job or job["state"] in {"DONE", "FAILED", "WAIT_MAIN"}:
            self._release_watch(job_id)
            return
        scope = (job["account"], job.get("traded_ticker", job["ticker"]))
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

            finally:
                if repo.require("jobs", job_id)["state"] in {"DONE", "FAILED"}:
                    self._release_watch(job_id)

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
            account = profile.expected_account_id
            if account in accounts:
                continue
            accounts.add(account)
            if self.recovery.state(account):
                continue
            cycle_id = self.cycle_id(profile)
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
                    if cycle_id != self.cycle_id(profile):
                        continue
                    self.repository.cycle_for_entry(
                        account,
                        profile.environment,
                        cycle_id,
                        sync,
                        profile.strategy.min_entry_notional_ratio,
                        profile_revision=revision,
                    )
                except Exception as exc:
                    if transient(exc):
                        self.recovery.fail(account, exc)
                    self.journal.set(
                        "execution_cycle_capture",
                        account,
                        {"cycle_id": cycle_id, "error": str(exc)},
                    )

    def cycle_id(self, profile):
        if profile.account_type == "CASH":
            return "cash:" + semantic_day(self.journal.clock()).isoformat()
        return "margin:" + self.sessions.cycle_id(
            self.journal.clock(), profile.strategy.exit_offset_minutes
        )

    async def _step(self, job: Any) -> Any:
        repo = self.repository
        self.drain_events()
        groups.advance(repo, job["execution_id"])
        execution = repo.require("executions", job["execution_id"])
        job = repo.require("jobs", job["id"])
        if job["state"] in {"WAIT_MAIN", "DONE", "FAILED"}:
            return
        profile = repo.profile(execution["profile_revision"])
        strategy = profile.strategy
        entry = job["leg"] == "ENTRY"
        if entry and execution.get("ownership_version") != 2 and not job.get("entry_abandoned_at"):
            job = repo.update_job(
                job,
                entry_abandoned_at=self.journal.clock().isoformat(),
                entry_abandon_reason="HISTORICAL_ENTRY_RETIRED",
            )
        if entry:
            from doxagent.exit_strategy.repository import entry_blocked

            with self.repository.transaction(write=False) as db:
                closing = entry_blocked(db, job["account"], job["ticker"])
            from .configuration import active_in

            with repo.transaction(write=False) as db:
                active_config = active_in(db, job["ticker"])
            blocked = groups.entry_block_reason(
                execution,
                job,
                self.journal.clock(),
                active_config["configuration"] if active_config else None,
            )
            if blocked == "BANNED_TRADE_DAY":
                job = repo.update_job(
                    job,
                    entry_abandoned_at=self.journal.clock().isoformat(),
                    entry_abandon_reason=blocked,
                )
            if (closing or blocked == "POSITION_EXIT_REQUESTED") and not job.get(
                "exit_requested_at"
            ):
                job = repo.update_job(job, exit_requested_at=self.journal.clock().isoformat())
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
        watch = self.quote_watches.get(job["id"])
        if (
            entry
            and not attempts
            and watch
            and job.get("entry_book_started_at")
            and not job.get("entry_book_passed_at")
            and not job.get("entry_abandoned_at")
            and not job.get("exit_requested_at")
            and not self.recovery.state(job["account"])
        ):
            observed_contract = watch[1]
            if self.sessions.classify(self.journal.clock(), observed_contract) not in {
                "RTH",
                "CLOSED",
            }:
                side = job.get("side") or ("BUY" if direction == "LONG" else "SELL")
                if not await self._entry_book_gate(job, observed_contract, side, strategy):
                    return
        # Observe working ENTRY from streaming callbacks each second. Cancellation
        # and terminal confirmation still return to the full reconciliation path.
        if entry and attempts:
            job = repo.require("jobs", job["id"])
            active = next(
                (
                    a
                    for a in reversed(repo.attempts(job["id"]))
                    if a["state"] not in {"SETTLED", "NOT_SENT"}
                ),
                None,
            )
            if (
                active
                and active["state"] == "WORKING"
                and active.get("broker_seen")
                and active.get("broker_status") not in TERMINAL_ORDERS
                and getattr(broker, "healthy", True)
                and not self.recovery.state(job["account"])
                and not job.get("entry_abandoned_at")
                and not job.get("exit_requested_at")
                and self.journal.clock() < datetime.fromisoformat(execution["intent"]["expires_at"])
                and (not job.get("cycle_id") or job["cycle_id"] == self.cycle_id(profile))
            ):
                await self._advance_order(job, active, broker, False)
                return
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
            entry and job.get("cycle_id") and job["cycle_id"] != self.cycle_id(profile)
        )
        expired = entry and (
            now >= datetime.fromisoformat(execution["intent"]["expires_at"])
            or cycle_ended
            or bool(job.get("entry_abandoned_at"))
            or bool(job.get("exit_requested_at"))
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
                else "POSITION_EXIT_REQUESTED"
                if job.get("exit_requested_at")
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
                else "POSITION_EXIT_REQUESTED"
                if job.get("exit_requested_at")
                else "CYCLE_CLOSED"
                if cycle_ended
                else "EXPIRED_SEMANTIC_DAY",
            )
            return
        cycle = None
        if entry:
            try:
                cycle = repo.cycle_for_entry(
                    job["account"],
                    profile.environment,
                    self.cycle_id(profile),
                    sync,
                    strategy.min_entry_notional_ratio,
                    profile_revision=execution["profile_revision"],
                )
            except ValueError as exc:
                repo.update_job(job, state="WAIT_QUOTE", delay=10, error=str(exc))
                return
            if cycle is None:
                repo.update_job(job, state="WAIT_QUOTE", delay=60, error="WAIT_CYCLE_EQUITY")
                return
        if entry and len(attempts) >= 3:
            repo.finish(job, "RETRY_BUDGET_EXHAUSTED")
            return
        if job["state"] == "DONE":
            return
        lot = repo.get("lots", job.get("leg_id", job["execution_id"]))
        if not entry and (not lot or lot["remaining_qty"] == 0):
            repo.finish(job, "NO_OWNED_POSITION", complete=True)
            return
        # An unresolved order for this contract must settle before an exit or another entry.
        with self.repository.transaction(write=False) as db:
            busy = db.execute(
                "SELECT 1 FROM te_attempts a JOIN te_jobs j ON a.job_id=j.id "
                "WHERE j.account=? AND "
                "COALESCE(json_extract(j.payload,'$.traded_ticker'),j.ticker)=? "
                "AND a.state NOT IN ('SETTLED','NOT_SENT')",
                (job["account"], job.get("traded_ticker", job["ticker"])),
            ).fetchone()
        if busy:
            repo.update_job(job, delay=1)
            return
        venue = self.sessions.venue(now)
        contract = await asyncio.to_thread(
            broker.contract, job.get("traded_ticker", job["ticker"]), venue
        )
        session = self.sessions.classify(now, contract)
        if session == "CLOSED":
            alternate = "OVERNIGHT" if venue == "SMART" else "SMART"
            try:
                other = await asyncio.to_thread(
                    broker.contract, job.get("traded_ticker", job["ticker"]), alternate
                )
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
        )
        if kind is None:
            repo.finish(job, "RETRY_BUDGET_EXHAUSTED")
            return
        if entry:
            side = job.get("side") or ("BUY" if direction == "LONG" else "SELL")
        else:
            assert lot is not None
            side = "SELL" if lot["sign"] > 0 else "BUY"
        if entry and not attempts and session != "RTH":
            if not await self._entry_book_gate(job, contract, side, strategy):
                return
            job = repo.require("jobs", job["id"])
        quote_broker = self.market_data(profile, execution["profile_revision"])
        quote_contract = await asyncio.to_thread(
            quote_broker.contract, job.get("traded_ticker", job["ticker"]), venue
        )
        if quote_contract["con_id"] != contract["con_id"]:
            raise ValueError("QUOTE_CONTRACT_MISMATCH")
        rules = await asyncio.to_thread(broker.market_rules, contract) if kind == "LMT" else []
        if entry:
            # Admission/working observers must not keep an old quote subscription
            # alive across fresh per-attempt pricing on a quiet after-hours book.
            self._release_watch(job["id"])
        quote = await asyncio.to_thread(quote_broker.quote, quote_contract, side, strategy=strategy)
        # A quote request may straddle the RTH boundary. Recompute instead of sending old MKT rules.
        if self.sessions.classify(self.journal.clock(), contract) != session:
            repo.update_job(job, delay=0)
            return
        fills = repo.fills(job["id"])
        remaining = decimal(cycle["equity"]) if entry and cycle else None
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
            if job.get("role", "MAIN") == "MAIN" and not execution.get("budget_usd"):
                hedge = next(
                    (leg for leg in execution.get("legs", []) if leg["role"] == "HEDGE"), None
                )
                if hedge:
                    hedge_contract = await asyncio.to_thread(
                        quote_broker.contract, hedge["traded_ticker"], "SMART"
                    )
                    try:
                        hedge_quote = await asyncio.to_thread(
                            quote_broker.quote, hedge_contract, hedge["side"], strategy=strategy
                        )
                        reference = decimal(hedge_quote["ask" if hedge["side"] == "BUY" else "bid"])
                        source = "LIVE_QUOTE"
                    except (ValueError, RuntimeError, TimeoutError):
                        # Reference sizes the plan; HEDGE execution requires a fresh quote.
                        bars = await asyncio.to_thread(quote_broker.planning_bars, hedge_contract)
                        if not bars:
                            raise ValueError("HEDGE_PLANNING_PRICE_UNAVAILABLE") from None
                        reference = decimal(bars[-1]["close"])
                        source = "HISTORICAL_CLOSE"
                    hedge_rules = await asyncio.to_thread(broker.market_rules, hedge_contract)
                    planned = price_and_quantity(
                        side=hedge["side"],
                        kind="LMT",
                        retry=False,
                        session=session,
                        quote={"bid": str(reference), "ask": str(reference)},
                        rules=hedge_rules,
                        strategy=strategy,
                        remaining_notional=decimal(cycle["equity"]),
                    )
                    data["hedge_plan"] = dict(
                        contract=hedge_contract,
                        sizing_price=planned["sizing_price"],
                        source=source,
                        at=self.journal.clock().isoformat(),
                    )
            async with self.account_locks.setdefault(job["account"], asyncio.Lock()):
                sync = await self._sync(broker, job["account"])
                self._position_check(job, contract, sync)
                field = "ask" if side == "BUY" else "bid"
                side_stamp = quote.get(field + "_received_at", quote.get(field + "_at"))
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
                    if reason in {
                        "PORTFOLIO_CAPACITY_EXHAUSTED",
                        "NO_VALID_INTEGER_COMBINATION",
                        "HEDGE_UNREPRESENTABLE_AFTER_PARTIAL",
                        "HEDGE_ABOVE_RATIO_LIMIT",
                        "POSITION_EXIT_REQUESTED",
                        "HEDGE_TARGET_SATISFIED",
                    }:
                        repo.finish(job, reason, complete=reason == "HEDGE_TARGET_SATISFIED")
                    else:
                        repo.update_job(job, state="WAIT_QUOTE", delay=10, error=reason)
                    return
                if profile.account_type == "CASH":
                    await self._submit_prepared(
                        repo, job, execution, broker, strategy, attempt, entry
                    )
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
        current = repo.require("jobs", job["id"])
        invalid_quote = (
            self.sessions.classify(self.journal.clock(), attempt["contract"]) != attempt["session"]
        )
        field = "ask" if attempt["side"] == "BUY" else "bid"
        stamp = attempt["quote"].get(field + "_received_at", attempt["quote"].get(field + "_at"))
        invalid_quote |= bool(stamp and time.time() - stamp > strategy.quote_max_age_seconds)
        if current["state"] in {"DONE", "FAILED"} or invalid_quote:
            repo.void_prepared_attempt(attempt, if_prepared=True)
            if current["state"] not in {"DONE", "FAILED"}:
                repo.update_job(current, state="READY", delay=0, error="FRESH_QUOTE_REQUIRED")
            return False
        if entry:
            current = repo.require("jobs", job["id"])
            if current["state"] in {"DONE", "FAILED"} or self.abandon_entry(current):
                repo.void_prepared_attempt(attempt, if_prepared=True)
                return False
        if attempt.get("cycle_id") and attempt["cycle_id"] != self.cycle_id(
            repo.profile(execution["profile_revision"])
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
        prepared = attempt
        attempt = repo.mark_submitting(attempt, wait_seconds=strategy.order_wait_seconds)
        if attempt is None:
            repo.void_prepared_attempt(prepared, if_prepared=True)
            if entry:
                self.abandon_entry(repo.require("jobs", job["id"]))
            return False
        self.sync_cache.clear()
        try:
            await asyncio.to_thread(broker.submit, attempt)
        finally:
            self.sync_cache.clear()
        repo.update_attempt(attempt, state="WORKING")
        if entry:
            await self._observe_quote(job, attempt["contract"], attempt["side"], strategy)
            repo.update_job(
                repo.require("jobs", job["id"]),
                state="ACTIVE",
                delay=min(1, strategy.order_wait_seconds),
            )
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
        lot = (
            repo.get("lots", job.get("leg_id", job["execution_id"]))
            if job["leg"] == "EXIT"
            else None
        )
        resize = job["leg"] == "EXIT" and attempt["quantity"] - filled > (lot or {}).get(
            "remaining_qty", 0
        )
        if job["leg"] == "ENTRY" and job.get("role") == "HEDGE":
            resize = resize or decimal(attempt.get("main_notional_usd", 0)) != decimal(
                job.get("main_notional_usd", 0)
            )
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
            elif (
                job["leg"] == "ENTRY"
                and job.get("role") == "HEDGE"
                and (execution := repo.require("executions", job["execution_id"])).get(
                    "allocation_policy_version", 1
                )
                >= 2
            ):
                from .planning import ratio_bounds

                main = decimal(job.get("main_notional_usd", 0))
                spent = sum(
                    (decimal(f["quantity"]) * decimal(f["price"]) for f in repo.fills(job["id"])),
                    Decimal(0),
                )
                lower, upper = ratio_bounds(decimal(execution["trading_config"]["hedge_ratio"]))
                if main and spent > upper * main:
                    repo.finish(job, "HEDGE_ABOVE_RATIO_LIMIT")
                elif main and spent > 0 and lower * main <= spent <= upper * main:
                    repo.finish(job, "HEDGE_TARGET_SATISFIED", complete=True)
                elif expired or status in {"Inactive", "Rejected"}:
                    repo.finish(job, expired_reason if expired else "BROKER_REJECTED")
                else:
                    repo.update_job(job, state="READY", failures=0)
            elif status == "Filled" or filled == attempt["quantity"]:
                repo.finish(job, "ORDER_FILLED", complete=True)
            elif status in {"Inactive", "Rejected"} or expired:
                repo.finish(job, "BROKER_REJECTED" if not expired else expired_reason)
            else:
                repo.update_job(job, state="READY", failures=0)
            return
        elapsed = (now - datetime.fromisoformat(attempt["sent_at"])).total_seconds()
        profile = repo.profile(repo.require("executions", job["execution_id"])["profile_revision"])
        early_cancel = False
        if (
            job["leg"] == "ENTRY"
            and not attempt.get("cancel_sent_at")
            and not expired
            and not resize
            and elapsed < profile.strategy.order_wait_seconds
        ):
            quote = await self._observe_quote(
                job, attempt["contract"], attempt["side"], profile.strategy
            )
            now = self.journal.clock()
            valid = valid_quote_side(quote, attempt["side"], profile.strategy.quote_max_age_seconds)
            reference = (
                decimal(quote["ask" if attempt["side"] == "BUY" else "bid"]) if valid else None
            )
            limit = decimal(attempt["limit_price"]) if attempt.get("limit_price") else None
            outside = bool(
                valid
                and limit is not None
                and (reference > limit if attempt["side"] == "BUY" else reference < limit)
            )
            since = attempt.get("outside_limit_since") if outside else None
            checked = attempt.get("price_checked_at")
            # Missing observations or a process restart cannot prove continuity.
            if outside and (
                not since
                or not checked
                or (now - datetime.fromisoformat(checked)).total_seconds() > 2
            ):
                since = now.isoformat()
            early_cancel = bool(
                since and (now - datetime.fromisoformat(since)).total_seconds() >= 2
            )
            attempt = repo.update_attempt(
                attempt, outside_limit_since=since, price_checked_at=now.isoformat()
            )
            elapsed = (now - datetime.fromisoformat(attempt["sent_at"])).total_seconds()
        if (
            elapsed < profile.strategy.order_wait_seconds
            and not expired
            and not resize
            and not early_cancel
        ):
            repo.update_job(
                job,
                state="ACTIVE",
                delay=min(1, profile.strategy.order_wait_seconds - elapsed)
                if job["leg"] == "ENTRY"
                else profile.strategy.order_wait_seconds - elapsed,
            )
            return
        if not attempt.get("cancel_sent_at"):
            repo.update_attempt(
                attempt,
                state="CANCEL_PENDING",
                cancel_sent_at=now.isoformat(),
                cancel_reason="PRICE_OUTSIDE_LIMIT"
                if early_cancel
                else "OWNED_QUANTITY_CHANGED"
                if resize
                else expired_reason
                if expired
                else "ORDER_WAIT_ELAPSED",
            )
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
        for job_id in list(self.quote_watches):
            self._release_watch(job_id)
        for reader in self.data_readers.values():
            reader.close()
        for broker in self.brokers.values():
            broker.close()
