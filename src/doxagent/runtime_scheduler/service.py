"""Ticker-level orchestration for monitoring and persistent runtime execution."""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, time
from threading import Lock, Thread
from zoneinfo import ZoneInfo

from doxagent.blackboard.errors import RunNotFoundError
from doxagent.monitoring.schema import (
    EventStreamItem,
    IngestBatchResult,
    MonitoringParameters,
    SourceType,
    TickerSourceBinding,
    UpdateActor,
    parameter_schema_for_source,
)
from doxagent.monitoring.service import MonitoringBusService
from doxagent.persistent_runtime.service import PersistentRuntimeExecutionService
from doxagent.runtime_scheduler.documents import RuntimeDocumentProvider, WorkflowDocumentProvider
from doxagent.runtime_scheduler.repository import (
    InMemoryRuntimeSchedulerRepository,
    RuntimeSchedulerRepository,
    SQLiteRuntimeSchedulerRepository,
)
from doxagent.runtime_scheduler.schema import (
    AuditSeverity,
    DashboardOverview,
    DocumentAvailability,
    DocumentBundle,
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
    TradeIntentView,
)
from doxagent.settings import DoxAgentSettings

ET = ZoneInfo("America/New_York")
LOW_FREQUENCY_SOURCE_IDS = frozenset({"stocktwits_messages"})
SOCIAL_RUNTIME_EXCLUDED_SOURCE_IDS = frozenset(
    {
        "stocktwits_messages",
        "tikhub_x_search",
        "tikhub_x_user_posts",
    }
)
RUNNABLE_STATUSES = {
    TickerRunStatus.RUNNING,
    TickerRunStatus.DEGRADED,
}
STARTUP_STEP_DEFINITIONS = (
    ("document1", "进行宏观投研"),
    ("document2", "拆解叙事预期"),
    ("document3", "生成执行策略"),
    ("message_bus", "配置消息监测"),
    ("runtime", "启动持久化监测"),
)
ENABLED_MONITOR_MODES = {
    MonitorMode.MESSAGE_MONITORING,
    MonitorMode.PAPER_TRADING,
}


@dataclass
class _WeeklyDocumentUpdateJob:
    ticker: str
    due_at: datetime
    thread: Thread | None = None
    current_bundle: DocumentBundle | None = None
    bundle: DocumentBundle | None = None
    error: Exception | None = None
    completed_at: datetime | None = None
    runtime_continues_audited: bool = False


class UnsupportedMonitorMode(ValueError):
    def __init__(self, monitor_mode: str) -> None:
        super().__init__(monitor_mode)
        self.monitor_mode = monitor_mode


class DocumentRunActivationError(ValueError):
    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class DocumentRunNotFound(DocumentRunActivationError):
    def __init__(self, ticker: str, document_run_id: str) -> None:
        super().__init__(
            "Document run was not found.",
            details={"ticker": ticker, "document_run_id": document_run_id},
        )
        self.ticker = ticker
        self.document_run_id = document_run_id


class UnifiedRuntimeSchedulerService:
    """Coordinate documents, monitoring bindings, event consumption, and state views."""

    def __init__(
        self,
        repository: RuntimeSchedulerRepository,
        *,
        document_provider: RuntimeDocumentProvider,
        monitoring_service: MonitoringBusService,
        runtime_service: PersistentRuntimeExecutionService,
        low_frequency_source_ids: set[str] | None = None,
        auto_media_enrichment_enabled: bool = True,
        auto_media_enrichment_limit: int = 5,
        auto_media_enrichment_concurrency: int = 2,
    ) -> None:
        self.repository = repository
        self.document_provider = document_provider
        self.monitoring_service = monitoring_service
        self.runtime_service = runtime_service
        self.low_frequency_source_ids = {
            source_id.strip().lower()
            for source_id in (low_frequency_source_ids or set(LOW_FREQUENCY_SOURCE_IDS))
        }
        self.auto_media_enrichment_enabled = auto_media_enrichment_enabled
        self.auto_media_enrichment_limit = max(1, auto_media_enrichment_limit)
        self.auto_media_enrichment_concurrency = max(1, auto_media_enrichment_concurrency)
        self._weekly_update_jobs: dict[str, _WeeklyDocumentUpdateJob] = {}
        self._weekly_update_lock = Lock()
        self._runtime_bundle_cache: dict[tuple[str, str], DocumentBundle] = {}
        self._runtime_context_cache: dict[tuple[str, str], dict[str, object]] = {}

    @classmethod
    def from_settings(
        cls,
        settings: DoxAgentSettings | None = None,
    ) -> UnifiedRuntimeSchedulerService:
        resolved = settings or DoxAgentSettings()
        if resolved.runtime_scheduler_storage_mode == "memory":
            repository: RuntimeSchedulerRepository = InMemoryRuntimeSchedulerRepository()
        else:
            repository = SQLiteRuntimeSchedulerRepository(resolved.runtime_scheduler_sqlite_path)
        return cls(
            repository,
            document_provider=WorkflowDocumentProvider(settings=resolved),
            monitoring_service=MonitoringBusService.from_settings(resolved),
            runtime_service=PersistentRuntimeExecutionService.from_settings(resolved),
            auto_media_enrichment_enabled=(resolved.monitoring_auto_media_enrichment_enabled),
            auto_media_enrichment_limit=resolved.monitoring_auto_media_enrichment_limit,
            auto_media_enrichment_concurrency=(
                resolved.monitoring_auto_media_enrichment_concurrency
            ),
        )

    def overview(self) -> DashboardOverview:
        return DashboardOverview(tickers=self.repository.list_states())

    def start_ticker(
        self,
        ticker: str,
        *,
        now: datetime | None = None,
        force_initialize: bool = False,
        monitor_mode: MonitorMode | str | None = None,
    ) -> TickerRunDetail:
        normalized = _ticker(ticker)
        current_time = _utc(now)
        phase = market_session_phase(current_time)
        existing = self.repository.get_state(normalized)
        resolved_mode = _resolve_monitor_mode(
            monitor_mode,
            default=_state_monitor_mode(existing) if existing is not None else None,
        )
        if existing is not None and existing.status in RUNNABLE_STATUSES and not force_initialize:
            self._audit(
                normalized,
                "ticker_start_idempotent",
                "Ticker is already running; start request reused existing state.",
                payload={"status": existing.status.value},
            )
            return self.detail(normalized, now=current_time)
        state = existing or TickerRunState(ticker=normalized, started_at=current_time)
        metadata = _monitor_mode_metadata(
            state,
            resolved_mode,
            now=current_time,
            reset_paper_window=existing is None or resolved_mode is MonitorMode.PAPER_TRADING,
        )
        metadata = _startup_progress_metadata(
            metadata,
            status="running",
            active_step_id="document1",
            updated_at=current_time,
        )
        state = state.model_copy(
            update={
                "status": TickerRunStatus.INITIALIZING,
                "health": RuntimeHealth.NORMAL,
                "session_phase": phase,
                "monitor_mode": resolved_mode,
                "updated_at": current_time,
                "stopped_at": None,
                "last_error": None,
                "metadata": metadata,
            },
            deep=True,
        )
        self.repository.upsert_state(state)
        self._audit(
            normalized,
            "ticker_started",
            "Ticker runtime start requested.",
            payload={"monitor_mode": resolved_mode.value},
        )
        try:
            bundle = self._ensure_documents(
                normalized,
                now=current_time,
                force_initialize=force_initialize,
            )
        except Exception as exc:
            failed_metadata = _startup_progress_metadata(
                state.metadata,
                status="blocked",
                active_step_id="document1",
                updated_at=current_time,
                blocked_step_id="document1",
                message=str(exc),
            )
            state = state.model_copy(update={"metadata": failed_metadata}, deep=True)
            blocked = self._blocked_state(
                state,
                now=current_time,
                message=f"Document initialization failed: {exc}",
            )
            self.repository.upsert_state(blocked)
            self._audit(
                normalized,
                "documents_initialization_failed",
                str(exc),
                severity=AuditSeverity.ERROR,
            )
            return self.detail(normalized, now=current_time)
        metadata = _startup_progress_from_documents(
            state.metadata,
            bundle,
            updated_at=current_time,
        )
        state = state.model_copy(
            update={
                "document_run_id": bundle.status.blackboard_run_id,
                "document_status": bundle.status,
                "metadata": metadata,
                "updated_at": current_time,
            },
            deep=True,
        )
        self.repository.upsert_state(state)
        self._clear_runtime_context_cache(normalized)
        if not bundle.status.usable:
            blocked_step_id = _first_blocked_document_step(bundle) or "document1"
            metadata = _startup_progress_metadata(
                state.metadata,
                status="blocked",
                active_step_id=blocked_step_id,
                updated_at=current_time,
                completed_step_ids=_completed_document_steps(bundle),
                blocked_step_id=blocked_step_id,
                message="Document set is missing, stale, or invalid.",
            )
            state = state.model_copy(update={"metadata": metadata}, deep=True)
            blocked = self._blocked_state(
                state,
                now=current_time,
                message="Document set is missing, stale, or invalid.",
                document_status=bundle.status,
            )
            self.repository.upsert_state(blocked)
            self._audit(
                normalized,
                "documents_blocked",
                "Ticker runtime blocked because Document 1/2/3 are not usable.",
                severity=AuditSeverity.ERROR,
                payload=bundle.status.model_dump(mode="json"),
            )
            return self.detail(normalized, now=current_time)
        metadata = _startup_progress_metadata(
            state.metadata,
            status="running",
            active_step_id="message_bus",
            updated_at=current_time,
            completed_step_ids={"document1", "document2", "document3"},
        )
        state = state.model_copy(
            update={"metadata": metadata, "updated_at": current_time},
            deep=True,
        )
        self.repository.upsert_state(state)
        applied_bindings = self._apply_monitoring_config(normalized, bundle)
        if bundle.monitoring_config is not None and not applied_bindings:
            metadata = _startup_progress_metadata(
                state.metadata,
                status="blocked",
                active_step_id="message_bus",
                updated_at=current_time,
                completed_step_ids={"document1", "document2", "document3"},
                blocked_step_id="message_bus",
                message="Monitoring Config produced no usable Message Bus bindings.",
            )
            state = state.model_copy(update={"metadata": metadata}, deep=True)
            blocked = self._blocked_state(
                state,
                now=current_time,
                message="Monitoring Config produced no usable Message Bus bindings.",
                document_status=bundle.status,
            )
            self.repository.upsert_state(blocked)
            return self.detail(normalized, now=current_time)
        metadata = _startup_progress_metadata(
            state.metadata,
            status="running",
            active_step_id="runtime",
            updated_at=current_time,
            completed_step_ids={"document1", "document2", "document3", "message_bus"},
        )
        state = state.model_copy(
            update={"metadata": metadata, "updated_at": current_time},
            deep=True,
        )
        self.repository.upsert_state(state)
        metadata = _startup_progress_metadata(
            state.metadata,
            status="completed",
            active_step_id=None,
            updated_at=current_time,
            completed_step_ids={
                "document1",
                "document2",
                "document3",
                "message_bus",
                "runtime",
            },
            visible=False,
        )
        state = state.model_copy(
            update={
                "status": TickerRunStatus.RUNNING,
                "health": RuntimeHealth.NORMAL,
                "session_phase": phase,
                "monitor_mode": resolved_mode,
                "document_run_id": bundle.status.blackboard_run_id,
                "document_status": bundle.status,
                "last_monitoring_config_version": _config_version(bundle),
                "updated_at": current_time,
                "metadata": metadata,
            },
            deep=True,
        )
        self.repository.upsert_state(state)
        self._clear_runtime_context_cache(normalized)
        self._audit(
            normalized,
            "ticker_running",
            "Ticker runtime is ready for scheduled polling and event consumption.",
            payload={
                "session_phase": phase.value,
                "binding_count": len(applied_bindings),
                "monitor_mode": resolved_mode.value,
            },
        )
        return self.detail(normalized, now=current_time)

    def set_monitor_mode(
        self,
        ticker: str,
        monitor_mode: MonitorMode | str,
        *,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> TickerRunDetail:
        normalized = _ticker(ticker)
        state = self.repository.get_state(normalized)
        if state is None:
            state = self._state_or_default(normalized, now=now)
        current_time = _utc(now)
        previous_mode = _state_monitor_mode(state)
        resolved_mode = _resolve_monitor_mode(monitor_mode)
        metadata = _monitor_mode_metadata(
            state,
            resolved_mode,
            now=current_time,
            reset_paper_window=previous_mode is not resolved_mode
            and resolved_mode is MonitorMode.PAPER_TRADING,
        )
        state = state.model_copy(
            update={
                "monitor_mode": resolved_mode,
                "updated_at": current_time,
                "metadata": metadata,
            },
            deep=True,
        )
        self.repository.upsert_state(state)
        self._audit(
            normalized,
            "ticker_monitor_mode_changed",
            reason or "Ticker monitor mode changed.",
            payload={
                "previous_monitor_mode": previous_mode.value,
                "monitor_mode": resolved_mode.value,
                "paper_trading_replays_historical_pending_events": False,
            },
        )
        return self.detail(normalized, now=current_time)

    def activate_document_run(
        self,
        ticker: str,
        document_run_id: str,
        *,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> TickerRunDetail:
        normalized = _ticker(ticker)
        resolved_run_id = document_run_id.strip()
        if not resolved_run_id:
            raise DocumentRunActivationError(
                "document_run_id is required.",
                details={"ticker": normalized},
            )
        current_time = _utc(now)
        bundle_loader = getattr(self.document_provider, "by_run_id", None)
        if bundle_loader is None:
            raise DocumentRunActivationError(
                "The configured document provider cannot load a document set by run id.",
                details={"ticker": normalized, "document_run_id": resolved_run_id},
            )
        try:
            bundle = bundle_loader(normalized, resolved_run_id, now=current_time)
        except RunNotFoundError as exc:
            raise DocumentRunNotFound(normalized, resolved_run_id) from exc
        if bundle.status.blackboard_run_id != resolved_run_id:
            raise DocumentRunActivationError(
                "Document run does not belong to the requested ticker.",
                details={"ticker": normalized, "document_run_id": resolved_run_id},
            )
        if not bundle.status.usable:
            raise DocumentRunActivationError(
                "Document set is not usable for Dashboard runtime activation.",
                details={
                    "ticker": normalized,
                    "document_run_id": resolved_run_id,
                    "missing_document_types": [
                        item.value for item in bundle.status.missing_document_types
                    ],
                    "stale": bundle.status.stale,
                    "components": [
                        component.model_dump(mode="json") for component in bundle.status.components
                    ],
                },
            )
        state = self._state_or_default(normalized, now=current_time)
        bindings = self._apply_monitoring_config(normalized, bundle)
        metadata = dict(state.metadata)
        metadata["document_activation"] = {
            "activated_at": current_time.isoformat(),
            "activated_by": "dashboard_user",
            "reason": reason or "Dashboard manual document activation.",
            "previous_document_run_id": state.document_run_id,
            "document_run_id": resolved_run_id,
            "binding_count": len(bindings),
        }
        metadata = _startup_progress_metadata(
            metadata,
            status="completed",
            active_step_id=None,
            updated_at=current_time,
            completed_step_ids={
                "document1",
                "document2",
                "document3",
                "message_bus",
                "runtime",
            },
            visible=False,
        )
        updated = state.model_copy(
            update={
                "document_run_id": resolved_run_id,
                "document_status": bundle.status,
                "last_monitoring_config_version": _config_version(bundle),
                "last_error": None,
                "updated_at": current_time,
                "metadata": metadata,
            },
            deep=True,
        )
        self.repository.upsert_state(updated)
        self._clear_runtime_context_cache(normalized)
        self._audit(
            normalized,
            "document_run_manual_activated",
            "Dashboard user manually activated a historical document set.",
            payload={
                "document_run_id": resolved_run_id,
                "previous_document_run_id": state.document_run_id,
                "reason": reason,
                "binding_count": len(bindings),
            },
        )
        return self.detail(normalized, now=current_time)

    def pause_ticker(
        self,
        ticker: str,
        *,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> TickerRunDetail:
        state = self._state_or_default(ticker, now=now)
        current_time = _utc(now)
        state = state.model_copy(
            update={
                "status": TickerRunStatus.PAUSED,
                "updated_at": current_time,
                "last_error": reason,
            },
            deep=True,
        )
        self.repository.upsert_state(state)
        self._audit(
            state.ticker,
            "ticker_paused",
            reason or "Ticker runtime paused by user/system request.",
        )
        return self.detail(state.ticker, now=current_time)

    def stop_ticker(
        self,
        ticker: str,
        *,
        reason: str | None = None,
        disable_bindings: bool = True,
        now: datetime | None = None,
    ) -> TickerRunDetail:
        state = self._state_or_default(ticker, now=now)
        current_time = _utc(now)
        disabled_count = self._disable_bindings(state.ticker) if disable_bindings else 0
        state = state.model_copy(
            update={
                "status": TickerRunStatus.STOPPED,
                "health": RuntimeHealth.NORMAL,
                "updated_at": current_time,
                "stopped_at": current_time,
                "last_error": reason,
            },
            deep=True,
        )
        self.repository.upsert_state(state)
        self._audit(
            state.ticker,
            "ticker_stopped",
            reason or "Ticker runtime stopped by user/system request.",
            payload={"disabled_binding_count": disabled_count},
        )
        return self.detail(state.ticker, now=current_time)

    def run_due_once(
        self,
        *,
        now: datetime | None = None,
        event_limit: int = 100,
    ) -> list[TickerRunDetail]:
        details: list[TickerRunDetail] = []
        for state in self.repository.list_states():
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
            return self.start_ticker(normalized, now=now)
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
        state = self._apply_completed_weekly_update_job(state, now=current_time)
        monitor_mode = _state_monitor_mode(state)
        should_run_runtime = monitor_mode is MonitorMode.PAPER_TRADING and phase in {
            MarketSessionPhase.PRE_MARKET_DIGEST,
            MarketSessionPhase.FORMAL_MONITORING,
        }
        runtime_context_bundle = (
            self._runtime_bundle_for_tick(state) if should_run_runtime else None
        )
        self._ensure_weekly_update_job(
            state,
            now=current_time,
            runtime_continues=should_run_runtime,
            current_bundle=runtime_context_bundle,
        )
        poll_failures = 0
        poll_messages = 0
        poll_events = 0
        poll_results: list[IngestBatchResult] = []
        for binding in self._due_bindings_for_phase(normalized, phase, now=current_time):
            try:
                result = self.monitoring_service.poll_binding(binding.ticker, binding.source_id)
                poll_results.append(result)
                poll_messages += result.collected_count
                poll_events += result.event_count
                self._audit(
                    normalized,
                    "message_poll_completed",
                    "Monitoring source poll completed.",
                    payload=result.model_dump(mode="json"),
                )
            except Exception as exc:
                poll_failures += 1
                self._audit(
                    normalized,
                    "message_poll_failed",
                    str(exc),
                    severity=AuditSeverity.WARNING,
                    payload={"source_id": binding.source_id},
                )
        self._maybe_enrich_polled_media(normalized, poll_results)
        pending_count_before_runtime = 0
        consumed_count = 0
        runtime_count = 0
        trade_intent_count = 0
        failed_event_count = 0
        runtime_failed = False
        if should_run_runtime:
            try:
                pending = self.monitoring_service.pending_events(
                    ticker=normalized,
                    limit=event_limit,
                )
                pending = self._exclude_runtime_ineligible_events(normalized, pending)
                pending = _runtime_eligible_events(state, pending)
                pending_count_before_runtime = len(pending)
                context = self._runtime_context(state, bundle=runtime_context_bundle)
                before_trade_count = len(
                    self.runtime_service.repository.list_trading_records(ticker=normalized)
                )
                records = self.runtime_service.execute_events(
                    pending,
                    context=context,
                    mark_consumed=self.monitoring_service.mark_event_consumed,
                )
                after_trade_count = len(
                    self.runtime_service.repository.list_trading_records(ticker=normalized)
                )
                runtime_count = len(records)
                consumed_count = len(pending)
                trade_intent_count = max(0, after_trade_count - before_trade_count)
                if consumed_count:
                    self._audit(
                        normalized,
                        "events_consumed",
                        "Pending event-stream items were consumed by Persistent Runtime.",
                        payload={
                            "event_count": consumed_count,
                            "runtime_record_count": runtime_count,
                            "trade_intent_count": trade_intent_count,
                        },
                    )
            except Exception as exc:
                runtime_failed = True
                failed_event_count = pending_count_before_runtime
                self._audit(
                    normalized,
                    "runtime_event_consumption_failed",
                    str(exc),
                    severity=AuditSeverity.ERROR,
                )
        pending_count_after_runtime = len(
            self.monitoring_service.pending_events(
                ticker=normalized,
                limit=event_limit,
            )
        )
        state = self._apply_completed_weekly_update_job(state, now=current_time)
        counters = state.counters.model_copy(
            update={
                "poll_cycles": state.counters.poll_cycles + 1,
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
        health = (
            RuntimeHealth.DEGRADED
            if state.health is RuntimeHealth.DEGRADED
            else RuntimeHealth.NORMAL
        )
        status = (
            TickerRunStatus.DEGRADED
            if state.status is TickerRunStatus.DEGRADED
            else TickerRunStatus.RUNNING
        )
        last_error = state.last_error if health is RuntimeHealth.DEGRADED else None
        if runtime_failed:
            health = RuntimeHealth.DEGRADED
            status = TickerRunStatus.DEGRADED
            last_error = "Runtime event consumption failed; pending events remain unconsumed."
        elif poll_failures:
            health = RuntimeHealth.DEGRADED
            status = TickerRunStatus.DEGRADED
            last_error = f"{poll_failures} monitoring source poll(s) failed."
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
        document_status = self._document_status_for_state(state, now=_utc(now))
        return TickerRunDetail(
            state=state,
            document_status=document_status,
            message_bus_status=self.monitoring_status(normalized, now=now, limit=limit),
            runtime_status=self.event_processing_status(normalized, limit=limit),
            trade_intents=self.trade_intents(normalized, limit=limit),
            exceptions=self.runtime_service.repository.list_exceptions(ticker=normalized)[-limit:],
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
        return self._document_status_for_state(
            state or self._state_or_default(normalized, now=now),
            now=_utc(now),
        )

    def monitoring_status(
        self,
        ticker: str,
        *,
        now: datetime | None = None,
        limit: int = 50,
    ) -> MonitoringRunStatus:
        normalized = _ticker(ticker)
        snapshot = self.monitoring_service.status_snapshot(ticker=normalized, limit=limit)
        poll_states = {state.binding_id: state for state in snapshot.poll_states}
        pending_events = self.monitoring_service.pending_events(ticker=normalized, limit=limit)
        last_success_at = _latest(
            state.last_success_at for state in snapshot.poll_states if state.last_success_at
        )
        last_error_at = _latest(
            state.last_error_at for state in snapshot.poll_states if state.last_error_at
        )
        last_error_message = None
        for state in sorted(
            snapshot.poll_states,
            key=lambda item: item.last_error_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        ):
            if state.last_error_message:
                last_error_message = state.last_error_message
                break
        return MonitoringRunStatus(
            ticker=normalized,
            session_phase=market_session_phase(_utc(now)),
            configured_sources=[
                MonitoringBindingStatus(
                    binding=binding,
                    poll_state=poll_states.get(binding.binding_id),
                )
                for binding in snapshot.bindings
            ],
            pending_event_count=len(pending_events),
            recent_event_count=len(snapshot.recent_events),
            recent_message_count=len(snapshot.recent_standard_messages),
            last_success_at=last_success_at,
            last_error_at=last_error_at,
            last_error_message=last_error_message,
        )

    def event_processing_status(
        self,
        ticker: str,
        *,
        limit: int = 50,
    ) -> EventProcessingStatus:
        normalized = _ticker(ticker)
        pending_events = self.monitoring_service.pending_events(ticker=normalized, limit=limit)
        recent_events = self.monitoring_service.recent_events(ticker=normalized, limit=limit)
        observations = self.runtime_service.runtime_observations(ticker=normalized)
        exceptions = self.runtime_service.repository.list_exceptions(ticker=normalized)
        last_execution_at = _latest(observation.created_at for observation in observations)
        return EventProcessingStatus(
            ticker=normalized,
            pending_event_count=len(pending_events),
            consumed_event_count=sum(1 for event in recent_events if event.consumed),
            runtime_execution_count=len(
                self.runtime_service.repository.list_executions(ticker=normalized)
            ),
            recent_observations=observations[-limit:],
            exception_count=len(exceptions),
            last_execution_at=last_execution_at,
        )

    def trade_intents(self, ticker: str, *, limit: int = 50) -> list[TradeIntentView]:
        records = self.runtime_service.repository.list_trading_records(ticker=_ticker(ticker))
        return [TradeIntentView.from_record(record) for record in records[-limit:]]

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

    def _ensure_documents(
        self,
        ticker: str,
        *,
        now: datetime,
        force_initialize: bool,
    ) -> DocumentBundle:
        bundle = self.document_provider.latest(ticker, now=now)
        if bundle.status.usable and not force_initialize:
            self._audit(
                ticker,
                "documents_reused",
                "Usable recent Document 1/2/3 set reused.",
                payload=bundle.status.model_dump(mode="json"),
            )
            return bundle
        self._audit(
            ticker,
            "documents_initialization_started",
            "Document set is missing/stale or force_initialize was requested.",
            payload=bundle.status.model_dump(mode="json"),
        )
        initialized = self.document_provider.initialize(ticker, now=now)
        self._audit(
            ticker,
            "documents_initialized",
            "Initialization workflow returned a document set.",
            payload=initialized.status.model_dump(mode="json"),
        )
        return initialized

    def _apply_monitoring_config(
        self,
        ticker: str,
        bundle: DocumentBundle,
    ) -> list[TickerSourceBinding]:
        document = bundle.monitoring_config
        if document is None:
            return []
        bindings: list[TickerSourceBinding] = []
        for item in document.monitoring_items:
            tool_input = dict(item.tool_input)
            source_id = str(tool_input.get("source_id") or "stocktwits_messages").strip().lower()
            try:
                parameters = _parameters_for_source(source_id, tool_input)
                binding = self.monitoring_service.configure_ticker_source(
                    ticker,
                    source_id,
                    parameters=parameters,
                    enabled=bool(tool_input.get("enabled", True)),
                    updated_by=UpdateActor.SYSTEM,
                    updated_reason=str(
                        tool_input.get("reason") or item.reasoning or "scheduler config apply"
                    ),
                    merge=str(tool_input.get("mode") or "merge").lower() != "replace",
                )
                bindings.append(binding)
            except Exception as exc:
                self._audit(
                    ticker,
                    "monitoring_config_item_failed",
                    str(exc),
                    severity=AuditSeverity.WARNING,
                    payload={"item_id": item.item_id, "source_id": source_id},
                )
        self._audit(
            ticker,
            "monitoring_config_applied",
            "Monitoring Config was applied to Message Bus bindings.",
            payload={
                "document_id": document.document_id,
                "applied_config_version": _config_version(bundle),
                "binding_count": len(bindings),
            },
        )
        return bindings

    def _due_bindings_for_phase(
        self,
        ticker: str,
        phase: MarketSessionPhase,
        *,
        now: datetime,
    ) -> list[TickerSourceBinding]:
        bindings: list[TickerSourceBinding] = []
        for binding in self.monitoring_service.due_bindings(now=now):
            if binding.ticker != ticker:
                continue
            if (
                phase is MarketSessionPhase.OFF_HOURS_LOW_FREQUENCY
                and binding.source_id not in self.low_frequency_source_ids
            ):
                continue
            bindings.append(binding)
        return bindings

    def _maybe_enrich_polled_media(
        self,
        ticker: str,
        poll_results: list[IngestBatchResult],
    ) -> None:
        if not self.auto_media_enrichment_enabled:
            return
        if not self._poll_results_include_media_events(poll_results):
            return
        try:
            payload = self.monitoring_service.enrich_recent_media(
                ticker=ticker,
                limit=self.auto_media_enrichment_limit,
                concurrency=self.auto_media_enrichment_concurrency,
                dry_run=False,
                incomplete_only=True,
            )
            stats = payload.get("stats") if isinstance(payload, dict) else None
            self._audit(
                ticker,
                "media_enrichment_completed",
                "Recent media messages were enriched after polling.",
                payload={"stats": stats if isinstance(stats, dict) else payload},
            )
        except Exception as exc:
            self._audit(
                ticker,
                "media_enrichment_failed",
                str(exc),
                severity=AuditSeverity.WARNING,
            )

    def _poll_results_include_media_events(
        self,
        poll_results: list[IngestBatchResult],
    ) -> bool:
        for result in poll_results:
            if result.event_count <= 0:
                continue
            source = self.monitoring_service.repository.get_source(result.source_id)
            if source is not None and source.source_type is SourceType.MEDIA:
                return True
        return False

    def _disable_bindings(self, ticker: str) -> int:
        disabled = 0
        for binding in self.monitoring_service.repository.list_bindings(ticker=ticker):
            if not binding.enabled:
                continue
            self.monitoring_service.configure_ticker_source(
                ticker,
                binding.source_id,
                parameters=binding.parameters,
                enabled=False,
                updated_by=UpdateActor.USER,
                updated_reason="Ticker runtime stopped.",
                merge=False,
            )
            disabled += 1
        return disabled

    def _ensure_weekly_update_job(
        self,
        state: TickerRunState,
        *,
        now: datetime,
        runtime_continues: bool,
        current_bundle: DocumentBundle | None = None,
    ) -> None:
        if not _weekly_update_due(state, now):
            return
        started = False
        audit_runtime_continue = False
        with self._weekly_update_lock:
            job = self._weekly_update_jobs.get(state.ticker)
            if job is None:
                job = _WeeklyDocumentUpdateJob(
                    ticker=state.ticker,
                    due_at=now,
                    current_bundle=current_bundle,
                )
                job.thread = Thread(
                    target=self._run_weekly_update_job,
                    args=(job,),
                    daemon=True,
                    name=f"doxagent-weekly-update-{state.ticker.lower()}",
                )
                self._weekly_update_jobs[state.ticker] = job
                started = True
            elif job.current_bundle is None and current_bundle is not None:
                job.current_bundle = current_bundle
            if runtime_continues and not job.runtime_continues_audited:
                job.runtime_continues_audited = True
                audit_runtime_continue = True
        if started:
            self._audit(
                state.ticker,
                "weekly_document_update_started",
                "Weekly Monday 06:00 ET document update started asynchronously.",
            )
            if job.thread is not None:
                job.thread.start()
        if audit_runtime_continue:
            self._audit(
                state.ticker,
                "weekly_document_update_continues_with_current_documents",
                (
                    "Persistent Runtime continues with the current document bundle "
                    "while the weekly update is still pending."
                ),
                payload={
                    "document_run_id": state.document_run_id,
                    "last_monitoring_config_version": (state.last_monitoring_config_version),
                    "session_phase": state.session_phase.value,
                    "monitor_mode": state.monitor_mode.value,
                    "weekly_update_due_at": now.isoformat(),
                },
            )

    def _runtime_bundle_for_tick(self, state: TickerRunState) -> DocumentBundle:
        with self._weekly_update_lock:
            job = self._weekly_update_jobs.get(state.ticker)
            if job is not None and job.current_bundle is not None:
                return job.current_bundle
        if state.document_run_id:
            cache_key = (state.ticker, state.document_run_id)
            cached = self._runtime_bundle_cache.get(cache_key)
            if cached is not None:
                return cached.model_copy(deep=True)
            loader = getattr(self.document_provider, "by_run_id", None)
            if callable(loader):
                bundle = loader(state.ticker, state.document_run_id)
                self._runtime_bundle_cache[cache_key] = bundle.model_copy(deep=True)
                return bundle
        bundle = self.document_provider.latest(state.ticker)
        if bundle.status.blackboard_run_id:
            self._runtime_bundle_cache[(state.ticker, bundle.status.blackboard_run_id)] = (
                bundle.model_copy(deep=True)
            )
        return bundle

    def _run_weekly_update_job(self, job: _WeeklyDocumentUpdateJob) -> None:
        bundle: DocumentBundle | None = None
        error: Exception | None = None
        try:
            bundle = self.document_provider.initialize(job.ticker, now=job.due_at)
        except Exception as exc:
            error = exc
        with self._weekly_update_lock:
            job.bundle = bundle
            job.error = error
            job.completed_at = datetime.now(UTC)

    def _apply_completed_weekly_update_job(
        self,
        state: TickerRunState,
        *,
        now: datetime,
    ) -> TickerRunState:
        with self._weekly_update_lock:
            job = self._weekly_update_jobs.get(state.ticker)
            if job is None or job.completed_at is None:
                return state
            self._weekly_update_jobs.pop(state.ticker, None)
        if job.error is not None:
            self._audit(
                state.ticker,
                "weekly_document_update_failed",
                str(job.error),
                severity=AuditSeverity.WARNING,
            )
            return state.model_copy(
                update={
                    "health": RuntimeHealth.DEGRADED,
                    "status": TickerRunStatus.DEGRADED,
                    "last_weekly_update_at": now,
                    "last_error": f"Weekly document update failed: {job.error}",
                },
                deep=True,
            )
        bundle = job.bundle
        if bundle is None or not bundle.status.usable:
            payload = bundle.status.model_dump(mode="json") if bundle is not None else {}
            self._audit(
                state.ticker,
                "weekly_document_update_failed",
                "Weekly update did not produce a usable document set; keeping old version.",
                severity=AuditSeverity.WARNING,
                payload=payload,
            )
            return state.model_copy(
                update={
                    "health": RuntimeHealth.DEGRADED,
                    "status": TickerRunStatus.DEGRADED,
                    "last_weekly_update_at": now,
                    "last_error": "Weekly document update did not produce usable docs.",
                },
                deep=True,
            )
        bindings = self._apply_monitoring_config(state.ticker, bundle)
        self._clear_runtime_context_cache(state.ticker)
        self._audit(
            state.ticker,
            "weekly_document_update_completed",
            "Weekly document update completed; scheduler switched to new usable version.",
            payload={
                "binding_count": len(bindings),
                "document_run_id": bundle.status.blackboard_run_id,
                "document_status": bundle.status.model_dump(mode="json"),
                "completed_at": (job.completed_at.isoformat() if job.completed_at else None),
                "switch_mode": "atomic_after_async_update",
            },
        )
        return state.model_copy(
            update={
                "document_run_id": bundle.status.blackboard_run_id,
                "document_status": bundle.status,
                "last_monitoring_config_version": _config_version(bundle),
                "last_weekly_update_at": now,
                "last_error": None,
            },
            deep=True,
        )

    def _exclude_runtime_ineligible_events(
        self,
        ticker: str,
        events: list[EventStreamItem],
    ) -> list[EventStreamItem]:
        eligible: list[EventStreamItem] = []
        excluded: list[EventStreamItem] = []
        for event in events:
            if self._is_social_runtime_excluded_event(event):
                excluded.append(event)
            else:
                eligible.append(event)
        if not excluded:
            return eligible
        for event in excluded:
            self.monitoring_service.mark_event_consumed(event.event_id)
        self._audit(
            ticker,
            "runtime_social_events_excluded",
            "Social source events were excluded from Persistent Runtime consumption.",
            payload={
                "reason": "social_sources_temporarily_message_bus_only",
                "event_count": len(excluded),
                "source_ids": sorted({event.source_id for event in excluded}),
                "event_ids": [event.event_id for event in excluded[:20]],
                "standard_message_ids": [event.standard_message_id for event in excluded[:20]],
            },
        )
        return eligible

    def _is_social_runtime_excluded_event(self, event: EventStreamItem) -> bool:
        if event.source_id.strip().lower() in SOCIAL_RUNTIME_EXCLUDED_SOURCE_IDS:
            return True
        payload_source_type = str(event.payload.get("source_type") or "").lower()
        if payload_source_type == SourceType.SOCIAL.value:
            return True
        source = self.monitoring_service.repository.get_source(event.source_id)
        return source is not None and source.source_type is SourceType.SOCIAL

    def _maybe_run_weekly_update(self, state: TickerRunState, *, now: datetime) -> TickerRunState:
        if not _weekly_update_due(state, now):
            return state
        try:
            self._audit(
                state.ticker,
                "weekly_document_update_started",
                "Weekly Monday 06:00 ET document update started.",
            )
            bundle = self.document_provider.initialize(state.ticker, now=now)
            if not bundle.status.usable:
                self._audit(
                    state.ticker,
                    "weekly_document_update_failed",
                    "Weekly update did not produce a usable document set; keeping old version.",
                    severity=AuditSeverity.WARNING,
                    payload=bundle.status.model_dump(mode="json"),
                )
                return state.model_copy(
                    update={
                        "health": RuntimeHealth.DEGRADED,
                        "status": TickerRunStatus.DEGRADED,
                        "last_weekly_update_at": now,
                        "last_error": "Weekly document update did not produce usable docs.",
                    },
                    deep=True,
                )
            bindings = self._apply_monitoring_config(state.ticker, bundle)
            self._clear_runtime_context_cache(state.ticker)
            self._audit(
                state.ticker,
                "weekly_document_update_completed",
                "Weekly document update completed; scheduler switched to new usable version.",
                payload={
                    "binding_count": len(bindings),
                    "document_run_id": bundle.status.blackboard_run_id,
                    "document_status": bundle.status.model_dump(mode="json"),
                },
            )
            return state.model_copy(
                update={
                    "document_run_id": bundle.status.blackboard_run_id,
                    "document_status": bundle.status,
                    "last_monitoring_config_version": _config_version(bundle),
                    "last_weekly_update_at": now,
                    "last_error": None,
                },
                deep=True,
            )
        except Exception as exc:
            self._audit(
                state.ticker,
                "weekly_document_update_failed",
                str(exc),
                severity=AuditSeverity.WARNING,
            )
            return state.model_copy(
                update={
                    "health": RuntimeHealth.DEGRADED,
                    "status": TickerRunStatus.DEGRADED,
                    "last_weekly_update_at": now,
                    "last_error": f"Weekly document update failed: {exc}",
                },
                deep=True,
            )

    def _runtime_context(
        self,
        state: TickerRunState,
        *,
        bundle: DocumentBundle | None = None,
    ) -> dict[str, object]:
        cache_key = (
            state.ticker,
            bundle.status.blackboard_run_id if bundle is not None else state.document_run_id,
        )
        if cache_key[1] and bundle is None and cache_key in self._runtime_context_cache:
            return deepcopy(self._runtime_context_cache[cache_key])
        bundle = bundle or self._runtime_bundle_for_tick(state)
        monitoring_policy = bundle.monitoring_policy
        known_events = bundle.known_events
        context: dict[str, object] = {
            "ticker": state.ticker,
            "document_run_id": bundle.status.blackboard_run_id or state.document_run_id,
        }
        if known_events is not None:
            context["known_events"] = [
                event.model_dump(mode="json") for event in known_events.events
            ]
        if monitoring_policy is not None:
            policies = monitoring_policy.policies or [
                *monitoring_policy.direct_trade_rules,
                *monitoring_policy.push_to_agent_rules,
                *monitoring_policy.cache_rules,
            ]
            context["monitoring_policies"] = [policy.model_dump(mode="json") for policy in policies]
        resolved_key = (state.ticker, str(context["document_run_id"] or ""))
        if resolved_key[1]:
            self._runtime_context_cache[resolved_key] = deepcopy(context)
        return context

    def _document_status_for_state(
        self,
        state: TickerRunState,
        *,
        now: datetime,
    ) -> DocumentSetStatus:
        if state.document_status is not None:
            return state.document_status
        if state.document_run_id:
            loader = getattr(self.document_provider, "by_run_id", None)
            if callable(loader):
                try:
                    return loader(state.ticker, state.document_run_id, now=now).status
                except RunNotFoundError:
                    pass
        return self.document_provider.latest(state.ticker, now=now).status

    def _clear_runtime_context_cache(self, ticker: str) -> None:
        normalized = _ticker(ticker)
        for key in [item for item in self._runtime_bundle_cache if item[0] == normalized]:
            self._runtime_bundle_cache.pop(key, None)
        for key in [item for item in self._runtime_context_cache if item[0] == normalized]:
            self._runtime_context_cache.pop(key, None)

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

    def _blocked_state(
        self,
        state: TickerRunState,
        *,
        now: datetime,
        message: str,
        document_status: DocumentSetStatus | None = None,
    ) -> TickerRunState:
        counters = state.counters.model_copy(
            update={"failure_count": state.counters.failure_count + 1},
            deep=True,
        )
        return state.model_copy(
            update={
                "status": TickerRunStatus.BLOCKED,
                "health": RuntimeHealth.BLOCKED,
                "updated_at": now,
                "document_status": document_status or state.document_status,
                "last_error": message,
                "counters": counters,
            },
            deep=True,
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


def _weekly_update_due(state: TickerRunState, now: datetime) -> bool:
    now_et = now.astimezone(ET)
    if now_et.weekday() != 0 or now_et.time() < time(6, 0):
        return False
    if state.last_weekly_update_at is None:
        return True
    return state.last_weekly_update_at.astimezone(ET).date() != now_et.date()


def _parameters_for_source(
    source_id: str,
    tool_input: dict[str, object],
) -> MonitoringParameters:
    allowed = set(parameter_schema_for_source(source_id))

    def allowed_list(key: str) -> list[str]:
        if key not in allowed:
            return []
        value = tool_input.get(key)
        if isinstance(value, list | tuple | set):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    return MonitoringParameters(
        keywords=allowed_list("keywords"),
        usernames=allowed_list("usernames"),
        search_terms=allowed_list("search_terms"),
        rss_urls=allowed_list("rss_urls"),
        source_filters=allowed_list("source_filters"),
    )


def _config_version(bundle: DocumentBundle) -> str | None:
    if bundle.monitoring_config is None:
        return None
    return (
        bundle.monitoring_config.applied_config_version
        or f"{bundle.monitoring_config.document_id}:scheduler"
    )


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
        try:
            resolved = MonitorMode(value.strip())
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
            return MonitorMode(metadata_value)
        except ValueError:
            pass
    return state.monitor_mode


def _monitor_mode_metadata(
    state: TickerRunState,
    monitor_mode: MonitorMode,
    *,
    now: datetime,
    reset_paper_window: bool,
) -> dict[str, object]:
    metadata = dict(state.metadata)
    metadata["monitor_mode"] = monitor_mode.value
    if monitor_mode is MonitorMode.PAPER_TRADING and (
        reset_paper_window or not metadata.get("paper_trading_enabled_at")
    ):
        metadata["paper_trading_enabled_at"] = (
            now.astimezone(UTC).isoformat().replace("+00:00", "Z")
        )
        metadata["paper_trading_replays_historical_pending_events"] = False
    return metadata


def _runtime_eligible_events(
    state: TickerRunState,
    events: list[EventStreamItem],
) -> list[EventStreamItem]:
    enabled_at = _metadata_datetime(state.metadata.get("paper_trading_enabled_at"))
    if enabled_at is None:
        return events
    return [event for event in events if event.event_time.astimezone(UTC) >= enabled_at]


def _metadata_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _utc(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _utc(parsed)


def _startup_progress_metadata(
    metadata: dict[str, object],
    *,
    status: str,
    active_step_id: str | None,
    updated_at: datetime,
    completed_step_ids: set[str] | None = None,
    blocked_step_id: str | None = None,
    message: str | None = None,
    visible: bool = True,
) -> dict[str, object]:
    completed = set(completed_step_ids or set())
    steps: list[dict[str, object]] = []
    for step_id, label in STARTUP_STEP_DEFINITIONS:
        if step_id in completed:
            step_status = "completed"
            progress = 100
        elif step_id == blocked_step_id:
            step_status = "blocked"
            progress = 100
        elif step_id == active_step_id and status == "running":
            step_status = "running"
            progress = 50
        else:
            step_status = "pending"
            progress = 0
        steps.append(
            {
                "step_id": step_id,
                "label": label,
                "status": step_status,
                "progress": progress,
            }
        )
    next_metadata = dict(metadata)
    next_metadata["startup_progress"] = {
        "status": status,
        "status_label": _startup_status_label(status),
        "visible": visible,
        "current_step_id": active_step_id,
        "retryable": status == "blocked",
        "message": message,
        "updated_at": updated_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "steps": steps,
    }
    return next_metadata


def _startup_progress_from_documents(
    metadata: dict[str, object],
    bundle: DocumentBundle,
    *,
    updated_at: datetime,
) -> dict[str, object]:
    completed = _completed_document_steps(bundle)
    if bundle.status.usable:
        return _startup_progress_metadata(
            metadata,
            status="running",
            active_step_id="message_bus",
            completed_step_ids=completed,
            updated_at=updated_at,
        )
    blocked_step_id = _first_blocked_document_step(bundle) or "document1"
    return _startup_progress_metadata(
        metadata,
        status="blocked",
        active_step_id=blocked_step_id,
        completed_step_ids=completed,
        blocked_step_id=blocked_step_id,
        message="Document set is missing, stale, or invalid.",
        updated_at=updated_at,
    )


def _completed_document_steps(bundle: DocumentBundle) -> set[str]:
    availability = _component_availability(bundle)
    completed: set[str] = set()
    if availability.get("global_research") is DocumentAvailability.AVAILABLE:
        completed.add("document1")
    if availability.get("expectation_unit") is DocumentAvailability.AVAILABLE:
        completed.add("document2")
    if (
        availability.get("known_events") is DocumentAvailability.AVAILABLE
        and availability.get("monitoring_policy") is DocumentAvailability.AVAILABLE
    ):
        completed.add("document3")
    return completed


def _first_blocked_document_step(bundle: DocumentBundle) -> str | None:
    completed = _completed_document_steps(bundle)
    for step_id in ("document1", "document2", "document3"):
        if step_id not in completed:
            return step_id
    return None


def _component_availability(bundle: DocumentBundle) -> dict[str, DocumentAvailability]:
    return {
        component.document_type.value: component.availability
        for component in bundle.status.components
    }


def _startup_status_label(status: str) -> str:
    labels = {
        "running": "启动中",
        "blocked": "阻塞",
        "completed": "已完成",
    }
    return labels.get(status, status)


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
