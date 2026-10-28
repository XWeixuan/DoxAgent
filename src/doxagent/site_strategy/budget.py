"""Small per-site priority budget shared by body, crawler and probes."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from math import ceil

from .schema import SitePurpose

BROWSER_ADMITTED = ContextVar("browser_admitted", default=False)


@dataclass
class _Waiter:
    purpose: SitePurpose
    sequence: int

    @property
    def priority(self) -> int:
        return {
            SitePurpose.BODY: 2,
            SitePurpose.CRAWLER: 1,
            SitePurpose.LOGIN: 0,
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
                                async with asyncio.timeout(delay):
                                    await self._condition.wait()
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
    site_limit: int
    identity_limit: int
    aging_seconds: float = 5.0
    work_class: str = "foreground"
    work_age_seconds: float = 0
    due_at: float = float('inf')

    def rank(self, now: float) -> tuple[int, float, int]:
        # Aging must be reachable before the crawler queue deadline.
        # earlier BODY work. LOGIN remains interactive priority.
        if self.purpose is SitePurpose.LOGIN:
            priority = 0
        elif self.purpose is SitePurpose.CRAWLER and self.work_class != "backfill":
            priority = 1
        elif self.purpose is SitePurpose.BODY and self.work_class != "recheck":
            priority = 2
        else:
            priority = 3
        return priority, self.due_at, self.sequence


class JointBudget:
    """Atomically admits against Site and Browser Identity limits."""

    def __init__(self, *, max_concurrency: int = 4) -> None:
        self._condition = asyncio.Condition()
        self._sequence = 0
        self._waiters: list[_JointWaiter] = []
        self._site_active: dict[str, int] = {}
        self._identity_active: dict[str, int] = {}
        self._site_next: dict[str, float] = {}
        self._identity_next: dict[str, float] = {}
        self.identity_interval = lambda identity_id, configured: configured
        self.body_max_concurrency = 2
        self.body_borrow = False
        self.pending_physical = lambda: 0
        self.max_concurrency = max_concurrency
        self._active = 0
        self._body_active = 0
        self._background_active=0
        self._last_background=0.0
        self._identity_body: dict[str, int] = {}
        self._costs: dict[str, float] = {}

    @asynccontextmanager
    async def control_navigation(self, identity_id, *, interval_ms, deadline):
        """Same pacing clock, no second business-capacity queue. Profile is drained."""
        async with asyncio.timeout_at(deadline):
            interval = await asyncio.to_thread(self.identity_interval, identity_id, interval_ms)
            async with self._condition:
                while True:
                    now = time.monotonic()
                    delay = self._identity_next.get(identity_id, 0) - now
                    if delay <= 0:
                        self._identity_next[identity_id] = now + interval / 1000
                        break
                    try:
                        async with asyncio.timeout(delay):
                            await self._condition.wait()
                    except TimeoutError:
                        pass
        yield

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
        work_class: str = "foreground",
        work_age_seconds: float = 0,
        due_at: str | None = None,
        expected_rate: float = 0,
    ) -> AsyncIterator[int]:
        started = time.monotonic()
        waiter: _JointWaiter | None = None
        acquired = False
        executing_at = None
        if expected_rate > 0 and purpose is SitePurpose.CRAWLER:
            site_max_concurrency = min(site_max_concurrency, max(1,
                ceil(expected_rate * self._costs.get(site_key, 1.0) / .75) + self._identity_body.get(identity_id, 0)))
        try:
            async with asyncio.timeout(timeout_seconds):
                # Resolve external state before touching any admission counters.
                # In particular SQLite failure/cancellation must not leak capacity
                # or block every unrelated identity under the joint condition.
                interval = await asyncio.to_thread(
                    self.identity_interval, identity_id, identity_min_interval_ms
                )
                async with self._condition:
                    self._sequence += 1
                    waiter = _JointWaiter(
                        site_key,
                        identity_id,
                        purpose,
                        self._sequence,
                        time.monotonic(),
                        site_max_concurrency,
                        identity_max_concurrency,
                        min(5.0, timeout_seconds / 2),
                        work_class,
                        work_age_seconds,
                        datetime.fromisoformat(due_at).timestamp() if due_at else float('inf'),
                    )
                    self._waiters.append(waiter)
                    while True:
                        now = time.monotonic()
                        # Only ready work competes for priority. A busy Identity must
                        # not block unrelated sites with their own free browser.
                        eligible = [
                            item
                            for item in self._waiters
                            if self._site_active.get(item.site_key, 0) < item.site_limit
                            and self._active + self.pending_physical() < self.max_concurrency
                            and (
                                item.purpose is not SitePurpose.BODY
                                or (
                                    self._identity_body.get(item.identity_id, 0) < max(1,item.identity_limit-int(any(
                                        w.purpose is SitePurpose.CRAWLER and w.identity_id==item.identity_id for w in self._waiters)))
                                    and self._body_active < min(self.max_concurrency if self.body_borrow and not any(
                                        w.purpose is SitePurpose.CRAWLER for w in self._waiters
                                    ) else self.body_max_concurrency, max(1, self.max_concurrency - 1))
                                )
                            )
                            and self._identity_active.get(item.identity_id, 0) < item.identity_limit
                            and self._site_next.get(item.site_key, 0.0) <= now
                            and self._identity_next.get(item.identity_id, 0.0) <= now
                        ]
                        best = min(eligible, key=lambda item: item.rank(now)) if eligible else None
                        bodies = [w for w in eligible if w.purpose is SitePurpose.BODY
                                  and w.work_class != 'recheck' and now-w.enqueued_at >= 1]
                        if bodies and self._body_active == 0:
                            best = min(bodies, key=lambda item: item.sequence)
                        background=[w for w in eligible if w.work_class in {'backfill','recheck'}]
                        if background and not self._background_active and now-self._last_background>=5:
                            best=min(background,key=lambda item:item.sequence)
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
                            self._active += 1
                            if work_class in {'backfill','recheck'}:
                                self._background_active+=1
                                self._last_background=now
                            if purpose is SitePurpose.BODY:
                                self._body_active += 1
                                self._identity_body[identity_id] = self._identity_body.get(identity_id, 0) + 1
                            self._site_active[site_key] = self._site_active.get(site_key, 0) + 1
                            self._identity_active[identity_id] = (
                                self._identity_active.get(identity_id, 0) + 1
                            )
                            self._site_next[site_key] = now + site_min_interval_ms / 1000
                            self._identity_next[identity_id] = (
                                now
                                + interval
                                / 1000
                            )
                            acquired = True
                            executing_at = time.monotonic()
                            break
                        try:
                            # Keep the condition wait and cancellation in this task;
                            # wait_for creates a child that also reacquires the lock.
                            async with asyncio.timeout(
                                max(0.01, min(delay, 1.0)) if delay > 0 else 1.0
                            ):
                                await self._condition.wait()
                        except TimeoutError:
                            pass
            token = BROWSER_ADMITTED.set(True)
            try:
                yield max(0, int((time.monotonic() - started) * 1000))
            finally:
                BROWSER_ADMITTED.reset(token)
        finally:
            async with self._condition:
                if waiter is not None and waiter in self._waiters:
                    self._waiters.remove(waiter)
                if acquired:
                    if purpose is SitePurpose.CRAWLER and work_class != 'backfill':
                        elapsed = time.monotonic() - executing_at
                        self._costs[site_key] = .8*self._costs.get(site_key, elapsed) + .2*elapsed
                    self._active -= 1
                    if work_class in {'backfill','recheck'}:self._background_active-=1
                    if purpose is SitePurpose.BODY:
                        self._body_active -= 1
                        self._identity_body[identity_id] -= 1
                    self._site_active[site_key] -= 1
                    self._identity_active[identity_id] -= 1
                self._condition.notify_all()


__all__ = ["JointBudget", "SiteBudget", "SiteBudgetManager"]
