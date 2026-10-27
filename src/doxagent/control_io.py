"""Database-scoped short-write owners and cancellation-safe blocking I/O.

Only SQL/immutable publication units belong here. No automatic callback retries.
"""

from __future__ import annotations

import asyncio
import contextvars
import itertools
import os
import queue
import threading
import time
from concurrent.futures import Future
from pathlib import Path

SHORT_WRITE = contextvars.ContextVar("short_sql_write", default=False)

_registry_lock = threading.Lock()
_owners = {}
_locks = {}


def database_key(path):
    return os.path.normcase(str(Path(path).resolve()))


def write_lock(path):
    key = database_key(path)
    with _registry_lock:
        return _locks.setdefault(key, threading.RLock())


class _Owner:
    def __init__(self, key):
        self.queue = queue.PriorityQueue(maxsize=128)
        self.sequence = itertools.count()
        self.thread = threading.Thread(
            target=self.run, name="short-write-" + Path(key).stem, daemon=True
        )
        self.thread.start()

    def run(self):
        while True:
            _, _, future, context, function, args, kwargs = self.queue.get()
            try:
                if future.set_running_or_notify_cancel():
                    try:

                        def owned_call():
                            token = SHORT_WRITE.set(True)
                            try:
                                return function(*args, **kwargs)
                            finally:
                                SHORT_WRITE.reset(token)

                        future.set_result(context.run(owned_call))
                    except BaseException as exc:
                        future.set_exception(exc)
            finally:
                self.queue.task_done()


async def settled(awaitable):
    task = asyncio.ensure_future(awaitable)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        await asyncio.gather(task, return_exceptions=True)
        raise


async def read_io(function, *args, **kwargs):
    return await settled(asyncio.to_thread(function, *args, **kwargs))


async def write_io(path, function, *args, priority=1, retry=False, **kwargs):
    key = database_key(path)
    with _registry_lock:
        owner = _owners.get(key)
        if owner is None:
            owner = _owners[key] = _Owner(key)
    due = time.monotonic() + 30
    while True:
        future = Future()
        try:
            owner.queue.put_nowait(
                (
                    priority,
                    next(owner.sequence),
                    future,
                    contextvars.copy_context(),
                    function,
                    args,
                    kwargs,
                )
            )
        except queue.Full as exc:
            from doxagent.message_bus_v2.storage_lane import WriteAdmissionBusy

            raise WriteAdmissionBusy("short-write queue full: " + Path(key).name) from exc
        try:
            return await settled(asyncio.wrap_future(future))
        except Exception as exc:
            from doxagent.persistent_runtime_v2.journal import is_sqlite_contention

            if not retry or not is_sqlite_contention(exc) or time.monotonic() >= due:
                raise
            # Only explicitly rollback-safe SQL callbacks opt in. The physical
            # owner/slot is already released; never sleep inside that queue.
            await asyncio.sleep(0.025)


async def loop_watch(state, *, interval=1.0):
    """Keep scalar lag diagnostics only; no per-tick database writes."""
    loop = asyncio.get_running_loop()
    while True:
        due = loop.time() + interval
        await asyncio.sleep(interval)
        lag = max(0.0, loop.time() - due) * 1000
        state.update(
            current_ms=round(lag, 2),
            max_ms=max(state.get("max_ms", 0), round(lag, 2)),
            over_250ms=state.get("over_250ms", 0) + int(lag > 250),
        )


_diagnostics = queue.Queue(maxsize=256)
_diagnostic_thread = None
_diagnostic_dropped = 0


def diagnostic_warning(name, message, *args):
    """Optional high-rate timing logs cannot block a business writer/loop."""
    global _diagnostic_thread, _diagnostic_dropped
    import logging

    with _registry_lock:
        if _diagnostic_thread is None:

            def drain():
                while True:
                    logger, text, values = _diagnostics.get()
                    try:
                        logging.getLogger(logger).warning(text, *values)
                    except Exception:
                        pass
                    finally:
                        _diagnostics.task_done()

            _diagnostic_thread = threading.Thread(
                target=drain, name="runtime-diagnostics", daemon=True
            )
            _diagnostic_thread.start()
    try:
        _diagnostics.put_nowait((name, message, args))
    except queue.Full:
        _diagnostic_dropped += 1


def diagnostic_status():
    return dict(queued=_diagnostics.qsize(), dropped=_diagnostic_dropped)
