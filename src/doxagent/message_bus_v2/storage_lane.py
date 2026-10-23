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
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    readers: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(2))
    preparation: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(2))
    waiting: list = field(default_factory=list)
    active: bool = False
    sequence: int = 0
    urgent_streak: int = 0

    @asynccontextmanager
    async def writer(self, priority):
        ticket = None
        admitted = False
        try:
            async with self.condition:
                self.sequence += 1
                ticket = (priority, self.sequence)
                self.waiting.append(ticket)
                while True:
                    ordinary = [t for t in self.waiting if t[0] > 0]
                    best = min(ordinary if ordinary and self.urgent_streak >= 8 else self.waiting)
                    if not self.active and best == ticket:
                        self.waiting.remove(ticket)
                        self.active = admitted = True
                        self.urgent_streak = self.urgent_streak + 1 if priority == 0 else 0
                        break
                    await self.condition.wait()
            yield
        finally:
            async with self.condition:
                if ticket in self.waiting:
                    self.waiting.remove(ticket)
                if admitted:
                    self.active = False
                self.condition.notify_all()


class StorageLane:
    def __init__(self) -> None:
        self._lanes: WeakKeyDictionary = WeakKeyDictionary()

    def _lane(self):
        return self._lanes.setdefault(asyncio.get_running_loop(), _Lane())

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
                        getattr(operation, "__name__", type(operation).__name__),
                        round(elapsed * 1000),
                        round(wait * 1000),
                    )
