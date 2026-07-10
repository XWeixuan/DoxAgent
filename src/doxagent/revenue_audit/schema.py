"""Contracts for intent-level paper-trading revenue audits."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class RevenueAuditModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RevenueBasis(StrEnum):
    SYSTEM_EXECUTABLE = "system_executable"
    MESSAGE_BUS = "message_bus"
    IDEAL_SIGNAL = "ideal_signal"


class RevenueAuditRunStatus(StrEnum):
    NOT_STARTED = "not_started"
    CALCULATING = "calculating"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class RevenueAuditRecordStatus(StrEnum):
    PENDING = "pending"
    AUDITED = "audited"
    MISSING_TIME = "missing_time"
    MISSING_MARKET_DATA = "missing_market_data"
    UNSUPPORTED_ACTION = "unsupported_action"
    FAILED = "failed"


class RevenueAuditConfig(RevenueAuditModel):
    method_version: str = "paper-trade-v1"
    market_data_provider: str = "twelvedata"
    slippage_bps: float = Field(default=5.0, ge=0)
    base_notional_usd: float = Field(default=10_000.0, gt=0)
    size_multipliers: dict[str, float] = Field(
        default_factory=lambda: {"small": 0.5, "normal": 1.0, "aggressive": 2.0}
    )
    exit_time_et: str = "15:50"
    price_field: str = "open"
    max_forward_calendar_days: int = Field(default=10, ge=1, le=30)

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def notional_for(self, size_bucket: str) -> float:
        multiplier = self.size_multipliers.get(size_bucket)
        if multiplier is None or multiplier <= 0:
            multiplier = self.size_multipliers["normal"]
        return self.base_notional_usd * multiplier


class MinuteBar(RevenueAuditModel):
    ticker: str
    timestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float | None = Field(default=None, ge=0)
    data_source: str


class RevenueAuditRun(RevenueAuditModel):
    run_id: str = Field(default_factory=lambda: f"revaud_{uuid4().hex}")
    ticker: str
    audit_date: date
    method_version: str
    config_fingerprint: str
    status: RevenueAuditRunStatus = RevenueAuditRunStatus.NOT_STARTED
    record_count: int = 0
    result_count: int = 0
    audited_count: int = 0
    issue_count: int = 0
    failure_reason: str | None = None
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None


class RevenueAuditResult(RevenueAuditModel):
    result_id: str
    run_id: str
    trading_record_id: str
    ticker: str
    source_message_id: str
    audit_date: date
    basis: RevenueBasis
    status: RevenueAuditRecordStatus = RevenueAuditRecordStatus.PENDING
    side: str
    size_bucket: str
    decision_source: str
    trigger_policy: str | None = None
    runtime_execution_id: str | None = None
    message_summary: str | None = None
    agent_summary: str | None = None
    trigger_reason: str | None = None
    published_at: datetime | None = None
    collected_at: datetime | None = None
    normalized_at: datetime | None = None
    message_bus_event_time: datetime | None = None
    runtime_started_at: datetime | None = None
    intent_generated_at: datetime
    anchor_time: datetime | None = None
    theoretical_entry_time: datetime | None = None
    theoretical_entry_price: float | None = None
    simulated_entry_price: float | None = None
    exit_time: datetime | None = None
    theoretical_exit_price: float | None = None
    simulated_exit_price: float | None = None
    notional_usd: float | None = None
    theoretical_return_pct: float | None = None
    simulated_return_pct: float | None = None
    theoretical_pnl_usd: float | None = None
    simulated_pnl_usd: float | None = None
    slippage_bps: float
    size_rule_snapshot: dict[str, Any] = Field(default_factory=dict)
    data_source: str | None = None
    failure_reason: str | None = None
    method_version: str
    config_fingerprint: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def result_id_for(
    trading_record_id: str,
    basis: RevenueBasis,
    method_version: str,
    config_fingerprint: str,
) -> str:
    raw = f"{trading_record_id}:{basis.value}:{method_version}:{config_fingerprint}"
    return "revaudr_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
