from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from doxagent.dashboard_api import create_app
from doxagent.dashboard_api.backtest import (
    DashboardBacktestService,
    InMemoryDashboardBacktestRepository,
)
from doxagent.dashboard_api.real_service import RealDashboardOverviewService
from doxagent.models import DocumentType, MonitoringConfigDocument, MonitoringItem
from doxagent.monitoring.repository import InMemoryMonitoringRepository
from doxagent.monitoring.schema import EventStreamItem, InterfaceType, SourceType
from doxagent.monitoring.service import MonitoringBusService
from doxagent.persistent_runtime.datasets import DatasetBuildResult, build_dataset
from doxagent.persistent_runtime.repository import InMemoryPersistentRuntimeRepository
from doxagent.persistent_runtime.schema import (
    KnownEventsPatch,
    KnownEventsPatchLog,
    RouteDecision,
    RuntimeExecutionRecord,
    RuntimeRoute,
    RuntimeSourceMessage,
)
from doxagent.persistent_runtime.service import PersistentRuntimeExecutionService
from doxagent.runtime_scheduler import (
    DashboardStateAPI,
    DocumentAvailability,
    DocumentBundle,
    DocumentComponentStatus,
    DocumentSetStatus,
    InMemoryRuntimeSchedulerRepository,
    UnifiedRuntimeSchedulerService,
)
from tests.fixtures.phase1_contracts import known_events_document, monitoring_policy_document


def test_dashboard_backtest_runs_serial_replay_without_monitoring_config(
    tmp_path: Path,
) -> None:
    scheduler, monitoring_service, live_runtime = _scheduler()
    spy_runtime = _SpyRuntimeService()
    service = _backtest_service(scheduler, tmp_path, runtime=spy_runtime)
    client = TestClient(
        create_app(
            mode="real",
            auth_mode="mock-open",
            real_service=RealDashboardOverviewService(
                DashboardStateAPI(scheduler),
                backtest_service=service,
            ),
        )
    )

    response = client.post(
        "/api/dashboard/v1/backtests",
        json={"ticker": "MU", "period": "7d", "force_initialize": False},
    )

    assert response.status_code == 200
    run = response.json()["data"]
    assert run["ticker"] == "MU"
    assert run["period"] == "7d"
    assert run["status"] == "completed"
    assert run["progress"]["processed_events"] == 2
    assert run["progress"]["percent"] == 100
    assert spy_runtime.seen_titles == ["older event patch", "newer event"]
    assert spy_runtime.concurrent_violation is False
    assert monitoring_service.repository.list_bindings(ticker="MU") == []
    assert live_runtime.repository.list_known_events_patch_logs(ticker="MU") == []
    assert spy_runtime.repository.list_known_events_patch_logs(ticker="MU")

    listed = client.get("/api/dashboard/v1/backtests?ticker=MU&limit=5")
    assert listed.status_code == 200
    assert listed.json()["data"]["items"][0]["run_id"] == run["run_id"]


def test_dashboard_backtest_allows_multiple_same_ticker_runs(tmp_path: Path) -> None:
    scheduler, _monitoring_service, _live_runtime = _scheduler()
    service = _backtest_service(scheduler, tmp_path)
    client = TestClient(
        create_app(
            mode="real",
            auth_mode="mock-open",
            real_service=RealDashboardOverviewService(
                DashboardStateAPI(scheduler),
                backtest_service=service,
            ),
        )
    )

    seven_day = client.post("/api/dashboard/v1/backtests", json={"ticker": "MU", "period": "7d"})
    thirty_day = client.post(
        "/api/dashboard/v1/backtests",
        json={"ticker": "MU", "period": "30d"},
    )

    assert seven_day.status_code == 200
    assert thirty_day.status_code == 200
    first = seven_day.json()["data"]
    second = thirty_day.json()["data"]
    assert first["run_id"] != second["run_id"]
    assert {first["period"], second["period"]} == {"7d", "30d"}

    listed = client.get("/api/dashboard/v1/backtests?ticker=MU")
    assert listed.status_code == 200
    runs = listed.json()["data"]["items"]
    assert len(runs) == 2
    assert {run["run_id"] for run in runs} == {first["run_id"], second["run_id"]}


def test_dashboard_backtest_errors_are_contract_shaped(tmp_path: Path) -> None:
    scheduler, _monitoring_service, _live_runtime = _scheduler()
    service = _backtest_service(scheduler, tmp_path)
    client = TestClient(
        create_app(
            mode="real",
            auth_mode="mock-open",
            real_service=RealDashboardOverviewService(
                DashboardStateAPI(scheduler),
                backtest_service=service,
            ),
        )
    )

    invalid = client.post("/api/dashboard/v1/backtests", json={"ticker": "MU", "period": "60d"})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_PARAMS"

    missing = client.get("/api/dashboard/v1/backtests/bt_missing")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"

    completed = client.post("/api/dashboard/v1/backtests", json={"ticker": "MU", "period": "15d"})
    run_id = completed.json()["data"]["run_id"]
    cancel = client.post(f"/api/dashboard/v1/backtests/{run_id}/cancel")
    assert cancel.status_code == 409
    assert cancel.json()["error"]["code"] == "CONFLICT"


class _DocumentProvider:
    def latest(self, ticker: str, *, now: datetime | None = None) -> DocumentBundle:
        return _usable_bundle(ticker)

    def initialize(self, ticker: str, *, now: datetime | None = None) -> DocumentBundle:
        return _usable_bundle(ticker)


class _SpyRuntimeService:
    def __init__(self) -> None:
        self.repository = InMemoryPersistentRuntimeRepository()
        self.seen_titles: list[str] = []
        self._active = False
        self.concurrent_violation = False

    def execute_event(
        self,
        event: EventStreamItem,
        *,
        context: dict[str, object] | None = None,
        mark_consumed: object | None = None,
    ) -> RuntimeExecutionRecord:
        if self._active:
            self.concurrent_violation = True
        self._active = True
        try:
            message = RuntimeSourceMessage.from_event(event)
            self.seen_titles.append(message.title or "")
            record = RuntimeExecutionRecord(
                source_message=message,
                route_decision=RouteDecision(
                    source_message_id=message.source_message_id,
                    ticker=message.ticker,
                    route=RuntimeRoute.ARCHIVE,
                    reason="unit test backtest route",
                ),
                message_statuses=["received", "workers_completed", "archived"],
                created_at=event.event_time,
            )
            self.repository.save_execution(record)
            if "patch" in (message.title or ""):
                self.repository.save_known_events_patch_log(
                    KnownEventsPatchLog(
                        source_message_id=message.source_message_id,
                        ticker=message.ticker,
                        known_event_id="known_event_from_backtest",
                        source_ref=message.source_message_id,
                        change_reason="unit test backtest patch",
                        patch=KnownEventsPatch(
                            event_id="known_event_from_backtest",
                            event_time_or_window="2026-06-01",
                            core_fact="Backtest patch should stay in backtest runtime repo.",
                            duplicate_detection_keys=["backtest-patch"],
                        ),
                    )
                )
            return record
        finally:
            self._active = False


def _backtest_service(
    scheduler: UnifiedRuntimeSchedulerService,
    tmp_path: Path,
    *,
    runtime: _SpyRuntimeService | None = None,
) -> DashboardBacktestService:
    resolved_runtime = runtime or _SpyRuntimeService()
    return DashboardBacktestService(
        scheduler,
        repository=InMemoryDashboardBacktestRepository(),
        dataset_fetcher=_dataset_fetcher,
        runtime_service_factory=lambda _path: resolved_runtime,
        backtest_dir=tmp_path,
        run_async=False,
        sleep=lambda _seconds: None,
    )


def _dataset_fetcher(
    *,
    ticker: str,
    days: int,
    source_ids: object = None,
    search_terms: object = (),
    usernames: object = (),
    rss_urls: object = (),
    limit_per_source: int | None = None,
    settings: object = None,
    now: datetime | None = None,
) -> DatasetBuildResult:
    newer = _event(ticker, "newer event", datetime(2026, 6, 2, 12, tzinfo=UTC), offset=2)
    older = _event(ticker, "older event patch", datetime(2026, 6, 1, 12, tzinfo=UTC), offset=1)
    dataset = build_dataset(
        [newer, older],
        ticker=ticker,
        source_types=[SourceType.MEDIA],
        window_start=None,
        window_end=None,
        source={"type": "unit_test", "days": days},
    )
    return DatasetBuildResult(dataset=dataset, diagnostics=["unit test dataset"])


def _event(ticker: str, title: str, published_at: datetime, *, offset: int) -> EventStreamItem:
    source_id = "benzinga_news"
    standard_id = f"std_{ticker.lower()}_{offset}"
    return EventStreamItem(
        event_id=f"evt_{ticker.lower()}_{offset}",
        stream_offset=offset,
        standard_message_id=standard_id,
        event_time=published_at,
        ticker=ticker,
        source_id=source_id,
        payload={
            "standard_message_id": standard_id,
            "raw_message_id": f"raw_{ticker.lower()}_{offset}",
            "ticker": ticker,
            "source_id": source_id,
            "source_type": SourceType.MEDIA.value,
            "interface_type": InterfaceType.BY_TICKER.value,
            "title": title,
            "body": title,
            "symbols": [ticker],
            "published_at": published_at.isoformat(),
            "collected_at": published_at.isoformat(),
            "metadata": {"test": True},
        },
    )


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
        document_provider=_DocumentProvider(),
        monitoring_service=monitoring_service,
        runtime_service=runtime_service,
    )
    return scheduler, monitoring_service, runtime_service


def _usable_bundle(ticker: str) -> DocumentBundle:
    normalized = ticker.strip().upper()
    now = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
    return DocumentBundle(
        status=DocumentSetStatus(
            ticker=normalized,
            blackboard_run_id=f"run_{normalized.lower()}_fixture",
            checked_at=now,
            usable=True,
            components=[
                DocumentComponentStatus(
                    document_type=document_type,
                    availability=DocumentAvailability.AVAILABLE,
                    document_ids=[f"doc_{document_type.value}_{normalized.lower()}"],
                    document_count=1,
                    newest_updated_at=now,
                )
                for document_type in [
                    DocumentType.GLOBAL_RESEARCH,
                    DocumentType.EXPECTATION_UNIT,
                    DocumentType.KNOWN_EVENTS,
                    DocumentType.MONITORING_CONFIG,
                    DocumentType.MONITORING_POLICY,
                ]
            ],
            applied_config_version=f"doc_monitoring_config_{normalized.lower()}:1:fixture",
        ),
        known_events=known_events_document().model_copy(update={"ticker": normalized}, deep=True),
        monitoring_config=_monitoring_config(normalized),
        monitoring_policy=monitoring_policy_document().model_copy(
            update={"ticker": normalized},
            deep=True,
        ),
    )


def _monitoring_config(ticker: str) -> MonitoringConfigDocument:
    now = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
    return MonitoringConfigDocument(
        document_id=f"doc_monitoring_config_{ticker.lower()}",
        ticker=ticker,
        created_at=now,
        applied_config_version=f"doc_monitoring_config_{ticker.lower()}:1:fixture",
        monitoring_items=[
            MonitoringItem(
                item_id=f"monitor_benzinga_{ticker.lower()}",
                tool_input={
                    "ticker": ticker,
                    "source_id": "benzinga_news",
                    "search_terms": [ticker],
                    "reason": "Track historical backtest signals.",
                    "mode": "merge",
                    "enabled": True,
                },
                reasoning="Track historical backtest signals.",
                base_keywords=[ticker],
                priority="high",
                trigger_condition="material update",
            )
        ],
    )
