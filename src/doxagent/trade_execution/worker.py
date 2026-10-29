"""Single local socket writer; each contract progresses independently."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from .recovery import DELAYS, RecoveryDeferred, transient


def active_profile_revisions(executor: Any) -> list[str]:
    """Return only profiles bound to ticker controls that are currently running."""
    from doxagent.v2_control.repository import ControlRepository, mode_binding_in

    control = ControlRepository(executor.journal)
    with control.read() as db:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('v2_ticker_control','v2_mode_binding')"
            )
        }
        if tables != {"v2_ticker_control", "v2_mode_binding"}:
            return []
        states = [json.loads(row[0]) for row in db.execute("SELECT payload FROM v2_ticker_control")]
        revisions = []
        for state in states:
            if state.get("status") != "RUNNING" or state.get("mode") not in {
                "PAPER_TRADING",
                "LIVE_TRADING",
            }:
                continue
            binding = mode_binding_in(db, state["ticker"], state["mode"])
            if binding and binding["revision"] not in revisions:
                revisions.append(binding["revision"])
        return revisions


def maintain_connections(executor: Any) -> dict[str, str]:
    """Keep configured execution sockets ready without submitting broker commands."""
    results = {}
    for revision in active_profile_revisions(executor):
        account = executor.repository.profile(revision).expected_account_id
        try:
            if executor.recovery.state(account):
                results[revision] = "RECOVERING"
            else:
                executor.broker(revision).connect()
                results[revision] = "CONNECTED"
        except Exception as exc:
            if transient(exc):
                executor.recovery.fail(account, exc)
            results[revision] = f"DISCONNECTED:{type(exc).__name__}"
        current = executor.journal.get("execution_connections", revision)
        value = {"status": results[revision]}
        if current != value:
            executor.journal.set("execution_connections", revision, value)
    return results


async def recover_idle_accounts(executor):
    """An idle account also recovers; active accounts are probed by their due jobs."""
    with executor.journal.transaction(write=False) as db:
        active = {
            r[0]
            for r in db.execute(
                "SELECT DISTINCT account FROM te_jobs WHERE state NOT IN ('DONE','FAILED')"
            )
        }
    seen = set()
    for revision in active_profile_revisions(executor):
        account = executor.repository.profile(revision).expected_account_id
        if account in active or account in seen or not executor.recovery.state(account):
            continue
        seen.add(account)
        try:
            executor.recovery.check(account)
            async with executor.recovery_locks.setdefault(account, asyncio.Lock()):
                executor.recovery.check(account)
                broker = executor.broker(revision)
                await asyncio.to_thread(broker.connect)
                await executor._sync(broker, account, force=True)
                executor.recovery.success(account)
        except RecoveryDeferred:
            pass
        except Exception as exc:
            if not transient(exc):
                raise
            executor.recovery.fail(account, exc)


class WriterLock:
    """OS-owned lock cannot expire while an old process still owns a socket.

    Process death releases it. Database leases alone cannot fence broker commands.
    Move the database only after stopping this writer; shared network SQLite is unsupported.
    """

    def __init__(self, database: Path) -> None:
        self.path = Path(str(database.resolve()) + ".executor.lock")
        self.handle: Any = None
        self.pid = os.getpid()

    def __enter__(self) -> Any:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
        except OSError:
            handle.close()
            raise RuntimeError("EXECUTOR_ALREADY_RUNNING") from None
        self.handle = handle
        return self

    def assert_owned(self) -> Any:
        if self.handle is None or self.handle.closed or os.getpid() != self.pid:
            raise RuntimeError("ORDER_WRITER_NOT_OWNED")

    def __exit__(self, *_: Any) -> Any:
        if self.handle:
            self.handle.close()
            self.handle = None


async def _periodic(name, operation, interval, stop, *, once=False):
    """One in-flight operation per lane; a failed lane backs off independently."""
    failures = 0
    while not stop.is_set():
        delay = interval
        try:
            await operation()
            failures = 0
        except Exception as exc:
            failures += 1
            delay = DELAYS[min(failures - 1, 4)] if transient(exc) else 30
            logging.getLogger(__name__).exception("executor_maintenance_deferred lane=%s", name)
        if once:
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            pass


def scheduler_health(executor):
    """Read actual job lag; a live process heartbeat is not business acceptance."""
    from datetime import datetime

    from .recovery import ENTRY_MAX_QUEUE_SECONDS

    now = executor.journal.clock()
    overdue = []
    old_entries = []
    with executor.repository.transaction(write=False) as db:
        rows = db.execute(
            "SELECT j.payload,e.payload,"
            "(SELECT COUNT(*) FROM te_attempts a WHERE a.job_id=j.id),l.payload "
            "FROM te_jobs j JOIN te_executions e ON e.id=j.execution_id "
            "LEFT JOIN te_lots l ON l.id=j.execution_id "
            "WHERE j.state NOT IN ('DONE','FAILED')"
        ).fetchall()
    for row in rows:
        job, execution, attempts = json.loads(row[0]), json.loads(row[1]), row[2]
        lot = json.loads(row[3]) if row[3] else {}
        deadline = (
            lot.get("scheduled_exit", job["due_at"]) if job["leg"] == "EXIT" else job["due_at"]
        )
        lag = (now - datetime.fromisoformat(deadline)).total_seconds()
        if job["leg"] == "EXIT" and lag >= 60:
            overdue.append(
                {"job_id": job["id"], "due_lag_seconds": round(lag), "attempts": attempts}
            )
        stamp = execution["intent"].get("released_at") or execution["created_at"]
        age = (now - datetime.fromisoformat(stamp)).total_seconds()
        if job["leg"] == "ENTRY" and age >= ENTRY_MAX_QUEUE_SECONDS:
            old_entries.append(
                {"job_id": job["id"], "age_seconds": round(age), "attempts": attempts}
            )
    value = {"checked_at": now.isoformat(), "overdue_exits": overdue, "old_entries": old_entries}
    if overdue or old_entries:
        logging.getLogger(__name__).warning("execution_jobs_stalled %s", json.dumps(value))
    executor.journal.set("execution_scheduler", "health", value)
    executor.journal.set("v2_workers", "executor", {"heartbeat_at": now.isoformat()})


async def run_worker(executor: Any, *, once: Any = False) -> Any:
    from doxagent.exit_strategy.runner import ExitRunner
    from .acceptance import tick_suites

    exit_runner = ExitRunner(executor)
    exit_task = asyncio.create_task(exit_runner.run()) if not once else None

    pending: dict[tuple[Any, ...], asyncio.Task[Any]] = {}
    stop = asyncio.Event()

    async def synchronous(operation):
        # Ledger mutation stays on this loop, with short storage waits. In particular,
        # drain_events cannot race another _sync/drain and move the cursor backwards.
        operation()

    async def connections():
        await asyncio.to_thread(maintain_connections, executor)
        await recover_idle_accounts(executor)

    async def cycles():
        revisions = await asyncio.to_thread(active_profile_revisions, executor)
        await executor.capture_ready_cycles(revisions)

    async def step(job):
        try:
            await executor.step(job["id"])
        except Exception as exc:
            logging.getLogger(__name__).exception("executor_job_deferred job=%s", job["id"])
            if not once:
                # Contention defers only this ticker, never the entire queue.
                await asyncio.sleep(2 if transient(exc) else 30)

    lanes = [
        ("heartbeat", lambda: asyncio.to_thread(scheduler_health, executor), 10),
        ("connections", connections, 1),
        ("cycles", cycles, 30),
        ("entry_limits", lambda: synchronous(executor.enforce_entry_failure_limits), 10),
        ("exit_recovery", lambda: synchronous(executor.repository.recover_failed_exits), 60),
        ("events", lambda: synchronous(executor.drain_events), 0.2),
        ("acceptance", lambda: tick_suites(executor), 30),
    ]
    maintenance = []
    try:
        while True:
            for key, task in list(pending.items()):
                if task.done():
                    pending.pop(key)
                    await task
            try:
                jobs = await asyncio.to_thread(executor.repository.jobs)
                for job in jobs:
                    key = (job["account"], job["ticker"])
                    if key not in pending:
                        pending[key] = asyncio.create_task(step(job))
            except Exception:
                logging.getLogger(__name__).exception("executor_job_scan_deferred")
            # Dispatch comes first. No health write, connection probe or cycle capture
            # is a prerequisite for advancing an existing EXIT or reconciling an order.
            if not maintenance:
                maintenance = [
                    asyncio.create_task(_periodic(name, operation, interval, stop, once=once))
                    for name, operation, interval in lanes
                ]
            if once:
                await exit_runner.tick()
                await asyncio.gather(*pending.values(), *maintenance)
                return
            await asyncio.sleep(0.2)
    finally:
        stop.set()
        # Do not cancel physical socket/SQLite work or release the sole writer early.
        await asyncio.gather(*pending.values(), *maintenance, return_exceptions=True)
        if exit_task:
            exit_task.cancel()
            await asyncio.gather(exit_task, return_exceptions=True)
        await exit_runner.close()
        executor.close()
