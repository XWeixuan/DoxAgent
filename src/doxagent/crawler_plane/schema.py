"""Strict contracts for the Message Bus v2 Crawler Plane."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

JsonObject = dict[str, Any]


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class CrawlerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CrawlerVersionStatus(StrEnum):
    WORKING = "WORKING"
    CERTIFIED = "CERTIFIED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"


class CrawlerExecutionStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


class NetworkMode(StrEnum):
    RECORD = "RECORD"
    REPLAY = "REPLAY"


class CertificationCheck(StrEnum):
    CONTRACT = "contract"
    REPLAY = "replay"
    TEMPORAL_REPLAY = "temporal_replay"
    SYNTHETIC_INCREMENT = "synthetic_increment"
    DETERMINISM = "determinism"
    FAILURE_REPLAY = "failure_replay"


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class CrawlerAlertStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class CrawlerAlertType(StrEnum):
    EXECUTION_FAILURE = "crawler_execution_failure"
    DISCOVERY_ANOMALY = "crawler_discovery_anomaly"
    CONTENT_DRIFT = "crawler_content_drift"
    TRANSPORT_ANOMALY = "crawler_transport_anomaly"


class CrawlerObservation(CrawlerModel):
    title: str | None = None
    body: str = Field(min_length=1)
    source: str = Field(min_length=1)
    url: str
    published_at: datetime
    external_id: str | None = None
    metadata: JsonObject = Field(default_factory=dict)
    raw_artifact_ref: str | None = None

    @field_validator("title", "external_id")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        result = value.strip()
        return result or None

    @field_validator("body", "source")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        result = value.strip()
        if not result:
            raise ValueError("value may not be blank")
        return result

    @field_validator("url")
    @classmethod
    def _absolute_url(cls, value: str) -> str:
        result = value.strip()
        parsed = urlsplit(result)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("url must be absolute HTTP(S)")
        return result

    @field_validator("published_at")
    @classmethod
    def _aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("published_at must be timezone-aware")
        return value.astimezone(UTC)


class CrawlerRunOutput(CrawlerModel):
    observations: list[CrawlerObservation] = Field(default_factory=list)
    next_checkpoint: JsonObject = Field(default_factory=dict)
    diagnostics: JsonObject = Field(default_factory=dict)


class CrawlerVersionSpec(CrawlerModel):
    """Minimal metadata replacing a package manifest file."""

    crawler_id: str
    version: int = Field(ge=1)
    entrypoint: str = "crawler.py:crawl"
    parameter_schema: JsonObject = Field(default_factory=lambda: {"type": "object"})
    checkpoint_schema_version: int = Field(default=1, ge=1)

    @field_validator("crawler_id")
    @classmethod
    def _crawler_id(cls, value: str) -> str:
        result = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", result):
            raise ValueError("invalid crawler_id")
        return result

    @field_validator("entrypoint")
    @classmethod
    def _entrypoint(cls, value: str) -> str:
        result = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9_./-]+\.py:[A-Za-z_][A-Za-z0-9_]*", result):
            raise ValueError("entrypoint must be relative/path.py:function")
        if ".." in result.split(":", 1)[0].split("/"):
            raise ValueError("entrypoint may not escape the crawler directory")
        return result

    @model_validator(mode="after")
    def _parameter_schema(self) -> CrawlerVersionSpec:
        if self.parameter_schema.get("type", "object") != "object":
            raise ValueError("parameter_schema root type must be object")
        return self


class CrawlerVersion(CrawlerModel):
    version_id: str
    spec: CrawlerVersionSpec
    status: CrawlerVersionStatus = CrawlerVersionStatus.WORKING
    working_path: str | None = None
    release_path: str | None = None
    content_digest: str | None = None
    certified_digest: str | None = None
    certification_run_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CrawlerPackage(CrawlerModel):
    crawler_id: str
    active_version: int | None = None
    latest_version: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CrawlerCheckpoint(CrawlerModel):
    crawler_id: str
    binding_id: str
    schema_version: int = Field(ge=1)
    value: JsonObject = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=utc_now)


class CrawlerExecutionRequest(CrawlerModel):
    crawler_id: str
    ticker: str
    binding_id: str
    source_id: str
    source_parameters: JsonObject = Field(default_factory=dict)
    poll_run_id: str
    network_mode: NetworkMode = NetworkMode.RECORD
    cassette_ref: str | None = None
    version: int | None = Field(default=None, ge=1)
    commit_checkpoint: bool = True
    checkpoint_override: JsonObject | None = None
    preserve_response_bodies: bool = False

    @field_validator("crawler_id", "source_id")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("ticker")
    @classmethod
    def _ticker(cls, value: str) -> str:
        return value.strip().upper()


class CrawlerExecutionResult(CrawlerModel):
    execution_id: str
    poll_run_id: str
    crawler_id: str
    crawler_version: int
    source_id: str
    binding_id: str
    ticker: str
    source_parameters: JsonObject = Field(default_factory=dict)
    status: CrawlerExecutionStatus
    observations: list[CrawlerObservation] = Field(default_factory=list)
    diagnostics: JsonObject = Field(default_factory=dict)
    artifact_refs: list[str] = Field(default_factory=list)
    cassette_ref: str | None = None
    checkpoint_before: JsonObject = Field(default_factory=dict)
    checkpoint_after: JsonObject = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    request_count: int = Field(default=0, ge=0)
    response_bytes: int = Field(default=0, ge=0)
    error_code: str | None = None
    error_message: str | None = None
    message_bus_telemetry: JsonObject = Field(default_factory=dict)


class NetworkExchange(CrawlerModel):
    sequence: int = Field(ge=1)
    transport: Literal["http", "browser"]
    method: str = "GET"
    request_url: str
    request_headers: dict[str, str] = Field(default_factory=dict)
    status_code: int = Field(ge=0)
    response_url: str
    response_headers: dict[str, str] = Field(default_factory=dict)
    response_body_ref: str | None = None
    response_body: str | None = None
    observed_at: datetime = Field(default_factory=utc_now)


class NetworkCassette(CrawlerModel):
    cassette_id: str = Field(default_factory=lambda: new_id("cassette"))
    crawler_id: str
    crawler_version: int
    execution_id: str | None = None
    exchanges: list[NetworkExchange] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    path: str | None = None


class ExecutionArtifact(CrawlerModel):
    artifact_id: str = Field(default_factory=lambda: new_id("artifact"))
    execution_id: str
    kind: str
    path: str
    sha256: str
    size_bytes: int = Field(ge=0)
    created_at: datetime = Field(default_factory=utc_now)


class CertificationCase(CrawlerModel):
    case_id: str
    kind: Literal["replay", "temporal", "synthetic"]
    cassette_refs: list[str] = Field(min_length=1)
    parameters: JsonObject = Field(default_factory=dict)
    initial_checkpoint: JsonObject = Field(default_factory=dict)
    expected_external_ids: list[str] = Field(default_factory=list)


class CertificationCheckResult(CrawlerModel):
    check: CertificationCheck
    status: CheckStatus
    test_case: str | None = None
    expected: JsonObject = Field(default_factory=dict)
    actual: JsonObject = Field(default_factory=dict)
    cassette_ref: str | None = None
    artifact_ref: str | None = None
    diagnostic: str | None = None


class CertificationResult(CrawlerModel):
    certification_run_id: str = Field(default_factory=lambda: new_id("cert"))
    crawler_id: str
    crawler_version: int
    content_digest: str
    overall: CheckStatus
    checks: list[CertificationCheckResult]
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime = Field(default_factory=utc_now)


class RegressionCase(CrawlerModel):
    regression_id: str = Field(default_factory=lambda: new_id("regression"))
    crawler_id: str
    source_execution_id: str
    cassette_ref: str
    checkpoint: JsonObject = Field(default_factory=dict)
    parameters: JsonObject = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class CrawlerAlertPolicy(CrawlerModel):
    crawler_id: str
    source_id: str | None = None
    alert_type: CrawlerAlertType
    enabled: bool = True
    threshold: int | None = Field(default=None, ge=0)
    window: int = Field(default=3, ge=1, le=100)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def policy_key(self) -> str:
        return f"{self.crawler_id}:{self.source_id or '*'}:{self.alert_type.value}"


class CrawlerAlert(CrawlerModel):
    alert_id: str = Field(default_factory=lambda: new_id("crawler_alert"))
    alert_key: str
    crawler_id: str
    source_id: str | None = None
    binding_id: str | None = None
    execution_id: str | None = None
    alert_type: CrawlerAlertType
    status: CrawlerAlertStatus = CrawlerAlertStatus.OPEN
    message: str
    metadata: JsonObject = Field(default_factory=dict)
    first_seen_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime = Field(default_factory=utc_now)
    repeat_count: int = Field(default=1, ge=1)
    resolved_at: datetime | None = None


class CrawlerSourceRegistration(CrawlerModel):
    source_id: str
    display_name: str
    crawler_id: str
    parameter_schema: JsonObject = Field(default_factory=lambda: {"type": "object"})
    default_parameters: JsonObject = Field(default_factory=dict)
    default_polling_config: JsonObject = Field(default_factory=dict)
    default_streaming_config: JsonObject = Field(default_factory=dict)
    scheduler_group: str
    scheduler_constraints: JsonObject = Field(default_factory=dict)


class WorkerJob(CrawlerModel):
    job_id: str
    execution_id: str
    package_path: str
    entrypoint: str
    ticker: str
    parameters: JsonObject
    checkpoint: JsonObject


class WorkerJobResult(CrawlerModel):
    job_id: str
    execution_id: str
    ok: bool
    output: JsonObject = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
