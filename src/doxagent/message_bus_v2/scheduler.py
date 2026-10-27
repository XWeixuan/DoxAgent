"""Global, scheduler-group-aware Message Bus v2 poll scheduler."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from doxagent.site_strategy.scope import source_routed

from .admission import AdmissionContext
from .capacity import RequestCost, required_capacity
from .collection_override import CollectionStopped, check_request_allowed
from .polling import CaptureIntake

if TYPE_CHECKING:
    from doxagent.message_bus_v2.distribution import DistributionWorker
    from doxagent.persistent_runtime_v2.bus_orchestration import BusOrchestration
    from doxagent.ticker_initialization.repository import InitializationRepository

from doxagent.message_bus_v2.adapters import AdapterRegistry
from doxagent.message_bus_v2.distribution_repository import DistributionRepository
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import (
    AcquisitionMode,
    AlertSeverity,
    JsonObject,
    OperationalAlert,
    PollContext,
    PollExecutionResult,
    PollState,
    PollStatus,
    SchedulerConstraints,
    SharedPollContext,
    SourceDefinition,
    TickerMonitoringStatus,
    TickerSourceBinding,
    canonical_json,
    new_id,
    sha256_text,
    utc_now,
)
from doxagent.message_bus_v2.search_plan import build_query_plan
from doxagent.message_bus_v2.service import MessageBusV2Service

MonotonicClock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]
POLL_TIMINGS = ContextVar("poll_timings", default=None)
SHARED_HTTP_CHECKPOINTS = {
    "builtin:defense_public@1": "defense_public",
    "builtin:technology_public@1": "technology_public",
    "builtin:energy_public@1": "energy_public",
}


class SchedulerGroupLimiter:
    """Enforce group max concurrency and minimum gap per actual request."""

    def __init__(
        self,
        constraints: SchedulerConstraints,
        *,
        clock: MonotonicClock = time.monotonic,
        sleep: Sleeper = asyncio.sleep,
        on_request: Callable[[], object] | None = None,
        initial_delay_seconds: float = 0.0,
    ) -> None:
        self.constraints = constraints
        self._clock = clock
        self._sleep = sleep
        self._semaphore = asyncio.Semaphore(constraints.max_concurrency)
        self._spacing_lock = asyncio.Lock()
        self._next_request_at = self._clock() + max(0.0, initial_delay_seconds)
        self._on_request = on_request
        self.cost = RequestCost()
        self._shrink_debt = 0

    @asynccontextmanager
    async def permit(self) -> AsyncIterator[None]:
        waiting_at = self._clock()
        acquired = False
        try:
            while True:
                delay = max(0, self._next_request_at - self._clock())
                if delay:
                    await self._sleep(delay)
                await self._semaphore.acquire()
                acquired = True
                async with self._spacing_lock:
                    delay = self._next_request_at - self._clock()
                    if delay <= 0:
                        self._next_request_at = (
                            self._clock() + self.constraints.minimum_request_gap_seconds
                        )
                        break
                self._semaphore.release()
                acquired = False
            # Persistence must never hold the pacing lock. The scheduler callback
            # only updates an in-memory batch; a caller-supplied awaitable is tracked.
            check_request_allowed()
            if self._on_request:
                recorded = self._on_request()
                if inspect.isawaitable(recorded):
                    asyncio.create_task(recorded)
            timings = POLL_TIMINGS.get()
            if timings is not None:
                timings["source_wait_ms"] = timings.get("source_wait_ms", 0) + int(
                    (self._clock() - waiting_at) * 1000
                )
            executing_at = self._clock()
            from doxagent.site_strategy.client import ACCESS_RESULTS

            accesses = ACCESS_RESULTS.get()
            position = len(accesses) if accesses is not None else 0
            try:
                yield
            finally:
                current_accesses = accesses[position:] if accesses is not None else []
                if current_accesses:
                    physical = (
                        sum(
                            a.network_ms
                            for a in current_accesses
                            if a.attempts and not a.served_from_shared_capture
                        )
                        / 1000
                    )
                    if physical > 0:
                        self.cost.observe(physical)
                else:
                    self.cost.observe(max(0.001, self._clock() - executing_at))
        finally:
            if acquired:
                if self._shrink_debt:
                    self._shrink_debt -= 1
                else:
                    self._semaphore.release()


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
        self.runtime_orchestration: BusOrchestration | None = None
        self.activation_admission: Callable[[], None] | None = None
        self.initialization_control: InitializationRepository | None = None
        self.terms = MonitoringTermsService(repository)
        self.distribution = DistributionRepository(repository)
        self.distribution_worker: DistributionWorker | None = None
        self._distribution_task: asyncio.Task[int] | None = None
        self.intake = CaptureIntake(service)
        self._request_starts = {}
        self._wake = asyncio.Event()
        self._query_plans = {}
        self._group_demand = {}
        self._source_demand = {}

    async def storage_call(self, operation, *args, **kwargs):
        return await self.service.storage.call(operation, *args, **kwargs)

    async def read_call(self, operation, *args, **kwargs):
        return await self.service.storage.read(
            self.repository.read_only, operation, *args, **kwargs
        )

    def _save_poll_scope_state(self, state, unit, source, binding):
        with self.repository.connection_scope():
            saved = self.repository.save_poll_unit_state(
                state.model_copy(
                    update={
                        "source_definition_version": source.version,
                        "binding_version": binding.version,
                    }
                ),
                unit,
            )
            if saved and state.status is PollStatus.SUCCEEDED and not unit.startswith("TAIL:"):
                if self.repository.get_poll_state(binding).status is PollStatus.SUCCEEDED:
                    self.repository.resolve_alert("poll_failure:" + binding.binding_id)
                refresh = getattr(self, "_health_refresh_at", {})
                if self._clock() - refresh.get(source.source_id, -60) >= 60:
                    refresh[source.source_id] = self._clock()
                    self._health_refresh_at = refresh
                    self.service._refresh_source_failure_alert(source.source_id, now=utc_now())
            return saved

    async def run_once(self) -> list[PollExecutionResult]:
        if getattr(self, 'collection_plane_enabled', False):
            self.intake.start()
            self.capture_bridge.start()
            if self.distribution_worker is not None:
                self.distribution_worker.start()
        admission = getattr(self, "activation_admission", None)
        if admission is not None:
            await self.storage_call(admission, _lane_priority=0)
        from doxagent.ticker_initialization.consumers import bus_revision_usable, consumer_heartbeat

        heartbeat = consumer_heartbeat(
            getattr(self, "initialization_control", None),
            "bus",
            lambda revision: bus_revision_usable(self.service, revision),
        )
        # This lifecycle reads/writes the initialization owner, not Bus business
        # state. It must not wait behind every intake shard or occupy its writer.
        await asyncio.to_thread(heartbeat.__enter__)
        try:
            return await self._run_admitted_once()
        finally:
            await asyncio.to_thread(heartbeat.__exit__, None, None, None)

    async def _run_admitted_once(self) -> list[PollExecutionResult]:
        self.intake.start()
        if getattr(self, 'collection_plane_enabled', False):
            if self.runtime_orchestration is not None:
                return await self.runtime_orchestration.run_once(self, collect=False)
            await self.storage_call(self.service.flush_due_buffers, now=utc_now())
            await self.storage_call(self.service.retry_pending_raw)
            return []
        if self._request_starts:
            starts, self._request_starts = self._request_starts, {}
            await self.service.storage.call(self._flush_request_starts, starts, _lane_priority=0)
        ir = getattr(self, "ir_coordinator", None)
        if ir is None:
            from .corporate_ir.coordinator import IrCoordinator

            self.ir_coordinator = ir = IrCoordinator(self)
        ir.start()
        orchestration = self.runtime_orchestration
        if orchestration is not None:
            return await orchestration.run_once(self)
        now = utc_now()
        eligible, _, poll_states = await self.prepare_cycle(now)
        entries = [
            (
                source,
                binding,
                options["acquisition_unit"],
                self.schedule_epoch(poll_states[binding.binding_id], options['acquisition_unit']),
                True,
            )
            for source, binding in eligible
            if source.source_id != "corporate_ir_press_releases"
            for _, options in self.work_units(
                source, binding, poll_states[binding.binding_id], now, include_all=True
            )
        ]
        await self.service.storage.call(
            self.repository.polling.schedules, entries, now, _lane_priority=0
        )
        due: list[tuple[SourceDefinition, TickerSourceBinding]] = []
        for source, binding in eligible:
            state = poll_states[binding.binding_id]
            if any(self.work_units(source, binding, state, now)):
                due.append((source, binding))
        shared: dict[str, tuple[SourceDefinition, list[TickerSourceBinding]]] = {}
        tasks = []
        long_running = getattr(self, "_long_running", False)
        inflight = getattr(self, "_standalone_inflight", {})
        self._standalone_inflight = inflight
        completed = []
        for key, task in list(inflight.items()):
            if task.done():
                completed.append(task.result())
                del inflight[key]
        due.sort(key=lambda pair: poll_states[pair[1].binding_id].next_dispatch_at or now)
        for source, binding in due:
            if source.source_id == "corporate_ir_press_releases":
                continue
            if source.acquisition_mode is AcquisitionMode.BY_DISTRIBUTION:
                continue
            else:
                for key, options in self.work_units(
                    source, binding, poll_states[binding.binding_id], now
                ):
                    if key in inflight:
                        continue
                    task = asyncio.create_task(self._poll(source, binding, now, **options))
                    if long_running:
                        inflight[key] = task
                        task.add_done_callback(lambda _: self._wake.set())
                    else:
                        tasks.append(task)
        for source, binding in eligible:
            if source.acquisition_mode is AcquisitionMode.BY_DISTRIBUTION:
                shared.setdefault(source.source_id, (source, []))[1].append(binding)
        for source, bindings in shared.values():
            for key, targets, options in self.distribution_work_units(
                source, bindings, now, poll_states=poll_states
            ):
                if key in inflight:
                    continue
                task = asyncio.create_task(self._poll_distribution(source, targets, now, **options))
                if long_running:
                    inflight[key] = task
                else:
                    tasks.append(task)
        results = await asyncio.gather(*tasks) if tasks else []
        if self.distribution_worker is not None and (
            self._distribution_task is None or self._distribution_task.done()
        ):
            if self._distribution_task is not None:
                self._distribution_task.result()
            pump = (
                self.distribution_worker.run_available
                if long_running
                else self.distribution_worker.run_once
            )
            self._distribution_task = asyncio.create_task(pump())
        await self.storage_call(self.service.flush_due_buffers, now=now)
        await self.storage_call(self.service.retry_pending_raw)
        return [*completed, *results]

    async def run_forever(self, *, loop_sleep_seconds: float, stop: asyncio.Event) -> None:
        self._long_running = True
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception as exc:
                from doxagent.persistent_runtime_v2.journal import is_sqlite_contention

                if not is_sqlite_contention(exc):
                    raise
                logging.getLogger(__name__).warning(
                    "bus runtime lock contention; preserving durable leases and retrying next loop"
                )
            try:
                self._wake.clear()
                stop_task = asyncio.create_task(stop.wait())
                wake_task = asyncio.create_task(self._wake.wait())
                await asyncio.wait(
                    (stop_task, wake_task),
                    timeout=loop_sleep_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except TimeoutError:
                pass
            finally:
                for waiting in (stop_task, wake_task):
                    waiting.cancel()
                await asyncio.gather(stop_task, wake_task, return_exceptions=True)

    async def close(self) -> None:
        if getattr(self, 'capture_bridge', None) is not None:
            await self.capture_bridge.close()
        tasks = list(getattr(self, "_standalone_inflight", {}).values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.intake.close()
        if getattr(self, "ir_coordinator", None) is not None:
            await self.ir_coordinator.close()
        if self.runtime_orchestration is not None:
            await self.runtime_orchestration.close()
        if self._distribution_task is not None:
            self._distribution_task.cancel()
            await asyncio.gather(self._distribution_task, return_exceptions=True)
        if self.distribution_worker is not None:
            await self.distribution_worker.close()

    def _eligible_bindings(
        self, now: datetime, *, include_inactive_hours: bool = False, mark_disabled: bool = True
    ) -> list[tuple[SourceDefinition, TickerSourceBinding]]:
        instant = now
        states = {state.ticker: state for state in self.repository.list_ticker_states()}
        sources = {source.source_id: source for source in self.repository.list_sources()}
        result: list[tuple[SourceDefinition, TickerSourceBinding]] = []
        for binding in self.repository.list_bindings(active_only=True):
            ticker_state = states.get(binding.ticker)
            source = sources.get(binding.source_id)
            if (
                ticker_state is None
                or ticker_state.status is not TickerMonitoringStatus.RUNNING
                or source is None
                or not source.enabled
                or not binding.polling.enabled
            ):
                if mark_disabled:
                    self._mark_disabled(binding)
                continue
            if not include_inactive_hours and not binding.polling.active_at(instant):
                continue
            result.append((source, binding))
        return result

    def _prepare_poll_cycle(self, now: datetime, *, readonly=False):
        """One storage work unit, fresh each cycle; no connection escapes to I/O."""
        with self.repository.connection_scope():
            self._intake_backlogged = self.repository.polling.backlogged(now=now)
            with self.repository._connect() as db:
                signature = (
                    tuple(
                        tuple(r)
                        for r in db.execute(
                            "SELECT source_id,version,enabled FROM source_definitions"
                        )
                    ),
                    tuple(
                        tuple(r)
                        for r in db.execute(
                            "SELECT binding_id,version,enabled,tombstoned_at FROM ticker_source_bindings"
                        )
                    ),
                    tuple(
                        tuple(r)
                        for r in db.execute("SELECT ticker,data_json FROM ticker_monitoring_states")
                    ),
                )
                self._group_state_cache = {
                    r[0]: r[1]
                    for r in db.execute(
                        "SELECT scheduler_group,data_json FROM scheduler_group_states"
                    )
                }
            cached = getattr(self, "_configuration_cache", None)
            if cached and cached[0] == signature:
                roster, sources = cached[1:]
            else:
                roster = self._eligible_bindings(
                    now, include_inactive_hours=True, mark_disabled=not readonly
                )
                sources = self.repository.list_sources()
                self._configuration_cache = (signature, roster, sources)
            self._configured_sources = sources
            self.group_limits = {
                source.scheduler_group: self._effective_constraints(
                    source.scheduler_group, sources=sources
                ).max_concurrency
                for source in sources
            }
            eligible = [(s, b) for s, b in roster if b.polling.active_at(now)]
            demand = defaultdict(float)
            for source, binding in eligible:
                demand[source.scheduler_group] += 1 / binding.polling.target_interval_seconds
            self._group_demand = dict(demand)
            states = {p.binding_id: p for p in self.repository.list_poll_states()}
            for _, binding in roster:
                states.setdefault(
                    binding.binding_id,
                    PollState(
                        binding_id=binding.binding_id,
                        source_id=binding.source_id,
                        ticker=binding.ticker,
                    ),
                )
            with self.repository._connect() as db:
                term_heads = tuple(tuple(r) for r in db.execute(
                    'SELECT ticker,current_revision FROM ticker_monitoring_terms ORDER BY ticker'))
            plan_signature = (term_heads, tuple((s.source_id,s.version,b.ticker)
                for s,b in eligible if s.acquisition_mode is AcquisitionMode.BY_SEARCH))
            if getattr(self, '_query_plan_signature', None) != plan_signature:
                self._query_plans = {}
                terms = {}
                for source, binding in eligible:
                    if source.acquisition_mode is AcquisitionMode.BY_SEARCH:
                        if binding.ticker not in terms:
                            terms[binding.ticker] = self.terms.get(binding.ticker)
                        found = terms[binding.ticker]
                        if found:
                            try:
                                self._query_plans[(source.source_id, binding.ticker)] = (
                                    build_query_plan(source, found[1], found[0]).model_dump(mode="json")
                                )
                            except ValueError:
                                pass  # The individual source poll records CONFIG_INCOMPLETE.
                self._query_plan_signature = plan_signature
            demand = defaultdict(float)
            source_demand = defaultdict(float)
            for source, binding in eligible:
                count = (
                    2
                    if source.source_id in {"yahoo_finance_news", "barrons_ticker_news"}
                    else len(
                        self._query_plans.get((source.source_id, binding.ticker), {}).get(
                            "queries", [None]
                        )
                    )
                )
                rate = count / binding.polling.target_interval_seconds
                if source.acquisition_mode is AcquisitionMode.BY_DISTRIBUTION:
                    source_demand[source.source_id] = max(source_demand[source.source_id], rate)
                else:
                    source_demand[source.source_id] += rate
            for source in sources:
                demand[source.scheduler_group] += source_demand[source.source_id]
            self._source_demand = dict(source_demand)
            self._group_demand = dict(demand)
            if not readonly:
                self._initialize_due_slots(eligible, now, poll_states=states)
                self._update_capacity_alerts(eligible)
        return eligible, roster, states

    async def prepare_cycle(self, now):
        eligible, roster, states = await self.read_call(
            self._prepare_poll_cycle, now, readonly=True
        )
        if any(
            states[b.binding_id].next_dispatch_at is None
            or states[b.binding_id].schedule_interval_seconds != b.polling.target_interval_seconds
            for _, b in eligible
        ):
            await self.service.storage.call(
                self._initialize_due_slots, eligible, now, poll_states=states, _lane_priority=0
            )
        if self._clock() - getattr(self, "_capacity_checked_at", -60) >= 60:
            self._capacity_checked_at = self._clock()
            await self.storage_call(self._update_capacity_alerts, eligible)
        return eligible, roster, states

    @staticmethod
    def schedule_epoch(state, unit):
        value = state.target_due_at if unit == 'SHARED' else state.units.get(unit, {}).get('target_due_at') or state.target_due_at
        return datetime.fromisoformat(value) if isinstance(value,str) else value

    def work_units(self, source, binding, state, now, *, include_all=False):
        from .pressure import background_allowed

        units = [
            ("SHARED" if source.acquisition_mode is AcquisitionMode.BY_DISTRIBUTION else "HEAD", {})
        ]
        if source.adapter_ref == "builtin:technology_public@1":
            from .technology_sources import endpoint_units

            units = [(unit, {}) for unit in endpoint_units(source)]
        elif source.adapter_ref == "builtin:energy_public@1":
            from .energy_catalog import endpoint_units

            units = [(unit, {}) for unit in endpoint_units(source, binding)]
        elif source.source_id == "yahoo_finance_news":
            units.append(("SUPPLEMENT_HEAD", {}))
            head = state.units.get("SUPPLEMENT_HEAD", {}).get("checkpoint", {})
            tail = state.units.get("TAIL:YAHOO_TIME", {}).get("checkpoint", {})
            dates = {
                **head.get("verified_article_dates", {}),
                **tail.get("verified_article_dates", {}),
            }
            pending = [r for r in head.get("pending_timestamp_rows", []) if r["link"] not in dates]
            if pending:
                units.append(
                    (
                        "TAIL:YAHOO_TIME",
                        {
                            "checkpoint": {
                                **tail,
                                "verified_article_dates": dates,
                                "pending_timestamp_rows": pending,
                            }
                        },
                    )
                )
        elif source.source_id == "barrons_ticker_news":
            units.append(("OTHER_HEAD", {}))
        elif (
            source.adapter_ref in {"builtin:newswire_rss", "builtin:newswire_rss@1"}
            and len(binding.source_parameters.get("rss_urls", [])) > 1
        ):
            units = [
                ("FEED:" + sha256_text(str(url))[:24], {})
                for url in binding.source_parameters["rss_urls"]
            ]
        elif source.acquisition_mode is AcquisitionMode.BY_SEARCH:
            plan = self._query_plans.get((source.source_id, binding.ticker))
            if plan:
                units = [
                    (
                        "QUERY:" + query["query_key"],
                        {
                            "freeze_query_plan": True,
                            "query_plan_override": {**plan, "queries": [query]},
                        },
                    )
                    for query in plan["queries"]
                ]
                if source.source_id == 'reuters_site_search':
                    for query in plan['queries']:
                        head_cp=state.units.get('QUERY:'+query['query_key'],{}).get('checkpoint',{})
                        saved=head_cp.get('queries',{}).get(query['query_key'],{})
                        if not saved.get('tail_pending'):
                            continue
                        unit='TAIL:'+query['query_key']
                        old=state.units.get(unit,{}).get('checkpoint',{})
                        tail=old.get('queries',{}).get(query['query_key'],{})
                        if tail.get('done') and tail.get('head_at')==saved.get('head_at'):
                            continue
                        checkpoint=old if tail and not tail.get('done') else {
                            'queries':{query['query_key']:{**saved,'done':False}}}
                        units.append((unit,{'freeze_query_plan':True,
                            'query_plan_override':{**plan,'queries':[query]},'checkpoint':checkpoint}))
                if source.source_id == "globenewswire_search":
                    for query in plan["queries"]:
                        head = state.units.get("QUERY:" + query["query_key"], {})
                        head_cp = head.get("checkpoint", {})
                        saved = head_cp.get("queries", {}).get(query["query_key"], {})
                        if saved and not saved.get("done", False):
                            unit = "TAIL:" + query["query_key"]
                            old = state.units.get(unit, {}).get("checkpoint", {})
                            tail = old.get("queries", {}).get(query["query_key"], {})
                            if tail.get("done") and tail.get("head_at") == saved.get("head_at"):
                                continue
                            options = {
                                "freeze_query_plan": True,
                                "query_plan_override": {**plan, "queries": [query]},
                            }
                            if not old or tail.get("done"):
                                options["checkpoint"] = head_cp
                            units.append((unit, options))
                if source.source_id == "calcalist_search":
                    for query in plan["queries"]:
                        head_cp = state.units.get("QUERY:" + query["query_key"], {}).get(
                            "checkpoint", {}
                        )
                        if any(
                            e.get("tail_pending") for e in head_cp.get("endpoints", {}).values()
                        ):
                            unit = "TAIL:" + query["query_key"]
                            old = state.units.get(unit, {}).get("checkpoint", {})
                            if not old or any(
                                e.get("tail_pending") for e in old.get("endpoints", {}).values()
                            ):
                                units.append(
                                    (
                                        unit,
                                        {
                                            "freeze_query_plan": True,
                                            "query_plan_override": {**plan, "queries": [query]},
                                            "checkpoint": old or head_cp,
                                        },
                                    )
                                )
        for unit, options in units:
            if (
                not include_all
                and unit.startswith("TAIL:")
                and unit != "TAIL:YAHOO_TIME"
                and (not background_allowed() or getattr(self, "_intake_backlogged", False))
            ):
                continue
            value = PollState.model_validate(
                {**state.model_dump(), **state.units.get(unit, {}), "units": {}}
            )
            if (
                source.adapter_ref in {"builtin:technology_public@1", "builtin:energy_public@1"}
                and unit not in state.units
            ):
                value = value.model_copy(update={"next_dispatch_at": None})
            changed_timestamps = unit == "TAIL:YAHOO_TIME" and options["checkpoint"][
                "pending_timestamp_rows"
            ] != state.units.get(unit, {}).get("checkpoint", {}).get("pending_timestamp_rows")
            if (
                include_all
                or changed_timestamps
                or value.next_dispatch_at is None
                or value.next_dispatch_at <= now
            ):
                key = binding.binding_id if unit == "HEAD" else binding.binding_id + "#" + unit
                yield key, {**options, "acquisition_unit": unit}

    def distribution_work_units(self, source, bindings, now, *, poll_states):
        """One shared acquisition per due endpoint, with its own ticker roster."""
        if source.adapter_ref not in {"builtin:technology_public@1", "builtin:energy_public@1"}:
            if self.distribution_due(bindings, now, poll_states=poll_states):
                yield "shared:" + source.source_id, bindings, {}
            return
        due = {}
        for binding in bindings:
            for _, options in self.work_units(source, binding, poll_states[binding.binding_id], now):
                due.setdefault(options["acquisition_unit"], []).append(binding)
        for unit, targets in due.items():
            yield "shared:" + source.source_id + "#" + unit, targets, {"acquisition_unit": unit}

    def _distribution_state(self, binding, unit):
        if unit == "SHARED":
            return self.repository.get_poll_state(binding)
        state = self.repository.get_poll_unit_state(binding, unit)
        if unit not in self.repository.get_poll_state(binding).units:
            state = state.model_copy(update={
                "last_attempt_at": None, "target_due_at": None, "next_dispatch_at": None,
            })
        return state

    def _initialize_due_slots(
        self,
        bindings: list[tuple[SourceDefinition, TickerSourceBinding]],
        now: datetime,
        *,
        poll_states: dict[str, PollState] | None = None,
    ) -> None:
        grouped: dict[str, list[tuple[SourceDefinition, TickerSourceBinding]]] = defaultdict(list)
        for pair in bindings:
            if pair[0].acquisition_mode is AcquisitionMode.BY_DISTRIBUTION:
                continue
            grouped[pair[0].scheduler_group].append(pair)
        for group, pairs in grouped.items():
            ordered = sorted(pairs, key=lambda pair: pair[1].binding_id)
            count = len(ordered)
            physical_units = [
                (s, b, options["acquisition_unit"])
                for s, b in ordered
                for _, options in self.work_units(
                    s,
                    b,
                    (
                        poll_states[b.binding_id]
                        if poll_states is not None
                        else self.repository.get_poll_state(b)
                    ),
                    now,
                    include_all=True,
                )
                if not options["acquisition_unit"].startswith("TAIL:")
            ]
            phases = {
                (b.binding_id, unit): b.polling.target_interval_seconds
                * index
                / max(1, len(physical_units))
                for index, (_, b, unit) in enumerate(physical_units)
            }
            power_feeds = sorted({
                unit for s, _, unit in physical_units if s.source_id == "power_magazine_energy"
            })
            for index, (s, b, unit) in enumerate(physical_units):
                current = (
                    poll_states[b.binding_id]
                    if poll_states is not None
                    else self.repository.get_poll_state(b)
                )
                if unit in current.units or (
                    unit == "HEAD" and current.next_dispatch_at is not None
                ):
                    continue
                phase = b.polling.target_interval_seconds * index / max(1, len(physical_units))
                if s.source_id == "power_magazine_energy":
                    phase = (
                        b.polling.target_interval_seconds * power_feeds.index(unit) / len(power_feeds)
                    )
                snapshot = self.repository.get_poll_unit_state(b, unit).model_copy(
                    update={
                        "target_due_at": now + timedelta(seconds=phase),
                        "next_dispatch_at": now + timedelta(seconds=phase),
                    }
                )
                self.repository.save_poll_unit_state(snapshot, unit)
                if poll_states is not None:
                    poll_states[b.binding_id] = self.repository.get_poll_state(b)
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
                state = (
                    poll_states[binding.binding_id]
                    if poll_states is not None
                    else self.repository.get_poll_state(binding)
                )
                interval_changed = state.schedule_interval_seconds not in (
                    None,
                    binding.polling.target_interval_seconds,
                )
                if state.next_dispatch_at is not None and not interval_changed:
                    if state.schedule_interval_seconds is None:
                        state = state.model_copy(
                            update={
                                "schedule_interval_seconds": binding.polling.target_interval_seconds
                            }
                        )
                        self.repository.save_poll_state(state)
                        if poll_states is not None:
                            poll_states[binding.binding_id] = state
                    continue
                phase = binding.polling.target_interval_seconds * index / max(count, 1)
                updated = state.model_copy(
                    update={
                        "schedule_interval_seconds": binding.polling.target_interval_seconds,
                        "units": {
                            unit: {
                                **snapshot,
                                "target_due_at": (
                                    snapshot.get("target_due_at") if not interval_changed else None
                                )
                                or (
                                    now
                                    + timedelta(
                                        seconds=phases.get((binding.binding_id, unit), phase)
                                    )
                                ).isoformat(),
                                "next_dispatch_at": (
                                    snapshot.get("next_dispatch_at")
                                    if not interval_changed
                                    else None
                                )
                                or (
                                    now
                                    + timedelta(
                                        seconds=phases.get((binding.binding_id, unit), phase)
                                    )
                                ).isoformat(),
                            }
                            for unit, snapshot in state.units.items()
                        },
                        "target_due_at": now + timedelta(seconds=phase),
                        "next_dispatch_at": now + timedelta(seconds=phase),
                        "updated_at": now,
                    }
                )
                self.repository.save_poll_state(updated)
                if poll_states is not None:
                    poll_states[binding.binding_id] = updated
            if rephase:
                self.repository.save_scheduler_group_state(
                    group_state.model_copy(
                        update={"schedule_signature": signature, "updated_at": now}
                    )
                )

    def distribution_due(
        self,
        bindings: list[TickerSourceBinding],
        now: datetime,
        *,
        poll_states: dict[str, PollState] | None = None,
    ) -> bool:
        return any(
            (
                state := (
                    poll_states[binding.binding_id]
                    if poll_states is not None
                    else self.repository.get_poll_state(binding)
                )
            ).next_dispatch_at
            is None
            or state.next_dispatch_at <= now
            for binding in bindings
        )

    @source_routed
    async def _poll_distribution(self, source, bindings, attempted_at, **kwargs):
        from doxagent.site_strategy.client import ACCESS_RESULTS

        from .poll_trace import DEMAND, REQUESTS

        requests = []
        trace = REQUESTS.set(requests)
        access = ACCESS_RESULTS.set([])
        demand = DEMAND.set(
            {
                "due_at": attempted_at.isoformat(),
                "rate": self._source_demand.get(source.source_id, 0),
                "work_class": "backfill" if kwargs.get("window_cutoff") else "foreground",
            }
        )
        ids = []
        result = None
        error = None
        try:
            states = await self.read_call(
                lambda: [self._distribution_state(
                    binding, kwargs.get("acquisition_unit", "SHARED")
                ) for binding in bindings]
            )
            ids = [new_id("poll") for _ in bindings]

            def start_runs():
                with self.repository.atomic_intake():
                    for run_id, binding, state in zip(ids, bindings, states, strict=True):
                        self.repository.polling.start(
                            run_id, source, binding, self._current_due(state.target_due_at, attempted_at, binding.polling.target_interval_seconds),
                            attempted_at, "TAIL:SHARED" if kwargs.get("window_cutoff")
                            else kwargs.get("acquisition_unit", "SHARED"),
                        )

            await self.service.storage.call(start_runs, _lane_priority=0)
            result = await self._poll_distribution_impl(source, bindings, attempted_at, **kwargs)
            return result
        except CollectionStopped:
            result = PollExecutionResult(
                binding_id=f"shared:{source.source_id}", site_access_deferred=True,
                site_access_deferred_reason="TEMPORARY_COLLECTION_ENDED", window_done=False,
            )
            return result
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            status = (
                "FAILED"
                if error or (result and result.error_code)
                else "DEFERRED"
                if result and result.site_access_deferred
                else "CAPTURED"
            )

            def finish_runs():
                with self.repository.atomic_intake():
                    for run_id in ids:
                        self.repository.polling.update_run(
                            run_id, status=status, reason=error or (
                                result.error_code or result.site_access_deferred_reason if result else None
                            ), requests=requests, shared_run_id=result.poll_run_id if result else None,
                        )

            if ids:
                await self.service.storage.call(finish_runs, _lane_priority=0)
            REQUESTS.reset(trace)
            ACCESS_RESULTS.reset(access)
            DEMAND.reset(demand)

    async def _poll_distribution_impl(
        self,
        source: SourceDefinition,
        bindings: list[TickerSourceBinding],
        attempted_at: datetime,
        *,
        window_start: datetime | None = None,
        window_cutoff: datetime | None = None,
        admission_context: AdmissionContext | None = None,
        acquisition_unit: str = "SHARED",
    ) -> PollExecutionResult:
        if not bindings:
            return PollExecutionResult(binding_id=f"shared:{source.source_id}")
        mode = "CLOSED_SWEEP" if window_cutoff else "REALTIME"
        if mode == "REALTIME":
            interval = min(b.polling.target_interval_seconds for b in bindings)
            slot = int(attempted_at.timestamp()) // interval
            work_key = f"{source.source_id}:{source.version}:REALTIME:{slot}"
        else:
            assert window_start is not None and window_cutoff is not None
            work_key = (
                f"{source.source_id}:{source.version}:CLOSED_SWEEP:"
                f"{window_start.isoformat()}:{window_cutoff.isoformat()}"
            )
        if acquisition_unit != "SHARED":
            work_key += ":" + acquisition_unit
        definitions = await self.read_call(
            lambda: [self.terms.get(binding.ticker) for binding in bindings]
        )
        roster = []
        for binding, found in zip(bindings, definitions, strict=True):
            if found is None:
                continue
            admission = admission_context or AdmissionContext()
            roster.append((binding, found[0], admission.model_dump(mode="json")))
        if not roster:
            return PollExecutionResult(
                binding_id=f"shared:{source.source_id}", error_code="CONFIG_INCOMPLETE"
            )
        run = await self.service.storage.call(
            self._prepare_distribution_run,
            work_key=work_key,
            source=source,
            mode=mode,
            window_start=window_start,
            cutoff=window_cutoff,
            roster=roster,
            _lane_priority=0,
        )
        if run["status"] == "DONE" or await self.read_call(
            self.repository.polling.pending, run["run_id"]
        ):
            await self.storage_call(
                self._record_distribution_poll, source, roster, run,
                _lane_priority=0,
                acquisition_unit=acquisition_unit,
                acquisition_checkpoint=__import__("json").loads(run["checkpoint_json"]),
            )
            return PollExecutionResult(
                binding_id=f"shared:{source.source_id}",
                poll_run_id=run["run_id"],
                window_done=True,
                window_coverage=run["coverage"],
            )
        token = await self.service.storage.call(self.distribution.claim_run, run["run_id"], _lane_priority=0)
        if token is None:
            return PollExecutionResult(
                binding_id=f"shared:{source.source_id}",
                poll_run_id=run["run_id"],
                window_done=False,
            )
        try:
            adapter = self.adapters.resolve(source.adapter_ref, source_version=source.version)
            shared_poll = getattr(adapter, "poll_shared", None)
            if shared_poll is None:
                raise TypeError(f"distribution adapter lacks poll_shared: {source.adapter_ref}")
            shared_checkpoint = __import__("json").loads(run["checkpoint_json"])
            if (
                not shared_checkpoint
                and mode == "REALTIME"
                and source.adapter_ref in SHARED_HTTP_CHECKPOINTS
            ):
                previous_state = await self.read_call(
                    self._distribution_state, roster[0][0], acquisition_unit
                )
                saved = previous_state.checkpoint.get(
                    SHARED_HTTP_CHECKPOINTS[source.adapter_ref], {}
                )
                if saved.get("source_version") == source.version:
                    shared_checkpoint = saved.get("checkpoint", {})
            context = SharedPollContext(
                run_id=run["run_id"],
                source=source,
                acquisition_unit=acquisition_unit,
                checkpoint=shared_checkpoint,
                window_start=window_start,
                window_cutoff=window_cutoff,
                requested_at=attempted_at,
                request_permit=self._limiter(source).permit,
            )
            result = await shared_poll(context)
            if source.adapter_ref in SHARED_HTTP_CHECKPOINTS:
                for failure in result.failures:
                    await self.storage_call(self.repository.save_failure, failure)
            from doxagent.source_maintenance.signals import shared

            shared(source, [b for b, _, _ in roster], run["run_id"], attempted_at, mode, result)
            if result.site_access_deferred and not result.messages:
                await self.storage_call(self.distribution.release_run, run["run_id"], token)
                if mode == "REALTIME":
                    retry_at = result.site_access_retry_not_before or self._next_distribution_due(
                        source
                    )
                    for binding, _, _ in roster:
                        state = await self.read_call(
                            self._distribution_state, binding, acquisition_unit
                        )
                        await self.storage_call(
                            self.repository.save_poll_unit_state,
                            state.model_copy(
                                update={
                                    "next_dispatch_at": retry_at,
                                    "updated_at": utc_now(),
                                    "source_definition_version": source.version,
                                    "binding_version": binding.version,
                                    "status": PollStatus.FAILED,
                                    "last_attempt_at": attempted_at,
                                    "last_failure_at": attempted_at,
                                    "last_error_code": "SOURCE_DEFERRED:"
                                    + (
                                        result.site_access_deferred_reason
                                        or "access_budget_deferred"
                                    ),
                                    "last_error_message": "Acquisition deferred until "
                                    + retry_at.isoformat(),
                                    "checkpoint": {
                                        **state.checkpoint,
                                        SHARED_HTTP_CHECKPOINTS[source.adapter_ref]: {
                                            "source_version": source.version,
                                            "checkpoint": result.next_checkpoint,
                                        },
                                    }
                                    if source.adapter_ref in SHARED_HTTP_CHECKPOINTS
                                    else state.checkpoint,
                                }
                            ),
                            acquisition_unit,
                        )
                return PollExecutionResult(
                    binding_id=f"shared:{source.source_id}",
                    poll_run_id=run["run_id"],
                    window_done=False,
                    site_access_deferred=True,
                    site_access_deferred_reason=result.site_access_deferred_reason,
                    site_access_retry_not_before=result.site_access_retry_not_before,
                )
            if getattr(self, "_long_running", False) and mode == "REALTIME":
                from .schema import PollResult

                observed_at = utc_now()
                roster_snapshot = __import__("json").loads(run["roster_json"])
                for target in roster_snapshot:
                    target["admission"] = {
                        **target["admission"],
                        "observed_at": observed_at.isoformat(),
                    }
                info = {
                    "run_id": run["run_id"],
                    "fetch_token": token,
                    "checkpoint": result.next_checkpoint,
                    "coverage": result.window_coverage,
                    "done": result.window_done,
                    "roster": roster_snapshot,
                    "targets": [
                        {
                            "binding_id": b.binding_id,
                            "ticker": b.ticker,
                            "binding_version": b.version,
                        }
                        for b, _, _ in roster
                    ],
                }
                prepared = await self.service.storage.prepare(
                    self.repository.polling.prepare,
                    source=source,
                    binding=roster[0][0],
                    result=PollResult(messages=result.messages),
                    run_id=run["run_id"],
                    observed_at=observed_at,
                    unit=acquisition_unit,
                    distribution=info,
                )
                await self.service.storage.call(
                    self.repository.polling.commit, prepared, _lane_priority=0
                )
                self.intake.start()
                job_ids = []
            else:
                job_ids = await self.storage_call(
                    self.distribution.ingest,
                    run["run_id"],
                    token,
                    source,
                    result.messages,
                    checkpoint=result.next_checkpoint,
                    coverage=result.window_coverage,
                    done=result.window_done,
                    deadline_seconds=self.service.enrichment_retry_deadline_seconds,
                    pipeline_version=self.service.enrichment_pipeline_version,
                )
            if mode == "REALTIME" and (result.window_done or getattr(self, "_long_running", False)):
                await self.storage_call(
                    self._record_distribution_poll,
                    source,
                    roster,
                    run,
                    _lane_priority=0,
                    coverage=result.window_coverage,
                    observed_at=utc_now(),
                    acquisition_checkpoint=result.next_checkpoint,
                    acquisition_unit=acquisition_unit,
                )
            return PollExecutionResult(
                binding_id=f"shared:{source.source_id}",
                poll_run_id=run["run_id"],
                collected_count=len(result.messages),
                queued_count=len(job_ids),
                enrichment_job_ids=job_ids,
                next_checkpoint=result.next_checkpoint,
                window_done=result.window_done,
                window_coverage=result.window_coverage,
            )
        except CollectionStopped:
            await self.storage_call(self.distribution.release_run, run["run_id"], token)
            raise
        except Exception as exc:
            await self.storage_call(self.distribution.release_run, run["run_id"], token)
            from doxagent.source_maintenance.signals import shared

            shared(
                source,
                [b for b, _, _ in roster],
                run["run_id"],
                attempted_at,
                mode,
                error=type(exc).__name__,
            )
            for binding, _, _ in roster:
                failed = await self.storage_call(
                    self.service.record_poll_failure,
                    binding,
                    code=type(exc).__name__,
                    message=str(exc),
                    attempted_at=attempted_at,
                    acquisition_unit=acquisition_unit,
                    source_version=source.version,
                )
                if mode == "REALTIME":
                    next_due = self._next_distribution_due(source)
                    await self.storage_call(
                        self.repository.save_poll_unit_state,
                        failed.model_copy(
                            update={
                                "target_due_at": next_due,
                                "next_dispatch_at": next_due,
                                "updated_at": utc_now(),
                            }
                        ),
                        acquisition_unit,
                    )
            raise

    @staticmethod
    def _next_distribution_due(source: SourceDefinition) -> datetime:
        interval = source.default_polling_config.target_interval_seconds
        return datetime.fromtimestamp((int(utc_now().timestamp()) // interval + 1) * interval, UTC)

    def _prepare_distribution_run(self, **kwargs):
        # One short owner transaction; ticker fanout must not round-trip through
        # the application queue once for every subscription before fetching.
        with self.repository.atomic_intake():
            run = self.distribution.get_or_create_run(**kwargs)
            for binding, revision, admission in kwargs["roster"]:
                self.distribution.attach_target(run["run_id"], binding, revision, admission)
            return run

    def _record_distribution_poll(
        self,
        source: SourceDefinition,
        roster: list[tuple[TickerSourceBinding, int, JsonObject]],
        run: JsonObject,
        *,
        coverage: str | None = None,
        observed_at: datetime | None = None,
        acquisition_checkpoint: JsonObject | None = None,
        acquisition_unit: str = "SHARED",
    ) -> None:
        """Project one shared acquisition onto its subscribed ticker poll states."""
        with self.repository.atomic_intake():
            with self.repository.transaction() as db:
                if observed_at is not None and run.get("run_id"):
                    db.execute(
                        "INSERT OR REPLACE INTO distribution_acquisitions VALUES(?,?)",
                        (run["run_id"], observed_at.isoformat()),
                    )
                acquired = (
                    db.execute(
                        "SELECT observed_at FROM distribution_acquisitions WHERE run_id=?",
                        (run["run_id"],),
                    ).fetchone()
                    if run.get("run_id")
                    else None
                )
            observed_at = (
                datetime.fromisoformat(acquired[0])
                if acquired
                else observed_at or datetime.fromisoformat(str(run["created_at"]))
            )
            partial = (coverage or str(run["coverage"])) != "COMPLETE"
            for binding, _, _ in roster:
                previous = self._distribution_state(binding, acquisition_unit)
                next_due = self._next_due(
                    previous.target_due_at or observed_at,
                    observed_at,
                    binding.polling.target_interval_seconds,
                )
                if previous.last_attempt_at and previous.last_attempt_at >= observed_at:
                    continue
                checkpoint = previous.checkpoint
                if (
                    source.adapter_ref in SHARED_HTTP_CHECKPOINTS
                    and acquisition_checkpoint is not None
                    and run["mode"] == "REALTIME"
                ):
                    checkpoint = {
                        **checkpoint,
                        SHARED_HTTP_CHECKPOINTS[source.adapter_ref]: {
                            "source_version": source.version,
                            "checkpoint": acquisition_checkpoint,
                        },
                    }
                self.repository.save_poll_unit_state(
                    previous.model_copy(
                        update={
                            "source_definition_version": source.version,
                            "binding_version": binding.version,
                            "last_capture_at": observed_at,
                            "status": PollStatus.PARTIAL if partial else PollStatus.SUCCEEDED,
                            "last_attempt_at": observed_at,
                            "last_success_at": observed_at,
                            "last_failure_at": observed_at if partial else None,
                            "failure_since": observed_at if partial else None,
                            "last_error_code": "SOURCE_WINDOW_PARTIAL" if partial else None,
                            "last_error_message": "Shared source coverage is partial"
                            if partial
                            else None,
                            "consecutive_failures": 0,
                            "target_due_at": next_due,
                            "next_dispatch_at": next_due,
                            "updated_at": utc_now(),
                            "checkpoint": checkpoint,
                        }
                    ),
                    acquisition_unit,
                )

    @source_routed
    async def _poll(
        self,
        source,
        binding,
        attempted_at,
        **kwargs,
    ):
        from doxagent.site_strategy.client import ACCESS_RESULTS

        from .poll_trace import DEMAND, REQUESTS

        if kwargs.get("window_cutoff") and "acquisition_unit" not in kwargs:
            kwargs["acquisition_unit"] = "TAIL:SWEEP"

        access_token = ACCESS_RESULTS.set([])
        timing_token = POLL_TIMINGS.set({})
        requests = []
        request_token = REQUESTS.set(requests)
        demand = {
            "due_at": attempted_at.isoformat(),
            "rate": self._source_demand.get(source.source_id, 0)
            / (2 if source.source_id == "yahoo_finance_news" else 1),
            "work_class": "backfill"
            if kwargs.get("window_cutoff")
            or (
                kwargs.get("acquisition_unit", "").startswith("TAIL:")
                and kwargs.get("acquisition_unit") != "TAIL:YAHOO_TIME"
            )
            else "foreground",
        }
        demand_token = DEMAND.set(demand)
        run_id = new_id("poll")
        result, error = None, None
        run_details = {}
        try:
            check_request_allowed()
            result = await self._poll_impl(
                source, binding, attempted_at, poll_run_id=run_id, run_details=run_details, **kwargs
            )
            return result
        except CollectionStopped:
            result = PollExecutionResult(
                binding_id=binding.binding_id, poll_run_id=run_id,
                site_access_deferred=True, site_access_deferred_reason="TEMPORARY_COLLECTION_ENDED",
                window_done=False,
            )
            return result
        except BaseException as exc:
            error = exc
            raise
        finally:
            status = (
                "CANCELLED"
                if isinstance(error, asyncio.CancelledError)
                else "FAILED"
                if error or (result and (result.error_code or result.invalid_count))
                else "DEFERRED"
                if result and result.site_access_deferred
                else "CAPTURED"
            )
            reason = (
                type(error).__name__
                if error
                else (result.error_code or result.site_access_deferred_reason if result else None)
            )
            scope_state = run_details.pop("_scope_state", None)
            def finish():
                with self.repository.atomic_intake():
                    if scope_state:
                        self._save_poll_scope_state(*scope_state)
                    self.repository.polling.update_run(
                        run_id, status=status, reason=reason, requests=requests, **run_details
                    )
            try:
                await self.service.storage.call(finish, _lane_priority=0)
            finally:
                ACCESS_RESULTS.reset(access_token)
                POLL_TIMINGS.reset(timing_token)
                REQUESTS.reset(request_token)
                DEMAND.reset(demand_token)
                self._wake.set()

    async def _poll_impl(
        self,
        source: SourceDefinition,
        binding: TickerSourceBinding,
        attempted_at: datetime,
        *,
        window_start: datetime | None = None,
        window_cutoff: datetime | None = None,
        checkpoint: JsonObject | None = None,
        admission_context: AdmissionContext | None = None,
        query_plan_override: JsonObject | None = None,
        freeze_query_plan: bool = False,
        poll_run_id: str | None = None,
        acquisition_unit: str = "ALL",
        run_details: dict | None = None,
    ) -> PollExecutionResult:
        # Snapshot source and binding for this poll. Updates become visible on the next poll.
        state = await self.read_call(self.repository.get_poll_unit_state, binding, acquisition_unit)
        state = state.model_copy(
            update={"source_definition_version": source.version, "binding_version": binding.version,
                    "target_due_at": self._current_due(state.target_due_at, attempted_at, binding.polling.target_interval_seconds)}
        )
        if source.source_id == "yahoo_finance_news" and acquisition_unit == "TAIL:YAHOO_TIME":
            primary = await self.read_call(
                self.repository.get_poll_unit_state, binding, "SUPPLEMENT_HEAD"
            )
            state = state.model_copy(update={"bootstrap_complete": primary.bootstrap_complete})
        if source.source_id == "yahoo_finance_news" and acquisition_unit == "SUPPLEMENT_HEAD":
            head = await self.read_call(self.repository.get_poll_unit_state, binding, "HEAD")
            tail = await self.read_call(
                self.repository.get_poll_unit_state, binding, "TAIL:YAHOO_TIME"
            )
            state = state.model_copy(
                update={
                    "checkpoint": {
                        **state.checkpoint,
                        "verified_article_dates": {
                            **state.checkpoint.get("verified_article_dates", {}),
                            **head.checkpoint.get("verified_article_dates", {}),
                            **tail.checkpoint.get("verified_article_dates", {}),
                        },
                    }
                }
            )
        from .poll_trace import DEMAND

        if DEMAND.get() is not None:
            DEMAND.get()["due_at"] = (state.target_due_at or attempted_at).isoformat()
        started_at = self._clock()
        limiter = self._limiter(source, acquisition_unit)
        poll_run_id = poll_run_id or new_id("poll")
        await self.service.storage.call(
            self.repository.polling.start,
            poll_run_id,
            source,
            binding,
            state.target_due_at or attempted_at,
            attempted_at,
            acquisition_unit,
            _lane_priority=0,
        )
        try:
            query_plan = (
                query_plan_override
                if freeze_query_plan
                else (
                    build_query_plan(source, found[1], found[0]).model_dump(mode="json")
                    if source.acquisition_mode is AcquisitionMode.BY_SEARCH
                    and (found := await self.read_call(self.terms.get, binding.ticker))
                    is not None
                    else None
                )
            )
        except ValueError as exc:
            if str(exc).startswith("CONFIG_INCOMPLETE"):
                return PollExecutionResult(
                    poll_run_id=poll_run_id,
                    binding_id=binding.binding_id,
                    error_code="CONFIG_INCOMPLETE",
                    window_coverage="PARTIAL",
                )
            raise
        page_receipts = []
        page_intakes = []
        page_captures = []
        accept_elapsed_ms = 0
        observed = set()
        last_checkpoint = None

        async def on_page(messages, *, checkpoint=None, observed_at=None, **evidence):
            nonlocal accept_elapsed_ms, last_checkpoint
            from doxagent.site_strategy.client import ACCESS_RESULTS

            intake_started = self._clock()
            access = ACCESS_RESULTS.get() or []
            from .poll_trace import REQUESTS

            listing = [
                r
                for r in REQUESTS.get() or []
                if r.get("role", "listing") == "listing" and r.get("response_received_at")
            ]
            captured_at = (
                observed_at
                or (
                    datetime.fromisoformat(listing[-1]["response_received_at"]) if listing else None
                )
                or (access[-1].capture_acquired_at if access else None)
                or utc_now()
            )
            from .schema import PollResult

            prepared = await self.service.storage.prepare(
                self.repository.polling.prepare,
                source=source,
                binding=binding,
                result=PollResult(messages=messages),
                observed_at=captured_at,
                run_id=poll_run_id,
                admission_context=admission_context,
                checkpoint=checkpoint if window_cutoff is None else None,
                bootstrap=(evidence['bootstrap'] if isinstance(evidence.get('bootstrap'), bool) else
                    window_cutoff is None and acquisition_unit != 'TAIL:YAHOO_TIME' and not state.bootstrap_complete),
                unit="HEAD" if acquisition_unit == "ALL" else acquisition_unit,
            )
            await self.service.storage.call(
                self.repository.polling.commit, prepared, _lane_priority=0
            )
            self.intake.start()
            if not getattr(self, "_long_running", False) or window_cutoff is not None:
                receipt = await self.intake.wait(poll_run_id)
                # wait returns the cumulative run; pages are accounted once at the end.
                page_receipts.clear()
            else:
                receipt = PollExecutionResult(
                    binding_id=binding.binding_id, collected_count=len(messages)
                )
            page_receipts.append(receipt)
            page_captures.append(captured_at)
            page_intakes.append(utc_now())
            accept_elapsed_ms += int((self._clock() - intake_started) * 1000)
            last_checkpoint = checkpoint
            observed.update((m.external_id, m.url) for m in messages)
            return receipt

        context = PollContext(
            is_bootstrap=not state.bootstrap_complete,
            is_gap_recovery=window_start is not None or window_cutoff is not None,
            poll_run_id=poll_run_id,
            ticker=binding.ticker,
            acquisition_unit=acquisition_unit,
            source=source,
            binding=binding,
            checkpoint=state.checkpoint if checkpoint is None else checkpoint,
            query_plan=query_plan,
            window_start=window_start,
            window_cutoff=window_cutoff,
            requested_at=attempted_at,
            request_permit=limiter.permit,
            on_page=on_page,
        )
        try:
            adapter = self.adapters.resolve(source.adapter_ref, source_version=source.version)
            acquisition_started = self._clock()
            result = await adapter.poll(context)
            acquisition_ms = int((self._clock() - acquisition_started) * 1000)
            final_accept_started = self._clock()
            result = result.model_copy(
                update={
                    "messages": [
                        m for m in result.messages if (m.external_id, m.url) not in observed
                    ]
                }
            )
            result = result.model_copy(
                update={
                    "acquisition_metadata": {
                        **result.acquisition_metadata,
                        "poll_run_id": poll_run_id,
                        "durable_capture": True,
                    }
                }
            )
            if result.site_access_deferred and not page_receipts and not result.messages:
                retry_at = result.site_access_retry_not_before or (
                    attempted_at + timedelta(seconds=5)
                )
                retry_at = max(retry_at, attempted_at + timedelta(seconds=1))
                await self.storage_call(
                    lambda value: self._save_poll_scope_state(
                        value, acquisition_unit, source, binding
                    ),
                    state.model_copy(
                        update={
                            "next_dispatch_at": retry_at,
                            "last_dispatch_at": attempted_at,
                            "last_latency_ms": max(0, int((self._clock() - started_at) * 1000)),
                            "updated_at": attempted_at,
                            "last_error_code": "SOURCE_DEFERRED:"
                            + (result.site_access_deferred_reason or "access_budget_deferred"),
                            "last_error_message": "Acquisition deferred until "
                            + retry_at.isoformat(),
                        }
                    ),
                )
                return PollExecutionResult(
                    poll_run_id=poll_run_id,
                    binding_id=binding.binding_id,
                    next_checkpoint=state.checkpoint,
                    window_coverage="UNKNOWN",
                    window_done=False,
                    site_access_deferred=True,
                    site_access_deferred_reason=result.site_access_deferred_reason,
                    site_access_retry_not_before=retry_at,
                )
            deferred_intake = getattr(self, "_long_running", False) and window_cutoff is None
            valid_list = (
                not result.failures
                and not result.site_access_deferred
                and (result.acquisition_metadata.get("query_attempt_count") != 0)
            )
            if source.source_id != "corporate_ir_press_releases" and (
                result.messages
                or (valid_list and not page_captures)
                or (not page_captures and result.next_checkpoint and result.next_checkpoint != last_checkpoint)
            ):
                # Final non-paged inputs use the same durable receipt as early pages.
                await on_page(
                    result.messages,
                    checkpoint=result.next_checkpoint,
                    observed_at=page_captures[-1]
                    if not result.messages and page_captures
                    else None,
                )
                result = result.model_copy(update={"messages": []})
            if source.source_id == "corporate_ir_press_releases":
                execution = await self.storage_call(
                    self.service._accept_poll_result,
                    source=source,
                    binding=binding,
                    result=result,
                    attempted_at=attempted_at,
                    admission_context=admission_context,
                    persist_poll_state=False,
                    bootstrap_override=False,
                    strict_intake=True,
                )
            elif not result.messages and not result.failures:
                # Messages are already durable and owned by CaptureIntake. An
                # empty health-only accept needlessly queues another DB writer.
                execution = PollExecutionResult(
                    binding_id=binding.binding_id,
                    poll_run_id=poll_run_id,
                    next_checkpoint=result.next_checkpoint,
                    window_coverage=result.window_coverage,
                    window_done=result.window_done,
                    site_access_deferred=result.site_access_deferred,
                    site_access_deferred_reason=result.site_access_deferred_reason,
                    site_access_retry_not_before=result.site_access_retry_not_before,
                    crawler_execution_id=str(value)
                    if (value := result.acquisition_metadata.get("crawler_execution_id"))
                    else None,
                )
            else:
                execution = await self.storage_call(
                    self.service._accept_poll_result,
                    source=source,
                    binding=binding,
                    result=result,
                    attempted_at=attempted_at,
                    admission_context=admission_context,
                    persist_poll_state=False,
                )
            accept_elapsed_ms += int((self._clock() - final_accept_started) * 1000)
            for receipt in page_receipts:
                execution.enrichment_job_ids.extend(receipt.enrichment_job_ids)
                for field in (
                    "collected_count",
                    "filtered_count",
                    "queued_count",
                    "inserted_count",
                    "duplicate_count",
                    "revision_count",
                    "invalid_count",
                    "published_count",
                    "bootstrap_suppressed_count",
                ):
                    setattr(execution, field, getattr(execution, field) + getattr(receipt, field))
            details = dict(
                outcome_status="DEFERRED"
                if result.site_access_deferred
                else "FAILED"
                if result.failures
                else "CAPTURED",
                capture_count=len(page_captures),
                intake_pending=deferred_intake,
                acquisition_ms=acquisition_ms,
            )
            if run_details is not None:
                run_details.update(details)
            else:
                await self.service.storage.call(
                    self.repository.polling.update_run, poll_run_id, **details, _lane_priority=0
                )
            if window_cutoff is not None:
                return execution
            if execution.crawler_execution_id and self.adapters.crawler_plane is not None:
                await self.storage_call(
                    self.adapters.crawler_plane.record_message_bus_telemetry,
                    execution.crawler_execution_id,
                    execution.model_dump(mode="json"),
                )
            refreshed = await self.read_call(
                self.repository.get_poll_unit_state, binding, acquisition_unit
            )
            from doxagent.site_strategy.client import ACCESS_RESULTS

            accesses = ACCESS_RESULTS.get() or []
            timing = {
                **(POLL_TIMINGS.get() or {}),
                "acquisition_ms": acquisition_ms,
                "accept_ms": accept_elapsed_ms,
                "site_wait_ms": sum(
                    a.queue_wait_ms for a in accesses if not a.served_from_shared_capture
                ),
                "runtime_ms": sum(
                    a.network_ms for a in accesses if not a.served_from_shared_capture
                ),
            }
            if run_details is not None:
                run_details['stages_ms'] = timing
            captures = [
                a.capture_acquired_at for a in accesses if a.capture_acquired_at
            ] + page_captures
            target_due = self._next_due(
                refreshed.target_due_at or attempted_at,
                attempted_at,
                binding.polling.target_interval_seconds,
            )
            next_dispatch = target_due
            if not result.window_done:
                next_dispatch = utc_now() + timedelta(seconds=1)
            if result.optional_next_poll_hint:
                next_dispatch = result.optional_next_poll_hint
            if result.site_access_retry_not_before:
                next_dispatch = max(next_dispatch, result.site_access_retry_not_before)
            final_state = refreshed.model_copy(
                    update={
                        "target_due_at": target_due,
                        "next_dispatch_at": next_dispatch,
                        "last_latency_ms": max(0, int((self._clock() - started_at) * 1000)),
                        "last_stage_timings": timing,
                        "last_attempt_at": attempted_at,
                        "last_dispatch_at": attempted_at,
                        "status": PollStatus.PARTIAL
                        if result.site_access_deferred
                        else PollStatus.PARTIAL
                        if result.failures and page_captures
                        else PollStatus.FAILED
                        if result.failures
                        else PollStatus.SUCCEEDED,
                        "bootstrap_complete": valid_list or refreshed.bootstrap_complete,
                        "last_error_code": None if valid_list else refreshed.last_error_code,
                        "last_error_message": None if valid_list else refreshed.last_error_message,
                        "failure_since": None if valid_list else refreshed.failure_since,
                        "consecutive_failures": 0 if valid_list else refreshed.consecutive_failures,
                        "last_success_at": utc_now() if valid_list else refreshed.last_success_at,
                        "last_first_capture_at": refreshed.last_first_capture_at
                        if deferred_intake
                        else page_captures[0]
                        if page_captures
                        else max(captures)
                        if captures
                        else None,
                        "last_first_intake_at": refreshed.last_first_intake_at
                        if deferred_intake
                        else page_intakes[0]
                        if page_intakes
                        else utc_now(),
                        "last_final_intake_at": refreshed.last_final_intake_at
                        if deferred_intake
                        else utc_now(),
                        "last_capture_at": max(
                            [at for at in [refreshed.last_capture_at, *captures] if at],
                            default=None,
                        ),
                        "updated_at": attempted_at,
                    }
                )
            if run_details is not None:
                run_details["_scope_state"] = (final_state, acquisition_unit, source, binding)
            else:
                await self.storage_call(
                    self._save_poll_scope_state, final_state, acquisition_unit, source, binding,
                    _lane_priority=0,
                )
            return execution
        except Exception as exc:
            crawler_execution_id = getattr(exc, "crawler_execution_id", None)
            await self.storage_call(
                self.service.record_poll_failure,
                binding,
                code=type(exc).__name__,
                message=str(exc),
                attempted_at=attempted_at,
                acquisition_unit=acquisition_unit,
                source_version=source.version,
            )
            if window_cutoff is None:
                refreshed = await self.read_call(
                    self.repository.get_poll_unit_state, binding, acquisition_unit
                )
                await self.storage_call(
                    lambda value: self._save_poll_scope_state(
                        value, acquisition_unit, source, binding
                    ),
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
                    ),
                )
            execution = PollExecutionResult(
                poll_run_id=poll_run_id,
                binding_id=binding.binding_id,
                crawler_execution_id=(str(crawler_execution_id) if crawler_execution_id else None),
                error_code=type(exc).__name__,
                error_message=str(exc)[:1000],
            )
            if execution.crawler_execution_id and self.adapters.crawler_plane is not None:
                await self.storage_call(
                    self.adapters.crawler_plane.record_message_bus_telemetry,
                    execution.crawler_execution_id,
                    execution.model_dump(mode="json"),
                )
            return execution

    @staticmethod
    def _current_due(previous_due, now, interval_seconds):
        value = previous_due or now
        # Missing old slots remain in the durable SCHEDULE denominator. A new
        # physical request belongs to the current grid slot, never an hours-old retry.
        return value + timedelta(seconds=max(0, int((now-value).total_seconds() // interval_seconds))*interval_seconds)

    @staticmethod
    def _next_due(previous_due: datetime, now: datetime, interval_seconds: int) -> datetime:
        return previous_due + timedelta(seconds=max(0, int((now-previous_due).total_seconds() // interval_seconds)+1)*interval_seconds)

    def _limiter(self, source: SourceDefinition, unit="ALL") -> SchedulerGroupLimiter:
        key = (
            source.source_id if source.scheduler_group == "newswire_rss" else source.scheduler_group
        )
        if source.source_id == "yahoo_finance_news":
            key += ":browser" if unit in {"SUPPLEMENT_HEAD", "TAIL:YAHOO_TIME"} else ":rss"
        current = self._limiters.get(key)
        constraints = self._effective_constraints(source.scheduler_group)
        if source.scheduler_constraints.capacity_mode == "adaptive":
            ceiling = (
                min(
                    s.scheduler_constraints.max_concurrency
                    for s in self._configured_sources
                    if s.scheduler_group == source.scheduler_group
                )
                if hasattr(self, "_configured_sources")
                else source.scheduler_constraints.max_concurrency
            )
            rate = self._source_demand.get(
                source.source_id, self._group_demand.get(source.scheduler_group, 0)
            )
            if source.source_id == "yahoo_finance_news":
                rate /= 2
            capacity, _ = required_capacity(
                rate, current[1].cost.seconds if current else 1, ceiling=ceiling
            )
            constraints = constraints.model_copy(update={"max_concurrency": capacity})
        if (
            source.kind.value == "crawler"
            and source.source_id != "globenewswire_search"
            and (
                source.source_id != "yahoo_finance_news"
                or unit in {"SUPPLEMENT_HEAD", "TAIL:YAHOO_TIME"}
            )
        ):
            constraints = constraints.model_copy(update={"minimum_request_gap_seconds": 0})
        if current is not None:
            limiter = current[1]
            delta = constraints.max_concurrency - limiter.constraints.max_concurrency
            if delta > 0:
                repaid = min(limiter._shrink_debt, delta)
                limiter._shrink_debt -= repaid
                for _ in range(delta - repaid):
                    limiter._semaphore.release()
            elif delta < 0:
                limiter._shrink_debt += -delta
                available = min(limiter._semaphore._value, limiter._shrink_debt)
                limiter._semaphore._value -= available
                limiter._shrink_debt -= available
            limiter.constraints = constraints
            self._limiters[key] = (constraints, limiter)
            return limiter

        def on_request():
            self._request_starts[key] = utc_now()
            self._wake.set()

        from .schema import SchedulerGroupState

        cached = getattr(self, "_group_state_cache", {}).get(key)
        persisted = (
            SchedulerGroupState.model_validate_json(cached)
            if cached
            else (
                self.repository.get_scheduler_group_state(key)
                if not hasattr(self, "_group_state_cache")
                else SchedulerGroupState(scheduler_group=key)
            )
        )
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
        self._limiters[key] = (constraints, limiter)
        return limiter

    def _flush_request_starts(self, starts):
        with self.repository.atomic_intake():
            for group, started in starts.items():
                self.repository.record_scheduler_request_start(group, started)

    def _effective_constraints(
        self, group: str, *, sources: list[SourceDefinition] | None = None
    ) -> SchedulerConstraints:
        sources = [
            source
            for source in (
                getattr(self, "_configured_sources", None) or self.repository.list_sources()
                if sources is None
                else sources
            )
            if source.scheduler_group == group
        ]
        if not sources:
            return SchedulerConstraints()
        configured = min(source.scheduler_constraints.max_concurrency for source in sources)
        adaptive = all(
            source.scheduler_constraints.capacity_mode == "adaptive" for source in sources
        )
        current = self._limiters.get(group)
        cost = current[1].cost.seconds if current else 1.0
        requested, shortfall = required_capacity(
            self._group_demand.get(group, 0), cost, ceiling=configured, floor=1
        )
        return SchedulerConstraints(
            minimum_request_gap_seconds=max(
                source.scheduler_constraints.minimum_request_gap_seconds for source in sources
            ),
            max_concurrency=requested if adaptive else configured,
        )

    def _update_capacity_alerts(
        self, bindings: list[tuple[SourceDefinition, TickerSourceBinding]]
    ) -> None:
        signature = (tuple((s.source_id,s.version,b.binding_id,b.version) for s,b in bindings),
                     getattr(self, '_intake_backlogged', False))
        now = time.monotonic()
        if signature == getattr(self, '_capacity_alert_signature', None) and now < getattr(self, '_capacity_alert_at', 0):
            return
        self._capacity_alert_signature, self._capacity_alert_at = signature, now+60
        with self.repository.atomic_intake():
            self._write_capacity_alerts(bindings)

    def _write_capacity_alerts(self, bindings):
        from .pressure import background_allowed, cpu_pressure

        if not background_allowed():
            self.repository.upsert_alert(
                OperationalAlert(
                    alert_key="news_cpu_pressure",
                    severity=AlertSeverity.WARNING,
                    code="cpu_pressure_background_paused",
                    message="CPU pressure pauses history and old article rechecks; realtime heads remain scheduled",
                    metadata={"cpu_psi_avg60": cpu_pressure()},
                )
            )
        else:
            self.repository.resolve_alert("news_cpu_pressure")
        if getattr(self, "_intake_backlogged", False):
            self.repository.upsert_alert(
                OperationalAlert(
                    alert_key="news_capture_intake_backlog",
                    severity=AlertSeverity.WARNING,
                    code="capture_intake_delayed",
                    message="Durable intake exceeds 3 seconds; optional history is paused, realtime heads remain scheduled",
                )
            )
        else:
            self.repository.resolve_alert("news_capture_intake_backlog")
        grouped: dict[str, list[TickerSourceBinding]] = defaultdict(list)
        for source, binding in bindings:
            grouped[source.scheduler_group].append(binding)
        sources = self.repository.list_sources()
        for group, values in grouped.items():
            constraints = self._effective_constraints(group, sources=sources)
            demand = self._group_demand.get(
                group, sum(1 / value.polling.target_interval_seconds for value in values)
            )
            gap = constraints.minimum_request_gap_seconds
            group_sources = [s for s in sources if s.scheduler_group == group]
            ceiling = min(s.scheduler_constraints.max_concurrency for s in group_sources)
            costs = [
                limiter.cost.seconds
                for key, (_, limiter) in self._limiters.items()
                if key.startswith(group) or any(key.startswith(s.source_id) for s in group_sources)
            ]
            cost = max(costs, default=1)
            needed = __import__("math").ceil(demand * cost / 0.75)
            capacity = min(float("inf") if gap == 0 else 1 / gap, ceiling * 0.75 / max(0.01, cost))
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
                            "required_slots": needed,
                            "configured_ceiling": ceiling,
                            "service_cost_seconds": cost,
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
