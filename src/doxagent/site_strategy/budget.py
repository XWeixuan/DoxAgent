"""Small per-site priority budget shared by body, crawler and probes."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from math import ceil

from .schema import SitePurpose

BROWSER_ADMITTED = ContextVar("browser_admitted", default=False)


class AdmissionDeferred(TimeoutError):
    """No execution slot was acquired. The durable BODY owner retries at this time."""
    def __init__(self, retry_seconds):
        self.retry_seconds = retry_seconds
        super().__init__("body_admission_pending")


class SiteBudget:
    """HTTP site limits use the same fair arbiter without browser-global limits."""
    def __init__(self, *, max_concurrency, min_interval_ms):
        self.joint = JointBudget(max_concurrency=max_concurrency)
        self.joint.body_max_concurrency = max_concurrency
        self.update(max_concurrency=max_concurrency, min_interval_ms=min_interval_ms)

    def update(self, *, max_concurrency, min_interval_ms):
        self.max_concurrency = self.joint.max_concurrency = max_concurrency
        self.joint.body_max_concurrency = max_concurrency
        self.min_interval_seconds = min_interval_ms / 1000

    @property
    def _active(self):
        return self.joint._active

    def permit(self, purpose, *, timeout_seconds, task_key=None, ready_since=None):
        return self.joint.permit(purpose, site_key="http", identity_id="http",
            site_max_concurrency=self.max_concurrency,
            site_min_interval_ms=int(self.min_interval_seconds * 1000),
            identity_max_concurrency=self.max_concurrency, identity_min_interval_ms=0,
            timeout_seconds=timeout_seconds, task_key=task_key, ready_since=ready_since)


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
    task_key: str | None = None
    ready_since: float = 0
    expires_at: float = 0
    attached: bool = True
    work_class: str = "foreground"
    work_age_seconds: float = 0
    due_at: float = float('inf')

    def rank(self, now: float) -> tuple[int, float, int]:
        # Aging must be reachable before the crawler queue deadline.
        # earlier BODY work. LOGIN remains interactive priority.
        if self.purpose is SitePurpose.LOGIN:
            priority = 0
        elif self.purpose is SitePurpose.BODY and self.work_class != "recheck":
            priority = 1
        elif self.purpose is SitePurpose.BODY:
            priority = 2
        elif self.purpose is SitePurpose.CRAWLER and self.work_class != "backfill":
            priority = 3
        else:
            priority = 4
        return priority, self.ready_since if self.purpose is SitePurpose.BODY else self.due_at, self.sequence


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
        self._body_streak = 0
        self._running: dict[int, tuple[float, str, str]] = {}
        self._body_claims: dict[str, _JointWaiter] = {}
        self._background_active=0
        self._last_background=0.0
        self._identity_body: dict[str, int] = {}
        self._costs: dict[str, float] = {}
        self._routing_pending: dict[str, int] = {}
        self._routing_last: dict[tuple[str, str], int] = {}
        self._routing_sequence = 0
        self._routing_affinity = OrderedDict()

    def _drop_claim(self, waiter):
        if waiter in self._waiters:
            self._waiters.remove(waiter)
        if waiter.task_key and self._body_claims.get(waiter.task_key) is waiter:
            self._body_claims.pop(waiter.task_key, None)

    def forget_identity(self, identity_id):
        for waiter in list(self._body_claims.values()):
            if waiter.identity_id == identity_id:
                self._drop_claim(waiter)

    def _expire_claims(self, now):
        for waiter in list(self._body_claims.values()):
            if not waiter.attached and waiter.expires_at <= now:
                self._drop_claim(waiter)

    @staticmethod
    def combined_interval_ms(configured, members):
        """Combine admitted members' existing rates; never resize a member.

        The legacy one-browser site clock must not cap two healthy browsers at
        one browser's throughput. All physical identity permits still apply.
        """
        if len(members) < 2 or any(interval <= 0 for _, _, interval in members):
            return configured
        return min(configured, ceil(1 / sum(1 / interval for _, _, interval in members)))

    @asynccontextmanager
    async def route_members(self, site_key, members, *, affinity_key=None, affinity_members=None):
        """Choose ONE member; normal permits still own all physical admission.

        members supplies identity_id, max_concurrency, min_interval_ms. These
        counters represent assignments, not extra permits or website requests.
        """
        if not members:
            raise ValueError("routing_requires_a_member")
        intervals = {key: await asyncio.to_thread(self.identity_interval, key, interval)
                     for key, limit, interval in members}
        async with self._condition:
            now = time.monotonic()

            def score(member):
                key, limit, _ = member
                pending = self._routing_pending.get(key, 0)
                body = self._identity_body.get(key, 0)
                interval = intervals[key] / 1000
                ready = max(0, self._identity_next.get(key, 0) - now)
                capacity_delay = max(0, pending + body - max(0, limit - 1)) * max(
                    interval, self._costs.get("identity:" + key,
                        self._costs.get(site_key, interval)) / max(1, limit))
                return (max(ready + pending * interval, capacity_delay),
                        self._routing_last.get((site_key, key), 0), key)

            selected = min(members, key=score)[0]
            if affinity_key:
                configured = tuple(affinity_members or [member[0] for member in members])
                key = (site_key, configured, affinity_key)
                owner = self._routing_affinity.get(key)
                if owner is None:
                    counts = {member: 0 for member in configured}
                    for (site, pool, _), assigned in self._routing_affinity.items():
                        if site == site_key and pool == configured:
                            counts[assigned] += 1
                    owner = min(configured, key=counts.get)
                    self._routing_affinity[key] = owner
                self._routing_affinity.move_to_end(key)
                while len(self._routing_affinity) > 128:
                    self._routing_affinity.popitem(last=False)
                preferred = next((member for member in members if member[0] == owner), None)
                # Preserve normal cache locality without waiting behind a slow,
                # busy or unavailable owner. Borrowing does not rewrite ownership.
                if preferred and score(preferred)[0] <= score(next(m for m in members if m[0] == selected))[0] + 1:
                    selected = owner
            self._routing_sequence += 1
            self._routing_last[site_key, selected] = self._routing_sequence
            self._routing_pending[selected] = self._routing_pending.get(selected, 0) + 1
        try:
            yield selected
        finally:
            async with self._condition:
                remaining = self._routing_pending[selected] - 1
                if remaining:
                    self._routing_pending[selected] = remaining
                else:
                    self._routing_pending.pop(selected, None)
                self._condition.notify_all()

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
        task_key: str | None = None,
        ready_since: str | None = None,
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
                    now = time.monotonic()
                    self._expire_claims(now)
                    waiter = self._body_claims.get(task_key) if purpose is SitePurpose.BODY and task_key else None
                    if waiter is None:
                        waiter = _JointWaiter(site_key, identity_id, purpose, self._sequence,
                            now, site_max_concurrency, identity_max_concurrency,
                            aging_seconds=min(1.0, max(.1, timeout_seconds / 2)),
                            work_class=work_class, work_age_seconds=work_age_seconds,
                            due_at=datetime.fromisoformat(due_at).timestamp() if due_at else float('inf'),
                            task_key=task_key,
                            ready_since=datetime.fromisoformat(ready_since).timestamp() if ready_since else time.time()-work_age_seconds)
                        self._waiters.append(waiter)
                        if purpose is SitePurpose.BODY and task_key:
                            # A new input generation invalidates its predecessor's soft claim.
                            family = task_key.split(":", 1)[0]
                            for key, old in list(self._body_claims.items()):
                                if key != task_key and key.split(":", 1)[0] == family:
                                    self._drop_claim(old)
                            self._body_claims[task_key] = waiter
                    waiter.site_key, waiter.identity_id = site_key, identity_id
                    waiter.site_limit, waiter.identity_limit = site_max_concurrency, identity_max_concurrency
                    waiter.attached = True
                    waiter.expires_at = now + 3
                    while True:
                        now = time.monotonic()
                        self._expire_claims(now)
                        # Only ready work competes for priority. A busy Identity must
                        # not block unrelated sites with their own free browser.
                        eligible = [
                            item
                            for item in self._waiters
                            if self._site_active.get(item.site_key, 0) < item.site_limit
                            and self._active + self.pending_physical() < self.max_concurrency
                            and (item.purpose is not SitePurpose.BODY or self._body_active < (
                                self.max_concurrency if self.body_borrow else self.body_max_concurrency))
                            and self._identity_active.get(item.identity_id, 0) < item.identity_limit
                            and self._site_next.get(item.site_key, 0.0) <= now
                            and self._identity_next.get(item.identity_id, 0.0) <= now
                        ]
                        # A detached soft claim has no executing coroutine. It
                        # may retain BODY order on its own identity, but must
                        # never win the global permit and leave every slot idle.
                        detached = {}
                        for item in eligible:
                            if not item.attached and item.purpose is SitePurpose.BODY:
                                rank = item.rank(now)
                                detached[item.identity_id] = min(rank, detached.get(item.identity_id, rank))
                        eligible = [item for item in eligible if item.attached and (
                            item.purpose is not SitePurpose.BODY or item.identity_id not in detached
                            or item.rank(now) < detached[item.identity_id])]
                        best = min(eligible, key=lambda item: item.rank(now)) if eligible else None
                        crawlers = [w for w in eligible if w.purpose is SitePurpose.CRAWLER
                                    and now-w.enqueued_at >= w.aging_seconds]
                        if crawlers and self._body_streak >= 3 and best and best.purpose is not SitePurpose.LOGIN:
                            best = min(crawlers, key=lambda w: w.sequence)
                        background=[w for w in eligible if w.work_class in {'backfill','recheck'}]
                        if background and not self._background_active and now-self._last_background>=5 and not any(
                            w.purpose is SitePurpose.BODY and w.work_class != 'recheck' for w in eligible):
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
                            self._drop_claim(waiter)
                            self._active += 1
                            self._body_streak = self._body_streak+1 if purpose is SitePurpose.BODY else 0
                            self._running[waiter.sequence] = (now+self._costs.get(("body:" if purpose is SitePurpose.BODY else "identity:")+identity_id, 1), site_key, identity_id)
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
                        if purpose is SitePurpose.BODY and task_key:
                            pacing = max(self._site_next.get(site_key, now), self._identity_next.get(identity_id, now))
                            releases = [at for at, site, identity in self._running.values()
                                        if site == site_key or identity == identity_id or self._active >= self.max_concurrency]
                            expected = min(releases, default=now+.25)
                            delay = max(pacing, expected)-now
                            if self._running and delay < .25:
                                delay = 1.0  # An underestimated in-flight duration must never busy-spin.
                            raise AdmissionDeferred(max(.1, min(2.0, delay)))
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
                    if purpose is SitePurpose.BODY and task_key and not acquired:
                        waiter.attached = False
                        waiter.expires_at = time.monotonic()+3
                    else:
                        self._drop_claim(waiter)
                if acquired:
                    self._running.pop(waiter.sequence, None)
                    elapsed = time.monotonic() - executing_at
                    if purpose is SitePurpose.BODY:
                        cost_key = "body:"+identity_id
                        self._costs[cost_key] = .8*self._costs.get(cost_key,elapsed)+.2*elapsed
                    if purpose is SitePurpose.CRAWLER:
                        cost_key = "identity:" + identity_id
                        self._costs[cost_key] = .8*self._costs.get(cost_key, elapsed) + .2*elapsed
                    if purpose is SitePurpose.CRAWLER and work_class != 'backfill':
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
