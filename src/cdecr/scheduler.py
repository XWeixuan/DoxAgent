"""Process-local concurrency lanes for CDECR model requests."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from threading import Condition, Lock, local
from time import monotonic, perf_counter, sleep
from typing import TypeVar, cast

from cdecr.models import ModelTier
from cdecr.ports import (
    EmbeddingClient,
    EmbeddingResult,
    ResponsesModelRequest,
    StructuredModelClient,
    StructuredModelRequest,
    StructuredModelResult,
)
from cdecr.provider_resilience import (
    is_provider_pressure,
    is_retryable_provider_failure,
    provider_retry_delay,
)

_T = TypeVar("_T")


@dataclass(frozen=True)
class ScheduledCallMetrics:
    lane: str
    queued_at_ms: int
    started_at_ms: int
    finished_at_ms: int
    queue_wait_ms: int
    attempt_count: int = 1
    provider_wait_ms: int = 0
    backoff_ms: int = 0


@dataclass(frozen=True)
class LaneSnapshot:
    limit: int
    active: int
    max_active: int
    completed: int
    total_queue_wait_ms: int


@dataclass(frozen=True)
class ProviderSnapshot:
    target: int
    hard_limit: int
    limit: int
    active: int
    max_active: int
    completed: int
    pressure_events: int


class RequestStartGate:
    """Smooth structured-request starts without serializing in-flight work."""

    def __init__(self, interval_seconds: float) -> None:
        self.base_interval_seconds = max(0.0, interval_seconds)
        self._interval_seconds = self.base_interval_seconds
        self._lock = Lock()
        self._next_start = 0.0
        self._sequence = 0

    def wait(self) -> None:
        if self.base_interval_seconds <= 0:
            return
        with self._lock:
            now = monotonic()
            self._sequence += 1
            jitter = min(0.25, self._interval_seconds * 0.25) * (
                (self._sequence % 5) / 4
            )
            reserved = max(now, self._next_start)
            self._next_start = reserved + self._interval_seconds + jitter
            delay = max(0.0, reserved - now)
        if delay:
            sleep(delay)

    def backoff(self) -> None:
        with self._lock:
            self._interval_seconds = max(2.0, self._interval_seconds)

    def recover(self) -> None:
        with self._lock:
            self._interval_seconds = self.base_interval_seconds


class StructuredProviderGate:
    """Work-conserving provider guard with a soft target and a separate safety ceiling."""

    def __init__(
        self,
        *,
        target: int,
        hard_limit: int,
        start_rate: float,
        burst: int,
    ) -> None:
        if target < 1 or hard_limit < target:
            raise ValueError("provider concurrency must satisfy 1 <= target <= hard_limit")
        self.target = target
        self.hard_limit = hard_limit
        self.minimum_limit = max(1, target // 2)
        self._condition = Condition(Lock())
        self._active = 0
        self._max_active = 0
        self._completed = 0
        self._pressure_events = 0
        # Start at the safety ceiling; target is the normal recovery ceiling.
        # With the production defaults both are 100, while custom callers retain
        # the historical hard-limit behavior.
        self._dynamic_limit = self.hard_limit
        self._successes = 0
        self._start_rate = max(1.0, start_rate)
        self._burst = max(1, burst)
        self._tokens = float(self._burst)
        self._tokens_updated_at = monotonic()
        self._start_lock = Lock()
        self._sequence = 0

    def _wait_for_start(self) -> None:
        while True:
            with self._start_lock:
                now = monotonic()
                elapsed = max(0.0, now - self._tokens_updated_at)
                self._tokens = min(self._burst, self._tokens + elapsed * self._start_rate)
                self._tokens_updated_at = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    self._sequence += 1
                    jitter = (self._sequence % 3) * 0.01
                    break
                delay = (1.0 - self._tokens) / self._start_rate
            sleep(delay)
        sleep(jitter)

    def acquire(self) -> None:
        self._wait_for_start()
        with self._condition:
            while self._active >= self._dynamic_limit:
                self._condition.wait()
            self._active += 1
            self._max_active = max(self._max_active, self._active)

    def pressure(self) -> None:
        with self._condition:
            self._pressure_events += 1
            self._dynamic_limit = max(self.minimum_limit, int(self._dynamic_limit * 0.8))
            self._successes = 0

    def release(self, *, succeeded: bool) -> None:
        with self._condition:
            self._active -= 1
            self._completed += 1
            if succeeded:
                self._successes += 1
                if self._successes >= 10 and self._dynamic_limit < self.target:
                    self._dynamic_limit = min(self.target, self._dynamic_limit + 10)
                    self._successes = 0
            self._condition.notify_all()

    def run(self, operation: Callable[[], _T]) -> _T:
        self.acquire()
        succeeded = False
        try:
            value = operation()
            succeeded = True
            return value
        except Exception as exc:
            if is_provider_pressure(exc):
                self.pressure()
            raise
        finally:
            self.release(succeeded=succeeded)

    def snapshot(self) -> ProviderSnapshot:
        with self._condition:
            return ProviderSnapshot(
                target=self.target,
                hard_limit=self.hard_limit,
                limit=self._dynamic_limit,
                active=self._active,
                max_active=self._max_active,
                completed=self._completed,
                pressure_events=self._pressure_events,
            )

    def reset_capacity(self) -> None:
        with self._condition:
            self._dynamic_limit = self.hard_limit
            self._successes = 0
            self._condition.notify_all()


class ConcurrencyLane:
    """Bound concurrent calls and retain aggregate queue telemetry."""

    def __init__(self, name: str, limit: int) -> None:
        if limit < 1:
            raise ValueError("lane limit must be positive")
        self.name = name
        self.limit = limit
        self._dynamic_limit = limit
        self._condition = Condition(Lock())
        self._call_state = local()
        self._active = 0
        self._max_active = 0
        self._completed = 0
        self._total_queue_wait_ms = 0

    def run(self, operation: Callable[[], _T]) -> tuple[_T, ScheduledCallMetrics]:
        origin = perf_counter()
        queued_at_ms = round(origin * 1000)
        with self._condition:
            while self._active >= self._dynamic_limit:
                self._condition.wait()
            self._active += 1
            self._max_active = max(self._max_active, self._active)
        started = perf_counter()
        queue_wait_ms = max(0, round((started - origin) * 1000))
        try:
            value = operation()
        finally:
            finished = perf_counter()
            metrics = ScheduledCallMetrics(
                lane=self.name,
                queued_at_ms=queued_at_ms,
                started_at_ms=round(started * 1000),
                finished_at_ms=round(finished * 1000),
                queue_wait_ms=queue_wait_ms,
            )
            self._call_state.metrics = metrics
            with self._condition:
                self._active -= 1
                self._completed += 1
                self._total_queue_wait_ms += queue_wait_ms
                self._condition.notify_all()
        return value, metrics

    def take_last_call_metrics(self) -> ScheduledCallMetrics | None:
        metrics = getattr(self._call_state, "metrics", None)
        self._call_state.metrics = None
        return metrics

    def snapshot(self) -> LaneSnapshot:
        with self._condition:
            return LaneSnapshot(
                limit=self._dynamic_limit,
                active=self._active,
                max_active=self._max_active,
                completed=self._completed,
                total_queue_wait_ms=self._total_queue_wait_ms,
            )

    def reduce_limit(self, factor: float = 0.5) -> None:
        with self._condition:
            self._dynamic_limit = max(1, int(self._dynamic_limit * factor))

    def increase_limit(self, amount: int = 2) -> None:
        with self._condition:
            self._dynamic_limit = min(self.limit, self._dynamic_limit + amount)
            self._condition.notify_all()


class CDECRScheduler:
    """One scheduler shared by document and cross-document processors."""

    def __init__(
        self,
        *,
        m1_limit: int = 2,
        m2_limit: int = 6,
        m3_limit: int = 3,
        m4_limit: int = 2,
        structured_start_interval_seconds: float = 0.0,
        structured_provider_target: int = 100,
        structured_provider_hard_limit: int = 160,
        structured_provider_start_rate: float = 50.0,
        structured_provider_initial_burst: int = 80,
        stage_limits: Mapping[str, int] | None = None,
        repair_limit: int = 16,
        provider_gate: StructuredProviderGate | None = None,
        max_retries: int = 1,
    ) -> None:
        self._lanes = {
            ModelTier.M1: ConcurrencyLane(ModelTier.M1.value, m1_limit),
            ModelTier.M2: ConcurrencyLane(ModelTier.M2.value, m2_limit),
            ModelTier.M3: ConcurrencyLane(ModelTier.M3.value, m3_limit),
            ModelTier.M4: ConcurrencyLane(ModelTier.M4.value, m4_limit),
        }
        self._structured_start_gate = RequestStartGate(
            structured_start_interval_seconds
        )
        self._provider_gate = provider_gate or StructuredProviderGate(
            target=structured_provider_target,
            hard_limit=structured_provider_hard_limit,
            start_rate=structured_provider_start_rate,
            burst=structured_provider_initial_burst,
        )
        self._max_retries = max(0, min(1, max_retries))
        self._stage_lanes = {
            stage: ConcurrencyLane(stage, limit)
            for stage, limit in (stage_limits or {}).items()
        }
        self._repair_lane = ConcurrencyLane("structured_repair", repair_limit)

    def run(
        self,
        tier: ModelTier,
        operation: Callable[[], _T],
        *,
        stage: str | None = None,
        priority: str = "normal",
    ) -> tuple[_T, ScheduledCallMetrics]:
        structured = tier is not ModelTier.M1
        if structured:
            self._structured_start_gate.wait()
        attempt_count = 0
        provider_wait_ms = 0
        backoff_ms = 0
        try:
            def provider_operation() -> _T:
                nonlocal attempt_count, provider_wait_ms, backoff_ms
                while True:
                    attempt_count += 1
                    provider_started = perf_counter()
                    try:
                        if structured:
                            self._provider_gate.acquire()
                            provider_wait_ms += round(
                                (perf_counter() - provider_started) * 1000
                            )
                            succeeded = False
                            try:
                                value = operation()
                                succeeded = True
                                return value
                            except Exception as exc:
                                if is_provider_pressure(exc):
                                    self._provider_gate.pressure()
                                raise
                            finally:
                                self._provider_gate.release(succeeded=succeeded)
                        return operation()
                    except Exception as exc:
                        if (
                            not structured
                            or attempt_count > self._max_retries
                            or not is_retryable_provider_failure(exc)
                        ):
                            raise
                        delay = provider_retry_delay(attempt_count)
                        backoff_ms += round(delay * 1000)
                        sleep(delay)

            wrapped: Callable[[], _T] = provider_operation
            if structured:
                stage_lane = self._stage_lanes.get(stage or "")
                if stage_lane is not None:
                    previous = wrapped

                    def stage_operation(
                        previous_operation: Callable[[], _T] = previous,
                    ) -> _T:
                        assert stage_lane is not None
                        return stage_lane.run(previous_operation)[0]

                    wrapped = stage_operation
                if priority == "repair":
                    previous = wrapped

                    def repair_operation(
                        previous_operation: Callable[[], _T] = previous,
                    ) -> _T:
                        return self._repair_lane.run(previous_operation)[0]

                    wrapped = repair_operation
            result = self._lanes[tier].run(wrapped)
        except Exception as exc:
            if structured and is_provider_pressure(exc):
                self._structured_start_gate.backoff()
            raise
        if structured:
            self._structured_start_gate.recover()
        value, metrics = result
        return value, replace(
            metrics,
            attempt_count=max(1, attempt_count),
            provider_wait_ms=provider_wait_ms,
            backoff_ms=backoff_ms,
        )

    def snapshot(self) -> dict[str, LaneSnapshot]:
        return {tier.value: lane.snapshot() for tier, lane in self._lanes.items()}

    def provider_snapshot(self) -> ProviderSnapshot:
        return self._provider_gate.snapshot()

    @property
    def provider_gate(self) -> StructuredProviderGate:
        return self._provider_gate

    def take_last_call_metrics(self, tier: ModelTier) -> ScheduledCallMetrics | None:
        return self._lanes[tier].take_last_call_metrics()

    def embedding_client(self, client: EmbeddingClient) -> ScheduledEmbeddingClient:
        return ScheduledEmbeddingClient(client=client, scheduler=self)

    def structured_client(
        self, client: StructuredModelClient, *, tier: ModelTier
    ) -> ScheduledStructuredModelClient:
        return ScheduledStructuredModelClient(client=client, scheduler=self, tier=tier)


class _ScheduledClient:
    def __init__(self) -> None:
        self._call_state = local()

    def _store_metrics(self, metrics: ScheduledCallMetrics) -> None:
        self._call_state.metrics = metrics

    def take_last_call_metrics(self) -> ScheduledCallMetrics | None:
        metrics = getattr(self._call_state, "metrics", None)
        self._call_state.metrics = None
        return metrics


class ScheduledEmbeddingClient(_ScheduledClient):
    def __init__(self, *, client: EmbeddingClient, scheduler: CDECRScheduler) -> None:
        super().__init__()
        self.client = client
        self.scheduler = scheduler
        self.model = getattr(client, "model", "embedding-model")

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        try:
            result, metrics = self.scheduler.run(
                ModelTier.M1, lambda: self.client.embed(texts)
            )
        except Exception:
            failure_metrics = _take_lane_failure_metrics(
                self.scheduler, ModelTier.M1
            )
            if failure_metrics is not None:
                self._store_metrics(failure_metrics)
            raise
        self._store_metrics(metrics)
        return result


class ScheduledStructuredModelClient(_ScheduledClient):
    def __init__(
        self,
        *,
        client: StructuredModelClient,
        scheduler: CDECRScheduler,
        tier: ModelTier,
    ) -> None:
        super().__init__()
        self.client = client
        self.scheduler = scheduler
        self.tier = tier
        self.model = getattr(client, "model", "structured-model")

    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        stage = str(request.metadata.get("stage", "unspecified"))
        priority = str(request.metadata.get("priority", "normal"))
        try:
            result, metrics = self.scheduler.run(
                self.tier,
                lambda: self.client.complete(request),
                stage=stage,
                priority=priority,
            )
        except Exception:
            failure_metrics = _take_lane_failure_metrics(
                self.scheduler, self.tier
            )
            if failure_metrics is not None:
                self._store_metrics(failure_metrics)
            raise
        self._store_metrics(metrics)
        return result

    def complete_response(self, request: ResponsesModelRequest) -> StructuredModelResult:
        stage = str(request.metadata.get("stage", "unspecified"))
        priority = str(request.metadata.get("priority", "normal"))
        responses_client = self.client
        complete_response = getattr(responses_client, "complete_response", None)
        if not callable(complete_response):
            raise TypeError("scheduled structured client does not support Responses")
        try:
            result, metrics = self.scheduler.run(
                self.tier,
                lambda: complete_response(request),
                stage=stage,
                priority=priority,
            )
        except Exception:
            failure_metrics = _take_lane_failure_metrics(self.scheduler, self.tier)
            if failure_metrics is not None:
                self._store_metrics(failure_metrics)
            raise
        self._store_metrics(metrics)
        return cast(StructuredModelResult, result)


def take_scheduled_call_metrics(client: object) -> ScheduledCallMetrics | None:
    getter = getattr(client, "take_last_call_metrics", None)
    return getter() if callable(getter) else None


def _take_lane_failure_metrics(
    scheduler: CDECRScheduler, tier: ModelTier
) -> ScheduledCallMetrics | None:
    return scheduler.take_last_call_metrics(tier)
