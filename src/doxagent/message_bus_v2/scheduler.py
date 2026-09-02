"""Global, scheduler-group-aware Message Bus v2 poll scheduler."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from doxagent.message_bus_v2.adapters import AdapterRegistry
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import (
    AlertSeverity,
    OperationalAlert,
    PollContext,
    PollExecutionResult,
    PollStatus,
    SchedulerConstraints,
    SourceDefinition,
    TickerMonitoringStatus,
    TickerSourceBinding,
    canonical_json,
    new_id,
    sha256_text,
    utc_now,
)
from doxagent.message_bus_v2.service import MessageBusV2Service

MonotonicClock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]


class SchedulerGroupLimiter:
    """Enforce group max concurrency and minimum gap per actual request."""

    def __init__(
        self,
        constraints: SchedulerConstraints,
        *,
        clock: MonotonicClock = time.monotonic,
        sleep: Sleeper = asyncio.sleep,
        on_request: Callable[[], None] | None = None,
        initial_delay_seconds: float = 0.0,
    ) -> None:
        self.constraints = constraints
        self._clock = clock
        self._sleep = sleep
        self._semaphore = asyncio.Semaphore(constraints.max_concurrency)
        self._spacing_lock = asyncio.Lock()
        self._next_request_at = self._clock() + max(0.0, initial_delay_seconds)
        self._on_request = on_request

    @asynccontextmanager
    async def permit(self) -> AsyncIterator[None]:
        async with self._semaphore:
            async with self._spacing_lock:
                now = self._clock()
                delay = max(0.0, self._next_request_at - now)
                if delay:
                    await self._sleep(delay)
                request_at = self._clock()
                self._next_request_at = request_at + self.constraints.minimum_request_gap_seconds
                if self._on_request:
                    self._on_request()
            yield


class GlobalPollScheduler:
    def __init__(
        self,
        repository: MessageBusV2Repository,
        service: MessageBusV2Service,
        adapters: AdapterRegistry,
        *,
        clock: MonotonicClock = time.monotonic,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self.repository = repository
        self.service = service
        self.adapters = adapters
        self._clock = clock
        self._sleep = sleep
        self._limiters: dict[str, tuple[SchedulerConstraints, SchedulerGroupLimiter]] = {}

    async def run_once(self) -> list[PollExecutionResult]:
        now = utc_now()
        eligible = self._eligible_bindings(now)
        self._initialize_due_slots(eligible, now)
        self._update_capacity_alerts(eligible)
        due: list[tuple[SourceDefinition, TickerSourceBinding]] = []
        for source, binding in eligible:
            state = self.repository.get_poll_state(binding)
            if state.next_dispatch_at is None or state.next_dispatch_at <= now:
                due.append((source, binding))
        tasks = [asyncio.create_task(self._poll(source, binding, now)) for source, binding in due]
        results = await asyncio.gather(*tasks) if tasks else []
        self.service.flush_due_buffers(now=now)
        self.service.retry_pending_raw()
        return list(results)

    async def run_forever(self, *, loop_sleep_seconds: float, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(stop.wait(), timeout=loop_sleep_seconds)
            except TimeoutError:
                pass

    def _eligible_bindings(
        self, now: datetime
    ) -> list[tuple[SourceDefinition, TickerSourceBinding]]:
        instant = now
        states = {state.ticker: state for state in self.repository.list_ticker_states()}
        result: list[tuple[SourceDefinition, TickerSourceBinding]] = []
        for binding in self.repository.list_bindings(active_only=True):
            ticker_state = states.get(binding.ticker)
            source = self.repository.get_source(binding.source_id)
            if (
                ticker_state is None
                or ticker_state.status is not TickerMonitoringStatus.RUNNING
                or source is None
                or not source.enabled
                or not binding.polling.enabled
            ):
                self._mark_disabled(binding)
                continue
            if not binding.polling.active_at(instant):
                continue
            result.append((source, binding))
        return result

    def _initialize_due_slots(
        self,
        bindings: list[tuple[SourceDefinition, TickerSourceBinding]],
        now: datetime,
    ) -> None:
        grouped: dict[str, list[tuple[SourceDefinition, TickerSourceBinding]]] = defaultdict(list)
        for pair in bindings:
            grouped[pair[0].scheduler_group].append(pair)
        for group, pairs in grouped.items():
            ordered = sorted(pairs, key=lambda pair: pair[1].binding_id)
            count = len(ordered)
            signature = sha256_text(
                canonical_json(
                    [
                        {
                            "binding_id": binding.binding_id,
                            "target_interval_seconds": binding.polling.target_interval_seconds,
                            "tolerance_ratio": binding.polling.tolerance_ratio,
                        }
                        for _, binding in ordered
                    ]
                )
            )
            group_state = self.repository.get_scheduler_group_state(group)
            rephase = group_state.schedule_signature != signature
            for index, (_, binding) in enumerate(ordered):
                state = self.repository.get_poll_state(binding)
                if state.next_dispatch_at is not None and not rephase:
                    continue
                phase = binding.polling.target_interval_seconds * index / max(count, 1)
                self.repository.save_poll_state(
                    state.model_copy(
                        update={
                            "target_due_at": now + timedelta(seconds=phase),
                            "next_dispatch_at": now + timedelta(seconds=phase),
                            "updated_at": now,
                        }
                    )
                )
            if rephase:
                self.repository.save_scheduler_group_state(
                    group_state.model_copy(
                        update={"schedule_signature": signature, "updated_at": now}
                    )
                )

    async def _poll(
        self,
        source: SourceDefinition,
        binding: TickerSourceBinding,
        attempted_at: datetime,
    ) -> PollExecutionResult:
        # Snapshot source and binding for this poll. Updates become visible on the next poll.
        state = self.repository.get_poll_state(binding)
        started_at = self._clock()
        limiter = self._limiter(source)
        poll_run_id = new_id("poll")
        context = PollContext(
            poll_run_id=poll_run_id,
            ticker=binding.ticker,
            source=source,
            binding=binding,
            checkpoint=state.checkpoint,
            requested_at=attempted_at,
            request_permit=limiter.permit,
        )
        try:
            adapter = self.adapters.resolve(source.adapter_ref, source_version=source.version)
            result = await adapter.poll(context)
            result = result.model_copy(
                update={
                    "acquisition_metadata": {
                        **result.acquisition_metadata,
                        "poll_run_id": poll_run_id,
                    }
                }
            )
            execution = await self.service.accept_poll_result(
                source=source,
                binding=binding,
                result=result,
                attempted_at=attempted_at,
            )
            if execution.crawler_execution_id and self.adapters.crawler_plane is not None:
                self.adapters.crawler_plane.record_message_bus_telemetry(
                    execution.crawler_execution_id,
                    execution.model_dump(mode="json"),
                )
            refreshed = self.repository.get_poll_state(binding)
            target_due = self._next_due(
                refreshed.target_due_at or attempted_at,
                attempted_at,
                binding.polling.target_interval_seconds,
            )
            next_dispatch = target_due
            if result.optional_next_poll_hint and result.optional_next_poll_hint > next_dispatch:
                next_dispatch = result.optional_next_poll_hint
            self.repository.save_poll_state(
                refreshed.model_copy(
                    update={
                        "target_due_at": target_due,
                        "next_dispatch_at": next_dispatch,
                        "last_latency_ms": max(0, int((self._clock() - started_at) * 1000)),
                        "updated_at": attempted_at,
                    }
                )
            )
            return execution
        except Exception as exc:
            crawler_execution_id = getattr(exc, "crawler_execution_id", None)
            self.service.record_poll_failure(
                binding,
                code=type(exc).__name__,
                message=str(exc),
                attempted_at=attempted_at,
            )
            refreshed = self.repository.get_poll_state(binding)
            self.repository.save_poll_state(
                refreshed.model_copy(
                    update={
                        "target_due_at": self._next_due(
                            refreshed.target_due_at or attempted_at,
                            attempted_at,
                            binding.polling.target_interval_seconds,
                        ),
                        "next_dispatch_at": self._next_due(
                            refreshed.target_due_at or attempted_at,
                            attempted_at,
                            binding.polling.target_interval_seconds,
                        ),
                        "last_latency_ms": max(0, int((self._clock() - started_at) * 1000)),
                        "updated_at": attempted_at,
                    }
                )
            )
            execution = PollExecutionResult(
                poll_run_id=poll_run_id,
                binding_id=binding.binding_id,
                crawler_execution_id=(str(crawler_execution_id) if crawler_execution_id else None),
                error_code=type(exc).__name__,
                error_message=str(exc)[:1000],
            )
            if execution.crawler_execution_id and self.adapters.crawler_plane is not None:
                self.adapters.crawler_plane.record_message_bus_telemetry(
                    execution.crawler_execution_id,
                    execution.model_dump(mode="json"),
                )
            return execution

    @staticmethod
    def _next_due(previous_due: datetime, now: datetime, interval_seconds: int) -> datetime:
        value = previous_due
        while value <= now:
            value += timedelta(seconds=interval_seconds)
        return value

    def _limiter(self, source: SourceDefinition) -> SchedulerGroupLimiter:
        current = self._limiters.get(source.scheduler_group)
        constraints = self._effective_constraints(source.scheduler_group)
        if current is not None and current[0] == constraints:
            return current[1]

        def on_request() -> None:
            now = utc_now()
            state = self.repository.get_scheduler_group_state(source.scheduler_group)
            self.repository.save_scheduler_group_state(
                state.model_copy(update={"last_request_at": now, "updated_at": now})
            )

        persisted = self.repository.get_scheduler_group_state(source.scheduler_group)
        remaining_gap = 0.0
        if persisted.last_request_at is not None:
            remaining_gap = max(
                0.0,
                constraints.minimum_request_gap_seconds
                - (utc_now() - persisted.last_request_at).total_seconds(),
            )
        limiter = SchedulerGroupLimiter(
            constraints,
            clock=self._clock,
            sleep=self._sleep,
            on_request=on_request,
            initial_delay_seconds=remaining_gap,
        )
        self._limiters[source.scheduler_group] = (constraints, limiter)
        return limiter

    def _effective_constraints(self, group: str) -> SchedulerConstraints:
        sources = [
            source for source in self.repository.list_sources() if source.scheduler_group == group
        ]
        if not sources:
            return SchedulerConstraints()
        return SchedulerConstraints(
            minimum_request_gap_seconds=max(
                source.scheduler_constraints.minimum_request_gap_seconds for source in sources
            ),
            max_concurrency=min(source.scheduler_constraints.max_concurrency for source in sources),
        )

    def _update_capacity_alerts(
        self, bindings: list[tuple[SourceDefinition, TickerSourceBinding]]
    ) -> None:
        grouped: dict[str, list[TickerSourceBinding]] = defaultdict(list)
        for source, binding in bindings:
            grouped[source.scheduler_group].append(binding)
        for group, values in grouped.items():
            constraints = self._effective_constraints(group)
            demand = sum(1 / value.polling.target_interval_seconds for value in values)
            gap = constraints.minimum_request_gap_seconds
            capacity = float("inf") if gap == 0 else 1 / gap
            key = f"scheduler_capacity:{group}"
            if demand > capacity:
                self.repository.upsert_alert(
                    OperationalAlert(
                        alert_key=key,
                        severity=AlertSeverity.WARNING,
                        code="scheduler_capacity_insufficient",
                        message=(
                            f"scheduler group {group} demand {demand:.4f} req/s exceeds "
                            f"safe capacity {capacity:.4f} req/s"
                        ),
                        scheduler_group=group,
                        metadata={
                            "demand_requests_per_second": demand,
                            "capacity_requests_per_second": capacity,
                            "binding_count": len(values),
                        },
                    )
                )
            else:
                self.repository.resolve_alert(key)

    def _mark_disabled(self, binding: TickerSourceBinding) -> None:
        state = self.repository.get_poll_state(binding)
        if state.status is PollStatus.DISABLED:
            return
        self.repository.save_poll_state(
            state.model_copy(update={"status": PollStatus.DISABLED, "updated_at": utc_now()})
        )


__all__ = ["GlobalPollScheduler", "SchedulerGroupLimiter"]
