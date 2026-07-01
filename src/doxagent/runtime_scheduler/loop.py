"""Minimal long-running loop for the unified runtime scheduler."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Event
from typing import Protocol

from doxagent.runtime_scheduler.schema import TickerRunDetail


class SchedulerLoopService(Protocol):
    def run_due_once(
        self,
        *,
        now: datetime | None = None,
        event_limit: int = 100,
    ) -> list[TickerRunDetail]:
        ...


@dataclass(frozen=True)
class RuntimeLoopCycle:
    iteration: int
    generated_at: datetime
    ticker_count: int = 0
    tickers: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class RuntimeLoopSummary:
    started_at: datetime
    stopped_at: datetime
    iteration_count: int
    failure_count: int
    cycles: list[RuntimeLoopCycle] = field(default_factory=list)


class RuntimeSchedulerLoop:
    """Run scheduler ticks until stopped.

    This is intentionally a thin service wrapper: durable state, restart recovery,
    event idempotency, and trade-intent idempotency remain owned by the existing
    scheduler, Monitoring Message Bus, and Persistent Runtime repositories.
    """

    def __init__(
        self,
        scheduler: SchedulerLoopService,
        *,
        sleep_seconds: float = 15.0,
        event_limit: int = 100,
        stop_event: Event | None = None,
    ) -> None:
        if sleep_seconds < 0:
            raise ValueError("sleep_seconds must be >= 0.")
        if event_limit <= 0:
            raise ValueError("event_limit must be > 0.")
        self.scheduler = scheduler
        self.sleep_seconds = sleep_seconds
        self.event_limit = event_limit
        self.stop_event = stop_event or Event()

    def stop(self) -> None:
        self.stop_event.set()

    def run(
        self,
        *,
        immediate: bool = True,
        max_iterations: int | None = None,
        now_fn: Callable[[], datetime | None] | None = None,
        on_cycle: Callable[[RuntimeLoopCycle], None] | None = None,
    ) -> RuntimeLoopSummary:
        if max_iterations is not None and max_iterations <= 0:
            raise ValueError("max_iterations must be > 0 when provided.")
        started_at = datetime.now(UTC)
        cycles: list[RuntimeLoopCycle] = []
        iteration_count = 0
        failure_count = 0
        should_tick = immediate
        while not self.stop_event.is_set():
            if not should_tick:
                if self.stop_event.wait(self.sleep_seconds):
                    break
                should_tick = True
                continue
            iteration = iteration_count + 1
            try:
                details = self.scheduler.run_due_once(
                    now=now_fn() if now_fn is not None else None,
                    event_limit=self.event_limit,
                )
                cycle = RuntimeLoopCycle(
                    iteration=iteration,
                    generated_at=datetime.now(UTC),
                    ticker_count=len(details),
                    tickers=[detail.state.ticker for detail in details],
                )
            except Exception as exc:
                failure_count += 1
                cycle = RuntimeLoopCycle(
                    iteration=iteration,
                    generated_at=datetime.now(UTC),
                    error=str(exc),
                )
            cycles.append(cycle)
            if on_cycle is not None:
                on_cycle(cycle)
            iteration_count = iteration
            if max_iterations is not None and iteration_count >= max_iterations:
                break
            if self.stop_event.wait(self.sleep_seconds):
                break
            should_tick = True
        return RuntimeLoopSummary(
            started_at=started_at,
            stopped_at=datetime.now(UTC),
            iteration_count=iteration_count,
            failure_count=failure_count,
            cycles=cycles,
        )
