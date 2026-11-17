"""Short single-owner writes, independent bounded reads and content preparation."""

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from weakref import WeakKeyDictionary

WRITE_ADMISSION = ContextVar("bus_write_admission", default=False)
WRITE_TIMINGS = ContextVar("bus_write_timings", default=None)


class WriteAdmissionBusy(Exception):
    """BEGIN failed before any mutation; safe to release the lane and retry."""


@dataclass
class _Lane:
    readers: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(2))
    preparation: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(2))
    waiting: list = field(default_factory=list)
    owner: tuple | None = None
    sequence: int = 0
    urgent_streak: int = 0

    def _grant_next(self):
        if self.owner is not None:
            return
        self.waiting[:] = [ticket for ticket in self.waiting if not ticket[2].cancelled()]
        if not self.waiting:
            return
        ordinary = [ticket for ticket in self.waiting if ticket[0] > 0]
        best = min(ordinary if ordinary and self.urgent_streak >= 8 else self.waiting)
        self.waiting.remove(best)
        self.owner = best
        self.urgent_streak = self.urgent_streak + 1 if best[0] == 0 else 0
        best[2].set_result(None)

    @asynccontextmanager
    async def writer(self, priority):
        # All queue mutations run synchronously on this event loop. Wake only
        # the next owner; notify_all otherwise makes every queued poll rescan
        # the full queue on each short commit (quadratic scheduling work).
        self.sequence += 1
        ticket = (priority, self.sequence, asyncio.get_running_loop().create_future())
        self.waiting.append(ticket)
        self._grant_next()
        try:
            await ticket[2]
            yield
        finally:
            if ticket in self.waiting:
                self.waiting.remove(ticket)
            if self.owner is ticket:
                self.owner = None
            self._grant_next()


class StorageLane:
    def __init__(self) -> None:
        self._lanes: WeakKeyDictionary = WeakKeyDictionary()
        self.metrics = Counter()

    def _lane(self):
        loop = asyncio.get_running_loop()
        lane = self._lanes.get(loop)
        if lane is None:
            lane = self._lanes[loop] = _Lane()
        return lane

    @staticmethod
    async def _execute(operation, args, kwargs, *, metrics=None):
        cpu = [0.0]
        def run():
            started = time.thread_time()
            try:
                return operation(*args, **kwargs)
            finally:
                cpu[0] = time.thread_time()-started
        work = asyncio.create_task(asyncio.to_thread(run))
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            # Threads cannot be cancelled halfway through a commit/file publish.
            while not work.done():
                try:
                    await asyncio.shield(work)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            await asyncio.gather(work, return_exceptions=True)
            raise
        finally:
            if metrics is not None:
                metrics['thread_cpu_s'] += cpu[0]

    async def read(self, operation: Callable[..., Any], *args, **kwargs):
        """Caller supplies an operation-scoped read connection; no writer lock."""
        queued = time.monotonic()
        async with self._lane().readers:
            started = time.monotonic()
            self.metrics['read_queue_s'] += started - queued
            try:
                return await self._execute(operation, args, kwargs, metrics=self.metrics)
            finally:
                self.metrics['read_work_s'] += time.monotonic() - started
                self.metrics['read_calls'] += 1

    async def prepare(self, operation: Callable[..., Any], *args, **kwargs):
        queued = time.monotonic()
        async with self._lane().preparation:
            started = time.monotonic()
            self.metrics['prepare_queue_s'] += started - queued
            try:
                return await self._execute(operation, args, kwargs, metrics=self.metrics)
            finally:
                self.metrics['prepare_work_s'] += time.monotonic() - started
                self.metrics['prepare_calls'] += 1

    async def call(self, operation: Callable[..., Any], *args, _lane_priority=1, **kwargs):
        queued_at = time.monotonic()
        timings = {"begin_wait_ms": 0, "transaction_ms": 0, "commit_ms": 0}
        token = WRITE_ADMISSION.set(True)
        trace = WRITE_TIMINGS.set(timings)
        try:
            while True:
                try:
                    admission_at = time.monotonic()
                    async with self._lane().writer(_lane_priority):
                        started = time.monotonic()
                        self.metrics["writer_queue_s"] += started-admission_at
                        try:
                            return await self._execute(operation, args, kwargs, metrics=self.metrics)
                        finally:
                            self.metrics["writer_attempts"] += 1
                            self.metrics["writer_work_s"] += time.monotonic()-started
                            elapsed, wait = time.monotonic() - started, started - queued_at
                            if elapsed >= 0.05 or wait >= 1:
                                logging.getLogger(__name__).warning(
                                    "bus_storage_slow operation=%s elapsed_ms=%s "
                                    "wait_ms=%s phases=%s",
                                    getattr(operation, "__qualname__", type(operation).__name__),
                                    round(elapsed * 1000),
                                    round(wait * 1000),
                                    timings,
                                )
                except WriteAdmissionBusy:
                    if time.monotonic() - queued_at >= 30:
                        raise
                    self.metrics["writer_backoff_count"] += 1
                    await asyncio.sleep(0.025)
        finally:
            for phase in ("begin_wait_ms", "transaction_ms", "commit_ms"):
                self.metrics[phase] += timings[phase]
            WRITE_ADMISSION.reset(token)
            WRITE_TIMINGS.reset(trace)
