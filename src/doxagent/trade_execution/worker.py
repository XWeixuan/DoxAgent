"""Single local socket writer; each contract progresses independently."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any


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
        try:
            executor.broker(revision).connect()
            results[revision] = "CONNECTED"
        except Exception as exc:
            results[revision] = f"DISCONNECTED:{type(exc).__name__}"
        current = executor.journal.get("execution_connections", revision)
        value = {"status": results[revision]}
        if current != value:
            executor.journal.set("execution_connections", revision, value)
    return results


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


async def run_worker(executor: Any, *, once: Any = False) -> Any:
    from .acceptance import tick_suites

    pending: dict[tuple[Any, ...], asyncio.Task[Any]] = {}
    acceptance = None
    next_acceptance = 0.0
    next_heartbeat = 0.0
    next_connection_check = 0.0
    try:
        while True:
            if time.monotonic() >= next_heartbeat:
                executor.journal.set(
                    "v2_workers",
                    "executor",
                    {
                        "heartbeat_at": executor.journal.clock().isoformat(),
                    },
                )
                next_heartbeat = time.monotonic() + 10
            if time.monotonic() >= next_connection_check:
                maintain_connections(executor)
                next_connection_check = time.monotonic() + 10
            executor.drain_events()
            if (acceptance is None or acceptance.done()) and time.monotonic() >= next_acceptance:
                if acceptance is not None:
                    await acceptance
                acceptance = asyncio.create_task(tick_suites(executor))
                next_acceptance = time.monotonic() + 30
            for key, task in list(pending.items()):
                if task.done():
                    await task
                    pending.pop(key)
            for job in executor.repository.jobs():
                key = (job["account"], job["ticker"])
                if key not in pending:
                    pending[key] = asyncio.create_task(executor.step(job["id"]))
            if once:
                await asyncio.gather(*pending.values())
                if acceptance:
                    await acceptance
                return
            await asyncio.sleep(0.2)
    finally:
        # Let bounded socket requests finish before releasing the OS writer lock.
        await asyncio.gather(*pending.values(), return_exceptions=True)
        if acceptance:
            await asyncio.gather(acceptance, return_exceptions=True)
        executor.close()
