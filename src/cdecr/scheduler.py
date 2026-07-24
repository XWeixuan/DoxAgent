"""Process-local concurrency lanes for CDECR model requests."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from threading import BoundedSemaphore, Lock, local
from time import perf_counter
from typing import TypeVar

from cdecr.models import ModelTier
from cdecr.ports import (
    EmbeddingClient,
    EmbeddingResult,
    StructuredModelClient,
    StructuredModelRequest,
    StructuredModelResult,
)

_T = TypeVar("_T")


@dataclass(frozen=True)
class ScheduledCallMetrics:
    lane: str
    queued_at_ms: int
    started_at_ms: int
    finished_at_ms: int
    queue_wait_ms: int


@dataclass(frozen=True)
class LaneSnapshot:
    limit: int
    active: int
    max_active: int
    completed: int
    total_queue_wait_ms: int


class ConcurrencyLane:
    """Bound concurrent calls and retain aggregate queue telemetry."""

    def __init__(self, name: str, limit: int) -> None:
        if limit < 1:
            raise ValueError("lane limit must be positive")
        self.name = name
        self.limit = limit
        self._semaphore = BoundedSemaphore(limit)
        self._lock = Lock()
        self._call_state = local()
        self._active = 0
        self._max_active = 0
        self._completed = 0
        self._total_queue_wait_ms = 0

    def run(self, operation: Callable[[], _T]) -> tuple[_T, ScheduledCallMetrics]:
        origin = perf_counter()
        queued_at_ms = round(origin * 1000)
        self._semaphore.acquire()
        started = perf_counter()
        queue_wait_ms = max(0, round((started - origin) * 1000))
        with self._lock:
            self._active += 1
            self._max_active = max(self._max_active, self._active)
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
            with self._lock:
                self._active -= 1
                self._completed += 1
                self._total_queue_wait_ms += queue_wait_ms
            self._semaphore.release()
        return value, metrics

    def take_last_call_metrics(self) -> ScheduledCallMetrics | None:
        metrics = getattr(self._call_state, "metrics", None)
        self._call_state.metrics = None
        return metrics

    def snapshot(self) -> LaneSnapshot:
        with self._lock:
            return LaneSnapshot(
                limit=self.limit,
                active=self._active,
                max_active=self._max_active,
                completed=self._completed,
                total_queue_wait_ms=self._total_queue_wait_ms,
            )


class CDECRScheduler:
    """One scheduler shared by document and cross-document processors."""

    def __init__(
        self,
        *,
        m1_limit: int = 2,
        m2_limit: int = 6,
        m3_limit: int = 3,
        m4_limit: int = 2,
    ) -> None:
        self._lanes = {
            ModelTier.M1: ConcurrencyLane(ModelTier.M1.value, m1_limit),
            ModelTier.M2: ConcurrencyLane(ModelTier.M2.value, m2_limit),
            ModelTier.M3: ConcurrencyLane(ModelTier.M3.value, m3_limit),
            ModelTier.M4: ConcurrencyLane(ModelTier.M4.value, m4_limit),
        }

    def run(
        self, tier: ModelTier, operation: Callable[[], _T]
    ) -> tuple[_T, ScheduledCallMetrics]:
        return self._lanes[tier].run(operation)

    def snapshot(self) -> dict[str, LaneSnapshot]:
        return {tier.value: lane.snapshot() for tier, lane in self._lanes.items()}

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
        try:
            result, metrics = self.scheduler.run(
                self.tier, lambda: self.client.complete(request)
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


def take_scheduled_call_metrics(client: object) -> ScheduledCallMetrics | None:
    getter = getattr(client, "take_last_call_metrics", None)
    return getter() if callable(getter) else None


def _take_lane_failure_metrics(
    scheduler: CDECRScheduler, tier: ModelTier
) -> ScheduledCallMetrics | None:
    return scheduler.take_last_call_metrics(tier)
