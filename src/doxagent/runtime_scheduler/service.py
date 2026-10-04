"""Scheduler for admitted V2 activations and Message Bus V2 streams."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.persistent_runtime_v2.schema import (
    RuntimeCaseStatus as RuntimeV2CaseStatus,
)
from doxagent.persistent_runtime_v2.schema import (
    RuntimePrimaryRoute as RuntimeV2PrimaryRoute,
)
from doxagent.persistent_runtime_v2.schema import (
    SourceMessageEnvelope,
)
from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service
from doxagent.runtime_scheduler.repository import RuntimeSchedulerRepository
from doxagent.runtime_scheduler.schema import (
    AuditSeverity,
    DocumentRefreshRequest,
    DocumentSetStatus,
    EventProcessingStatus,
    MarketSessionPhase,
    MonitoringBindingStatus,
    MonitoringRunStatus,
    MonitorMode,
    RefreshRequestSource,
    RuntimeAuditEvent,
    RuntimeHealth,
    TickerRunDetail,
    TickerRunState,
    TickerRunStatus,
)
from doxagent.settings import DoxAgentSettings

ET = ZoneInfo("America/New_York")
RUNNABLE_STATUSES = {TickerRunStatus.RUNNING, TickerRunStatus.DEGRADED}
ENABLED_MONITOR_MODES = {MonitorMode.MESSAGE_MONITORING, MonitorMode.TRADING}
RUNTIME_V2_CONSUMER_ID = "persistent_runtime_v2"


class UnsupportedMonitorMode(ValueError):
    pass


class RuntimeModeUnavailable(RuntimeError):
    pass


class UnifiedRuntimeSchedulerService:
    """Consume streams under an immutable admitted activation; documents are initialized separately."""

    def __init__(
        self,
        repository: RuntimeSchedulerRepository,
        *,
        runtime_v2_service: PersistentRuntimeV2Service | None = None,
        message_bus_v2_service: MessageBusV2Service | None = None,
        message_bus_v2_enabled: bool = True,
    ) -> None:
        self.repository = repository
        self.runtime_v2_service = runtime_v2_service
        self.message_bus_v2_service = message_bus_v2_service
        self.message_bus_v2_enabled = message_bus_v2_enabled
        self.initialization_control_path: str | None = None

    @classmethod
    def from_settings(
        cls, settings: DoxAgentSettings | None = None
    ) -> UnifiedRuntimeSchedulerService:
        from doxagent.runtime_scheduler.v2 import build

        return build(settings)

    def admit_activation(
        self, ticker: str, revision_id: str, *, now: datetime | None = None
    ) -> TickerRunState:
        """Admit a published V2 activation without invoking legacy document workflows."""
        normalized = _ticker(ticker)
        runtime = self._require_runtime_v2_for_trading()
        bus = self._require_message_bus_v2()
        from doxagent.persistent_runtime_v2.journal import RuntimeJournal
        from doxagent.v2_control.repository import ControlRepository

        control = (
            ControlRepository(runtime.journal).get(normalized)
            if isinstance(runtime.journal, RuntimeJournal)
            else None
        )
        if control and not (control["analysis_allowed"] or control.get("admission_allowed")):
            raise ValueError("V2 control has stopped admission")
        loader = runtime.input_snapshot_loader
        from doxagent.ticker_initialization.runtime_inputs import ActivatedRuntimeInputs

        snapshot = (
            loader.for_admission(normalized, revision_id)
            if isinstance(loader, ActivatedRuntimeInputs)
            else loader(normalized)
            if loader
            else None
        )
        if snapshot is None or snapshot.activation_revision_id != revision_id:
            raise ValueError("Runtime has not loaded the requested activation revision")
        if snapshot.index is None or snapshot.projection is None:
            raise ValueError("Activated Runtime Index/Projection is unavailable")
        if bus.repository.get_ticker_state(normalized) is None:
            raise ValueError("Message Bus must admit the ticker before Runtime")
        initial_offset = bus.initialize_runtime_cursor(RUNTIME_V2_CONSUMER_ID, normalized)
        current_time = _utc(now)
        state = self.repository.get_state(normalized) or TickerRunState(
            ticker=normalized, started_at=current_time
        )
        state = state.model_copy(
            update={
                "status": TickerRunStatus.RUNNING,
                "health": RuntimeHealth.NORMAL,
                "monitor_mode": (
                    MonitorMode.MESSAGE_MONITORING
                    if control and control["requested_mode"] == "MESSAGE_MONITORING"
                    else MonitorMode.TRADING
                ),
                "session_phase": market_session_phase(current_time),
                "updated_at": current_time,
                "stopped_at": None,
                "last_error": None,
                "document_status": DocumentSetStatus(
                    ticker=normalized, usable=True, checked_at=current_time
                ),
                "metadata": {
                    **state.metadata,
                    "activation_revision_id": revision_id,
                    "workflow_version": "V2",
                    "initial_offset": initial_offset,
                    "v2_control_epoch": control["epoch"] if control else None,
                    "v2_control_mode": control["requested_mode"] if control else None,
                },
            },
            deep=True,
        )
        self.repository.upsert_state(state)
        return state

    def run_due_once(
        self,
        *,
        now: datetime | None = None,
        event_limit: int = 100,
    ) -> list[TickerRunDetail]:
        control_path = getattr(self, "initialization_control_path", None)
        admitted: set[str] | None = None
        if control_path:
            from doxagent.ticker_initialization.consumers import admit_runtime_revisions
            from doxagent.ticker_initialization.repository import InitializationRepository

            admitted = admit_runtime_revisions(InitializationRepository(control_path), self)
        from doxagent.ticker_initialization.consumers import consumer_heartbeat

        def usable(revision: dict[str, Any]) -> bool:
            state = self.repository.get_state(revision["ticker"])
            return bool(
                state
                and state.status in RUNNABLE_STATUSES
                and state.metadata.get("activation_revision_id") == revision["revision_id"]
            )

        with consumer_heartbeat(
            InitializationRepository(control_path) if control_path else None, "runtime", usable
        ):
            return self._run_admitted_once(admitted, now=now, event_limit=event_limit)

    def _run_admitted_once(
        self, admitted: set[str] | None, *, now: datetime | None, event_limit: int
    ) -> list[TickerRunDetail]:
        details: list[TickerRunDetail] = []
        for state in self.repository.list_states():
            if (
                admitted is not None
                and state.metadata.get("activation_revision_id")
                and state.ticker not in admitted
            ):
                continue
            if state.status not in RUNNABLE_STATUSES:
                continue
            details.append(self.tick_ticker(state.ticker, now=now, event_limit=event_limit))
        return details

    def tick_ticker(
        self,
        ticker: str,
        *,
        now: datetime | None = None,
        event_limit: int = 100,
    ) -> TickerRunDetail:
        normalized = _ticker(ticker)
        state = self.repository.get_state(normalized)
        if state is None:
            raise ValueError("Ticker has no admitted activation")
        current_time = _utc(now)
        if state.status not in RUNNABLE_STATUSES:
            self._audit(
                normalized,
                "ticker_tick_skipped",
                "Ticker tick skipped because runtime is not runnable.",
                payload={"status": state.status.value},
            )
            return self.detail(normalized, now=current_time)
        phase = market_session_phase(current_time)
        state = state.model_copy(
            update={"session_phase": phase, "updated_at": current_time},
            deep=True,
        )

        monitor_mode = _state_monitor_mode(state)
        from doxagent.persistent_runtime_v2.journal import RuntimeJournal
        from doxagent.v2_control.repository import ControlRepository

        runtime_journal = getattr(self.runtime_v2_service, "journal", None)
        control = (
            ControlRepository(runtime_journal).get(normalized)
            if isinstance(runtime_journal, RuntimeJournal)
            else None
        )
        should_run_runtime = (
            control["analysis_allowed"] if control else monitor_mode is MonitorMode.TRADING
        ) and (
            self.message_bus_v2_enabled
            or phase
            in {
                MarketSessionPhase.PRE_MARKET_DIGEST,
                MarketSessionPhase.FORMAL_MONITORING,
            }
        )
        poll_failures = poll_messages = poll_events = 0
        pending_count_before_runtime = 0
        consumed_count = 0
        runtime_count = 0
        trade_intent_count = 0
        failed_event_count = 0
        runtime_failed = False
        if should_run_runtime and self.message_bus_v2_enabled:
            try:
                bus = self._require_message_bus_v2()
                runtime = self._require_runtime_v2_for_trading()
                pending_stream = bus.pending_stream(
                    RUNTIME_V2_CONSUMER_ID,
                    normalized,
                    limit=event_limit,
                )
                pending_count_before_runtime = len(pending_stream)
                coordinator = getattr(runtime, "coordinator", None)
                from doxagent.persistent_runtime_v2.coordinator import RuntimeCoordinator

                if not isinstance(coordinator, RuntimeCoordinator):
                    coordinator = None
                if coordinator is not None:
                    coordinator.reconcile(normalized)
                if coordinator is None:
                    runtime.process_pending_effects(limit=20)
                for stream_item in pending_stream:
                    if coordinator is not None:
                        coordinator.accept_stream(stream_item)
                        coordinator.journal.set(
                            "inbox_highwater", normalized, stream_item.item.stream_offset
                        )
                        bus.commit_stream(RUNTIME_V2_CONSUMER_ID, stream_item)
                        consumed_count += 1
                        continue
                    from doxagent.message_bus_v2.admission import evaluate_admission
                    from doxagent.message_bus_v2.schema import MaterializedStreamItem, utc_now

                    original_stream = stream_item
                    existing_source = SourceMessageEnvelope.from_stream_item(stream_item)
                    if (
                        runtime.repository.get_case_by_source(existing_source.source_message_id)
                        is None
                    ):
                        eligible = []
                        for member in stream_item.members:
                            reason = evaluate_admission(
                                member.published_at, None, utc_now(), member.publication_time_basis
                            )
                            if reason:
                                bus.repository.record_admission(
                                    member, reason, "RUNTIME_LEGACY", member.binding_id
                                )
                            else:
                                eligible.append(member)
                        if not eligible:
                            bus.commit_stream(RUNTIME_V2_CONSUMER_ID, original_stream)
                            consumed_count += 1
                            continue
                        stream_item = MaterializedStreamItem(
                            item=stream_item.item.model_copy(
                                update={"member_count": len(eligible)}
                            ),
                            members=[
                                m.model_copy(update={"member_index": i})
                                for i, m in enumerate(eligible)
                            ],
                        )
                    case = runtime.execute_message(
                        SourceMessageEnvelope.from_stream_item(stream_item)
                    )
                    if case.status not in {
                        RuntimeV2CaseStatus.ADJUDICATED,
                        RuntimeV2CaseStatus.COMPLETED,
                        RuntimeV2CaseStatus.PENDING_W3,
                    }:
                        raise RuntimeError(
                            f"Runtime V2 case {case.case_id} did not reach adjudication"
                        )
                    bus.commit_stream(RUNTIME_V2_CONSUMER_ID, stream_item)
                    runtime_count += 1
                    consumed_count += 1
                    trade_intent_count += int(
                        case.route is not None
                        and case.route.primary_route is RuntimeV2PrimaryRoute.TRADE
                    )
                if coordinator is not None:
                    coordinator.tick(normalized)
            except Exception as exc:
                runtime_failed = True
                failed_event_count = max(0, pending_count_before_runtime - consumed_count)
                self._audit(
                    normalized,
                    "runtime_stream_consumption_failed",
                    str(exc),
                    severity=AuditSeverity.ERROR,
                )
        if self.message_bus_v2_enabled:
            pending_count_after_runtime = len(
                self._require_message_bus_v2().pending_stream(
                    RUNTIME_V2_CONSUMER_ID,
                    normalized,
                    limit=event_limit,
                )
            )
        else:
            pending_count_after_runtime = 0
        counters = state.counters.model_copy(
            update={
                "poll_cycles": state.counters.poll_cycles,
                "messages_collected": state.counters.messages_collected + poll_messages,
                "events_created": state.counters.events_created + poll_events,
                "events_consumed": state.counters.events_consumed + consumed_count,
                "pending_event_count": pending_count_after_runtime,
                "processed_event_count": state.counters.processed_event_count + consumed_count,
                "failed_event_count": state.counters.failed_event_count + failed_event_count,
                "trade_intents_generated": (
                    state.counters.trade_intents_generated + trade_intent_count
                ),
                "runtime_executions": state.counters.runtime_executions + runtime_count,
                "execution_failure_count": (
                    state.counters.execution_failure_count + int(runtime_failed)
                ),
                "failure_count": (
                    state.counters.failure_count + poll_failures + int(runtime_failed)
                ),
            },
            deep=True,
        )
        if runtime_failed:
            health = RuntimeHealth.DEGRADED
            status = TickerRunStatus.DEGRADED
            last_error = "Runtime event consumption failed; pending events remain unconsumed."
        elif poll_failures:
            health = RuntimeHealth.DEGRADED
            status = TickerRunStatus.DEGRADED
            last_error = f"{poll_failures} monitoring source poll(s) failed."
        else:
            health = RuntimeHealth.NORMAL
            status = TickerRunStatus.RUNNING
            last_error = None
        state = state.model_copy(
            update={
                "status": status,
                "health": health,
                "session_phase": phase,
                "monitor_mode": monitor_mode,
                "updated_at": current_time,
                "last_poll_at": (
                    current_time if poll_messages or poll_events else state.last_poll_at
                ),
                "last_event_consumed_at": (
                    current_time if consumed_count else state.last_event_consumed_at
                ),
                "last_trade_intent_at": (
                    current_time if trade_intent_count else state.last_trade_intent_at
                ),
                "last_error": last_error,
                "counters": counters,
            },
            deep=True,
        )
        self.repository.upsert_state(state)
        return self.detail(normalized, now=current_time)

    def detail(
        self,
        ticker: str,
        *,
        now: datetime | None = None,
        limit: int = 50,
    ) -> TickerRunDetail:
        normalized = _ticker(ticker)
        state = self._state_or_default(normalized, now=now)
        document_status = state.document_status or DocumentSetStatus(
            ticker=normalized, checked_at=_utc(now)
        )
        return TickerRunDetail(
            state=state,
            document_status=document_status,
            message_bus_status=self.monitoring_status(normalized, now=now, limit=limit),
            runtime_status=self.event_processing_status(normalized, limit=limit),
            refresh_requests=self.repository.list_refresh_requests(
                ticker=normalized,
                limit=limit,
            ),
            audit_events=self.repository.list_audit_events(ticker=normalized, limit=limit),
        )

    def document_status(
        self,
        ticker: str,
        *,
        now: datetime | None = None,
    ) -> DocumentSetStatus:
        normalized = _ticker(ticker)
        state = self.repository.get_state(normalized)
        if state is not None and state.document_status is not None:
            return state.document_status
        return DocumentSetStatus(ticker=normalized, checked_at=_utc(now))

    def monitoring_status(
        self,
        ticker: str,
        *,
        now: datetime | None = None,
        limit: int = 50,
    ) -> MonitoringRunStatus:
        normalized = _ticker(ticker)
        if self.message_bus_v2_enabled:
            bus = self._require_message_bus_v2()
            bindings = bus.repository.list_bindings(ticker=normalized)
            poll_values = bus.repository.list_poll_states(ticker=normalized)
            v2_poll_states = {value.binding_id: value for value in poll_values}
            pending = bus.pending_stream(RUNTIME_V2_CONSUMER_ID, normalized, limit=limit)
            recent_stream = bus.repository.read_stream(normalized, after_offset=0, limit=limit)
            recent_messages = bus.repository.list_standard(ticker=normalized, limit=limit)
            active_states = [
                v2_poll_states[binding.binding_id]
                for binding in bindings
                if binding.enabled and binding.binding_id in v2_poll_states
            ]
            last_error_state = max(
                (value for value in active_states if value.last_failure_at),
                key=lambda value: value.last_failure_at or datetime.min.replace(tzinfo=UTC),
                default=None,
            )
            return MonitoringRunStatus(
                ticker=normalized,
                session_phase=market_session_phase(_utc(now)),
                configured_sources=[
                    MonitoringBindingStatus(
                        binding=binding,
                        poll_state=v2_poll_states.get(binding.binding_id),
                    )
                    for binding in bindings
                ],
                pending_event_count=len(pending),
                recent_event_count=len(recent_stream),
                recent_message_count=len(recent_messages),
                last_success_at=_latest(value.last_success_at for value in active_states),
                last_error_at=(last_error_state.last_failure_at if last_error_state else None),
                last_error_message=(
                    last_error_state.last_error_message if last_error_state else None
                ),
            )
        return MonitoringRunStatus(ticker=normalized, session_phase=market_session_phase(_utc(now)))

    def event_processing_status(
        self,
        ticker: str,
        *,
        limit: int = 50,
    ) -> EventProcessingStatus:
        normalized = _ticker(ticker)
        if self.message_bus_v2_enabled:
            bus = self._require_message_bus_v2()
            pending = bus.pending_stream(RUNTIME_V2_CONSUMER_ID, normalized, limit=limit)
            offset = bus.repository.get_consumer_offset(RUNTIME_V2_CONSUMER_ID, normalized)
            state = self.repository.get_state(normalized)
            return EventProcessingStatus(
                ticker=normalized,
                pending_event_count=len(pending),
                consumed_event_count=offset.stream_offset,
                runtime_execution_count=(
                    state.counters.runtime_executions if state is not None else 0
                ),
                exception_count=0,
                last_execution_at=(state.last_event_consumed_at if state is not None else None),
            )
        return EventProcessingStatus(ticker=normalized)

    def submit_refresh_request(
        self,
        ticker: str,
        *,
        requested_by: RefreshRequestSource,
        reason: str,
        trigger_event_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> DocumentRefreshRequest:
        request = DocumentRefreshRequest(
            ticker=ticker,
            requested_by=requested_by,
            reason=reason,
            trigger_event_id=trigger_event_id,
            metadata=dict(metadata or {}),
        )
        saved = self.repository.save_refresh_request(request)
        self._audit(
            saved.ticker,
            "document_refresh_requested",
            "Document refresh request recorded; automatic agent refresh remains disabled.",
            payload=saved.model_dump(mode="json"),
        )
        return saved

    def _require_message_bus_v2(self) -> MessageBusV2Service:
        if not self.message_bus_v2_enabled or self.message_bus_v2_service is None:
            raise RuntimeError("Message Bus v2 is not enabled or configured")
        return self.message_bus_v2_service

    def _require_runtime_v2_for_trading(self) -> PersistentRuntimeV2Service:
        if self.runtime_v2_service is None:
            raise RuntimeModeUnavailable(
                "monitor mode 'trading' requires DOXAGENT_PERSISTENT_RUNTIME_V2_ENABLED"
            )
        return self.runtime_v2_service

    def _state_or_default(self, ticker: str, *, now: datetime | None = None) -> TickerRunState:
        normalized = _ticker(ticker)
        state = self.repository.get_state(normalized)
        if state is not None:
            return state
        current_time = _utc(now)
        return TickerRunState(
            ticker=normalized,
            status=TickerRunStatus.STOPPED,
            health=RuntimeHealth.NORMAL,
            session_phase=market_session_phase(current_time),
            started_at=current_time,
            updated_at=current_time,
        )

    def _audit(
        self,
        ticker: str,
        event_type: str,
        message: str,
        *,
        severity: AuditSeverity = AuditSeverity.INFO,
        payload: dict[str, object] | None = None,
    ) -> RuntimeAuditEvent:
        return self.repository.append_audit_event(
            RuntimeAuditEvent(
                ticker=ticker,
                event_type=event_type,
                severity=severity,
                message=message,
                payload=dict(payload or {}),
            )
        )


def market_session_phase(now: datetime | None = None) -> MarketSessionPhase:
    current = _utc(now).astimezone(ET)
    weekday = current.weekday()
    if weekday <= 4:
        digest_start = time(7, 0) if weekday == 0 else time(7, 30)
        if digest_start <= current.time() < time(8, 0):
            return MarketSessionPhase.PRE_MARKET_DIGEST
        if time(8, 0) <= current.time() < time(18, 0):
            return MarketSessionPhase.FORMAL_MONITORING
    return MarketSessionPhase.OFF_HOURS_LOW_FREQUENCY


def _latest(values: Iterable[datetime | None]) -> datetime | None:
    dates = [value for value in values if isinstance(value, datetime)]
    return max(dates) if dates else None


def _ticker(value: str) -> str:
    normalized = value.strip().upper()
    if not normalized:
        raise ValueError("ticker is required.")
    return normalized


def _resolve_monitor_mode(
    value: MonitorMode | str | None,
    *,
    default: MonitorMode | None = None,
) -> MonitorMode:
    if value is None:
        return default or MonitorMode.MESSAGE_MONITORING
    if isinstance(value, MonitorMode):
        resolved = value
    else:
        normalized = value.strip().lower()
        if normalized in {"paper_trading", "broker_trading"}:
            normalized = MonitorMode.TRADING.value
        try:
            resolved = MonitorMode(normalized)
        except ValueError as exc:
            raise UnsupportedMonitorMode(str(value)) from exc
    if resolved not in ENABLED_MONITOR_MODES:
        raise UnsupportedMonitorMode(resolved.value)
    return resolved


def _state_monitor_mode(state: TickerRunState | None) -> MonitorMode:
    if state is None:
        return MonitorMode.MESSAGE_MONITORING
    metadata_value = state.metadata.get("monitor_mode")
    if isinstance(metadata_value, str):
        try:
            return _resolve_monitor_mode(metadata_value)
        except ValueError:
            pass
    return state.monitor_mode


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
