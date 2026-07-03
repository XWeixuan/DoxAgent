from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from doxagent.blackboard import BlackboardService, InMemoryBlackboardRepository
from doxagent.models import (
    AgentName,
    DocumentType,
    ExpectationUnitDocument,
    MonitoringConfigDocument,
    MonitoringItem,
)
from doxagent.monitoring.repository import InMemoryMonitoringRepository, SQLiteMonitoringRepository
from doxagent.monitoring.schema import (
    EventStreamItem,
    InterfaceType,
    SourceType,
    StandardMessage,
    UpdateActor,
)
from doxagent.monitoring.service import MonitoringBusService
from doxagent.persistent_runtime import (
    InMemoryPersistentRuntimeRepository,
    PersistentRuntimeExecutionService,
    RuntimeExecutionRecord,
    SQLitePersistentRuntimeRepository,
)
from doxagent.runtime_scheduler import (
    DocumentAvailability,
    DocumentBundle,
    DocumentComponentStatus,
    DocumentRefreshRequest,
    DocumentSetStatus,
    InMemoryRuntimeSchedulerRepository,
    MarketSessionPhase,
    MonitorMode,
    RefreshRequestSource,
    RuntimeAuditEvent,
    RuntimeHealth,
    RuntimeSchedulerLoop,
    SQLiteRuntimeSchedulerRepository,
    TickerRunDetail,
    TickerRunState,
    TickerRunStatus,
    UnifiedRuntimeSchedulerService,
    WorkflowDocumentProvider,
    market_session_phase,
)
from tests.fixtures.phase1_contracts import (
    expectation_document,
    global_research_document,
    known_events_document,
    monitoring_policy_document,
)


class FakeDocumentProvider:
    def __init__(
        self,
        bundle: DocumentBundle,
        initialized_bundle: DocumentBundle | None = None,
    ) -> None:
        self.bundle = bundle
        self.initialized_bundle = initialized_bundle or bundle
        self.latest_calls = 0
        self.initialize_calls = 0

    def latest(self, ticker: str, *, now: datetime | None = None) -> DocumentBundle:
        self.latest_calls += 1
        return self.bundle

    def initialize(self, ticker: str, *, now: datetime | None = None) -> DocumentBundle:
        self.initialize_calls += 1
        self.bundle = self.initialized_bundle
        return self.bundle


def test_market_session_phase_uses_et_mvp_workday_rules() -> None:
    assert market_session_phase(datetime(2026, 6, 29, 11, 15, tzinfo=UTC)) == (
        MarketSessionPhase.PRE_MARKET_DIGEST
    )
    assert market_session_phase(datetime(2026, 6, 30, 11, 15, tzinfo=UTC)) == (
        MarketSessionPhase.OFF_HOURS_LOW_FREQUENCY
    )
    assert market_session_phase(datetime(2026, 6, 30, 11, 45, tzinfo=UTC)) == (
        MarketSessionPhase.PRE_MARKET_DIGEST
    )
    assert market_session_phase(datetime(2026, 6, 30, 12, 15, tzinfo=UTC)) == (
        MarketSessionPhase.FORMAL_MONITORING
    )
    assert market_session_phase(datetime(2026, 6, 30, 22, 30, tzinfo=UTC)) == (
        MarketSessionPhase.OFF_HOURS_LOW_FREQUENCY
    )
    assert market_session_phase(datetime(2026, 7, 4, 15, 0, tzinfo=UTC)) == (
        MarketSessionPhase.OFF_HOURS_LOW_FREQUENCY
    )


def test_start_ticker_initializes_missing_documents_and_applies_monitoring_config() -> None:
    scheduler, provider, monitoring_service, _runtime_service = _scheduler(
        _missing_bundle(),
        initialized_bundle=_usable_bundle(),
    )

    detail = scheduler.start_ticker(
        "nvda",
        now=datetime(2026, 6, 30, 12, 15, tzinfo=UTC),
    )

    assert provider.initialize_calls == 1
    assert detail.state.status is TickerRunStatus.RUNNING
    assert detail.state.health is RuntimeHealth.NORMAL
    assert detail.state.monitor_mode is MonitorMode.MESSAGE_MONITORING
    assert detail.document_status.usable is True
    startup_progress = detail.state.metadata["startup_progress"]
    assert startup_progress["status"] == "completed"
    assert startup_progress["visible"] is False
    binding = monitoring_service.repository.get_binding("NVDA", "benzinga_news")
    assert binding is not None
    assert binding.enabled is True
    assert detail.monitoring_status.configured_sources[0].binding.source_id == "benzinga_news"
    audit_types = [event.event_type for event in detail.audit_events]
    assert "documents_initialization_started" in audit_types
    assert "monitoring_config_applied" in audit_types


def test_start_ticker_exposes_blocked_startup_progress_when_documents_fail() -> None:
    scheduler, provider, _monitoring_service, _runtime_service = _scheduler(_missing_bundle())

    detail = scheduler.start_ticker(
        "nvda",
        now=datetime(2026, 6, 30, 12, 15, tzinfo=UTC),
    )

    progress = detail.state.metadata["startup_progress"]
    assert provider.initialize_calls == 1
    assert detail.state.status is TickerRunStatus.BLOCKED
    assert progress["status"] == "blocked"
    assert progress["visible"] is True
    assert progress["retryable"] is True
    assert progress["current_step_id"] == "document1"
    assert [step["label"] for step in progress["steps"]] == [
        "进行宏观投研",
        "拆解叙事预期",
        "生成执行策略",
        "配置消息监测",
        "启动持久化监测",
    ]


def test_repeated_start_is_idempotent_for_running_ticker() -> None:
    scheduler, provider, _monitoring_service, _runtime_service = _scheduler(_usable_bundle())

    scheduler.start_ticker("NVDA", now=datetime(2026, 6, 30, 12, 15, tzinfo=UTC))
    first_initialize_calls = provider.initialize_calls
    detail = scheduler.start_ticker("NVDA", now=datetime(2026, 6, 30, 12, 20, tzinfo=UTC))

    assert provider.initialize_calls == first_initialize_calls
    assert detail.state.status is TickerRunStatus.RUNNING
    assert "ticker_start_idempotent" in [event.event_type for event in detail.audit_events]


def test_message_monitoring_tick_leaves_pending_events_without_trade_intent() -> None:
    scheduler, _provider, monitoring_service, runtime_service = _scheduler(_usable_bundle())
    scheduler.start_ticker("NVDA", now=datetime(2026, 6, 30, 12, 15, tzinfo=UTC))
    _disable_due_polling(monitoring_service)
    event = _append_standard_event(monitoring_service)

    detail = scheduler.tick_ticker(
        "NVDA",
        now=datetime(2026, 6, 30, 12, 30, tzinfo=UTC),
    )

    pending = monitoring_service.recent_events(ticker="NVDA")[0]
    assert pending.event_id == event.event_id
    assert pending.consumed is False
    assert detail.event_processing_status.pending_event_count == 1
    assert detail.event_processing_status.runtime_execution_count == 0
    assert detail.trade_intents == []
    assert runtime_service.repository.list_trading_records(ticker="NVDA") == []
    assert detail.state.counters.events_consumed == 0
    assert detail.state.counters.trade_intents_generated == 0


def test_paper_trading_tick_consumes_pending_events_and_records_trade_intent() -> None:
    scheduler, _provider, monitoring_service, runtime_service = _scheduler(_usable_bundle())
    scheduler.start_ticker(
        "NVDA",
        now=datetime(2026, 6, 30, 12, 15, tzinfo=UTC),
        monitor_mode=MonitorMode.PAPER_TRADING,
    )
    _disable_due_polling(monitoring_service)
    event = _append_standard_event(monitoring_service)

    detail = scheduler.tick_ticker(
        "NVDA",
        now=datetime(2026, 6, 30, 12, 30, tzinfo=UTC),
    )

    consumed = monitoring_service.recent_events(ticker="NVDA")[0]
    assert consumed.event_id == event.event_id
    assert consumed.consumed is True
    assert detail.event_processing_status.pending_event_count == 0
    assert detail.event_processing_status.runtime_execution_count == 1
    assert detail.trade_intents
    assert detail.trade_intents[0].side == "long"
    assert runtime_service.repository.list_trading_records(ticker="NVDA")
    assert detail.state.counters.events_consumed == 1
    assert detail.state.counters.processed_event_count == 1
    assert detail.state.counters.pending_event_count == 0
    assert detail.state.counters.trade_intents_generated == 1
    assert detail.state.counters.llm_call_count is None
    assert detail.state.counters.llm_call_count_status == "not_yet_integrated"


def test_switch_to_paper_trading_does_not_replay_existing_pending_events() -> None:
    scheduler, _provider, monitoring_service, runtime_service = _scheduler(_usable_bundle())
    scheduler.start_ticker("NVDA", now=datetime(2026, 6, 30, 12, 15, tzinfo=UTC))
    _disable_due_polling(monitoring_service)
    event = _append_standard_event(monitoring_service)

    switched = scheduler.set_monitor_mode(
        "NVDA",
        MonitorMode.PAPER_TRADING,
        now=event.event_time + timedelta(seconds=1),
        reason="unit test switch",
    )
    detail = scheduler.tick_ticker(
        "NVDA",
        now=event.event_time + timedelta(seconds=2),
    )

    persisted_event = monitoring_service.recent_events(ticker="NVDA")[0]
    assert switched.state.monitor_mode is MonitorMode.PAPER_TRADING
    assert switched.state.metadata["paper_trading_replays_historical_pending_events"] is False
    assert persisted_event.consumed is False
    assert detail.runtime_status.pending_event_count == 1
    assert detail.state.counters.events_consumed == 0
    assert runtime_service.repository.list_trading_records(ticker="NVDA") == []
    assert "ticker_monitor_mode_changed" in [
        item.event_type for item in detail.audit_events
    ]


def test_dashboard_detail_uses_contract_status_field_names() -> None:
    scheduler, _provider, monitoring_service, _runtime_service = _scheduler(_usable_bundle())
    detail = scheduler.start_ticker("NVDA", now=datetime(2026, 6, 30, 12, 15, tzinfo=UTC))
    _disable_due_polling(monitoring_service)
    _append_standard_event(monitoring_service)

    detail = scheduler.tick_ticker(
        "NVDA",
        now=datetime(2026, 6, 30, 12, 30, tzinfo=UTC),
    )

    payload = detail.model_dump(mode="json")
    assert "message_bus_status" in payload
    assert "runtime_status" in payload
    assert "monitoring_status" not in payload
    assert "event_processing_status" not in payload
    assert detail.monitoring_status == detail.message_bus_status
    assert detail.event_processing_status == detail.runtime_status


def test_repeated_tick_does_not_reconsume_event_or_duplicate_trade_intent() -> None:
    scheduler, _provider, monitoring_service, runtime_service = _scheduler(_usable_bundle())
    scheduler.start_ticker(
        "NVDA",
        now=datetime(2026, 6, 30, 12, 15, tzinfo=UTC),
        monitor_mode=MonitorMode.PAPER_TRADING,
    )
    _disable_due_polling(monitoring_service)
    _append_standard_event(monitoring_service)
    scheduler.tick_ticker("NVDA", now=datetime(2026, 6, 30, 12, 30, tzinfo=UTC))

    detail = scheduler.tick_ticker("NVDA", now=datetime(2026, 6, 30, 12, 35, tzinfo=UTC))

    assert detail.runtime_status.pending_event_count == 0
    assert detail.state.counters.events_consumed == 1
    assert detail.state.counters.processed_event_count == 1
    assert detail.state.counters.trade_intents_generated == 1
    assert detail.state.counters.runtime_executions == 1
    assert len(runtime_service.repository.list_trading_records(ticker="NVDA")) == 1


def test_runtime_failure_keeps_pending_event_and_surfaces_degraded_state() -> None:
    scheduler, _provider, monitoring_service, runtime_service = _scheduler(_usable_bundle())
    scheduler.runtime_service = _FailingRuntimeService(runtime_service.repository)
    scheduler.start_ticker(
        "NVDA",
        now=datetime(2026, 6, 30, 12, 15, tzinfo=UTC),
        monitor_mode=MonitorMode.PAPER_TRADING,
    )
    _disable_due_polling(monitoring_service)
    event = _append_standard_event(monitoring_service)

    detail = scheduler.tick_ticker("NVDA", now=datetime(2026, 6, 30, 12, 30, tzinfo=UTC))

    persisted_event = monitoring_service.recent_events(ticker="NVDA")[0]
    assert persisted_event.event_id == event.event_id
    assert persisted_event.consumed is False
    assert monitoring_service.recent_messages(ticker="NVDA")[0].standard_message_id == (
        event.standard_message_id
    )
    assert detail.state.status is TickerRunStatus.DEGRADED
    assert detail.state.health is RuntimeHealth.DEGRADED
    assert detail.runtime_status.pending_event_count == 1
    assert detail.state.counters.failed_event_count == 1
    assert detail.state.counters.execution_failure_count == 1
    assert "runtime_event_consumption_failed" in [
        item.event_type for item in detail.audit_events
    ]


def test_stop_ticker_marks_state_and_disables_monitoring_bindings() -> None:
    scheduler, _provider, monitoring_service, _runtime_service = _scheduler(_usable_bundle())
    scheduler.start_ticker("NVDA", now=datetime(2026, 6, 30, 12, 15, tzinfo=UTC))

    detail = scheduler.stop_ticker("NVDA", reason="operator requested stop")

    assert detail.state.status is TickerRunStatus.STOPPED
    binding = monitoring_service.repository.get_binding("NVDA", "benzinga_news")
    assert binding is not None
    assert binding.enabled is False
    assert "ticker_stopped" in [event.event_type for event in detail.audit_events]


def test_refresh_request_is_recorded_without_auto_refreshing_documents() -> None:
    scheduler, provider, _monitoring_service, _runtime_service = _scheduler(_usable_bundle())

    request = scheduler.submit_refresh_request(
        "NVDA",
        requested_by=RefreshRequestSource.AGENT,
        reason="Major supplier event may invalidate Document 1/2 assumptions.",
        trigger_event_id="evt_supplier_1",
    )

    assert request.ticker == "NVDA"
    assert provider.initialize_calls == 0
    saved = scheduler.repository.list_refresh_requests(ticker="NVDA")
    assert saved[0].request_id == request.request_id


def test_scheduler_sqlite_repository_restores_state_audit_and_refresh_request(tmp_path) -> None:
    path = tmp_path / "scheduler.sqlite3"
    repository = SQLiteRuntimeSchedulerRepository(path)
    state = repository.upsert_state(
        _state_for_sqlite(
            "NVDA",
            now=datetime(2026, 6, 30, 12, 0, tzinfo=UTC),
        )
    )
    request = repository.save_refresh_request(scheduler_request := _refresh_request("NVDA"))
    repository.append_audit_event(scheduler_audit := _audit_event("NVDA", "ticker_started"))

    restored = SQLiteRuntimeSchedulerRepository(path)

    assert restored.get_state("NVDA") == state
    assert restored.list_states()[0].ticker == "NVDA"
    assert restored.list_refresh_requests(ticker="NVDA")[0] == request == scheduler_request
    assert restored.list_audit_events(ticker="NVDA")[0] == scheduler_audit


def test_sqlite_restart_restores_running_state_and_consumes_pending_event(tmp_path) -> None:
    scheduler_path = tmp_path / "scheduler.sqlite3"
    monitoring_path = tmp_path / "monitoring.sqlite3"
    runtime_path = tmp_path / "persistent_runtime.sqlite3"
    provider = FakeDocumentProvider(_usable_bundle())
    monitoring_service = MonitoringBusService(SQLiteMonitoringRepository(monitoring_path))
    runtime_service = _sqlite_runtime_service(runtime_path)
    scheduler = UnifiedRuntimeSchedulerService(
        SQLiteRuntimeSchedulerRepository(scheduler_path),
        document_provider=provider,
        monitoring_service=monitoring_service,
        runtime_service=runtime_service,
    )
    scheduler.start_ticker(
        "NVDA",
        now=datetime(2026, 6, 30, 12, 15, tzinfo=UTC),
        monitor_mode=MonitorMode.PAPER_TRADING,
    )
    _disable_due_polling(monitoring_service)
    event = _append_standard_event(monitoring_service)

    restarted = UnifiedRuntimeSchedulerService(
        SQLiteRuntimeSchedulerRepository(scheduler_path),
        document_provider=provider,
        monitoring_service=MonitoringBusService(SQLiteMonitoringRepository(monitoring_path)),
        runtime_service=_sqlite_runtime_service(runtime_path),
    )
    detail = restarted.tick_ticker(
        "NVDA",
        now=datetime(2026, 6, 30, 12, 30, tzinfo=UTC),
    )

    assert detail.state.status is TickerRunStatus.RUNNING
    assert detail.runtime_status.pending_event_count == 0
    assert detail.state.counters.events_consumed == 1
    restored_event = restarted.monitoring_service.recent_events(ticker="NVDA")[0]
    assert restored_event.event_id == event.event_id
    assert restored_event.consumed is True
    assert len(restarted.runtime_service.repository.list_trading_records(ticker="NVDA")) == 1


def test_runtime_scheduler_loop_ticks_until_max_iterations() -> None:
    now = datetime(2026, 6, 30, 12, 30, tzinfo=UTC)
    scheduler = _LoopScheduler()
    loop = RuntimeSchedulerLoop(scheduler, sleep_seconds=0, event_limit=7)

    summary = loop.run(max_iterations=2, now_fn=lambda: now)

    assert summary.iteration_count == 2
    assert summary.failure_count == 0
    assert scheduler.calls == [(now, 7), (now, 7)]


def test_workflow_document_provider_reuses_recent_blackboard_document_set() -> None:
    now = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
    blackboard = BlackboardService(InMemoryBlackboardRepository())
    run = blackboard.start_run("NVDA", created_by=_system_agent())
    run.belief_state.documents = _blackboard_documents(now)
    blackboard.repository.save(run)
    provider = WorkflowDocumentProvider(
        workflow=_NoopWorkflow(blackboard),
        blackboard=blackboard,
    )

    bundle = provider.latest("NVDA", now=now)

    assert bundle.status.usable is True
    assert bundle.status.blackboard_run_id == run.run_id
    assert bundle.monitoring_config is not None
    assert bundle.monitoring_config.document_id == "doc_monitoring_config_nvda"
    assert bundle.monitoring_policy is not None
    assert bundle.known_events is not None


def _scheduler(
    bundle: DocumentBundle,
    *,
    initialized_bundle: DocumentBundle | None = None,
) -> tuple[
    UnifiedRuntimeSchedulerService,
    FakeDocumentProvider,
    MonitoringBusService,
    PersistentRuntimeExecutionService,
]:
    provider = FakeDocumentProvider(bundle, initialized_bundle)
    monitoring_service = MonitoringBusService(InMemoryMonitoringRepository())
    runtime_service = PersistentRuntimeExecutionService.from_settings()
    runtime_service.repository = InMemoryPersistentRuntimeRepository()
    scheduler = UnifiedRuntimeSchedulerService(
        InMemoryRuntimeSchedulerRepository(),
        document_provider=provider,
        monitoring_service=monitoring_service,
        runtime_service=runtime_service,
    )
    return scheduler, provider, monitoring_service, runtime_service


def _state_for_sqlite(ticker: str, *, now: datetime):
    return TickerRunState(
        ticker=ticker,
        status=TickerRunStatus.RUNNING,
        health=RuntimeHealth.NORMAL,
        session_phase=MarketSessionPhase.FORMAL_MONITORING,
        started_at=now,
        updated_at=now,
        document_run_id="run_nvda_fixture",
        document_status=_usable_bundle().status,
        last_monitoring_config_version="doc_monitoring_config_nvda:1:fixture",
    )


def _refresh_request(ticker: str) -> DocumentRefreshRequest:
    return DocumentRefreshRequest(
        ticker=ticker,
        requested_by=RefreshRequestSource.USER,
        reason="fixture refresh request",
    )


def _audit_event(ticker: str, event_type: str) -> RuntimeAuditEvent:
    return RuntimeAuditEvent(
        ticker=ticker,
        event_type=event_type,
        message="fixture audit event",
    )


def _system_agent() -> AgentName:
    return AgentName.SYSTEM


class _NoopWorkflow:
    def __init__(self, blackboard: BlackboardService) -> None:
        self.blackboard = blackboard

    def run(self, ticker: str):
        raise AssertionError(f"unexpected initialization for {ticker}")


class _FailingRuntimeService(PersistentRuntimeExecutionService):
    def execute_events(
        self,
        events: list[EventStreamItem],
        *,
        context: dict[str, object] | None = None,
        mark_consumed: Callable[[str], object] | None = None,
    ) -> list[RuntimeExecutionRecord]:
        raise RuntimeError("fixture runtime failure")


class _LoopScheduler:
    def __init__(self) -> None:
        self.calls: list[tuple[datetime | None, int]] = []

    def run_due_once(
        self,
        *,
        now: datetime | None = None,
        event_limit: int = 100,
    ) -> list[TickerRunDetail]:
        self.calls.append((now, event_limit))
        return []


def _blackboard_documents(now: datetime) -> dict[DocumentType, dict[str, object]]:
    global_research = global_research_document().model_copy(
        update={"ticker": "NVDA", "created_at": now},
        deep=True,
    )
    expectation = ExpectationUnitDocument.model_validate(expectation_document()).model_copy(
        update={"ticker": "NVDA", "created_at": now},
        deep=True,
    )
    known = known_events_document().model_copy(
        update={"ticker": "NVDA", "created_at": now},
        deep=True,
    )
    config = _monitoring_config()
    policy = monitoring_policy_document().model_copy(
        update={"ticker": "NVDA", "created_at": now},
        deep=True,
    )
    return {
        DocumentType.GLOBAL_RESEARCH: {
            global_research.document_id: global_research.model_dump(mode="json"),
        },
        DocumentType.EXPECTATION_UNIT: {
            expectation.document_id: expectation.model_dump(mode="json"),
        },
        DocumentType.KNOWN_EVENTS: {
            known.document_id: {"document": known.model_dump(mode="json")},
        },
        DocumentType.MONITORING_CONFIG: {
            config.document_id: {"document": config.model_dump(mode="json")},
        },
        DocumentType.MONITORING_POLICY: {
            policy.document_id: {"document": policy.model_dump(mode="json")},
        },
    }


def _missing_bundle() -> DocumentBundle:
    now = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
    return DocumentBundle(
        status=DocumentSetStatus(
            ticker="NVDA",
            checked_at=now,
            usable=False,
            missing_document_types=[DocumentType.MONITORING_CONFIG],
            components=[
                DocumentComponentStatus(
                    document_type=DocumentType.MONITORING_CONFIG,
                    availability=DocumentAvailability.MISSING,
                    reason="fixture missing config",
                )
            ],
        )
    )


def _usable_bundle() -> DocumentBundle:
    now = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
    config = _monitoring_config()
    known = known_events_document().model_copy(
        update={"ticker": "NVDA"},
        deep=True,
    )
    known.events[0] = known.events[0].model_copy(
        update={"duplicate_detection_keys": ["prior earnings release"]},
        deep=True,
    )
    return DocumentBundle(
        status=DocumentSetStatus(
            ticker="NVDA",
            blackboard_run_id="run_nvda_fixture",
            checked_at=now,
            usable=True,
            components=[
                DocumentComponentStatus(
                    document_type=document_type,
                    availability=DocumentAvailability.AVAILABLE,
                    document_ids=[f"doc_{document_type.value}"],
                    document_count=1,
                    newest_updated_at=now - timedelta(hours=1),
                    stale_after=now + timedelta(days=3),
                )
                for document_type in [
                    DocumentType.GLOBAL_RESEARCH,
                    DocumentType.EXPECTATION_UNIT,
                    DocumentType.KNOWN_EVENTS,
                    DocumentType.MONITORING_CONFIG,
                    DocumentType.MONITORING_POLICY,
                ]
            ],
            applied_config_version="doc_monitoring_config_nvda:1:fixture",
        ),
        known_events=known,
        monitoring_config=config,
        monitoring_policy=monitoring_policy_document().model_copy(
            update={"ticker": "NVDA"},
            deep=True,
        ),
    )


def _monitoring_config() -> MonitoringConfigDocument:
    now = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
    return MonitoringConfigDocument(
        document_id="doc_monitoring_config_nvda",
        ticker="NVDA",
        created_at=now,
        applied_config_version="doc_monitoring_config_nvda:1:fixture",
        monitoring_items=[
            MonitoringItem(
                item_id="monitor_benzinga_nvda",
                tool_input={
                    "ticker": "NVDA",
                    "source_id": "benzinga_news",
                    "search_terms": ["confirmed order"],
                    "reason": "Track confirmed order signals.",
                    "mode": "merge",
                    "enabled": True,
                },
                reasoning="Track confirmed order signals.",
                base_keywords=["NVDA"],
                priority="high",
                trigger_condition="confirmed order materially above expectation",
            )
        ],
    )


def _append_standard_event(monitoring_service: MonitoringBusService):
    now = datetime.now(UTC) + timedelta(seconds=1)
    message = StandardMessage(
        standard_message_id="std_nvda_order",
        raw_message_id="raw_nvda_order",
        source_id="benzinga_news",
        binding_id="NVDA:benzinga_news",
        ticker="NVDA",
        source_type=SourceType.MEDIA,
        interface_type=InterfaceType.BY_TICKER,
        title="NVDA confirmed order materially above expectation",
        body="NVDA confirmed order materially above expectation from a hyperscaler customer.",
        symbols=["NVDA"],
        published_at=now,
        collected_at=now,
    )
    standard = monitoring_service.repository.save_standard_message(message)
    return monitoring_service.repository.append_event(standard)


def _disable_due_polling(monitoring_service: MonitoringBusService) -> None:
    for binding in monitoring_service.repository.list_bindings(ticker="NVDA"):
        monitoring_service.configure_ticker_source(
            binding.ticker,
            binding.source_id,
            parameters=binding.parameters,
            enabled=False,
            updated_by=UpdateActor.SYSTEM,
            updated_reason="unit test injects pending event without external polling",
            merge=False,
        )


def _sqlite_runtime_service(path) -> PersistentRuntimeExecutionService:
    service = PersistentRuntimeExecutionService.from_settings()
    service.repository = SQLitePersistentRuntimeRepository(path)
    return service
