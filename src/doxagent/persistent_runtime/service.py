"""Service orchestration for Persistent Runtime Execution."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, TypeVar, cast

from doxagent.monitoring.schema import EventStreamItem, SourceType
from doxagent.persistent_runtime.repository import (
    InMemoryPersistentRuntimeRepository,
    PersistentRuntimeRepository,
    SQLitePersistentRuntimeRepository,
)
from doxagent.persistent_runtime.router import RouteEngine
from doxagent.persistent_runtime.schema import (
    A2Result,
    ArchiveItem,
    Conviction,
    ExecutionExceptionLog,
    IngestQueueItem,
    KnownEventsPatchLog,
    O3PrimaryAction,
    O3Result,
    O3RuntimeBudget,
    RouteDecision,
    RuntimeExecutionObservation,
    RuntimeExecutionRecord,
    RuntimeNodeTrace,
    RuntimeObjectionRecord,
    RuntimeRoute,
    RuntimeSourceMessage,
    RuntimeWorkerTimeout,
    SizeBucket,
    TradeIntent,
    TradeRecordStatus,
    TradeSide,
    TradingRecord,
    W1Confidence,
    W1Result,
    W2Result,
    W2Type,
    runtime_duplicate_keys,
)
from doxagent.persistent_runtime.workers import HeuristicW1Worker, HeuristicW2Worker
from doxagent.settings import DoxAgentSettings

JsonObject = dict[str, object]
T = TypeVar("T")


@dataclass
class _PendingO3Item:
    message: RuntimeSourceMessage
    context: JsonObject
    w1: W1Result | None
    w2: W2Result | None
    a2: A2Result | None
    decision: RouteDecision
    exception_ids: list[str]
    node_traces: list[RuntimeNodeTrace]


@dataclass
class _WorkerOutcome:
    result: object | None
    error: Exception | None
    trace: RuntimeNodeTrace


class W1Worker(Protocol):
    def classify(self, message: RuntimeSourceMessage, context: JsonObject) -> W1Result:
        ...


class W2Worker(Protocol):
    def classify(self, message: RuntimeSourceMessage, context: JsonObject) -> W2Result:
        ...


class A2Worker(Protocol):
    def verify(self, message: RuntimeSourceMessage, context: JsonObject) -> A2Result:
        ...


class O3Worker(Protocol):
    def judge(
        self,
        message: RuntimeSourceMessage,
        context: JsonObject,
        budget: O3RuntimeBudget,
    ) -> O3Result:
        ...


class PersistentRuntimeExecutionService:
    """Consume Message Bus events and persist runtime routing side effects."""

    def __init__(
        self,
        repository: PersistentRuntimeRepository,
        *,
        route_engine: RouteEngine | None = None,
        w1_worker: W1Worker | None = None,
        w2_worker: W2Worker | None = None,
        a2_worker: A2Worker | None = None,
        o3_worker: O3Worker | None = None,
        o3_budget: O3RuntimeBudget | None = None,
    ) -> None:
        self.repository = repository
        self.route_engine = route_engine or RouteEngine()
        self.w1_worker = w1_worker
        self.w2_worker = w2_worker
        self.a2_worker = a2_worker
        self.o3_worker = o3_worker
        self.o3_budget = o3_budget or O3RuntimeBudget()

    @classmethod
    def from_settings(
        cls,
        settings: DoxAgentSettings | None = None,
        *,
        route_engine: RouteEngine | None = None,
        w1_worker: W1Worker | None = None,
        w2_worker: W2Worker | None = None,
        a2_worker: A2Worker | None = None,
        o3_worker: O3Worker | None = None,
        o3_budget: O3RuntimeBudget | None = None,
    ) -> PersistentRuntimeExecutionService:
        resolved = settings or DoxAgentSettings()
        if resolved.persistent_runtime_storage_mode == "memory":
            repository: PersistentRuntimeRepository = InMemoryPersistentRuntimeRepository()
        else:
            repository = SQLitePersistentRuntimeRepository(
                resolved.persistent_runtime_sqlite_path
            )
        return cls(
            repository,
            route_engine=route_engine,
            w1_worker=w1_worker or HeuristicW1Worker(),
            w2_worker=w2_worker or HeuristicW2Worker(),
            a2_worker=a2_worker,
            o3_worker=o3_worker,
            o3_budget=o3_budget,
        )

    def execute_message(
        self,
        message: RuntimeSourceMessage,
        *,
        context: JsonObject | None = None,
    ) -> RuntimeExecutionRecord:
        timing_started_at = datetime.now(UTC)
        timing_started = time.monotonic()
        existing = self.repository.execution_for_source(message.source_message_id)
        if existing is not None:
            return existing

        def finish(record: RuntimeExecutionRecord) -> RuntimeExecutionRecord:
            return self._save_execution_timing(
                record,
                started_at=timing_started_at,
                started=timing_started,
            )

        runtime_context = self._context_with_runtime_known_events(
            context,
            ticker=message.ticker,
        )
        exception_ids: list[str] = []
        node_traces: list[RuntimeNodeTrace] = []
        duplicate = self.repository.execution_for_duplicate(message)
        if duplicate is not None:
            duplicate_key = _first_duplicate_key_match(message, duplicate.source_message)
            decision = self.route_engine.plan_duplicate_archive(
                message,
                duplicate_of_source_message_id=duplicate.source_message.source_message_id,
                duplicate_key=duplicate_key,
            )
            return finish(
                self._persist_terminal(
                    message,
                    decision,
                    context=runtime_context,
                    w1=None,
                    w2=None,
                    a2=None,
                    o3=None,
                    exception_ids=exception_ids,
                    node_traces=node_traces,
                )
            )
        w1, w2 = self._run_w1_w2(message, runtime_context, exception_ids, node_traces)
        if w1 is None:
            decision = self.route_engine.plan_w1_failure(
                message,
                reason="W1 unavailable after retry.",
            )
            return finish(
                self._apply_decision(
                    message,
                    decision,
                    context=runtime_context,
                    w1=None,
                    w2=w2,
                    a2=None,
                    o3=None,
                    exception_ids=exception_ids,
                    node_traces=node_traces,
                )
            )
        if w2 is None:
            decision = self.route_engine.plan_w2_failure(
                message,
                w1=w1,
                reason="W2 unavailable after retry.",
            )
            return finish(
                self._apply_decision(
                    message,
                    decision,
                    context=runtime_context,
                    w1=w1,
                    w2=None,
                    a2=None,
                    o3=None,
                    exception_ids=exception_ids,
                    node_traces=node_traces,
                )
            )
        decision = self.route_engine.plan_initial(message, w1=w1, w2=w2)
        return finish(
            self._apply_decision(
                message,
                decision,
                context=runtime_context,
                w1=w1,
                w2=w2,
                a2=None,
                o3=None,
                exception_ids=exception_ids,
                node_traces=node_traces,
            )
        )

    def execute_event(
        self,
        event: EventStreamItem,
        *,
        context: JsonObject | None = None,
        mark_consumed: Callable[[str], object] | None = None,
    ) -> RuntimeExecutionRecord:
        event_started_at = datetime.now(UTC)
        event_started = time.monotonic()
        convert_started = time.monotonic()
        message = RuntimeSourceMessage.from_event(event)
        event_to_message_ms = _duration_ms(convert_started)
        execute_started = time.monotonic()
        record = self.execute_message(message, context=context)
        execute_message_ms = _duration_ms(execute_started)
        mark_consumed_ms = 0
        if mark_consumed is not None:
            mark_started = time.monotonic()
            mark_consumed(event.event_id)
            mark_consumed_ms = _duration_ms(mark_started)
        event_timing = {
            "event_id": event.event_id,
            "stream_offset": event.stream_offset,
            "event_time": _dt_json(event.event_time),
            "event_received_at": _dt_json(event_started_at),
            "event_to_message_ms": event_to_message_ms,
            "execute_message_ms": execute_message_ms,
            "mark_consumed_ms": mark_consumed_ms,
            "event_total_ms": _duration_ms(event_started),
        }
        record = self._merge_execution_timing(record, {"event_layer": event_timing})
        return record

    def execute_events(
        self,
        events: list[EventStreamItem],
        *,
        context: JsonObject | None = None,
        mark_consumed: Callable[[str], object] | None = None,
    ) -> list[RuntimeExecutionRecord]:
        records: list[RuntimeExecutionRecord] = []
        social_batches: dict[tuple[str, str], list[EventStreamItem]] = {}
        for event in sorted(events, key=lambda item: item.stream_offset):
            message = RuntimeSourceMessage.from_event(event)
            if message.source_type is SourceType.SOCIAL:
                batch_window_id = _batch_window_id(message)
                social_batches.setdefault((message.ticker, batch_window_id), []).append(event)
                continue
            records.append(
                self.execute_event(event, context=context, mark_consumed=mark_consumed)
            )
        for (ticker, batch_window_id), batch_events in social_batches.items():
            batch_records = self.execute_social_batch(
                [RuntimeSourceMessage.from_event(event) for event in batch_events],
                ticker=ticker,
                batch_window_id=batch_window_id,
                context=context,
            )
            records.extend(batch_records)
            if mark_consumed is not None:
                for event in batch_events:
                    mark_consumed(event.event_id)
        return records

    def recent_executions(
        self,
        *,
        ticker: str | None = None,
    ) -> list[RuntimeExecutionRecord]:
        return self.repository.list_executions(ticker=ticker)

    def runtime_observations(
        self,
        *,
        ticker: str | None = None,
    ) -> list[RuntimeExecutionObservation]:
        trading_sources = {
            item.source_message_id
            for item in self.repository.list_trading_records(ticker=ticker)
        }
        ingest_sources = {
            item.source_message_id for item in self.repository.list_ingest_queue(ticker=ticker)
        }
        archive_sources = {
            item.source_message_id for item in self.repository.list_archive(ticker=ticker)
        }
        known_event_sources = {
            item.source_message_id
            for item in self.repository.list_known_events_patch_logs(ticker=ticker)
        }
        objections_by_source: dict[str, set[O3PrimaryAction]] = {}
        for objection in self.repository.list_objections(ticker=ticker):
            objections_by_source.setdefault(objection.source_message_id, set()).add(
                objection.objection_type
            )
        exceptions_by_source: dict[str, list[str]] = {}
        for exception in self.repository.list_exceptions(ticker=ticker):
            exceptions_by_source.setdefault(exception.source_message_id, []).append(
                exception.exception_type
            )
        observations = []
        for execution in self.repository.list_executions(ticker=ticker):
            source_id = execution.source_message.source_message_id
            observation_objections = objections_by_source.get(source_id, set())
            observations.append(
                RuntimeExecutionObservation(
                    source_message_id=source_id,
                    ticker=execution.source_message.ticker,
                    source_type=execution.source_message.source_type,
                    final_route=execution.route_decision.route,
                    message_statuses=list(execution.message_statuses),
                    w1_result=execution.w1_result,
                    w2_result=execution.w2_result,
                    a2_result=execution.a2_result,
                    o3_result=execution.o3_result,
                    entered_trading_records=source_id in trading_sources,
                    entered_ingest_queue=source_id in ingest_sources,
                    entered_archive=source_id in archive_sources,
                    known_events_updated=source_id in known_event_sources,
                    objection_created=O3PrimaryAction.OBJECTION in observation_objections,
                    objection_note_created=(
                        O3PrimaryAction.OBJECTION_NOTE in observation_objections
                    ),
                    exception_types=list(exceptions_by_source.get(source_id, [])),
                    node_durations_ms={
                        trace.node: trace.duration_ms for trace in execution.node_traces
                    },
                    created_at=execution.created_at,
                )
            )
        return observations

    def execute_social_batch(
        self,
        messages: list[RuntimeSourceMessage],
        *,
        ticker: str,
        batch_window_id: str,
        context: JsonObject | None = None,
    ) -> list[RuntimeExecutionRecord]:
        records: list[RuntimeExecutionRecord] = []
        pending_o3: list[_PendingO3Item] = []
        new_items = 0
        non_irrelevant_items = 0
        base_context = self._context_with_runtime_known_events(context, ticker=ticker)
        for index, message in enumerate(messages):
            if message.source_type is not SourceType.SOCIAL:
                raise ValueError("execute_social_batch only accepts social messages.")
            existing = self.repository.execution_for_source(message.source_message_id)
            if existing is not None:
                records.append(existing)
                continue
            batch_context: JsonObject = {
                **base_context,
                "batch_window_id": batch_window_id,
                "batch_item_index": index,
                "batch_size": len(messages),
            }
            exception_ids: list[str] = []
            node_traces: list[RuntimeNodeTrace] = []
            duplicate = self.repository.execution_for_duplicate(message)
            if duplicate is not None:
                duplicate_key = _first_duplicate_key_match(message, duplicate.source_message)
                decision = self.route_engine.plan_duplicate_archive(
                    message,
                    duplicate_of_source_message_id=duplicate.source_message.source_message_id,
                    duplicate_key=duplicate_key,
                )
                records.append(
                    self._persist_terminal(
                        message,
                        decision,
                        context=batch_context,
                        w1=None,
                        w2=None,
                        a2=None,
                        o3=None,
                        exception_ids=exception_ids,
                        node_traces=node_traces,
                    )
                )
                continue
            w1, w2 = self._run_w1_w2(message, batch_context, exception_ids, node_traces)
            if w1 is not None and w1.is_new:
                new_items += 1
            if w2 is not None and w2.type is not W2Type.IRRELEVANT:
                non_irrelevant_items += 1
            if w1 is None:
                decision = self.route_engine.plan_w1_failure(
                    message,
                    reason="W1 unavailable after retry.",
                )
                records.append(
                    self._persist_terminal(
                        message,
                        decision,
                        context=batch_context,
                        w1=None,
                        w2=w2,
                        a2=None,
                        o3=None,
                        exception_ids=exception_ids,
                        node_traces=node_traces,
                    )
                )
                continue
            if w2 is None:
                decision = self.route_engine.plan_w2_failure(
                    message,
                    w1=w1,
                    reason="W2 unavailable after retry.",
                )
                if decision.route is RuntimeRoute.O3:
                    pending_o3.append(
                        _PendingO3Item(
                            message=message,
                            context=batch_context,
                            w1=w1,
                            w2=None,
                            a2=None,
                            decision=decision,
                            exception_ids=exception_ids,
                            node_traces=node_traces,
                        )
                    )
                    continue
                records.append(
                    self._persist_terminal(
                        message,
                        decision,
                        context=batch_context,
                        w1=w1,
                        w2=None,
                        a2=None,
                        o3=None,
                        exception_ids=exception_ids,
                        node_traces=node_traces,
                    )
                )
                continue
            decision = self.route_engine.plan_initial(message, w1=w1, w2=w2)
            if decision.route is RuntimeRoute.A2:
                def run_a2_for_item(
                    current_message: RuntimeSourceMessage = message,
                    current_context: JsonObject = batch_context,
                ) -> A2Result:
                    return self._require_a2().verify(current_message, current_context)

                a2 = self._resolve_worker_result(
                    message,
                    "A2",
                    run_a2_for_item,
                    exception_ids,
                    node_traces,
                )
                next_decision = (
                    self.route_engine.plan_a2_failure(
                        message,
                        w1=w1,
                        w2=w2,
                        reason="A2 unavailable after retry.",
                    )
                    if a2 is None
                    else self.route_engine.plan_after_a2(message, w1=w1, w2=w2, a2=a2)
                )
                if next_decision.route is RuntimeRoute.O3:
                    pending_o3.append(
                        _PendingO3Item(
                            message=message,
                            context=batch_context,
                            w1=w1,
                            w2=w2,
                            a2=a2,
                            decision=next_decision,
                            exception_ids=exception_ids,
                            node_traces=node_traces,
                        )
                    )
                    continue
                records.append(
                    self._persist_terminal(
                        message,
                        next_decision,
                        context=batch_context,
                        w1=w1,
                        w2=w2,
                        a2=a2,
                        o3=None,
                        exception_ids=exception_ids,
                        node_traces=node_traces,
                    )
                )
                continue
            if decision.route is RuntimeRoute.O3:
                pending_o3.append(
                    _PendingO3Item(
                        message=message,
                        context=batch_context,
                        w1=w1,
                        w2=w2,
                        a2=None,
                        decision=decision,
                        exception_ids=exception_ids,
                        node_traces=node_traces,
                    )
                )
                continue
            records.append(
                self._persist_terminal(
                    message,
                    decision,
                    context=batch_context,
                    w1=w1,
                    w2=w2,
                    a2=None,
                    o3=None,
                    exception_ids=exception_ids,
                    node_traces=node_traces,
                )
            )
        records.extend(
            self._run_social_batch_o3(
                pending_o3,
                ticker=ticker,
                batch_window_id=batch_window_id,
                total_items=len(messages),
                new_items=new_items,
                non_irrelevant_items=non_irrelevant_items,
                context=base_context,
            )
        )
        return records

    def _run_social_batch_o3(
        self,
        pending_items: list[_PendingO3Item],
        *,
        ticker: str,
        batch_window_id: str,
        total_items: int,
        new_items: int,
        non_irrelevant_items: int,
        context: JsonObject,
    ) -> list[RuntimeExecutionRecord]:
        if not pending_items:
            return []
        batch_context = _social_batch_o3_context(
            pending_items,
            ticker=ticker,
            batch_window_id=batch_window_id,
            total_items=total_items,
            new_items=new_items,
            non_irrelevant_items=non_irrelevant_items,
            base_context=context,
        )
        outcome = self._run_o3_worker_outcome(
            "O3",
            lambda: self._require_o3().judge(
                pending_items[0].message,
                batch_context,
                self.o3_budget,
            ),
        )
        if outcome.error is not None:
            records: list[RuntimeExecutionRecord] = []
            for item in pending_items:
                exception = self._save_exception(
                    item.message,
                    node="O3",
                    exception_type=_exception_type(outcome.error),
                    message_text=str(outcome.error),
                    payload={
                        "batch_window_id": batch_window_id,
                        "batch_size": total_items,
                        "o3_mode": "social_batch",
                    },
                )
                item.exception_ids.append(exception.exception_id)
                item.node_traces.append(
                    outcome.trace.model_copy(update={"exception_id": exception.exception_id})
                )
                decision = self.route_engine.plan_o3_failure(
                    item.message,
                    upstream_trade_path=item.decision.upstream_trade_path,
                    reason=str(outcome.error)[:200]
                    or "social batch O3 unavailable after retry.",
                    timeout=isinstance(outcome.error, RuntimeWorkerTimeout),
                )
                records.append(
                    self._persist_terminal(
                        item.message,
                        decision,
                        context=item.context,
                        w1=item.w1,
                        w2=item.w2,
                        a2=item.a2,
                        o3=None,
                        exception_ids=item.exception_ids,
                        node_traces=item.node_traces,
                    )
                )
            return records
        o3 = cast(O3Result, outcome.result)
        records = []
        for item in pending_items:
            item.node_traces.append(outcome.trace.model_copy(deep=True))
            next_decision = _decision_from_o3(item.message, o3, item.decision)
            records.append(
                self._persist_terminal(
                    item.message,
                    next_decision,
                    context=item.context,
                    w1=item.w1,
                    w2=item.w2,
                    a2=item.a2,
                    o3=o3,
                    exception_ids=item.exception_ids,
                    node_traces=item.node_traces,
                )
            )
        return records

    def record_o3_timeout_on_trade_path(
        self,
        message: RuntimeSourceMessage,
        *,
        context: JsonObject | None = None,
        w1: W1Result | None = None,
        w2: W2Result | None = None,
    ) -> RuntimeExecutionRecord:
        exception = self._save_exception(
            message,
            node="O3",
            exception_type="o3_timeout",
            message_text="O3 timed out on an upstream trade path.",
            payload={},
        )
        node_traces = [
            RuntimeNodeTrace(
                node="O3",
                status="failed",
                duration_ms=0,
                attempts=2,
                exception_id=exception.exception_id,
            )
        ]
        decision = self.route_engine.plan_o3_failure(
            message,
            upstream_trade_path=True,
            reason="timeout",
            timeout=True,
        )
        return self._persist_terminal(
            message,
            decision,
            context=dict(context or {}),
            w1=w1,
            w2=w2,
            a2=None,
            o3=None,
            exception_ids=[exception.exception_id],
            node_traces=node_traces,
        )

    def _run_w1_w2(
        self,
        message: RuntimeSourceMessage,
        context: JsonObject,
        exception_ids: list[str],
        node_traces: list[RuntimeNodeTrace],
    ) -> tuple[W1Result | None, W2Result | None]:
        with ThreadPoolExecutor(max_workers=2) as executor:
            w1_future = executor.submit(
                self._run_worker_outcome,
                "W1",
                lambda: self._require_w1().classify(message, context),
            )
            w2_future = executor.submit(
                self._run_worker_outcome,
                "W2",
                lambda: self._require_w2().classify(message, context),
            )
            w1 = self._resolve_worker_outcome(
                message,
                "W1",
                w1_future.result(),
                exception_ids,
                node_traces,
            )
            w2 = self._resolve_worker_outcome(
                message,
                "W2",
                w2_future.result(),
                exception_ids,
                node_traces,
            )
        return w1, w2

    def _apply_decision(
        self,
        message: RuntimeSourceMessage,
        decision: RouteDecision,
        *,
        context: JsonObject,
        w1: W1Result | None,
        w2: W2Result | None,
        a2: A2Result | None,
        o3: O3Result | None,
        exception_ids: list[str],
        node_traces: list[RuntimeNodeTrace],
    ) -> RuntimeExecutionRecord:
        if decision.route is RuntimeRoute.A2:
            return self._run_a2_then_apply(
                message,
                decision,
                context=context,
                w1=w1,
                w2=w2,
                exception_ids=exception_ids,
                node_traces=node_traces,
            )
        if decision.route is RuntimeRoute.O3:
            return self._run_o3_then_apply(
                message,
                decision,
                context=context,
                w1=w1,
                w2=w2,
                a2=a2,
                exception_ids=exception_ids,
                node_traces=node_traces,
            )
        return self._persist_terminal(
            message,
            decision,
            context=context,
            w1=w1,
            w2=w2,
            a2=a2,
            o3=o3,
            exception_ids=exception_ids,
            node_traces=node_traces,
        )

    def _run_a2_then_apply(
        self,
        message: RuntimeSourceMessage,
        decision: RouteDecision,
        *,
        context: JsonObject,
        w1: W1Result | None,
        w2: W2Result | None,
        exception_ids: list[str],
        node_traces: list[RuntimeNodeTrace],
    ) -> RuntimeExecutionRecord:
        if w1 is None or w2 is None:
            raise ValueError("A2 routing requires W1 and W2 results.")
        a2 = self._resolve_worker_result(
            message,
            "A2",
            lambda: self._require_a2().verify(message, context),
            exception_ids,
            node_traces,
        )
        if a2 is None:
            next_decision = self.route_engine.plan_a2_failure(
                message,
                w1=w1,
                w2=w2,
                reason="A2 unavailable after retry.",
            )
        else:
            next_decision = self.route_engine.plan_after_a2(message, w1=w1, w2=w2, a2=a2)
        return self._apply_decision(
            message,
            next_decision,
            context=context,
            w1=w1,
            w2=w2,
            a2=a2,
            o3=None,
            exception_ids=exception_ids,
            node_traces=node_traces,
        )

    def _run_o3_then_apply(
        self,
        message: RuntimeSourceMessage,
        decision: RouteDecision,
        *,
        context: JsonObject,
        w1: W1Result | None,
        w2: W2Result | None,
        a2: A2Result | None,
        exception_ids: list[str],
        node_traces: list[RuntimeNodeTrace],
    ) -> RuntimeExecutionRecord:
        outcome = self._run_o3_worker_outcome(
            "O3",
            lambda: self._require_o3().judge(message, context, self.o3_budget),
        )
        if outcome.error is not None:
            exception = self._save_exception(
                message,
                node="O3",
                exception_type=_exception_type(outcome.error),
                message_text=str(outcome.error),
                payload={},
            )
            exception_ids.append(exception.exception_id)
            node_traces.append(
                outcome.trace.model_copy(update={"exception_id": exception.exception_id})
            )
            next_decision = self.route_engine.plan_o3_failure(
                message,
                upstream_trade_path=decision.upstream_trade_path,
                reason="O3 unavailable after retry.",
                timeout=isinstance(outcome.error, RuntimeWorkerTimeout),
            )
            return self._persist_terminal(
                message,
                next_decision,
                context=context,
                w1=w1,
                w2=w2,
                a2=a2,
                o3=None,
                exception_ids=exception_ids,
                node_traces=node_traces,
            )
        o3 = cast(O3Result, outcome.result)
        node_traces.append(outcome.trace)
        next_decision = _decision_from_o3(message, o3, decision)
        return self._persist_terminal(
            message,
            next_decision,
            context=context,
            w1=w1,
            w2=w2,
            a2=a2,
            o3=o3,
            exception_ids=exception_ids,
            node_traces=node_traces,
        )

    def _persist_terminal(
        self,
        message: RuntimeSourceMessage,
        decision: RouteDecision,
        *,
        context: JsonObject,
        w1: W1Result | None,
        w2: W2Result | None,
        a2: A2Result | None,
        o3: O3Result | None,
        exception_ids: list[str],
        node_traces: list[RuntimeNodeTrace],
    ) -> RuntimeExecutionRecord:
        if isinstance(context.get("batch_window_id"), str) and decision.batch_id is None:
            decision = decision.model_copy(update={"batch_id": context["batch_window_id"]})
        if decision.route is RuntimeRoute.OBJECTION and o3:
            objection = _objection_from_o3(message, decision=decision, o3=o3)
            if o3.confidence is W1Confidence.LOW or self._should_downgrade_objection(objection):
                decision = decision.model_copy(
                    update={
                        "route": RuntimeRoute.OBJECTION_NOTE,
                        "reason": (
                            f"{decision.reason} Downgraded to objection_note by runtime "
                            "blocking-objection frequency limit."
                        ),
                    }
                )
        known_events_updated = o3 is not None and o3.known_events_patch is not None
        if decision.route is RuntimeRoute.TRADING_RECORD:
            self.repository.save_trading_record(
                TradingRecord(
                    source_message_id=message.source_message_id,
                    ticker=message.ticker,
                    source_type=message.source_type,
                    route=_trade_route_code(
                        decision,
                        w2=w2,
                        a2=a2,
                        o3=o3,
                        exception_ids=exception_ids,
                    ),
                    matched_policy_code=w2.matched_policy_code if w2 else None,
                    trade_intent=_resolve_trade_intent(context=context, w2=w2, o3=o3),
                    status=(
                        TradeRecordStatus.RECORDED_WITH_EXCEPTION
                        if exception_ids
                        else TradeRecordStatus.RECORDED
                    ),
                    exception_type=self._exception_type_from_ids(
                        exception_ids,
                        ticker=message.ticker,
                    ),
                    w1_result=w1,
                    w2_result=w2,
                    a2_result=a2,
                    o3_result=o3,
                )
            )
            if decision.requires_o3_known_events_update:
                known_events_updated = self._run_o3_known_events_update(
                    message,
                    context,
                    exception_ids,
                    node_traces,
                ) or known_events_updated
        elif decision.route is RuntimeRoute.INGEST_QUEUE:
            self.repository.save_ingest_queue_item(
                IngestQueueItem(
                    source_message_id=message.source_message_id,
                    ticker=message.ticker,
                    reason=decision.reason,
                    payload={"route": decision.model_dump(mode="json")},
                )
            )
        elif decision.route is RuntimeRoute.ARCHIVE:
            self.repository.save_archive_item(
                ArchiveItem(
                    source_message_id=message.source_message_id,
                    ticker=message.ticker,
                    reason=decision.reason,
                    payload={"route": decision.model_dump(mode="json")},
                )
            )
        elif decision.route in {RuntimeRoute.OBJECTION, RuntimeRoute.OBJECTION_NOTE} and o3:
            self.repository.save_objection(
                _objection_from_o3(message, decision=decision, o3=o3)
            )
            if decision.route is RuntimeRoute.OBJECTION_NOTE:
                self.repository.save_ingest_queue_item(
                    IngestQueueItem(
                        source_message_id=message.source_message_id,
                        ticker=message.ticker,
                        reason="objection_note source message requires close-after review.",
                        queue_type="daily_close_review",
                        available_for_doxatlas=False,
                        available_for_research_agent=True,
                        available_after=_next_close_review_time(),
                        payload={
                            "route": decision.model_dump(mode="json"),
                            "review_queue": "daily_close_review",
                        },
                    )
                )
        if o3 is not None and o3.known_events_patch is not None:
            self.repository.save_known_events_patch_log(
                KnownEventsPatchLog(
                    source_message_id=message.source_message_id,
                    ticker=message.ticker,
                    known_event_id=o3.known_events_patch.event_id,
                    source_ref=message.url or message.source_message_id,
                    change_reason=o3.reasoning,
                    patch=o3.known_events_patch,
                )
            )
        record = RuntimeExecutionRecord(
            source_message=message,
            route_decision=decision,
            w1_result=w1,
            w2_result=w2,
            a2_result=a2,
            o3_result=o3,
            exception_ids=exception_ids,
            status="completed" if not exception_ids else "completed_with_exception",
            message_statuses=_message_statuses(
                decision,
                w1=w1,
                w2=w2,
                a2=a2,
                o3=o3,
                exception_ids=exception_ids,
                known_events_updated=known_events_updated,
                node_traces=node_traces,
            ),
            node_traces=[trace.model_copy(deep=True) for trace in node_traces],
        )
        return self.repository.save_execution(record)

    def _save_execution_timing(
        self,
        record: RuntimeExecutionRecord,
        *,
        started_at: datetime,
        started: float,
    ) -> RuntimeExecutionRecord:
        completed_at = datetime.now(UTC)
        node_durations: dict[str, int] = {}
        for trace in record.node_traces:
            node_durations[trace.node] = node_durations.get(trace.node, 0) + trace.duration_ms
        node_total_ms = sum(node_durations.values())
        total_ms = _duration_ms(started)
        source_lag_ms: int | None = None
        if record.source_message.published_at is not None:
            source_lag_ms = max(
                0,
                int(
                    (
                        started_at - record.source_message.published_at.astimezone(UTC)
                    ).total_seconds()
                    * 1000
                ),
            )
        timing = {
            **dict(record.timing),
            "runtime": {
                "started_at": _dt_json(started_at),
                "completed_at": _dt_json(completed_at),
                "total_ms": total_ms,
                "node_total_ms": node_total_ms,
                "non_node_ms": max(0, total_ms - node_total_ms),
                "node_durations_ms": node_durations,
                "source_lag_ms": source_lag_ms,
                "route": record.route_decision.route.value,
                "status": record.status,
                "exception_count": len(record.exception_ids),
            },
        }
        updated = record.model_copy(
            update={"timing": timing, "updated_at": completed_at},
            deep=True,
        )
        return self.repository.save_execution(updated)

    def _merge_execution_timing(
        self,
        record: RuntimeExecutionRecord,
        patch: JsonObject,
    ) -> RuntimeExecutionRecord:
        timing = {**dict(record.timing), **patch}
        updated = record.model_copy(
            update={"timing": timing, "updated_at": datetime.now(UTC)},
            deep=True,
        )
        return self.repository.save_execution(updated)

    def _context_with_runtime_known_events(
        self,
        context: JsonObject | None,
        *,
        ticker: str,
    ) -> JsonObject:
        runtime_context = dict(context or {})
        runtime_events = [
            event.model_dump(mode="json")
            for event in self.repository.list_known_events(ticker=ticker)
        ]
        if not runtime_events:
            return runtime_context

        existing = runtime_context.get("known_events")
        if isinstance(existing, dict):
            existing = existing.get("events") or existing.get("known_events") or []
        if not isinstance(existing, list):
            existing = []

        merged: list[object] = []
        seen_ids: set[str] = set()
        for event in [*runtime_events, *existing]:
            if isinstance(event, dict):
                event_id = str(event.get("event_id") or event.get("id") or "").strip()
                if event_id and event_id in seen_ids:
                    continue
                if event_id:
                    seen_ids.add(event_id)
            merged.append(event)
        runtime_context["known_events"] = merged
        return runtime_context

    def _run_o3_known_events_update(
        self,
        message: RuntimeSourceMessage,
        context: JsonObject,
        exception_ids: list[str],
        node_traces: list[RuntimeNodeTrace],
    ) -> bool:
        if self.o3_worker is None:
            return False
        update_context = {
            **context,
            "o3_mode": "known_events_update_only",
            "trade_judgment_locked": True,
        }
        outcome = self._run_o3_worker_outcome(
            "O3_KNOWN_EVENTS",
            lambda: self._require_o3().judge(message, update_context, self.o3_budget),
        )
        if outcome.error is not None:
            exception = self._save_exception(
                message,
                node="O3",
                exception_type=_exception_type(outcome.error),
                message_text=str(outcome.error),
                payload={"mode": "known_events_update_only"},
            )
            exception_ids.append(exception.exception_id)
            node_traces.append(
                outcome.trace.model_copy(update={"exception_id": exception.exception_id})
            )
            return False
        node_traces.append(outcome.trace)
        result = cast(O3Result, outcome.result)
        if result.known_events_patch is not None:
            self.repository.save_known_events_patch_log(
                KnownEventsPatchLog(
                    source_message_id=message.source_message_id,
                    ticker=message.ticker,
                    known_event_id=result.known_events_patch.event_id,
                    source_ref=message.url or message.source_message_id,
                    change_reason=result.reasoning,
                    patch=result.known_events_patch,
                )
            )
            return True
        return False

    def _should_downgrade_objection(self, objection: RuntimeObjectionRecord) -> bool:
        if objection.objection_type is not O3PrimaryAction.OBJECTION:
            return False
        today = datetime.now(UTC).date()
        for existing in self.repository.list_objections(ticker=objection.ticker):
            if existing.objection_type is not O3PrimaryAction.OBJECTION:
                continue
            if existing.created_at.date() != today:
                continue
            return True
        return False

    def _run_with_retry(self, node: str, callback: Callable[[], T]) -> T:
        last_error: Exception | None = None
        for _attempt in range(2):
            try:
                return callback()
            except Exception as exc:
                last_error = exc
        if last_error is None:
            raise RuntimeError(f"{node} did not run.")
        raise last_error

    def _run_worker_outcome(self, node: str, callback: Callable[[], T]) -> _WorkerOutcome:
        started_at = datetime.now(UTC)
        started = time.monotonic()
        try:
            result = self._run_with_retry(node, callback)
        except Exception as exc:
            return _WorkerOutcome(
                result=None,
                error=exc,
                trace=RuntimeNodeTrace(
                    node=node,
                    status="failed",
                    duration_ms=_duration_ms(started),
                    attempts=2,
                    started_at=started_at,
                ),
            )
        return _WorkerOutcome(
            result=result,
            error=None,
            trace=RuntimeNodeTrace(
                node=node,
                status="succeeded",
                duration_ms=_duration_ms(started),
                attempts=1,
                started_at=started_at,
            ),
        )

    def _run_o3_worker_outcome(self, node: str, callback: Callable[[], T]) -> _WorkerOutcome:
        started_at = datetime.now(UTC)
        started = time.monotonic()
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self._run_with_retry, node, callback)
        try:
            try:
                result = future.result(timeout=max(0.001, float(self.o3_budget.target_seconds)))
            except FutureTimeoutError:
                future.cancel()
                return _WorkerOutcome(
                    result=None,
                    error=RuntimeWorkerTimeout(
                        f"{node} exceeded {self.o3_budget.target_seconds}s runtime budget."
                    ),
                    trace=RuntimeNodeTrace(
                        node=node,
                        status="failed",
                        duration_ms=_duration_ms(started),
                        attempts=2,
                        started_at=started_at,
                    ),
                )
            except Exception as exc:
                return _WorkerOutcome(
                    result=None,
                    error=exc,
                    trace=RuntimeNodeTrace(
                        node=node,
                        status="failed",
                        duration_ms=_duration_ms(started),
                        attempts=2,
                        started_at=started_at,
                    ),
                )
            return _WorkerOutcome(
                result=result,
                error=None,
                trace=RuntimeNodeTrace(
                    node=node,
                    status="succeeded",
                    duration_ms=_duration_ms(started),
                    attempts=1,
                    started_at=started_at,
                ),
            )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _resolve_worker_outcome(
        self,
        message: RuntimeSourceMessage,
        node: str,
        outcome: _WorkerOutcome,
        exception_ids: list[str],
        node_traces: list[RuntimeNodeTrace],
    ) -> T | None:
        if outcome.error is None:
            node_traces.append(outcome.trace)
            return cast(T, outcome.result)
        exception = self._save_exception(
            message,
            node=node,
            exception_type=_exception_type(outcome.error),
            message_text=str(outcome.error),
            payload={},
        )
        exception_ids.append(exception.exception_id)
        node_traces.append(
            outcome.trace.model_copy(update={"exception_id": exception.exception_id})
        )
        return None

    def _resolve_worker_result(
        self,
        message: RuntimeSourceMessage,
        node: str,
        callback: Callable[[], T],
        exception_ids: list[str],
        node_traces: list[RuntimeNodeTrace],
    ) -> T | None:
        outcome = self._run_worker_outcome(node, callback)
        if outcome.error is None:
            node_traces.append(outcome.trace)
            return cast(T, outcome.result)
        exception = self._save_exception(
            message,
            node=node,
            exception_type=_exception_type(outcome.error),
            message_text=str(outcome.error),
            payload={},
        )
        exception_ids.append(exception.exception_id)
        node_traces.append(
            outcome.trace.model_copy(update={"exception_id": exception.exception_id})
        )
        return None

    def _save_exception(
        self,
        message: RuntimeSourceMessage,
        *,
        node: str,
        exception_type: str,
        message_text: str,
        payload: JsonObject,
    ) -> ExecutionExceptionLog:
        payload_with_flags = dict(payload)
        failure_key = {
            "W1": "w1_failed",
            "W2": "w2_failed",
            "A2": "a2_failed",
            "O3": "o3_failed",
            "O3_KNOWN_EVENTS": "o3_failed",
        }.get(node)
        if failure_key:
            payload_with_flags[failure_key] = True
        if exception_type == "o3_timeout":
            payload_with_flags["o3_timeout"] = True
        return self.repository.save_exception(
            ExecutionExceptionLog(
                source_message_id=message.source_message_id,
                ticker=message.ticker,
                node=node,
                exception_type=exception_type,
                message=message_text[:500],
                payload=payload_with_flags,
            )
        )

    def _exception_type_from_ids(
        self,
        exception_ids: list[str],
        *,
        ticker: str,
    ) -> str | None:
        if not exception_ids:
            return None
        logs = {
            item.exception_id: item
            for item in self.repository.list_exceptions(ticker=ticker)
            if item.exception_id in exception_ids
        }
        ordered = [logs[exception_id] for exception_id in exception_ids if exception_id in logs]
        for item in ordered:
            if item.exception_type == "o3_timeout":
                return "o3_timeout"
        return ordered[0].exception_type if ordered else "runtime_exception"

    def _require_w1(self) -> W1Worker:
        if self.w1_worker is None:
            raise RuntimeError("W1 worker is not configured.")
        return self.w1_worker

    def _require_w2(self) -> W2Worker:
        if self.w2_worker is None:
            raise RuntimeError("W2 worker is not configured.")
        return self.w2_worker

    def _require_a2(self) -> A2Worker:
        if self.a2_worker is None:
            raise RuntimeError("A2 worker is not configured.")
        return self.a2_worker

    def _require_o3(self) -> O3Worker:
        if self.o3_worker is None:
            raise RuntimeError("O3 worker is not configured.")
        return self.o3_worker


def _decision_from_o3(
    message: RuntimeSourceMessage,
    o3: O3Result,
    previous: RouteDecision,
) -> RouteDecision:
    route_by_action = {
        O3PrimaryAction.TRADING_RECORD: RuntimeRoute.TRADING_RECORD,
        O3PrimaryAction.INGEST_QUEUE: RuntimeRoute.INGEST_QUEUE,
        O3PrimaryAction.ARCHIVE: RuntimeRoute.ARCHIVE,
        O3PrimaryAction.OBJECTION: RuntimeRoute.OBJECTION,
        O3PrimaryAction.OBJECTION_NOTE: RuntimeRoute.OBJECTION_NOTE,
    }
    return RouteDecision(
        source_message_id=message.source_message_id,
        ticker=message.ticker,
        route=route_by_action[o3.primary_action],
        reason=f"O3 final action: {o3.reasoning}",
        upstream_trade_path=previous.upstream_trade_path
        or o3.primary_action is O3PrimaryAction.TRADING_RECORD,
        o3_must_check_novelty_first=previous.o3_must_check_novelty_first,
    )


def _social_batch_o3_context(
    pending_items: list[_PendingO3Item],
    *,
    ticker: str,
    batch_window_id: str,
    total_items: int,
    new_items: int,
    non_irrelevant_items: int,
    base_context: JsonObject,
) -> JsonObject:
    timestamps = [
        item.message.published_at or item.message.collected_at for item in pending_items
    ]
    items = []
    for item in pending_items:
        message = item.message
        items.append(
            {
                "source_message_id": message.source_message_id,
                "source_id": message.source_id,
                "title": message.title,
                "body": message.body,
                "url": message.url,
                "author": message.author,
                "username": message.username,
                "published_at": _dt_json(message.published_at),
                "collected_at": _dt_json(message.collected_at),
                "w1_result": item.w1.model_dump(mode="json") if item.w1 else None,
                "w2_result": item.w2.model_dump(mode="json") if item.w2 else None,
                "a2_result": item.a2.model_dump(mode="json") if item.a2 else None,
            }
        )
    batch_package: JsonObject = {
        "batch_window_id": batch_window_id,
        "window_start": _dt_json(min(timestamps)) if timestamps else None,
        "window_end": _dt_json(max(timestamps)) if timestamps else None,
        "ticker": ticker.strip().upper(),
        "items": items,
        "summary_stats": {
            "total_items": total_items,
            "new_items": new_items,
            "non_irrelevant_items": non_irrelevant_items,
            "a2_passed_items": len(pending_items),
        },
    }
    return {
        **base_context,
        "o3_mode": "social_batch",
        "batch_window_id": batch_window_id,
        "social_batch": batch_package,
    }


def _resolve_trade_intent(
    *,
    context: JsonObject,
    w2: W2Result | None,
    o3: O3Result | None,
) -> TradeIntent:
    if o3 is not None and o3.trade_intent is not None:
        return o3.trade_intent
    action = _matched_policy_action(context, w2)
    if not action and (w2 is None or w2.matched_policy_code is None):
        return TradeIntent(
            side=TradeSide.LONG,
            conviction=Conviction.LOW,
            size_bucket=SizeBucket.SMALL,
            reasoning="O3 timeout/failure placeholder trade intent; requires exception review.",
        )
    return TradeIntent(
        side=_enum_or_default(TradeSide, action.get("side"), TradeSide.LONG),
        conviction=_enum_or_default(Conviction, action.get("conviction"), Conviction.MEDIUM),
        size_bucket=_enum_or_default(SizeBucket, action.get("size_bucket"), SizeBucket.NORMAL),
        reasoning=str(action.get("reasoning") or action.get("note") or "DTC policy trade intent."),
    )


def _matched_policy_action(context: JsonObject, w2: W2Result | None) -> dict[str, object]:
    if w2 is None or not w2.matched_policy_code:
        return {}
    policies = context.get("monitoring_policies")
    if not isinstance(policies, list):
        return {}
    for item in policies:
        if not isinstance(item, dict):
            continue
        if item.get("policy_id") != w2.matched_policy_code:
            continue
        action = item.get("action")
        return dict(action) if isinstance(action, dict) else {}
    return {}


def _trade_route_code(
    decision: RouteDecision,
    *,
    w2: W2Result | None,
    a2: A2Result | None,
    o3: O3Result | None,
    exception_ids: list[str],
) -> str:
    if exception_ids and "O3 timeout" in decision.reason:
        return "o3_timeout_trade"
    if o3 is not None and o3.primary_action is O3PrimaryAction.TRADING_RECORD:
        return "o3_trade"
    if a2 is not None and w2 is not None and w2.type is W2Type.DIRECT_TRADE_CANDIDATE:
        return "a2_confirmed_dtc"
    if w2 is not None and w2.type is W2Type.DIRECT_TRADE_CANDIDATE:
        return "new_dtc"
    return decision.route.value


def _batch_window_id(message: RuntimeSourceMessage) -> str:
    for key in ("batch_window_id", "polling_window_id", "poll_window_id"):
        value = message.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    published = message.published_at or message.collected_at
    return published.strftime("%Y%m%d%H%M")


def _dt_json(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _first_duplicate_key_match(
    message: RuntimeSourceMessage,
    existing: RuntimeSourceMessage,
) -> str:
    matches = sorted(runtime_duplicate_keys(message) & runtime_duplicate_keys(existing))
    return matches[0] if matches else "unknown"


def _next_close_review_time(now: datetime | None = None) -> datetime:
    current = now or datetime.now(UTC)
    next_day = current + timedelta(days=1)
    return next_day.replace(hour=0, minute=0, second=0, microsecond=0)


def _message_statuses(
    decision: RouteDecision,
    *,
    w1: W1Result | None,
    w2: W2Result | None,
    a2: A2Result | None,
    o3: O3Result | None,
    exception_ids: list[str],
    node_traces: list[RuntimeNodeTrace],
    known_events_updated: bool = False,
) -> list[str]:
    trace_nodes = {trace.node for trace in node_traces}
    statuses = ["received", "cleaned"]
    if decision.duplicate_of_source_message_id is not None:
        statuses.append("deduplicated")
    if w1 is not None or "W1" in trace_nodes:
        statuses.append("w1_running")
    if w2 is not None or "W2" in trace_nodes:
        statuses.append("w2_running")
    if w1 is not None or w2 is not None or "W1" in trace_nodes or "W2" in trace_nodes:
        statuses.append("workers_completed")
    if a2 is not None or "A2" in trace_nodes:
        statuses.append("a2_running")
    if o3 is not None or "O3" in trace_nodes or "O3_KNOWN_EVENTS" in trace_nodes:
        statuses.append("o3_running")
    if decision.route is RuntimeRoute.TRADING_RECORD:
        statuses.append("routed_to_trading_records")
    elif decision.route is RuntimeRoute.INGEST_QUEUE:
        statuses.append("routed_to_ingest_queue")
    elif decision.route is RuntimeRoute.ARCHIVE:
        statuses.append("routed_to_archive")
    elif decision.route is RuntimeRoute.OBJECTION:
        statuses.append("objection_created")
    elif decision.route is RuntimeRoute.OBJECTION_NOTE:
        statuses.append("objection_note_created")
    if known_events_updated or (o3 is not None and o3.known_events_patch is not None):
        statuses.append("known_events_updated")
    if exception_ids:
        statuses.append("failed_with_exception")
    return statuses


def _enum_or_default(enum_type: type[T], value: object, default: T) -> T:
    if value is None:
        return default
    try:
        return enum_type(str(value))  # type: ignore[call-arg]
    except ValueError:
        return default


def _objection_from_o3(
    message: RuntimeSourceMessage,
    *,
    decision: RouteDecision,
    o3: O3Result,
) -> RuntimeObjectionRecord:
    objection_type = (
        O3PrimaryAction.OBJECTION_NOTE
        if decision.route is RuntimeRoute.OBJECTION_NOTE
        else O3PrimaryAction.OBJECTION
    )
    return RuntimeObjectionRecord(
        source_message_id=message.source_message_id,
        ticker=message.ticker,
        objection_type=objection_type,
        blackboard_target=o3.blackboard_target or "document3",
        reason=o3.reasoning,
        evidence_refs=o3.evidence_refs,
    )


def _exception_type(exc: Exception) -> str:
    if isinstance(exc, RuntimeWorkerTimeout):
        return "o3_timeout"
    name = exc.__class__.__name__
    if "validationerror" in name.lower():
        return "schema_invalid"
    if "timeout" in name.lower():
        return "timeout"
    return "runtime_error"


def _exception_type_from_ids(exception_ids: list[str]) -> str | None:
    if not exception_ids:
        return None
    return "runtime_exception"
