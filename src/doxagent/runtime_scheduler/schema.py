"""Contracts for ticker-level unified runtime scheduling."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from doxagent.message_bus_v2.schema import PollState as MessageBusV2PollState
from doxagent.message_bus_v2.schema import (
    TickerSourceBinding as MessageBusV2TickerSourceBinding,
)
from doxagent.models import DocumentType

JsonObject = dict[str, Any]


class RuntimeSchedulerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TickerRunStatus(StrEnum):
    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


class RuntimeHealth(StrEnum):
    NORMAL = "normal"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class MarketSessionPhase(StrEnum):
    PRE_MARKET_DIGEST = "pre_market_digest"
    FORMAL_MONITORING = "formal_monitoring"
    OFF_HOURS_LOW_FREQUENCY = "off_hours_low_frequency"


class MonitorMode(StrEnum):
    MESSAGE_MONITORING = "message_monitoring"
    TRADING = "trading"
    PAPER_TRADING = "trading"
    BROKER_TRADING = "trading"


class DocumentAvailability(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"
    STALE = "stale"
    INVALID = "invalid"


class AuditSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class RefreshRequestSource(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class RefreshRequestStatus(StrEnum):
    PENDING = "pending"
    EXECUTED = "executed"
    REJECTED = "rejected"


class DocumentComponentStatus(RuntimeSchedulerModel):
    document_type: DocumentType
    availability: DocumentAvailability
    document_ids: list[str] = Field(default_factory=list)
    document_count: int = 0
    newest_updated_at: datetime | None = None
    stale_after: datetime | None = None
    reason: str | None = None


class DocumentSetStatus(RuntimeSchedulerModel):
    ticker: str
    blackboard_run_id: str | None = None
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    usable: bool = False
    stale: bool = False
    missing_document_types: list[DocumentType] = Field(default_factory=list)
    components: list[DocumentComponentStatus] = Field(default_factory=list)
    applied_config_version: str | None = None
    weekly_update_due: bool = False

    @field_validator("ticker")
    @classmethod
    def _ticker_upper(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("ticker is required.")
        return normalized


class TickerRunCounters(RuntimeSchedulerModel):
    poll_cycles: int = 0
    messages_collected: int = 0
    events_created: int = 0
    events_consumed: int = 0
    pending_event_count: int = 0
    processed_event_count: int = 0
    failed_event_count: int = 0
    trade_intents_generated: int = 0
    runtime_executions: int = 0
    execution_failure_count: int = 0
    llm_call_count: int | None = None
    llm_call_count_status: Literal["not_yet_integrated"] = "not_yet_integrated"
    failure_count: int = 0


class TickerRunState(RuntimeSchedulerModel):
    ticker: str
    status: TickerRunStatus = TickerRunStatus.INITIALIZING
    health: RuntimeHealth = RuntimeHealth.NORMAL
    session_phase: MarketSessionPhase = MarketSessionPhase.OFF_HOURS_LOW_FREQUENCY
    monitor_mode: MonitorMode = MonitorMode.MESSAGE_MONITORING
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    stopped_at: datetime | None = None
    document_run_id: str | None = None
    document_status: DocumentSetStatus | None = None
    last_monitoring_config_version: str | None = None
    last_poll_at: datetime | None = None
    last_event_consumed_at: datetime | None = None
    last_trade_intent_at: datetime | None = None
    last_weekly_update_at: datetime | None = None
    last_error: str | None = None
    counters: TickerRunCounters = Field(default_factory=TickerRunCounters)
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator("ticker")
    @classmethod
    def _ticker_upper(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("ticker is required.")
        return normalized


class RuntimeAuditEvent(RuntimeSchedulerModel):
    audit_id: str = Field(default_factory=lambda: new_scheduler_id("audit"))
    ticker: str
    event_type: str
    severity: AuditSeverity = AuditSeverity.INFO
    message: str
    payload: JsonObject = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("ticker")
    @classmethod
    def _ticker_upper(cls, value: str) -> str:
        return value.strip().upper()


class DocumentRefreshRequest(RuntimeSchedulerModel):
    request_id: str = Field(default_factory=lambda: new_scheduler_id("refresh"))
    ticker: str
    requested_by: RefreshRequestSource
    reason: str
    trigger_event_id: str | None = None
    status: RefreshRequestStatus = RefreshRequestStatus.PENDING
    executed_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator("ticker")
    @classmethod
    def _ticker_upper(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("ticker is required.")
        return normalized


class MonitoringBindingStatus(RuntimeSchedulerModel):
    binding: MessageBusV2TickerSourceBinding
    poll_state: MessageBusV2PollState | None = None


class MonitoringRunStatus(RuntimeSchedulerModel):
    ticker: str
    session_phase: MarketSessionPhase
    configured_sources: list[MonitoringBindingStatus] = Field(default_factory=list)
    pending_event_count: int = 0
    recent_event_count: int = 0
    recent_message_count: int = 0
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error_message: str | None = None


class EventProcessingStatus(RuntimeSchedulerModel):
    ticker: str
    pending_event_count: int = 0
    consumed_event_count: int = 0
    runtime_execution_count: int = 0
    exception_count: int = 0
    last_execution_at: datetime | None = None


class DashboardOverview(RuntimeSchedulerModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tickers: list[TickerRunState] = Field(default_factory=list)


class TickerRunDetail(RuntimeSchedulerModel):
    state: TickerRunState
    document_status: DocumentSetStatus
    message_bus_status: MonitoringRunStatus
    runtime_status: EventProcessingStatus
    refresh_requests: list[DocumentRefreshRequest] = Field(default_factory=list)
    audit_events: list[RuntimeAuditEvent] = Field(default_factory=list)

    @property
    def monitoring_status(self) -> MonitoringRunStatus:
        """Backward-compatible Python attribute for Phase 25 callers."""
        return self.message_bus_status

    @property
    def event_processing_status(self) -> EventProcessingStatus:
        """Backward-compatible Python attribute for Phase 25 callers."""
        return self.runtime_status


def new_scheduler_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"
