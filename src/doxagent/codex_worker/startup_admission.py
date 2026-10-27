"""Worker-local cold-start admission. Never limits running model turns."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from pathlib import Path

_scope = ContextVar("startup_scope", default=None)


@contextmanager
def startup_scope(*, key, priority=0, deadline=None, progress=None):
    token = _scope.set(dict(key=key, priority=priority, deadline=deadline, progress=progress))
    try:
        yield
    finally:
        _scope.reset(token)


async def progress(stage, **values):
    scope = _scope.get() or {}
    callback = scope.get("progress")
    if callback:
        import inspect

        result = callback(stage, **values)
        if inspect.isawaitable(result):
            await result


def remaining(limit):
    deadline = (_scope.get() or {}).get("deadline")
    return max(0.001, min(limit, deadline - time.time())) if deadline else limit


class PriorityGate:
    def __init__(self, max_pending=10000):
        self.max_pending = max_pending
        self.condition = asyncio.Condition()
        self.waiters = []
        self.sequence = 0
        self.active = False
        self._owner = None
        self._owner_since = None
        self._owner_label = None

    def snapshot(self):
        return {
            "condition_locked": self.condition.locked(),
            "queued": len(self.waiters),
            "oldest_wait_seconds": max(
                (time.monotonic() - entry[2] for entry in self.waiters), default=0.0
            ),
            "owner_alive": self._owner is not None and not self._owner.done(),
            "owner_label": self._owner_label,
            "held_seconds": (
                max(0.0, time.monotonic() - self._owner_since)
                if self._owner_since is not None
                else None
            ),
        }

    @asynccontextmanager
    async def acquire(self, priority=0, allowed=lambda: True, timeout=180, label=None):
        rank = priority if callable(priority) else lambda: priority
        owner = asyncio.current_task()
        admitted = False
        try:
            # The initial lock acquisition is part of the admission budget too.
            async with asyncio.timeout(timeout):
                async with self.condition:
                    if len(self.waiters) >= self.max_pending:
                        raise RuntimeError("startup admission queue full; retry original operation")
                    self.sequence += 1
                    entry = (rank, self.sequence, time.monotonic())
                    self.waiters.append(entry)
                    try:
                        while (
                            self.active
                            or min(self.waiters, key=lambda x: (x[0](), x[1])) != entry
                            or not allowed()
                        ):
                            try:
                                # Condition owns the current task's lock. A wait_for
                                # child can outlive a repeatedly cancelled parent.
                                async with asyncio.timeout(0.25):
                                    await self.condition.wait()
                            except TimeoutError:
                                pass
                        self.active = admitted = True
                        self._owner = owner
                        self._owner_since = time.monotonic()
                        self._owner_label = label
                    finally:
                        self.waiters.remove(entry)
                        self.condition.notify_all()
            yield
        finally:
            if admitted and self._owner is owner:
                # Ownership release must precede every await. Notification may be
                # cancelled; waiters already recheck admission every 250ms.
                self.active = False
                self._owner = self._owner_since = self._owner_label = None
                async with self.condition:
                    self.condition.notify_all()


class StartupAdmission:
    def __init__(self, safety=None, handshake=None):
        self.cold = PriorityGate()
        self.handshake = handshake or asyncio.Semaphore(3)
        self.safety = safety
        self.startup_pressure = False
        self._sample_at = 0.0
        self._healthy_at = None
        self.quarantined = set()

    def memory_level(self):
        if self.safety:
            level = self.safety.read().level
            return getattr(level, "value", level)
        return "NORMAL"

    def allow_speculation(self):
        if self.memory_level() != "NORMAL":
            return False
        now = time.monotonic()
        if now - self._sample_at >= 1:
            self._sample_at = now
            try:

                def psi(kind):
                    line = Path("/proc/pressure", kind).read_text().splitlines()[0]
                    return float(dict(x.split("=") for x in line.split()[1:])["avg10"])

                cpu, io = psi("cpu"), psi("io")
                if cpu >= 60 or io >= 20:
                    self.startup_pressure, self._healthy_at = True, None
                elif cpu < 40 and io < 10:
                    self._healthy_at = self._healthy_at or now
                    if now - self._healthy_at >= 10:
                        self.startup_pressure = False
                else:
                    self._healthy_at = None
            except (OSError, ValueError, IndexError, KeyError):
                self.startup_pressure = False
        return not self.startup_pressure

    @asynccontextmanager
    async def boot(self, *, priority=None):
        scope = _scope.get() or {}
        rank = priority if priority is not None else scope.get("priority", 0)
        rank = (
            rank
            if callable(rank)
            else lambda: priority if priority is not None else scope.get("priority", 0)
        )
        await progress("WAIT_BOOT_SLOT")
        async with self.cold.acquire(
            rank,
            lambda: (
                not self.quarantined
                and self.memory_level() != "CRITICAL"
                and (rank() == 0 or self.allow_speculation())
            ),
            remaining(180),
            label=scope.get("key"),
        ):
            yield
