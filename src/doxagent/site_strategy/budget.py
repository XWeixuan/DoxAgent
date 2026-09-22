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
        self.joint = JointBudget()

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


@dataclass
class _JointWaiter:
    site_key: str
    identity_id: str
    purpose: SitePurpose
    sequence: int
    enqueued_at: float

    def rank(self, now: float) -> tuple[int, int]:
        # A crawler waiting 30 seconds joins the BODY FIFO without outranking
        # earlier BODY work. LOGIN remains interactive priority.
        if self.purpose is SitePurpose.LOGIN:
            priority = 0
        elif self.purpose is SitePurpose.BODY:
            priority = 1
        elif self.purpose is SitePurpose.CRAWLER and now - self.enqueued_at >= 30:
            priority = 1
        elif self.purpose is SitePurpose.CRAWLER:
            priority = 2
        else:
            priority = 3
        return priority, self.sequence


class JointBudget:
    """Atomically admits against Site and Browser Identity limits."""

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._sequence = 0
        self._waiters: list[_JointWaiter] = []
        self._site_active: dict[str, int] = {}
        self._identity_active: dict[str, int] = {}
        self._site_next: dict[str, float] = {}
        self._identity_next: dict[str, float] = {}

    @asynccontextmanager
    async def permit(
        self,
        purpose: SitePurpose,
        *,
        site_key: str,
        identity_id: str,
        site_max_concurrency: int,
        site_min_interval_ms: int,
        identity_max_concurrency: int,
        identity_min_interval_ms: int,
        timeout_seconds: float,
    ) -> AsyncIterator[int]:
        started = time.monotonic()
        waiter: _JointWaiter | None = None
        acquired = False
        try:
            async with asyncio.timeout(timeout_seconds):
                async with self._condition:
                    self._sequence += 1
                    waiter = _JointWaiter(
                        site_key,
                        identity_id,
                        purpose,
                        self._sequence,
                        time.monotonic(),
                    )
                    self._waiters.append(waiter)
                    while True:
                        now = time.monotonic()
                        best = min(self._waiters, key=lambda item: item.rank(now))
                        capacity = (
                            self._site_active.get(site_key, 0) < site_max_concurrency
                            and self._identity_active.get(identity_id, 0) < identity_max_concurrency
                        )
                        delay = (
                            max(
                                self._site_next.get(site_key, 0.0),
                                self._identity_next.get(identity_id, 0.0),
                            )
                            - now
                        )
                        if best is waiter and capacity and delay <= 0:
                            self._waiters.remove(waiter)
                            self._site_active[site_key] = self._site_active.get(site_key, 0) + 1
                            self._identity_active[identity_id] = (
                                self._identity_active.get(identity_id, 0) + 1
                            )
                            self._site_next[site_key] = now + site_min_interval_ms / 1000
                            self._identity_next[identity_id] = now + identity_min_interval_ms / 1000
                            acquired = True
                            break
                        try:
                            await asyncio.wait_for(
                                self._condition.wait(),
                                timeout=max(0.01, min(delay, 1.0)) if delay > 0 else 1.0,
                            )
                        except TimeoutError:
                            pass
            yield max(0, int((time.monotonic() - started) * 1000))
        finally:
            async with self._condition:
                if waiter is not None and waiter in self._waiters:
                    self._waiters.remove(waiter)
                if acquired:
                    self._site_active[site_key] -= 1
                    self._identity_active[identity_id] -= 1
                self._condition.notify_all()


__all__ = ["JointBudget", "SiteBudget", "SiteBudgetManager"]
