"""Dashboard-scoped backtest runs for historical monitoring replay."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from doxagent.models.documents import MonitoringConfigDocument
from doxagent.monitoring.schema import EventStreamItem, JsonObject, canonical_json
from doxagent.persistent_runtime.datasets import (
    DatasetBuildResult,
    event_observed_at,
    fetch_live_dataset,
)
from doxagent.persistent_runtime.repository import PersistentRuntimeRepository
from doxagent.persistent_runtime.schema import RuntimeExecutionRecord
from doxagent.persistent_runtime.service import PersistentRuntimeExecutionService
from doxagent.runtime_scheduler.schema import AuditSeverity, DocumentBundle, RuntimeAuditEvent
from doxagent.runtime_scheduler.service import UnifiedRuntimeSchedulerService
from doxagent.settings import DoxAgentSettings

BACKTEST_PERIODS: dict[str, int] = {"7d": 7, "15d": 15, "30d": 30}
DEFAULT_BACKTEST_DIR = Path(".tmp/dashboard_backtests")
DEFAULT_REPLAY_INTERVAL_MS = 250
MAX_REPLAY_INTERVAL_MS = 10_000
ACTIVE_BACKTEST_STATUSES = {
    "queued",
    "initializing_documents",
    "collecting_dataset",
    "replaying",
    "draining_runtime",
}
TERMINAL_BACKTEST_STATUSES = {"completed", "failed", "cancelled"}


class BacktestRunStatus(StrEnum):
    QUEUED = "queued"
    INITIALIZING_DOCUMENTS = "initializing_documents"
    COLLECTING_DATASET = "collecting_dataset"
    REPLAYING = "replaying"
    DRAINING_RUNTIME = "draining_runtime"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BacktestHealth(StrEnum):
    NORMAL = "normal"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class BacktestProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_events: int = 0
    collected_events: int = 0
    injected_events: int = 0
    processed_events: int = 0
    failed_events: int = 0
    percent: float = 0.0


class BacktestDatasetInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str | None = None
    source_type_counts: dict[str, int] = Field(default_factory=dict)
    diagnostics: list[str] = Field(default_factory=list)
    source: JsonObject = Field(default_factory=dict)


class BacktestRuntimeInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_sqlite_path: str | None = None
    execution_count: int = 0
    trade_intent_count: int = 0
    known_event_patch_count: int = 0
    exception_count: int = 0


class DashboardBacktestRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(default_factory=lambda: f"bt_{uuid4().hex}")
    ticker: str
    period: str
    period_days: int
    status: BacktestRunStatus = BacktestRunStatus.QUEUED
    health: BacktestHealth = BacktestHealth.UNKNOWN
    force_initialize: bool = False
    replay_interval_ms: int = DEFAULT_REPLAY_INTERVAL_MS
    progress: BacktestProgress = Field(default_factory=BacktestProgress)
    dataset: BacktestDatasetInfo = Field(default_factory=BacktestDatasetInfo)
    runtime: BacktestRuntimeInfo = Field(default_factory=BacktestRuntimeInfo)
    current_event_id: str | None = None
    current_event_time: datetime | None = None
    last_error: str | None = None
    cancel_requested: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator("ticker")
    @classmethod
    def _ticker_upper(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("ticker is required.")
        return normalized


class DashboardBacktestRepository(Protocol):
    def save_run(self, run: DashboardBacktestRun) -> DashboardBacktestRun:
        ...

    def get_run(self, run_id: str) -> DashboardBacktestRun | None:
        ...

    def list_runs(self) -> list[DashboardBacktestRun]:
        ...


class InMemoryDashboardBacktestRepository:
    def __init__(self) -> None:
        self._runs: dict[str, DashboardBacktestRun] = {}
        self._lock = threading.RLock()

    def save_run(self, run: DashboardBacktestRun) -> DashboardBacktestRun:
        with self._lock:
            self._runs[run.run_id] = run.model_copy(deep=True)
            return run.model_copy(deep=True)

    def get_run(self, run_id: str) -> DashboardBacktestRun | None:
        with self._lock:
            run = self._runs.get(run_id)
            return run.model_copy(deep=True) if run is not None else None

    def list_runs(self) -> list[DashboardBacktestRun]:
        with self._lock:
            runs = sorted(self._runs.values(), key=lambda item: item.created_at, reverse=True)
            return [run.model_copy(deep=True) for run in runs]


class SQLiteDashboardBacktestRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if self.path.parent != Path("."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def save_run(self, run: DashboardBacktestRun) -> DashboardBacktestRun:
        payload = run.model_dump(mode="json")
        with self._connect() as conn:
            conn.execute(
                """
                insert into dashboard_backtest_runs
                    (run_id, ticker, status, created_at, updated_at, payload_json)
                values (?, ?, ?, ?, ?, ?)
                on conflict(run_id) do update set
                    ticker = excluded.ticker,
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    payload_json = excluded.payload_json
                """,
                (
                    run.run_id,
                    run.ticker,
                    run.status.value,
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                    canonical_json(payload),
                ),
            )
        resolved = self.get_run(run.run_id)
        if resolved is None:
            raise RuntimeError(f"backtest run was not persisted: {run.run_id}")
        return resolved

    def get_run(self, run_id: str) -> DashboardBacktestRun | None:
        with self._connect() as conn:
            row = conn.execute(
                "select payload_json from dashboard_backtest_runs where run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return DashboardBacktestRun.model_validate_json(str(row["payload_json"]))

    def list_runs(self) -> list[DashboardBacktestRun]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select payload_json
                from dashboard_backtest_runs
                order by created_at desc, rowid desc
                """
            ).fetchall()
        return [DashboardBacktestRun.model_validate_json(str(row["payload_json"])) for row in rows]

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists dashboard_backtest_runs (
                    run_id text primary key,
                    ticker text not null,
                    status text not null,
                    created_at text not null,
                    updated_at text not null,
                    payload_json text not null
                )
                """
            )
            conn.execute(
                """
                create index if not exists idx_dashboard_backtest_runs_ticker_created
                on dashboard_backtest_runs (ticker, created_at desc)
                """
            )
            conn.execute(
                """
                create index if not exists idx_dashboard_backtest_runs_status_created
                on dashboard_backtest_runs (status, created_at desc)
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


class DatasetFetcher(Protocol):
    def __call__(
        self,
        *,
        ticker: str,
        days: int,
        source_ids: Iterable[str] | None = None,
        search_terms: Iterable[str] = (),
        usernames: Iterable[str] = (),
        rss_urls: Iterable[str] = (),
        limit_per_source: int | None = None,
        settings: DoxAgentSettings | None = None,
        now: datetime | None = None,
    ) -> DatasetBuildResult:
        ...


class RuntimeServiceLike(Protocol):
    repository: PersistentRuntimeRepository

    def execute_event(
        self,
        event: EventStreamItem,
        *,
        context: JsonObject | None = None,
        mark_consumed: Callable[[str], object] | None = None,
    ) -> RuntimeExecutionRecord:
        ...


RuntimeServiceFactory = Callable[[Path], RuntimeServiceLike]


class UnsupportedBacktestPeriod(ValueError):
    def __init__(self, period: object) -> None:
        super().__init__(str(period))
        self.period = str(period)


class BacktestRunNotFound(LookupError):
    def __init__(self, run_id: str) -> None:
        super().__init__(run_id)
        self.run_id = run_id


class BacktestRunNotCancellable(ValueError):
    def __init__(self, run_id: str, status: str) -> None:
        super().__init__(f"{run_id}:{status}")
        self.run_id = run_id
        self.status = status


class DashboardBacktestService:
    """Run historical replay without mutating ticker-level scheduler state."""

    def __init__(
        self,
        scheduler: UnifiedRuntimeSchedulerService,
        *,
        settings: DoxAgentSettings | None = None,
        repository: DashboardBacktestRepository | None = None,
        dataset_fetcher: DatasetFetcher = fetch_live_dataset,
        runtime_service_factory: RuntimeServiceFactory | None = None,
        backtest_dir: str | Path | None = None,
        run_async: bool = True,
        max_workers: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.scheduler = scheduler
        self.settings = settings or DoxAgentSettings()
        self.backtest_dir = Path(
            backtest_dir
            or os.getenv("DOXAGENT_DASHBOARD_BACKTEST_DIR")
            or DEFAULT_BACKTEST_DIR
        )
        self.runtime_dir = self.backtest_dir / "runtime"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.repository = repository or SQLiteDashboardBacktestRepository(
            os.getenv("DOXAGENT_DASHBOARD_BACKTEST_SQLITE_PATH")
            or self.backtest_dir / "backtests.sqlite3"
        )
        self.dataset_fetcher = dataset_fetcher
        self.runtime_service_factory = runtime_service_factory or self._runtime_service_for_path
        self.run_async = run_async
        self.sleep = sleep
        resolved_workers = max_workers or _positive_int_env(
            "DOXAGENT_DASHBOARD_BACKTEST_MAX_WORKERS",
            default=2,
        )
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, resolved_workers),
            thread_name_prefix="dashboard-backtest",
        )

    def start_backtest(
        self,
        ticker: str,
        *,
        period: str | int,
        force_initialize: bool = False,
        replay_interval_ms: int | None = None,
    ) -> JsonObject:
        normalized = _ticker(ticker)
        period_key, period_days = _resolve_period(period)
        interval = _resolve_replay_interval_ms(replay_interval_ms)
        run = DashboardBacktestRun(
            ticker=normalized,
            period=period_key,
            period_days=period_days,
            force_initialize=force_initialize,
            replay_interval_ms=interval,
            health=BacktestHealth.UNKNOWN,
            metadata={
                "source": "dashboard_state_api",
                "runtime_namespace": "dashboard_backtest",
            },
        )
        self.repository.save_run(run)
        self._audit(
            run,
            "dashboard.backtest.queued",
            "Dashboard backtest run queued.",
            payload={"run_id": run.run_id, "period": run.period},
        )
        if self.run_async:
            self._executor.submit(self._run_backtest, run.run_id)
            return self._public_run(self._require_run(run.run_id))
        self._run_backtest(run.run_id)
        return self._public_run(self._require_run(run.run_id))

    def list_backtests(
        self,
        *,
        status: str | None = None,
        ticker: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> JsonObject:
        runs = self.repository.list_runs()
        if status and status != "all":
            runs = [run for run in runs if run.status.value == status]
        if ticker:
            normalized = _ticker(ticker)
            runs = [run for run in runs if run.ticker == normalized]
        items = [self._public_run(run) for run in runs]
        return _paginate(items, limit=limit, cursor=cursor)

    def get_backtest(self, run_id: str) -> JsonObject:
        return self._public_run(self._require_run(run_id))

    def cancel_backtest(self, run_id: str) -> JsonObject:
        run = self._require_run(run_id)
        if run.status.value in TERMINAL_BACKTEST_STATUSES:
            raise BacktestRunNotCancellable(run.run_id, run.status.value)
        now = datetime.now(UTC)
        run = run.model_copy(
            update={
                "cancel_requested": True,
                "updated_at": now,
                "health": BacktestHealth.DEGRADED,
            },
            deep=True,
        )
        self.repository.save_run(run)
        self._audit(
            run,
            "dashboard.backtest.cancel_requested",
            "Dashboard backtest cancellation requested.",
            payload={"run_id": run.run_id, "status": run.status.value},
        )
        return self._public_run(run)

    def _run_backtest(self, run_id: str) -> None:
        try:
            run = self._transition(
                run_id,
                BacktestRunStatus.INITIALIZING_DOCUMENTS,
                health=BacktestHealth.UNKNOWN,
                started_at=datetime.now(UTC),
            )
            if run.cancel_requested:
                self._cancel(run)
                return

            bundle = self._ensure_documents(run)
            context = _runtime_context_from_bundle(bundle)
            context.update(
                {
                    "backtest_run_id": run.run_id,
                    "backtest_period": run.period,
                    "backtest_period_days": run.period_days,
                    "runtime_namespace": "dashboard_backtest",
                    "known_event_patches_are_record_only": True,
                }
            )

            run = self._transition(run.run_id, BacktestRunStatus.COLLECTING_DATASET)
            if run.cancel_requested:
                self._cancel(run)
                return

            dataset_options = _dataset_options_from_monitoring_config(bundle.monitoring_config)
            result = self.dataset_fetcher(
                ticker=run.ticker,
                days=run.period_days,
                source_ids=dataset_options.source_ids or None,
                search_terms=dataset_options.search_terms,
                usernames=dataset_options.usernames,
                rss_urls=dataset_options.rss_urls,
                limit_per_source=_limit_per_source(),
                settings=self.settings,
                now=datetime.now(UTC),
            )
            dataset = result.dataset
            events = [
                _backtest_scoped_event(run, event, index)
                for index, event in enumerate(dataset.events, start=1)
            ]
            progress = run.progress.model_copy(
                update={
                    "total_events": len(events),
                    "collected_events": len(events),
                    "percent": 0.0 if events else 100.0,
                }
            )
            run = run.model_copy(
                update={
                    "progress": progress,
                    "dataset": BacktestDatasetInfo(
                        dataset_id=dataset.manifest.dataset_id,
                        source_type_counts=dataset.source_type_counts(),
                        diagnostics=list(result.diagnostics),
                        source=dict(dataset.manifest.source),
                    ),
                    "updated_at": datetime.now(UTC),
                },
                deep=True,
            )
            run = self.repository.save_run(run)

            runtime_path = self.runtime_dir / f"{run.run_id}.sqlite3"
            runtime_service = self.runtime_service_factory(runtime_path)
            run = run.model_copy(
                update={
                    "runtime": run.runtime.model_copy(
                        update={"runtime_sqlite_path": str(runtime_path)}
                    ),
                    "updated_at": datetime.now(UTC),
                },
                deep=True,
            )
            run = self.repository.save_run(run)

            if not events:
                self._complete(run, runtime_service.repository)
                return

            run = self._transition(run.run_id, BacktestRunStatus.REPLAYING)
            for event in events:
                run = self._require_run(run.run_id)
                if run.cancel_requested:
                    self._cancel(run, repository=runtime_service.repository)
                    return
                run = self._set_current_event(run, event)
                try:
                    runtime_service.execute_event(event, context=context)
                except Exception as exc:
                    failed = run.progress.failed_events + 1
                    self._fail(run, f"Backtest event execution failed: {exc}", failed_events=failed)
                    return
                processed = run.progress.processed_events + 1
                injected = run.progress.injected_events + 1
                run = self._update_progress(
                    run,
                    processed_events=processed,
                    injected_events=injected,
                )
                if run.replay_interval_ms > 0 and processed < len(events):
                    self.sleep(run.replay_interval_ms / 1000)

            run = self._transition(run.run_id, BacktestRunStatus.DRAINING_RUNTIME)
            self._complete(run, runtime_service.repository)
        except Exception as exc:
            run_for_failure = self.repository.get_run(run_id)
            if run_for_failure is not None:
                self._fail(run_for_failure, f"Backtest run failed: {exc}")

    def _ensure_documents(self, run: DashboardBacktestRun) -> DocumentBundle:
        now = datetime.now(UTC)
        bundle = self.scheduler.document_provider.latest(run.ticker, now=now)
        if bundle.status.usable and not run.force_initialize:
            return bundle
        initialized = self.scheduler.document_provider.initialize(run.ticker, now=now)
        if not initialized.status.usable:
            raise RuntimeError("Document set is not usable for backtest.")
        return initialized

    def _transition(
        self,
        run_id: str,
        status: BacktestRunStatus,
        *,
        health: BacktestHealth | None = None,
        started_at: datetime | None = None,
    ) -> DashboardBacktestRun:
        run = self._require_run(run_id)
        run = run.model_copy(
            update={
                "status": status,
                "health": health or _health_for_status(status),
                "started_at": started_at or run.started_at,
                "updated_at": datetime.now(UTC),
                "last_error": None,
            },
            deep=True,
        )
        run = self.repository.save_run(run)
        self._audit(
            run,
            "dashboard.backtest.status_changed",
            "Dashboard backtest status changed.",
            payload={"run_id": run.run_id, "status": run.status.value},
        )
        return run

    def _set_current_event(
        self,
        run: DashboardBacktestRun,
        event: EventStreamItem,
    ) -> DashboardBacktestRun:
        updated = run.model_copy(
            update={
                "current_event_id": event.event_id,
                "current_event_time": event_observed_at(event),
                "updated_at": datetime.now(UTC),
            },
            deep=True,
        )
        return self.repository.save_run(updated)

    def _update_progress(
        self,
        run: DashboardBacktestRun,
        *,
        processed_events: int,
        injected_events: int,
    ) -> DashboardBacktestRun:
        total = max(0, run.progress.total_events)
        percent = 100.0 if total == 0 else min(100.0, round(processed_events / total * 100, 2))
        progress = run.progress.model_copy(
            update={
                "processed_events": processed_events,
                "injected_events": injected_events,
                "percent": percent,
            }
        )
        updated = run.model_copy(
            update={
                "progress": progress,
                "updated_at": datetime.now(UTC),
            },
            deep=True,
        )
        return self.repository.save_run(updated)

    def _complete(
        self,
        run: DashboardBacktestRun,
        repository: PersistentRuntimeRepository,
    ) -> DashboardBacktestRun:
        runtime = _runtime_counts(repository, ticker=run.ticker)
        progress = run.progress.model_copy(
            update={
                "processed_events": run.progress.total_events,
                "injected_events": run.progress.total_events,
                "percent": 100.0,
            }
        )
        completed = run.model_copy(
            update={
                "status": BacktestRunStatus.COMPLETED,
                "health": BacktestHealth.NORMAL,
                "runtime": runtime.model_copy(
                    update={"runtime_sqlite_path": run.runtime.runtime_sqlite_path}
                ),
                "progress": progress,
                "current_event_id": None,
                "current_event_time": None,
                "completed_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
                "last_error": None,
            },
            deep=True,
        )
        saved = self.repository.save_run(completed)
        self._audit(
            saved,
            "dashboard.backtest.completed",
            "Dashboard backtest completed.",
            payload={"run_id": saved.run_id, "processed_events": saved.progress.processed_events},
        )
        return saved

    def _cancel(
        self,
        run: DashboardBacktestRun,
        *,
        repository: PersistentRuntimeRepository | None = None,
    ) -> DashboardBacktestRun:
        runtime = (
            _runtime_counts(repository, ticker=run.ticker)
            if repository is not None
            else run.runtime
        )
        cancelled = run.model_copy(
            update={
                "status": BacktestRunStatus.CANCELLED,
                "health": BacktestHealth.DEGRADED,
                "runtime": runtime.model_copy(
                    update={"runtime_sqlite_path": run.runtime.runtime_sqlite_path}
                ),
                "current_event_id": None,
                "current_event_time": None,
                "completed_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            },
            deep=True,
        )
        saved = self.repository.save_run(cancelled)
        self._audit(
            saved,
            "dashboard.backtest.cancelled",
            "Dashboard backtest cancelled.",
            payload={"run_id": saved.run_id},
        )
        return saved

    def _fail(
        self,
        run: DashboardBacktestRun,
        message: str,
        *,
        failed_events: int | None = None,
    ) -> DashboardBacktestRun:
        progress = run.progress
        if failed_events is not None:
            progress = progress.model_copy(update={"failed_events": failed_events})
        failed = run.model_copy(
            update={
                "status": BacktestRunStatus.FAILED,
                "health": BacktestHealth.BLOCKED,
                "progress": progress,
                "last_error": message,
                "completed_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            },
            deep=True,
        )
        saved = self.repository.save_run(failed)
        self._audit(
            saved,
            "dashboard.backtest.failed",
            message,
            payload={"run_id": saved.run_id},
            severity=AuditSeverity.ERROR,
        )
        return saved

    def _runtime_service_for_path(self, path: Path) -> PersistentRuntimeExecutionService:
        settings = self.settings.model_copy(
            update={
                "persistent_runtime_storage_mode": "sqlite",
                "persistent_runtime_sqlite_path": str(path),
            }
        )
        return PersistentRuntimeExecutionService.from_settings(settings)

    def _require_run(self, run_id: str) -> DashboardBacktestRun:
        run = self.repository.get_run(run_id)
        if run is None:
            raise BacktestRunNotFound(run_id)
        return run

    def _audit(
        self,
        run: DashboardBacktestRun,
        event_type: str,
        message: str,
        *,
        payload: JsonObject | None = None,
        severity: AuditSeverity = AuditSeverity.INFO,
    ) -> None:
        try:
            self.scheduler.repository.append_audit_event(
                RuntimeAuditEvent(
                    ticker=run.ticker,
                    event_type=event_type,
                    severity=severity,
                    message=message,
                    payload={
                        "backtest_run_id": run.run_id,
                        "period": run.period,
                        **(payload or {}),
                    },
                )
            )
        except Exception:
            return

    def _public_run(self, run: DashboardBacktestRun) -> JsonObject:
        payload = run.model_dump(mode="json")
        payload["status_label"] = _status_label(run.status)
        payload["can_cancel"] = (
            run.status.value in ACTIVE_BACKTEST_STATUSES and not run.cancel_requested
        )
        return payload


class _DatasetOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ids: list[str] = Field(default_factory=list)
    search_terms: list[str] = Field(default_factory=list)
    usernames: list[str] = Field(default_factory=list)
    rss_urls: list[str] = Field(default_factory=list)


def _dataset_options_from_monitoring_config(
    document: MonitoringConfigDocument | None,
) -> _DatasetOptions:
    options = _DatasetOptions()
    if document is None:
        return options
    for item in document.monitoring_items:
        tool_input = dict(item.tool_input)
        source_id = _optional_lower_text(tool_input.get("source_id"))
        if source_id:
            options.source_ids.append(source_id)
        options.search_terms.extend(_text_list(tool_input.get("search_terms")))
        options.search_terms.extend(_text_list(tool_input.get("keywords")))
        options.search_terms.extend(str(term) for term in item.base_keywords if str(term).strip())
        options.usernames.extend(_text_list(tool_input.get("usernames")))
        username = _optional_text(tool_input.get("username"))
        if username:
            options.usernames.append(username)
        options.rss_urls.extend(_text_list(tool_input.get("rss_urls")))
        rss_url = _optional_text(tool_input.get("rss_url"))
        if rss_url:
            options.rss_urls.append(rss_url)
    return _DatasetOptions(
        source_ids=_dedupe(options.source_ids),
        search_terms=_dedupe(options.search_terms),
        usernames=_dedupe(options.usernames),
        rss_urls=_dedupe(options.rss_urls),
    )


def _runtime_context_from_bundle(bundle: DocumentBundle) -> JsonObject:
    context: JsonObject = {
        "ticker": bundle.status.ticker,
        "document_run_id": bundle.status.blackboard_run_id,
    }
    if bundle.known_events is not None:
        context["known_events"] = [
            event.model_dump(mode="json") for event in bundle.known_events.events
        ]
    if bundle.monitoring_policy is not None:
        policies = bundle.monitoring_policy.policies or [
            *bundle.monitoring_policy.direct_trade_rules,
            *bundle.monitoring_policy.push_to_agent_rules,
            *bundle.monitoring_policy.cache_rules,
        ]
        context["monitoring_policies"] = [
            policy.model_dump(mode="json") for policy in policies
        ]
    return context


def _backtest_scoped_event(
    run: DashboardBacktestRun,
    event: EventStreamItem,
    index: int,
) -> EventStreamItem:
    payload = dict(event.payload)
    original_metadata = payload.get("metadata")
    metadata = dict(original_metadata) if isinstance(original_metadata, dict) else {}
    original_source_id = str(payload.get("source_id") or event.source_id).strip().lower()
    original_standard_id = str(
        payload.get("standard_message_id") or event.standard_message_id
    )
    scoped_source_id = f"backtest_{run.run_id}_{original_source_id}".lower()
    scoped_standard_id = f"{run.run_id}_{original_standard_id}"
    metadata.update(
        {
            "backtest_run_id": run.run_id,
            "backtest_period": run.period,
            "backtest_period_days": run.period_days,
            "backtest_original_source_id": original_source_id,
            "backtest_original_standard_message_id": original_standard_id,
            "runtime_namespace": "dashboard_backtest",
        }
    )
    payload.update(
        {
            "ticker": run.ticker,
            "source_id": scoped_source_id,
            "standard_message_id": scoped_standard_id,
            "raw_message_id": f"{run.run_id}_{payload.get('raw_message_id') or event.event_id}",
            "binding_id": f"{run.ticker}:{scoped_source_id}",
            "metadata": metadata,
        }
    )
    return event.model_copy(
        update={
            "event_id": f"evt_{run.run_id}_{index:06d}",
            "stream_offset": index,
            "standard_message_id": scoped_standard_id,
            "ticker": run.ticker,
            "source_id": scoped_source_id,
            "payload": payload,
            "consumed": False,
        },
        deep=True,
    )


def _runtime_counts(
    repository: PersistentRuntimeRepository,
    *,
    ticker: str,
) -> BacktestRuntimeInfo:
    return BacktestRuntimeInfo(
        execution_count=len(repository.list_executions(ticker=ticker)),
        trade_intent_count=len(repository.list_trading_records(ticker=ticker)),
        known_event_patch_count=len(repository.list_known_events_patch_logs(ticker=ticker)),
        exception_count=len(repository.list_exceptions(ticker=ticker)),
    )


def _resolve_period(period: str | int) -> tuple[str, int]:
    if isinstance(period, int):
        for key, days in BACKTEST_PERIODS.items():
            if days == period:
                return key, days
        raise UnsupportedBacktestPeriod(period)
    text = period.strip().lower()
    if text in BACKTEST_PERIODS:
        return text, BACKTEST_PERIODS[text]
    if text.endswith("d") and text[:-1].isdigit():
        value = int(text[:-1])
        for key, days in BACKTEST_PERIODS.items():
            if days == value:
                return key, days
    if text.isdigit():
        value = int(text)
        for key, days in BACKTEST_PERIODS.items():
            if days == value:
                return key, days
    raise UnsupportedBacktestPeriod(period)


def _resolve_replay_interval_ms(value: int | None) -> int:
    if value is None:
        return _positive_int_env(
            "DOXAGENT_DASHBOARD_BACKTEST_REPLAY_INTERVAL_MS",
            default=DEFAULT_REPLAY_INTERVAL_MS,
        )
    if value < 0 or value > MAX_REPLAY_INTERVAL_MS:
        raise ValueError("replay_interval_ms is out of range.")
    return value


def _limit_per_source() -> int | None:
    raw = os.getenv("DOXAGENT_DASHBOARD_BACKTEST_LIMIT_PER_SOURCE")
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _positive_int_env(name: str, *, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _health_for_status(status: BacktestRunStatus) -> BacktestHealth:
    if status is BacktestRunStatus.FAILED:
        return BacktestHealth.BLOCKED
    if status is BacktestRunStatus.CANCELLED:
        return BacktestHealth.DEGRADED
    if status is BacktestRunStatus.COMPLETED:
        return BacktestHealth.NORMAL
    return BacktestHealth.UNKNOWN


def _status_label(status: BacktestRunStatus) -> str:
    labels = {
        BacktestRunStatus.QUEUED: "排队中",
        BacktestRunStatus.INITIALIZING_DOCUMENTS: "初始化文档",
        BacktestRunStatus.COLLECTING_DATASET: "采集历史数据",
        BacktestRunStatus.REPLAYING: "串行回放",
        BacktestRunStatus.DRAINING_RUNTIME: "等待运行完成",
        BacktestRunStatus.COMPLETED: "已完成",
        BacktestRunStatus.FAILED: "失败",
        BacktestRunStatus.CANCELLED: "已取消",
    }
    return labels[status]


def _paginate(
    items: list[JsonObject],
    *,
    limit: int | None,
    cursor: str | None,
) -> JsonObject:
    resolved_limit = max(1, min(int(limit or 50), 100))
    offset = 0
    if cursor:
        try:
            offset = max(0, int(cursor))
        except ValueError:
            offset = 0
    page_items = items[offset : offset + resolved_limit]
    next_offset = offset + len(page_items)
    has_more = next_offset < len(items)
    return {
        "items": page_items,
        "page": {
            "limit": resolved_limit,
            "next_cursor": str(next_offset) if has_more else None,
            "has_more": has_more,
        },
    }


def _ticker(value: str) -> str:
    normalized = value.strip().upper()
    if not normalized:
        raise ValueError("ticker is required.")
    return normalized


def _optional_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _optional_lower_text(value: object) -> str | None:
    text = _optional_text(value)
    return text.lower() if text else None


def _text_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, Iterable):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for value in values:
        cleaned = value.strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            results.append(cleaned)
            seen.add(key)
    return results
