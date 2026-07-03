from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from doxagent.dashboard_api import create_app
from doxagent.models import DocumentType, MonitoringConfigDocument, MonitoringItem
from doxagent.monitoring.repository import InMemoryMonitoringRepository
from doxagent.monitoring.schema import InterfaceType, SourceType, StandardMessage
from doxagent.monitoring.service import MonitoringBusService
from doxagent.persistent_runtime import InMemoryPersistentRuntimeRepository
from doxagent.persistent_runtime.schema import (
    Conviction,
    RouteDecision,
    RuntimeExecutionRecord,
    RuntimeRoute,
    RuntimeSourceMessage,
    SizeBucket,
    TradeIntent,
    TradeRecordStatus,
    TradeSide,
    TradingRecord,
)
from doxagent.persistent_runtime.service import PersistentRuntimeExecutionService
from doxagent.runtime_scheduler import (
    DashboardStateAPI,
    DocumentAvailability,
    DocumentBundle,
    DocumentComponentStatus,
    DocumentSetStatus,
    InMemoryRuntimeSchedulerRepository,
    MarketSessionPhase,
    RuntimeHealth,
    TickerRunState,
    TickerRunStatus,
    UnifiedRuntimeSchedulerService,
)
from tests.fixtures.phase1_contracts import known_events_document, monitoring_policy_document


def test_dashboard_real_overview_reads_scheduler_monitoring_and_runtime_state() -> None:
    scheduler, monitoring_service, runtime_service = _scheduler()
    started = datetime(2026, 6, 30, 12, 15, tzinfo=UTC)
    scheduler.start_ticker("NVDA", now=started)
    message_time = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=1)
    query_date = message_time.date().isoformat()
    expected_message_time = message_time.isoformat().replace("+00:00", "Z")
    _append_real_message(monitoring_service, message_time)
    _append_real_runtime(runtime_service, message_time)
    client = TestClient(
        create_app(
            mode="real",
            auth_mode="mock-open",
            dashboard_api=DashboardStateAPI(scheduler),
        )
    )

    overview = client.get(f"/api/dashboard/v1/overview?date={query_date}&tz=UTC")

    assert overview.status_code == 200
    payload = overview.json()["data"]
    assert payload["system"]["dashboard_api_status"] == "normal"
    assert payload["system"]["message_bus_status"] == "normal"
    assert payload["kpis"]["running_ticker_count"] == 1
    assert payload["kpis"]["today_message_count"] == 1
    assert payload["kpis"]["today_dtc_count"] == 1
    assert payload["kpis"]["today_token_cost_usd"] is None
    assert payload["tickers"][0]["ticker"] == "NVDA"
    assert payload["tickers"][0]["last_message_at"] == expected_message_time
    assert payload["tickers"][0]["last_worker_processed_at"] == expected_message_time
    assert payload["tickers"][0]["today_cost_usd"] is None

    tickers = client.get(
        f"/api/dashboard/v1/tickers?status=running&limit=1&date={query_date}&tz=UTC"
    )
    assert tickers.status_code == 200
    ticker_page = tickers.json()["data"]
    assert ticker_page["items"][0]["ticker"] == "NVDA"
    assert ticker_page["page"] == {"limit": 1, "next_cursor": None, "has_more": False}


def test_dashboard_real_overview_ticker_operations_are_contract_shaped() -> None:
    scheduler, _monitoring_service, _runtime_service = _scheduler()
    scheduler.start_ticker("NVDA", now=datetime(2026, 6, 30, 12, 15, tzinfo=UTC))
    client = TestClient(
        create_app(
            mode="real",
            auth_mode="mock-open",
            dashboard_api=DashboardStateAPI(scheduler),
        )
    )

    duplicate = client.post("/api/dashboard/v1/tickers", json={"ticker": "NVDA"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "TICKER_ALREADY_RUNNING"

    paper = client.post(
        "/api/dashboard/v1/tickers",
        json={"ticker": "AMD", "monitor_mode": "paper_trading"},
    )
    assert paper.status_code == 200
    assert paper.json()["data"]["ticker_state"]["monitor_mode"] == "paper_trading"

    mode_switch = client.patch(
        "/api/dashboard/v1/tickers/AMD/monitor-mode",
        json={"monitor_mode": "message_monitoring", "reason": "unit test switch"},
    )
    assert mode_switch.status_code == 200
    assert mode_switch.json()["data"]["operation"] == "monitor_mode"
    assert mode_switch.json()["data"]["ticker_state"]["monitor_mode"] == "message_monitoring"

    unsupported_mode = client.post(
        "/api/dashboard/v1/tickers",
        json={"ticker": "TSLA", "monitor_mode": "broker_trading"},
    )
    assert unsupported_mode.status_code == 422
    assert unsupported_mode.json()["error"]["code"] == "INVALID_PARAMS"

    paused = client.post("/api/dashboard/v1/tickers/NVDA/pause", json={"reason": "unit test"})
    assert paused.status_code == 200
    assert paused.json()["data"]["ticker_state"]["status"] == "paused"

    deleted = client.request(
        "DELETE",
        "/api/dashboard/v1/tickers/NVDA?delete_history=false",
        json={"reason": "unit test cleanup"},
    )
    assert deleted.status_code == 200
    assert deleted.json()["data"]["operation"] == "delete"
    assert deleted.json()["data"]["history_deleted"] is False
    assert deleted.json()["data"]["deleted_binding_count"] >= 1

    delete_history = client.request(
        "DELETE",
        "/api/dashboard/v1/tickers/NVDA?delete_history=true",
        json={"reason": "unsupported"},
    )
    assert delete_history.status_code == 422
    assert delete_history.json()["error"]["code"] == "INVALID_PARAMS"


def test_dashboard_real_overview_exposes_real_startup_progress_from_scheduler_state() -> None:
    scheduler, _monitoring_service, _runtime_service = _scheduler()
    now = datetime(2026, 6, 30, 12, 15, tzinfo=UTC)
    scheduler.repository.upsert_state(
        TickerRunState(
            ticker="AMD",
            status=TickerRunStatus.INITIALIZING,
            health=RuntimeHealth.NORMAL,
            session_phase=MarketSessionPhase.FORMAL_MONITORING,
            started_at=now,
            updated_at=now,
            metadata={
                "startup_progress": {
                    "status": "running",
                    "status_label": "启动中",
                    "visible": True,
                    "current_step_id": "document1",
                    "retryable": False,
                    "message": None,
                    "updated_at": "2026-06-30T12:15:00Z",
                    "steps": [
                        {
                            "step_id": "document1",
                            "label": "进行宏观投研",
                            "status": "running",
                            "progress": 50,
                        }
                    ],
                }
            },
        )
    )
    client = TestClient(
        create_app(
            mode="real",
            auth_mode="mock-open",
            dashboard_api=DashboardStateAPI(scheduler),
        )
    )

    response = client.get("/api/dashboard/v1/tickers?status=initializing")

    assert response.status_code == 200
    progress = response.json()["data"]["items"][0]["startup_progress"]
    assert progress["status"] == "running"
    assert progress["status_label"] == "启动中"
    assert progress["steps"][0]["label"] == "进行宏观投研"


class _DocumentProvider:
    def __init__(self, bundle: DocumentBundle) -> None:
        self.bundle = bundle

    def latest(self, ticker: str, *, now: datetime | None = None) -> DocumentBundle:
        return self.bundle

    def initialize(self, ticker: str, *, now: datetime | None = None) -> DocumentBundle:
        return self.bundle


def _scheduler() -> tuple[
    UnifiedRuntimeSchedulerService,
    MonitoringBusService,
    PersistentRuntimeExecutionService,
]:
    monitoring_service = MonitoringBusService(InMemoryMonitoringRepository())
    runtime_service = PersistentRuntimeExecutionService.from_settings()
    runtime_service.repository = InMemoryPersistentRuntimeRepository()
    scheduler = UnifiedRuntimeSchedulerService(
        InMemoryRuntimeSchedulerRepository(),
        document_provider=_DocumentProvider(_usable_bundle()),
        monitoring_service=monitoring_service,
        runtime_service=runtime_service,
    )
    return scheduler, monitoring_service, runtime_service


def _usable_bundle() -> DocumentBundle:
    now = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
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
        known_events=known_events_document().model_copy(update={"ticker": "NVDA"}, deep=True),
        monitoring_config=_monitoring_config(),
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


def _append_real_message(
    monitoring_service: MonitoringBusService,
    timestamp: datetime,
) -> None:
    message = StandardMessage(
        standard_message_id="std_nvda_dashboard_real",
        raw_message_id="raw_nvda_dashboard_real",
        source_id="benzinga_news",
        binding_id="NVDA:benzinga_news",
        ticker="NVDA",
        source_type=SourceType.MEDIA,
        interface_type=InterfaceType.BY_TICKER,
        title="NVDA confirmed order materially above expectation",
        body="NVDA confirmed order materially above expectation from a hyperscaler customer.",
        symbols=["NVDA"],
        published_at=timestamp,
        collected_at=timestamp,
        normalized_at=timestamp,
    )
    standard = monitoring_service.repository.save_standard_message(message)
    monitoring_service.repository.append_event(standard)


def _append_real_runtime(
    runtime_service: PersistentRuntimeExecutionService,
    timestamp: datetime,
) -> None:
    source_message = RuntimeSourceMessage(
        source_message_id="std_nvda_dashboard_real",
        raw_message_id="raw_nvda_dashboard_real",
        ticker="NVDA",
        source_type=SourceType.MEDIA,
        source_id="benzinga_news",
        title="NVDA confirmed order materially above expectation",
        body="NVDA confirmed order materially above expectation.",
        symbols=["NVDA"],
        collected_at=timestamp,
    )
    execution = RuntimeExecutionRecord(
        source_message=source_message,
        route_decision=RouteDecision(
            source_message_id=source_message.source_message_id,
            ticker="NVDA",
            route=RuntimeRoute.TRADING_RECORD,
            reason="fixture DTC route",
        ),
        message_statuses=["received", "workers_completed", "routed_to_trading_records"],
        created_at=timestamp,
    )
    trade = TradingRecord(
        source_message_id=source_message.source_message_id,
        ticker="NVDA",
        source_type=SourceType.MEDIA,
        route="new_dtc",
        matched_policy_code="POLICY_DTC_HBM_ORDER",
        trade_intent=TradeIntent(
            side=TradeSide.LONG,
            conviction=Conviction.MEDIUM,
            size_bucket=SizeBucket.NORMAL,
            reasoning="fixture trade intent",
        ),
        status=TradeRecordStatus.RECORDED_ONLY,
        created_at=timestamp,
    )
    runtime_service.repository.save_execution(execution)
    runtime_service.repository.save_trading_record(trade)
