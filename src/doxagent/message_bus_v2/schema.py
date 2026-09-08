"""Strict contracts for Message Bus v2.

The bus owns collection, normalization, durable publication and consumer cursors.
It deliberately has no dependency on Runtime, legacy MonitoringConfigDocument, or
the v1 monitoring package.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, time
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

JsonObject = dict[str, Any]


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def binding_id_for(ticker: str, source_id: str) -> str:
    return f"{ticker.strip().upper()}:{source_id.strip().lower()}"


class BusModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class UpdateActor(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class SourceKind(StrEnum):
    API = "api"
    CRAWLER = "crawler"


class PublicationMode(StrEnum):
    IMMEDIATE = "immediate"
    BUFFERED = "buffered"


class TickerMonitoringStatus(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


class RawProcessingStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class PollStatus(StrEnum):
    NEVER_POLLED = "never_polled"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    DISABLED = "disabled"


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class IngestDecision(StrEnum):
    INSERTED = "inserted"
    DUPLICATE = "duplicate"
    REVISION = "revision"
    BOOTSTRAP_SUPPRESSED = "bootstrap_suppressed"
    INVALID = "invalid"


class SchedulerConstraints(BusModel):
    minimum_request_gap_seconds: float = Field(default=1.0, ge=0)
    max_concurrency: int = Field(default=1, ge=1, le=100)


class ActiveWindow(BusModel):
    timezone: str
    weekdays: list[int] = Field(default_factory=lambda: list(range(7)))
    start_time: time
    end_time: time

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown IANA timezone: {value}") from exc
        return value

    @field_validator("weekdays")
    @classmethod
    def _valid_weekdays(cls, value: list[int]) -> list[int]:
        result = sorted(set(value))
        if not result or any(day < 0 or day > 6 for day in result):
            raise ValueError("weekdays must contain values from 0 (Monday) to 6 (Sunday)")
        return result

    def contains(self, instant: datetime) -> bool:
        local = instant.astimezone(ZoneInfo(self.timezone))
        local_time = local.timetz().replace(tzinfo=None)
        if self.start_time == self.end_time:
            return local.weekday() in self.weekdays
        if self.start_time < self.end_time:
            return (
                local.weekday() in self.weekdays and self.start_time <= local_time < self.end_time
            )
        # A cross-midnight interval is attributed to its starting weekday.
        if local_time >= self.start_time:
            return local.weekday() in self.weekdays
        previous_day = (local.weekday() - 1) % 7
        return previous_day in self.weekdays and local_time < self.end_time


class PollingConfig(BusModel):
    enabled: bool = True
    target_interval_seconds: int = Field(default=60, ge=1)
    tolerance_ratio: float = Field(default=0.10, ge=0, le=0.5)
    alert_after_seconds: int = Field(default=1800, ge=1)
    active_windows: list[ActiveWindow] = Field(default_factory=list)

    def active_at(self, instant: datetime) -> bool:
        return not self.active_windows or any(
            window.contains(instant) for window in self.active_windows
        )


class BufferConfig(BusModel):
    max_items: int = Field(default=20, ge=1, le=10_000)
    max_wait_seconds: int = Field(default=300, ge=1)
    max_compiled_body_chars: int = Field(default=120_000, ge=1_000, le=120_000)


class StreamingConfig(BusModel):
    publication_mode: PublicationMode = PublicationMode.IMMEDIATE
    buffer: BufferConfig = Field(default_factory=BufferConfig)


class SourceDefinition(BusModel):
    source_id: str
    display_name: str
    kind: SourceKind = Field(validation_alias=AliasChoices("kind", "source_kind"))
    adapter_ref: str
    parameter_schema: JsonObject = Field(default_factory=lambda: {"type": "object"})
    default_parameters: JsonObject = Field(default_factory=dict)
    default_polling_config: PollingConfig = Field(default_factory=PollingConfig)
    default_streaming_config: StreamingConfig = Field(default_factory=StreamingConfig)
    scheduler_group: str
    scheduler_constraints: SchedulerConstraints = Field(default_factory=SchedulerConstraints)
    enabled: bool = True
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    updated_by: UpdateActor = UpdateActor.USER
    updated_reason: str | None = None

    @field_validator("source_id", "scheduler_group")
    @classmethod
    def _normalized_identifier(cls, value: str) -> str:
        result = value.strip().lower()
        if not result or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", result):
            raise ValueError("identifier must use lowercase letters, numbers, '.', '_' or '-'")
        return result

    @field_validator("display_name", "adapter_ref")
    @classmethod
    def _required_text(cls, value: str) -> str:
        result = value.strip()
        if not result:
            raise ValueError("value is required")
        return result


class DefaultProfileEntry(BusModel):
    source_id: str
    source_parameters: JsonObject = Field(default_factory=dict)
    polling: PollingConfig = Field(default_factory=PollingConfig)
    streaming: StreamingConfig = Field(default_factory=StreamingConfig)

    @field_validator("source_id")
    @classmethod
    def _source_id(cls, value: str) -> str:
        return value.strip().lower()


class DefaultMonitoringProfile(BusModel):
    profile_id: str = "default"
    entries: list[DefaultProfileEntry]
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    updated_by: UpdateActor = UpdateActor.SYSTEM
    updated_reason: str | None = None

    @field_validator("profile_id")
    @classmethod
    def _profile_id(cls, value: str) -> str:
        result = value.strip().lower()
        if not result:
            raise ValueError("profile_id is required")
        return result

    @model_validator(mode="after")
    def _unique_sources(self) -> DefaultMonitoringProfile:
        ids = [entry.source_id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("default profile source_id values must be unique")
        return self


class TickerSourceBinding(BusModel):
    binding_id: str
    ticker: str
    source_id: str
    source_parameters: JsonObject = Field(default_factory=dict)
    polling: PollingConfig = Field(default_factory=PollingConfig)
    streaming: StreamingConfig = Field(default_factory=StreamingConfig)
    enabled: bool = True
    version: int = Field(default=1, ge=1)
    source_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    updated_by: UpdateActor = UpdateActor.USER
    updated_reason: str | None = None
    tombstoned_at: datetime | None = None

    @field_validator("ticker")
    @classmethod
    def _ticker(cls, value: str) -> str:
        result = value.strip().upper()
        if not result:
            raise ValueError("ticker is required")
        return result

    @field_validator("source_id")
    @classmethod
    def _source(cls, value: str) -> str:
        result = value.strip().lower()
        if not result:
            raise ValueError("source_id is required")
        return result

    @model_validator(mode="after")
    def _binding_id(self) -> TickerSourceBinding:
        expected = binding_id_for(self.ticker, self.source_id)
        if self.binding_id != expected:
            raise ValueError(f"binding_id must be {expected}")
        return self


class TickerMonitoringState(BusModel):
    continuous_run_started_at: datetime | None = None
    ticker: str
    status: TickerMonitoringStatus = TickerMonitoringStatus.RUNNING
    profile_id: str = "default"
    profile_version: int
    started_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    updated_by: UpdateActor = UpdateActor.SYSTEM
    runtime_cursor_initialized: bool = False

    @field_validator("ticker")
    @classmethod
    def _ticker(cls, value: str) -> str:
        return value.strip().upper()


class RawMessageInput(BusModel):
    external_id: str | None = None
    source_item_key: str | None = None
    title: str | None = None
    body: str
    source: str | None = None
    url: str
    published_at: datetime
    raw_payload: JsonObject
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator("body", "url")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        result = value.strip()
        if not result:
            raise ValueError("value is required")
        return result

    @field_validator("title", "source")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        result = value.strip()
        return result or None

    @field_validator("url")
    @classmethod
    def _absolute_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("url must be an absolute HTTP(S) URL")
        tracking_names = {
            "fbclid",
            "gclid",
            "mc_cid",
            "mc_eid",
            "ref",
        }
        query = [
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in tracking_names
        ]
        return urlunsplit(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                parsed.path or "/",
                urlencode(sorted(query)),
                "",
            )
        )

    @field_validator("published_at")
    @classmethod
    def _aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("published_at must be timezone-aware")
        return value.astimezone(UTC)


class RawMessage(BusModel):
    raw_message_id: str
    schema_version: Literal[2] = 2
    ticker: str
    source_id: str
    binding_id: str
    source_definition_version: int
    external_id: str | None = None
    source_item_key: str
    identity_key: str
    content_hash: str
    raw_hash: str
    revision: int = Field(default=1, ge=1)
    title: str | None = None
    body: str
    source: str
    url: str
    published_at: datetime
    collected_at: datetime
    raw_payload: JsonObject
    metadata: JsonObject = Field(default_factory=dict)
    streaming_config: StreamingConfig = Field(default_factory=StreamingConfig)
    processing_status: RawProcessingStatus = RawProcessingStatus.PENDING
    processing_attempts: int = 0
    processing_error: str | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    duplicate_seen_count: int = 0
    bootstrap_suppressed: bool = False


class StandardMessage(BusModel):
    standard_message_id: str
    schema_version: Literal[2] = 2
    raw_message_id: str
    ticker: str
    source_id: str
    binding_id: str
    source_definition_version: int
    external_id: str | None = None
    source_item_key: str
    revision: int
    title: str | None = None
    body: str
    source: str
    url: str
    published_at: datetime
    collected_at: datetime
    normalized_at: datetime = Field(default_factory=utc_now)
    metadata: JsonObject = Field(default_factory=dict)


class StreamMember(BusModel):
    stream_item_id: str
    member_index: int = Field(ge=0)
    standard_message_id: str


class MaterializedStreamMember(BusModel):
    stream_item_id: str
    member_index: int = Field(ge=0)
    standard_message_id: str
    source_id: str
    binding_id: str
    title: str | None = None
    body: str
    source: str
    url: str
    published_at: datetime
    normalized_at: datetime | None = None


class StreamItem(BusModel):
    stream_item_id: str
    schema_version: Literal[2] = 2
    ticker: str
    stream_offset: int = Field(ge=1)
    publication_mode: PublicationMode
    event_type: str = "message_bus_v2.stream_item.published"
    published_at: datetime = Field(default_factory=utc_now)
    member_count: int = Field(ge=1)


class MaterializedStreamItem(BusModel):
    item: StreamItem
    members: list[MaterializedStreamMember]

    @model_validator(mode="after")
    def _membership(self) -> MaterializedStreamItem:
        if self.item.member_count != len(self.members):
            raise ValueError("stream item member_count does not match members")
        if [m.member_index for m in self.members] != list(range(len(self.members))):
            raise ValueError("stream members must be contiguous and ordered")
        return self


class ConsumerOffset(BusModel):
    consumer_id: str
    ticker: str
    stream_offset: int = Field(default=0, ge=0)
    committed_at: datetime = Field(default_factory=utc_now)


class PollState(BusModel):
    last_standard_revision_count: int | None = Field(default=None, ge=0)
    binding_id: str
    source_id: str
    ticker: str
    status: PollStatus = PollStatus.NEVER_POLLED
    checkpoint: JsonObject = Field(default_factory=dict)
    bootstrap_complete: bool = False
    target_due_at: datetime | None = None
    next_dispatch_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    failure_since: datetime | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    consecutive_failures: int = 0
    last_latency_ms: int | None = Field(default=None, ge=0)
    collected_count: int = 0
    published_count: int = 0
    updated_at: datetime = Field(default_factory=utc_now)


class AcquisitionFailure(BusModel):
    failure_id: str = Field(default_factory=lambda: new_id("acqf"))
    source_id: str
    binding_id: str
    ticker: str
    error_code: str
    error_message: str
    raw_hash: str
    original_payload: JsonObject
    first_seen_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime = Field(default_factory=utc_now)
    repeat_count: int = 1


class OperationalAlert(BusModel):
    alert_id: str = Field(default_factory=lambda: new_id("alert"))
    alert_key: str
    severity: AlertSeverity
    code: str
    message: str
    source_id: str | None = None
    binding_id: str | None = None
    scheduler_group: str | None = None
    first_seen_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime = Field(default_factory=utc_now)
    repeat_count: int = 1
    resolved_at: datetime | None = None
    metadata: JsonObject = Field(default_factory=dict)


class SchedulerGroupState(BusModel):
    scheduler_group: str
    schedule_signature: str | None = None
    next_request_at: datetime | None = None
    last_request_at: datetime | None = None
    active_request_count: int = 0
    updated_at: datetime = Field(default_factory=utc_now)


class PollResult(BusModel):
    messages: list[RawMessageInput] = Field(default_factory=list)
    next_checkpoint: JsonObject = Field(default_factory=dict)
    acquisition_metadata: JsonObject = Field(default_factory=dict)
    optional_next_poll_hint: datetime | None = None
    failures: list[AcquisitionFailure] = Field(default_factory=list)


RequestPermitFactory = Callable[[], AbstractAsyncContextManager[None]]


class PollContext(BusModel):
    poll_run_id: str = Field(default_factory=lambda: new_id("poll"))
    ticker: str
    source: SourceDefinition
    binding: TickerSourceBinding
    checkpoint: JsonObject = Field(default_factory=dict)
    requested_at: datetime = Field(default_factory=utc_now)
    request_permit: RequestPermitFactory = Field(exclude=True)

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


@runtime_checkable
class SourceAdapter(Protocol):
    async def poll(self, context: PollContext) -> PollResult: ...


class IngestResult(BusModel):
    decision: IngestDecision
    raw_message_id: str | None = None
    standard_message_id: str | None = None
    stream_item_ids: list[str] = Field(default_factory=list)
    error_code: str | None = None


class PollExecutionResult(BusModel):
    poll_run_id: str = Field(default_factory=lambda: new_id("poll"))
    binding_id: str
    crawler_execution_id: str | None = None
    collected_count: int = 0
    inserted_count: int = 0
    duplicate_count: int = 0
    revision_count: int = 0
    invalid_count: int = 0
    bootstrap_suppressed_count: int = 0
    published_count: int = 0
    error_code: str | None = None
    error_message: str | None = None


class AuditRecord(BusModel):
    audit_id: str = Field(default_factory=lambda: new_id("audit"))
    entity_type: str
    entity_id: str
    action: str
    actor: UpdateActor
    reason: str | None = None
    version: int | None = None
    payload: JsonObject = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class HardDeleteResult(BusModel):
    source_id: str
    deleted_binding_count: int
    flushed_stream_item_ids: list[str] = Field(default_factory=list)
    historical_messages_preserved: bool = True


def content_hash_for(message: RawMessageInput) -> str:
    return sha256_text(
        canonical_json(
            {
                "title": _hash_text(message.title),
                "body": _hash_text(message.body),
                "source": _hash_text(message.source),
                "url": message.url,
                "published_at": message.published_at,
            }
        )
    )


def _hash_text(value: str | None) -> str | None:
    return re.sub(r"\s+", " ", value).strip() if value is not None else None


def identity_key_for(source_id: str, message: RawMessageInput) -> str:
    if message.external_id:
        return f"{source_id}:external:{message.external_id}"
    if message.source_item_key:
        return f"{source_id}:item:{message.source_item_key}"
    return f"{source_id}:url:{sha256_text(message.url)}"


def source_item_key_for(source_id: str, message: RawMessageInput) -> str:
    if message.source_item_key:
        return message.source_item_key
    if message.external_id:
        return f"{source_id}:{message.external_id}"
    return f"{source_id}:{sha256_text(message.url)}"


def validate_parameter_schema(schema: JsonObject, value: JsonObject) -> None:
    """Validate the JSON-schema subset used by source manifests.

    This avoids making jsonschema a runtime dependency while covering object,
    required, additionalProperties, primitive types, arrays, enums and bounds.
    """

    if schema.get("type", "object") != "object":
        raise ValueError("source parameter_schema root type must be object")
    required = schema.get("required", [])
    if not isinstance(required, list):
        raise ValueError("parameter_schema.required must be an array")
    missing = [key for key in required if key not in value]
    if missing:
        raise ValueError(f"missing required source parameter(s): {', '.join(missing)}")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError("parameter_schema.properties must be an object")
    if schema.get("additionalProperties") is False:
        extras = sorted(set(value) - set(properties))
        if extras:
            raise ValueError(f"unsupported source parameter(s): {', '.join(extras)}")
    for key, item in value.items():
        spec = properties.get(key)
        if not isinstance(spec, dict):
            continue
        _validate_schema_value(key, spec, item)


def _validate_schema_value(path: str, spec: JsonObject, value: object) -> None:
    expected = spec.get("type")
    checks: dict[str, type[Any] | tuple[type[Any], ...]] = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    if expected in checks and (
        isinstance(value, bool)
        and expected in {"integer", "number"}
        or not isinstance(value, checks[expected])
    ):
        raise ValueError(f"{path} must be {expected}")
    if "enum" in spec and value not in spec["enum"]:
        raise ValueError(f"{path} must be one of {spec['enum']}")
    if isinstance(value, str):
        if len(value) < int(spec.get("minLength", 0)):
            raise ValueError(f"{path} is shorter than minLength")
        if "maxLength" in spec and len(value) > int(spec["maxLength"]):
            raise ValueError(f"{path} is longer than maxLength")
    if isinstance(value, list):
        if len(value) < int(spec.get("minItems", 0)):
            raise ValueError(f"{path} has too few items")
        if "maxItems" in spec and len(value) > int(spec["maxItems"]):
            raise ValueError(f"{path} has too many items")
        child = spec.get("items")
        if isinstance(child, dict):
            for index, item in enumerate(value):
                _validate_schema_value(f"{path}[{index}]", child, item)


__all__ = [name for name in globals() if not name.startswith("_")]
