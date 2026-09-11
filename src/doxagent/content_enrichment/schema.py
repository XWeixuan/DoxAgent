"""Durable queue contracts for the global content-enrichment hub."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from doxagent.message_bus_v2.schema import (
    BusModel,
    RawMessageInput,
    SourceDefinition,
    TickerSourceBinding,
    utc_now,
)


class EnrichmentJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"


class EnrichmentJob(BusModel):
    job_id: str
    intake_key: str
    poll_run_id: str
    source: SourceDefinition
    binding: TickerSourceBinding
    message: RawMessageInput
    provider_raw_hash: str | None = None
    bootstrap: bool = False
    status: EnrichmentJobStatus = EnrichmentJobStatus.QUEUED
    attempt_count: int = Field(default=0, ge=0)
    prior_attempts: list[dict[str, object]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime = Field(default_factory=utc_now)
    not_before: datetime = Field(default_factory=utc_now)
    deadline_at: datetime
    lease_expires_at: datetime | None = None


__all__ = ["EnrichmentJob", "EnrichmentJobStatus"]
