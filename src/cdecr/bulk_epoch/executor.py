"""Shared native-async structured-model executor for BULK_EPOCH."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from time import monotonic, perf_counter
from typing import Protocol

from cdecr.models import ModelTier
from cdecr.ports import StructuredModelRequest, StructuredModelResult


class AsyncStructuredModelClient(Protocol):
    model: str

    async def acomplete(self, request: StructuredModelRequest) -> StructuredModelResult: ...


@dataclass(frozen=True)
class AsyncCallTelemetry:
    tier: str
    stage: str
    priority: str
    queued_at_ms: int
    started_at_ms: int
    finished_at_ms: int
    queue_wait_ms: int
    attempt_count: int
    status: str
    error_code: str | None


class _TokenBucket:
    def __init__(self, *, rate: float, burst: int) -> None:
        self.rate = max(0.01, rate)
        self.capacity = max(1, burst)
        self.tokens = float(self.capacity)
        self.updated_at = monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self.lock:
                now = monotonic()
                elapsed = max(0.0, now - self.updated_at)
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                self.updated_at = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                delay = (1.0 - self.tokens) / self.rate
            await asyncio.sleep(delay)


def _provider_pressure(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    code = str(getattr(exc, "code", "")).casefold()
    text = f"{type(exc).__name__}:{exc}".casefold()
    return status == 429 or any(
        token in f"{code}:{text}"
        for token in ("429", "rate_limit", "connection reset", "connectionreset", "timeout")
    )


class AsyncModelExecutor:
    """Run all M2/M3/M4 HTTP calls on one shared event loop and connection pool.

    Existing business request builders remain synchronous. Their bounded worker threads submit
    coroutines to this hub and wait for the result; HTTP execution itself is native async and is
    governed by per-tier lanes, fixed token buckets, and per-stage active caps.
    """

    def __init__(
        self,
        *,
        clients: Mapping[ModelTier, AsyncStructuredModelClient],
        tier_limits: Mapping[ModelTier, int],
        stage_limits: Mapping[str, int],
        repair_limit: int,
        rates: Mapping[ModelTier, tuple[float, int]],
        max_retries: int = 2,
    ) -> None:
        self._clients = dict(clients)
        self._tier_limits = dict(tier_limits)
        self._stage_limits = dict(stage_limits)
        self._repair_limit = max(1, repair_limit)
        self._rates = dict(rates)
        self._max_retries = max(0, max_retries)
        self._telemetry: list[AsyncCallTelemetry] = []
        self._telemetry_lock = threading.Lock()
        self._ready = threading.Event()
        self._closed = False
        self._thread = threading.Thread(
            target=self._run_loop,
            name="cdecr-bulk-async-model-executor",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait(timeout=10)
        if not self._ready.is_set():
            raise RuntimeError("bulk async model executor did not start")

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._tier_semaphores = {
            tier: asyncio.Semaphore(limit) for tier, limit in self._tier_limits.items()
        }
        self._stage_semaphores = {
            stage: asyncio.Semaphore(limit) for stage, limit in self._stage_limits.items()
        }
        self._repair_semaphore = asyncio.Semaphore(self._repair_limit)
        self._buckets = {
            tier: _TokenBucket(rate=rate, burst=burst)
            for tier, (rate, burst) in self._rates.items()
        }
        self._ready.set()
        self._loop.run_forever()
        pending = asyncio.all_tasks(self._loop)
        for task in pending:
            task.cancel()
        if pending:
            self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self._loop.close()

    async def _complete(
        self, tier: ModelTier, request: StructuredModelRequest
    ) -> StructuredModelResult:
        origin = perf_counter()
        queued_at_ms = round(origin * 1000)
        stage = str(request.metadata.get("stage", "unspecified"))
        priority = str(request.metadata.get("priority", "normal"))
        tier_semaphore = self._tier_semaphores[tier]
        stage_semaphore = self._stage_semaphores.get(stage)
        priority_semaphore = self._repair_semaphore if priority == "repair" else None
        semaphores = [tier_semaphore]
        if stage_semaphore is not None:
            semaphores.append(stage_semaphore)
        if priority_semaphore is not None:
            semaphores.append(priority_semaphore)
        for semaphore in semaphores:
            await semaphore.acquire()
        started = perf_counter()
        attempt = 0
        try:
            await self._buckets[tier].acquire()
            while True:
                attempt += 1
                try:
                    result = await self._clients[tier].acomplete(request)
                    self._record(
                        tier=tier,
                        stage=stage,
                        priority=priority,
                        queued_at_ms=queued_at_ms,
                        started=started,
                        attempt=attempt,
                        status="SUCCEEDED",
                        error_code=None,
                    )
                    return result
                except Exception as exc:
                    if attempt <= self._max_retries and _provider_pressure(exc):
                        retry_after = getattr(exc, "retry_after", None)
                        delay = float(retry_after) if isinstance(retry_after, (int, float)) else 1.0
                        await asyncio.sleep(max(0.1, min(delay, 10.0)))
                        continue
                    self._record(
                        tier=tier,
                        stage=stage,
                        priority=priority,
                        queued_at_ms=queued_at_ms,
                        started=started,
                        attempt=attempt,
                        status="FAILED",
                        error_code=str(getattr(exc, "code", type(exc).__name__)),
                    )
                    raise
        finally:
            for semaphore in reversed(semaphores):
                semaphore.release()

    def _record(
        self,
        *,
        tier: ModelTier,
        stage: str,
        priority: str,
        queued_at_ms: int,
        started: float,
        attempt: int,
        status: str,
        error_code: str | None,
    ) -> None:
        finished = perf_counter()
        item = AsyncCallTelemetry(
            tier=tier.value,
            stage=stage,
            priority=priority,
            queued_at_ms=queued_at_ms,
            started_at_ms=round(started * 1000),
            finished_at_ms=round(finished * 1000),
            queue_wait_ms=max(0, round((started * 1000) - queued_at_ms)),
            attempt_count=attempt,
            status=status,
            error_code=error_code,
        )
        with self._telemetry_lock:
            self._telemetry.append(item)

    def complete(self, tier: ModelTier, request: StructuredModelRequest) -> StructuredModelResult:
        if self._closed:
            raise RuntimeError("bulk async model executor is closed")
        future = asyncio.run_coroutine_threadsafe(self._complete(tier, request), self._loop)
        return future.result()

    def telemetry(self) -> list[AsyncCallTelemetry]:
        with self._telemetry_lock:
            return list(self._telemetry)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=15)

    def __enter__(self) -> AsyncModelExecutor:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def client(self, tier: ModelTier) -> BlockingAsyncStructuredClient:
        return BlockingAsyncStructuredClient(executor=self, tier=tier)


class BlockingAsyncStructuredClient:
    """Synchronous business-port facade backed by the shared async executor."""

    def __init__(self, *, executor: AsyncModelExecutor, tier: ModelTier) -> None:
        self.executor = executor
        self.tier = tier
        self.model = executor._clients[tier].model

    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        return self.executor.complete(self.tier, request)
