"""Dashboard-facing API facade for unified runtime scheduling."""

from __future__ import annotations

from datetime import datetime

from doxagent.runtime_scheduler.schema import (
    DashboardOverview,
    DocumentRefreshRequest,
    DocumentSetStatus,
    EventProcessingStatus,
    MonitoringRunStatus,
    RefreshRequestSource,
    TickerRunDetail,
)
from doxagent.runtime_scheduler.service import UnifiedRuntimeSchedulerService


class DashboardStateAPI:
    """Small facade shaped for future FastAPI route handlers."""

    def __init__(self, scheduler: UnifiedRuntimeSchedulerService) -> None:
        self.scheduler = scheduler

    @classmethod
    def from_settings(cls) -> DashboardStateAPI:
        return cls(UnifiedRuntimeSchedulerService.from_settings())

    def list_tickers(self) -> DashboardOverview:
        return self.scheduler.overview()

    def get_ticker(self, ticker: str) -> TickerRunDetail:
        return self.scheduler.detail(ticker)

    def start_ticker(
        self,
        ticker: str,
        *,
        force_initialize: bool = False,
    ) -> TickerRunDetail:
        return self.scheduler.start_ticker(
            ticker,
            force_initialize=force_initialize,
        )

    def pause_ticker(self, ticker: str, *, reason: str | None = None) -> TickerRunDetail:
        return self.scheduler.pause_ticker(ticker, reason=reason)

    def stop_ticker(
        self,
        ticker: str,
        *,
        reason: str | None = None,
        disable_bindings: bool = True,
    ) -> TickerRunDetail:
        return self.scheduler.stop_ticker(
            ticker,
            reason=reason,
            disable_bindings=disable_bindings,
        )

    def tick(
        self,
        ticker: str,
        *,
        now: datetime | None = None,
        event_limit: int = 100,
    ) -> TickerRunDetail:
        return self.scheduler.tick_ticker(ticker, now=now, event_limit=event_limit)

    def document_status(self, ticker: str) -> DocumentSetStatus:
        return self.scheduler.document_status(ticker)

    def monitoring_status(self, ticker: str) -> MonitoringRunStatus:
        return self.scheduler.monitoring_status(ticker)

    def message_bus_status(self, ticker: str) -> MonitoringRunStatus:
        return self.scheduler.monitoring_status(ticker)

    def event_processing_status(self, ticker: str) -> EventProcessingStatus:
        return self.scheduler.event_processing_status(ticker)

    def runtime_status(self, ticker: str) -> EventProcessingStatus:
        return self.scheduler.event_processing_status(ticker)

    def request_document_refresh(
        self,
        ticker: str,
        *,
        requested_by: RefreshRequestSource,
        reason: str,
        trigger_event_id: str | None = None,
    ) -> DocumentRefreshRequest:
        return self.scheduler.submit_refresh_request(
            ticker,
            requested_by=requested_by,
            reason=reason,
            trigger_event_id=trigger_event_id,
        )
