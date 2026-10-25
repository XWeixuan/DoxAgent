"""Short single-owner writes, independent bounded reads and content preparation."""

import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from weakref import WeakKeyDictionary


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

    def _lane(self):
        loop = asyncio.get_running_loop()
        lane = self._lanes.get(loop)
        if lane is None:
            lane = self._lanes[loop] = _Lane()
        return lane

    @staticmethod
    async def _execute(operation, args, kwargs):
        work = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
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

    async def read(self, operation: Callable[..., Any], *args, **kwargs):
        """Caller supplies an operation-scoped read connection; no writer lock."""
        async with self._lane().readers:
            return await self._execute(operation, args, kwargs)

    async def prepare(self, operation: Callable[..., Any], *args, **kwargs):
        async with self._lane().preparation:
            return await self._execute(operation, args, kwargs)

    async def call(self, operation: Callable[..., Any], *args, _lane_priority=1, **kwargs):
        queued_at = time.monotonic()
        async with self._lane().writer(_lane_priority):
            started = time.monotonic()
            try:
                return await self._execute(operation, args, kwargs)
            finally:
                elapsed, wait = time.monotonic() - started, started - queued_at
                budget = 0.05 if getattr(operation, "__name__", "") == "accept_piece" else 1
                if elapsed >= budget or wait >= 1:
                    logging.getLogger(__name__).warning(
                        "bus_storage_slow operation=%s elapsed_ms=%s wait_ms=%s",
                        getattr(operation, "__qualname__", type(operation).__name__),
                        round(elapsed * 1000),
                        round(wait * 1000),
                    )
