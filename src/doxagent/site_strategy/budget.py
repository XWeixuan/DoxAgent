"""Small per-site priority budget shared by body, crawler and probes."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from .schema import SitePurpose


@dataclass
class _Waiter:
    purpose: SitePurpose
    sequence: int

    @property
    def priority(self) -> int:
        return {
            SitePurpose.BODY: 0,
            SitePurpose.CRAWLER: 1,
            SitePurpose.LOGIN: 1,
            SitePurpose.PROBE: 2,
        }[self.purpose]


class SiteBudget:
    def __init__(self, *, max_concurrency: int, min_interval_ms: int) -> None:
        self.max_concurrency = max_concurrency
        self.min_interval_seconds = min_interval_ms / 1000
        self._condition = asyncio.Condition()
        self._active = 0
        self._next_start = 0.0
        self._sequence = 0
        self._waiters: list[_Waiter] = []

    def update(self, *, max_concurrency: int, min_interval_ms: int) -> None:
        self.max_concurrency = max_concurrency
        self.min_interval_seconds = min_interval_ms / 1000

    @asynccontextmanager
    async def permit(self, purpose: SitePurpose, *, timeout_seconds: float) -> AsyncIterator[int]:
        started = time.monotonic()
        waiter: _Waiter | None = None
        acquired = False
        try:
            async with asyncio.timeout(timeout_seconds):
                async with self._condition:
                    self._sequence += 1
                    waiter = _Waiter(purpose=purpose, sequence=self._sequence)
                    self._waiters.append(waiter)
                    while True:
                        best = min(self._waiters, key=lambda item: (item.priority, item.sequence))
                        if best is waiter and self._active < self.max_concurrency:
                            delay = self._next_start - time.monotonic()
                            if delay <= 0:
                                self._waiters.remove(waiter)
                                self._active += 1
                                self._next_start = time.monotonic() + self.min_interval_seconds
                                acquired = True
                                break
                            try:
                                await asyncio.wait_for(self._condition.wait(), timeout=delay)
                            except TimeoutError:
                                pass
                        else:
                            await self._condition.wait()
            yield max(0, int((time.monotonic() - started) * 1000))
        finally:
            async with self._condition:
                if waiter is not None and waiter in self._waiters:
                    self._waiters.remove(waiter)
                if acquired:
                    self._active -= 1
                self._condition.notify_all()


class SiteBudgetManager:
    def __init__(self) -> None:
        self._budgets: dict[str, SiteBudget] = {}

    def get(self, runtime_key: str, *, max_concurrency: int, min_interval_ms: int) -> SiteBudget:
        budget = self._budgets.get(runtime_key)
        if budget is None:
            budget = SiteBudget(
                max_concurrency=max_concurrency,
                min_interval_ms=min_interval_ms,
            )
            self._budgets[runtime_key] = budget
        else:
            budget.update(max_concurrency=max_concurrency, min_interval_ms=min_interval_ms)
        return budget


__all__ = ["SiteBudget", "SiteBudgetManager"]
