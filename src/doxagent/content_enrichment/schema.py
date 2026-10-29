"""Durable queue contracts for the global content-enrichment hub."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

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
    COMPLETED = "completed"
    FAILED = "failed"


class EnrichmentJob(BusModel):
    job_id: str
    intake_key: str
    poll_run_id: str
    source: SourceDefinition
    binding: TickerSourceBinding | None = None
    owner_kind: Literal["ticker_binding", "distribution_article", "live_article"] = "ticker_binding"
    article_id: str | None = None
    message: RawMessageInput
    provider_raw_hash: str | None = None
    bootstrap: bool = False
    status: EnrichmentJobStatus = EnrichmentJobStatus.QUEUED
    attempt_count: int = Field(default=0, ge=0)
    fetch_round: int = Field(default=0, ge=0)
    prior_attempts: list[dict[str, object]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime = Field(default_factory=utc_now)
    not_before: datetime = Field(default_factory=utc_now)
    deadline_at: datetime
    lease_expires_at: datetime | None = None
    claim_token: str | None = None
    pipeline_version: str | None = None
    execution_seq: int = Field(default=0, ge=0)
    budget_started_at: datetime | None = None
    first_fetch_at: datetime | None = None
    fetch_budget_seconds: int = Field(default=180, ge=1, le=1800)
    body_result_key: str | None = None
    known_article_raw_id: str | None = None
    work_kind: Literal["new_article", "content_update", "recheck"] = "new_article"
    result: dict[str, object] = Field(default_factory=dict)

    @property
    def execution_id(self) -> str:
        if self.owner_kind=="live_article":
            return f"{self.job_id}:{self.claim_token}"
        return f"{self.job_id}:{self.execution_seq}"

    @model_validator(mode="after")
    def _owner(self) -> EnrichmentJob:
        if self.owner_kind == "ticker_binding" and self.binding is None:
            raise ValueError("ticker enrichment requires binding")
        if self.owner_kind == "distribution_article" and (
            self.binding is not None or not self.article_id
        ):
            raise ValueError("shared enrichment requires article_id and no binding")
        if self.owner_kind == "live_article" and not self.article_id:
            raise ValueError("live article requires article_id")
        return self


__all__ = ["EnrichmentJob", "EnrichmentJobStatus"]
