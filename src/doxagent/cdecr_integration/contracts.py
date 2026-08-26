"""Contracts for the first deterministic ticker-scoped CDECR foundation."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from doxagent.event_library.contracts import StrictModel


class RuntimeRegistryBinding(StrictModel):
    market: str = Field(min_length=1)
    ticker: str = Field(min_length=1)
    runtime_scope: str = Field(min_length=1)
    registry_path: str = Field(min_length=1)
    binding_version: Literal["cdecr-per-ticker-registry-v1"] = "cdecr-per-ticker-registry-v1"


class RuntimeNovelBatchStatus(StrEnum):
    FINALIZED = "FINALIZED"


class RuntimeNovelMessageBatch(StrictModel):
    """Frozen Step-3 test boundary; deliberately not a production bus contract."""

    contract_version: Literal["runtime-novel-message-batch-v1"] = (
        "runtime-novel-message-batch-v1"
    )
    batch_id: str = Field(min_length=1)
    market: str = Field(min_length=1)
    ticker: str = Field(min_length=1)
    trading_date: date
    status: RuntimeNovelBatchStatus = RuntimeNovelBatchStatus.FINALIZED
    new_non_social_message_refs: list[str]
    source_snapshot_or_lookup_ref: str = Field(min_length=1)

    @field_validator("market", "ticker")
    @classmethod
    def normalize_scope(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("new_non_social_message_refs")
    @classmethod
    def unique_message_refs(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("novel message refs must not be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("novel message refs must be unique")
        return normalized

    @model_validator(mode="after")
    def batch_identity_is_complete(self) -> RuntimeNovelMessageBatch:
        if not self.source_snapshot_or_lookup_ref.strip():
            raise ValueError("source snapshot or lookup ref is required")
        return self


class CDECRWorkflowResult(StrictModel):
    market: str
    ticker: str
    runtime_scope: str
    status: Literal["FINALIZED", "FINALIZED_NOOP"]
    message_ids: list[str]
    document_count: int = Field(ge=0)
    eligible_document_count: int = Field(ge=0)
    epoch_id: str | None = None
    completed_at: datetime


class TickerJobMode(StrEnum):
    INITIALIZE = "INITIALIZE"
    UPDATE = "UPDATE"


class TickerJobStage(StrEnum):
    CREATED = "CREATED"
    HISTORICAL_STAGING = "HISTORICAL_STAGING"
    SOURCES_READY = "SOURCES_READY"
    CDECR_RUNNING = "CDECR_RUNNING"
    RUNTIME_FINALIZED = "RUNTIME_FINALIZED"
    DELTA_READY = "DELTA_READY"
    O2_RUNNING = "O2_RUNNING"
    BUNDLE_READY = "BUNDLE_READY"
    PUBLISHED = "PUBLISHED"
    FINALIZED_NOOP = "FINALIZED_NOOP"
    FAILED = "FAILED"


class TickerJobState(StrictModel):
    job_id: str
    market: str
    ticker: str
    mode: TickerJobMode
    as_of: datetime
    stage: TickerJobStage
    runtime_scope: str
    registry_path: str
    staging_path: str
    event_library_path: str
    message_ids: list[str] = Field(default_factory=list)
    epoch_id: str | None = None
    runtime_snapshot_id: str | None = None
    delta_batch_id: str | None = None
    o2_run_id: str | None = None
    thread_id: str | None = None
    upstream_batch_id: str | None = None
    published_library_version: int | None = Field(default=None, ge=1)
    error_code: str | None = None
    error_message: str | None = None
    updated_at: datetime


class HistoricalLoadReport(StrictModel):
    market: str
    ticker: str
    window_start: datetime
    window_end: datetime
    provider_counts: dict[str, int] = Field(default_factory=dict)
    staged_count: int = Field(ge=0)
    qualified_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    selected_count: int = Field(ge=0, le=500)
    rejected_counts: dict[str, int] = Field(default_factory=dict)
    selected_message_ids: list[str] = Field(default_factory=list)


class AtomicRuntimeActivity(StrictModel):
    runtime_atomic_id: str
    last_observed_at: datetime | None = None
    eligible_until: datetime | None = None
    is_active: bool


class PackageRuntimeActivity(StrictModel):
    runtime_package_id: str
    active_atomic_count: int = Field(ge=0)
    is_active: bool


class RuntimeActivitySnapshot(StrictModel):
    runtime_scope: str
    as_of: datetime
    eligibility_days: Literal[60] = 60
    atomics: list[AtomicRuntimeActivity]
    packages: list[PackageRuntimeActivity]

    @property
    def eligible_atomic_ids(self) -> set[str]:
        return {item.runtime_atomic_id for item in self.atomics if item.is_active}


class TickerPipelineResult(StrictModel):
    job: TickerJobState
    historical: HistoricalLoadReport | None = None
    cdecr: CDECRWorkflowResult | None = None
    activity: RuntimeActivitySnapshot | None = None
    delta_batch_id: str | None = None
    frozen_view_id: str | None = None
    published_library_version: int | None = Field(default=None, ge=1)


class O2UpstreamContextManifest(StrictModel):
    contract_version: Literal["o2-upstream-context-v1"] = "o2-upstream-context-v1"
    ticker: str
    unified_as_of: datetime
    d1_run_id: str
    d1_published_at: datetime
    research_artifacts: dict[str, dict[str, Any]]
    entity_relations: list[dict[str, Any]] = Field(default_factory=list)
    future_nodes: list[dict[str, Any]] = Field(default_factory=list)
    citation_manifest: dict[str, Any]
    cdecr_epoch_id: str
    runtime_snapshot_id: str
    delta_batch_id: str


class InitializationOrchestrationStage(StrEnum):
    CREATED = "CREATED"
    UPSTREAM_RUNNING = "UPSTREAM_RUNNING"
    UPSTREAM_READY = "UPSTREAM_READY"
    O2_RUNNING = "O2_RUNNING"
    O2_PUBLISHED = "O2_PUBLISHED"
    D2_RUNNING = "D2_RUNNING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class InitializationOrchestrationState(StrictModel):
    run_id: str
    market: str
    ticker: str
    as_of: datetime
    stage: InitializationOrchestrationStage
    d1_run_id: str
    cdecr_job_id: str | None = None
    o2_run_id: str | None = None
    d2_run_id: str | None = None
    event_library_version: int | None = None
    event_library_sha256: str | None = None
    event_library_published_at: datetime | None = None
    error: str | None = None
    updated_at: datetime
