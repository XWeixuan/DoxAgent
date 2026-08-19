"""Shared native-async structured-model executor for BULK_EPOCH."""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from time import monotonic, perf_counter
from typing import Protocol

from cdecr.models import ModelTier
from cdecr.ports import StructuredModelRequest, StructuredModelResult
from cdecr.provider_resilience import (
    classify_provider_error,
    is_provider_pressure,
    is_retryable_provider_failure,
    provider_retry_delay,
)
from cdecr.scheduler import StructuredProviderGate


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


@dataclass(frozen=True)
class AsyncAttemptTelemetry:
    tier: str
    stage: str
    priority: str
    attempt_index: int
    started_at_ms: int
    finished_at_ms: int
    provider_wait_ms: int
    backoff_ms: int
    status: str
    error_class: str | None
    error_code: str | None
    provider_key_fingerprint: str | None


@dataclass(frozen=True)
class AsyncProviderSnapshot:
    target: int
    hard_limit: int
    limit: int
    active: int
    max_active: int
    completed: int


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


class _AdaptiveLimiter:
    def __init__(self, limit: int) -> None:
        self.maximum = max(1, limit)
        self.limit = self.maximum
        self.active = 0
        self.successes = 0
        self.outcomes: deque[bool] = deque(maxlen=100)
        self.condition = asyncio.Condition()

    async def acquire(self) -> None:
        async with self.condition:
            while self.active >= self.limit:
                await self.condition.wait()
            self.active += 1

    async def release(self) -> None:
        async with self.condition:
            self.active -= 1
            self.condition.notify_all()

    async def pressure(self) -> None:
        async with self.condition:
            self.outcomes.append(False)
            self.successes = 0
            error_rate = 1.0 - (sum(self.outcomes) / len(self.outcomes))
            factor = 0.6 if error_rate > 0.03 else 0.8
            self.limit = max(1, int(self.limit * factor))

    async def success(self) -> None:
        async with self.condition:
            self.outcomes.append(True)
            self.successes += 1
            if self.successes >= 100:
                self.successes = 0
                self.limit = min(self.maximum, self.limit + 8)
                self.condition.notify_all()


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
        provider_target: int = 100,
        provider_hard_limit: int = 160,
        provider_start_rate: float = 50.0,
        provider_initial_burst: int = 80,
        retry_limit: int = 16,
        max_retries: int = 1,
    ) -> None:
        self._clients = dict(clients)
        self._tier_limits = dict(tier_limits)
        self._stage_limits = dict(stage_limits)
        self._repair_limit = max(1, repair_limit)
        self._rates = dict(rates)
        if provider_target < 1 or provider_hard_limit < provider_target:
            raise ValueError("provider concurrency must satisfy 1 <= target <= hard_limit")
        self._provider_target = provider_target
        self._provider_hard_limit = provider_hard_limit
        self._provider_start_rate = provider_start_rate
        self._provider_initial_burst = provider_initial_burst
        self._retry_limit = max(1, retry_limit)
        self._max_retries = max(0, max_retries)
        self._telemetry: list[AsyncCallTelemetry] = []
        self._attempt_telemetry: list[AsyncAttemptTelemetry] = []
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
            tier: _AdaptiveLimiter(limit) for tier, limit in self._tier_limits.items()
        }
        self._stage_semaphores = {
            stage: asyncio.Semaphore(limit) for stage, limit in self._stage_limits.items()
        }
        self._repair_semaphore = asyncio.Semaphore(self._repair_limit)
        self._retry_semaphore = asyncio.Semaphore(self._retry_limit)
        self._provider_gates: dict[str, StructuredProviderGate] = {}
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
        semaphores: list[asyncio.Semaphore] = []
        if stage_semaphore is not None:
            semaphores.append(stage_semaphore)
        if priority_semaphore is not None:
            semaphores.append(priority_semaphore)
        await tier_semaphore.acquire()
        for semaphore in semaphores:
            await semaphore.acquire()
        started = perf_counter()
        attempt = 0
        total_backoff_ms = 0
        try:
            await self._buckets[tier].acquire()
            while True:
                attempt += 1
                provider_gate = self._provider_gates.setdefault(
                    stage,
                    StructuredProviderGate(
                        target=self._provider_target,
                        hard_limit=self._provider_hard_limit,
                        start_rate=self._provider_start_rate,
                        burst=self._provider_initial_burst,
                    ),
                )
                provider_wait_started = perf_counter()
                await asyncio.to_thread(provider_gate.acquire)
                provider_wait_ms = round((perf_counter() - provider_wait_started) * 1000)
                attempt_started = perf_counter()
                provider_succeeded = False
                try:
                    result = await self._clients[tier].acomplete(request)
                    provider_succeeded = True
                    self._record_attempt(
                        tier=tier,
                        stage=stage,
                        priority=priority,
                        attempt=attempt,
                        started=attempt_started,
                        provider_wait_ms=provider_wait_ms,
                        backoff_ms=total_backoff_ms,
                        status="SUCCEEDED",
                        error=None,
                        provider_key_fingerprint=result.provider_key_fingerprint,
                    )
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
                    self._record_attempt(
                        tier=tier,
                        stage=stage,
                        priority=priority,
                        attempt=attempt,
                        started=attempt_started,
                        provider_wait_ms=provider_wait_ms,
                        backoff_ms=total_backoff_ms,
                        status="FAILED",
                        error=exc,
                        provider_key_fingerprint=getattr(exc, "provider_key_fingerprint", None),
                    )
                    if is_provider_pressure(exc):
                        provider_gate.pressure()
                    if attempt <= self._max_retries and is_retryable_provider_failure(exc):
                        retry_after = getattr(exc, "retry_after", None)
                        delay = (
                            float(retry_after)
                            if isinstance(retry_after, (int, float))
                            else provider_retry_delay(attempt)
                        )
                        async with self._retry_semaphore:
                            await asyncio.sleep(max(0.1, min(delay, 10.0)))
                        total_backoff_ms += round(max(0.1, min(delay, 10.0)) * 1000)
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
                    provider_gate.release(succeeded=provider_succeeded)
        finally:
            for semaphore in reversed(semaphores):
                semaphore.release()
            await tier_semaphore.release()

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

    def _record_attempt(
        self,
        *,
        tier: ModelTier,
        stage: str,
        priority: str,
        attempt: int,
        started: float,
        provider_wait_ms: int,
        backoff_ms: int,
        status: str,
        error: Exception | None,
        provider_key_fingerprint: str | None,
    ) -> None:
        finished = perf_counter()
        item = AsyncAttemptTelemetry(
            tier=tier.value,
            stage=stage,
            priority=priority,
            attempt_index=attempt,
            started_at_ms=round(started * 1000),
            finished_at_ms=round(finished * 1000),
            provider_wait_ms=provider_wait_ms,
            backoff_ms=backoff_ms,
            status=status,
            error_class=(classify_provider_error(error).value if error is not None else None),
            error_code=(
                str(getattr(error, "code", type(error).__name__))
                if error is not None
                else None
            ),
            provider_key_fingerprint=provider_key_fingerprint,
        )
        with self._telemetry_lock:
            self._attempt_telemetry.append(item)

    def complete(self, tier: ModelTier, request: StructuredModelRequest) -> StructuredModelResult:
        if self._closed:
            raise RuntimeError("bulk async model executor is closed")
        future = asyncio.run_coroutine_threadsafe(self._complete(tier, request), self._loop)
        return future.result()

    def complete_many(
        self,
        tier: ModelTier,
        requests: Sequence[StructuredModelRequest],
    ) -> list[StructuredModelResult | Exception]:
        """Submit a whole stage wave to the shared native-async hub."""

        if self._closed:
            raise RuntimeError("bulk async model executor is closed")

        async def gather() -> list[StructuredModelResult | Exception]:
            values = await asyncio.gather(
                *(self._complete(tier, request) for request in requests),
                return_exceptions=True,
            )
            normalized: list[StructuredModelResult | Exception] = []
            for value in values:
                if isinstance(value, BaseException) and not isinstance(value, Exception):
                    normalized.append(RuntimeError(f"{type(value).__name__}: {value}"))
                else:
                    normalized.append(value)
            return normalized

        future = asyncio.run_coroutine_threadsafe(gather(), self._loop)
        return future.result()

    def telemetry(self) -> list[AsyncCallTelemetry]:
        with self._telemetry_lock:
            return list(self._telemetry)

    def attempt_telemetry(self) -> list[AsyncAttemptTelemetry]:
        with self._telemetry_lock:
            return list(self._attempt_telemetry)

    def provider_stage_snapshots(self) -> dict[str, dict[str, int]]:
        return {
            stage: {
                "target": snapshot.target,
                "hard_limit": snapshot.hard_limit,
                "limit": snapshot.limit,
                "active": snapshot.active,
                "max_active": snapshot.max_active,
                "completed": snapshot.completed,
                "pressure_events": snapshot.pressure_events,
            }
            for stage, gate in sorted(self._provider_gates.items())
            for snapshot in [gate.snapshot()]
        }

    def capacity_config(self) -> dict[str, object]:
        return {
            "tier_limits": {
                tier.value: limit for tier, limit in sorted(
                    self._tier_limits.items(), key=lambda item: item[0].value
                )
            },
            "stage_limits": dict(sorted(self._stage_limits.items())),
            "repair_limit": self._repair_limit,
            "retry_limit": self._retry_limit,
            "provider_target": self._provider_target,
            "provider_hard_limit": self._provider_hard_limit,
            "provider_start_rate": self._provider_start_rate,
            "provider_initial_burst": self._provider_initial_burst,
            "provider_scope": "stage_local",
        }

    async def _provider_snapshot(self) -> AsyncProviderSnapshot:
        snapshots = [gate.snapshot() for gate in self._provider_gates.values()]
        return AsyncProviderSnapshot(
            target=self._provider_target,
            hard_limit=self._provider_hard_limit,
            limit=min((item.limit for item in snapshots), default=self._provider_hard_limit),
            active=sum(item.active for item in snapshots),
            max_active=max((item.max_active for item in snapshots), default=0),
            completed=sum(item.completed for item in snapshots),
        )

    def provider_snapshot(self) -> AsyncProviderSnapshot:
        if self._closed:
            snapshots = [gate.snapshot() for gate in self._provider_gates.values()]
            return AsyncProviderSnapshot(
                target=self._provider_target,
                hard_limit=self._provider_hard_limit,
                limit=min(
                    (item.limit for item in snapshots), default=self._provider_hard_limit
                ),
                active=0,
                max_active=max((item.max_active for item in snapshots), default=0),
                completed=sum(item.completed for item in snapshots),
            )
        future = asyncio.run_coroutine_threadsafe(self._provider_snapshot(), self._loop)
        return future.result()

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

    def complete_many(
        self, requests: Sequence[StructuredModelRequest]
    ) -> list[StructuredModelResult | Exception]:
        return self.executor.complete_many(self.tier, requests)
