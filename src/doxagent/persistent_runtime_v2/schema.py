"""Strict contracts for the isolated Persistent Runtime V2 workflow."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Final, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from doxagent.event_library.contracts import (
    CanonicalAssertionState,
    CanonicalEvent,
    ReferenceViewDeltaSnapshot,
)
from doxagent.monitoring.schema import EventStreamItem, SourceType, StandardMessage
from doxagent.workflows.codex_document3.schema import PolicyDecision

JsonObject = dict[str, Any]
RUNTIME_V2_CONTRACT_VERSION: Final[Literal["persistent-runtime.v2"]] = (
    "persistent-runtime.v2"
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_runtime_v2_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class RuntimeV2Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuntimeConfidence(StrEnum):
    NORMAL = "normal"
    LOW = "low"


class W1NoveltyVerdict(StrEnum):
    NEW = "NEW"
    OLD = "OLD"


class W1CaptureMode(StrEnum):
    NEW_CAPTURE = "NEW_CAPTURE"
    AMBIGUOUS_CAPTURE = "AMBIGUOUS_CAPTURE"


class RuntimePrimaryRoute(StrEnum):
    ARCHIVE = "ARCHIVE"
    TRADE = "TRADE"
    ADD_TO_DELTA = "ADD_TO_DELTA"
    W3 = "W3"


class RuntimeSideEffect(StrEnum):
    ARCHIVE_MESSAGE = "archive_message"
    EMIT_DELTA = "emit_delta"
    CREATE_TRADE_RECORD = "create_trade_record"
    MARK_BADCASE = "mark_badcase"
    ROUTE_TO_W3 = "route_to_w3"


class RuntimeCaseStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    ADJUDICATED = "ADJUDICATED"
    COMPLETED = "COMPLETED"
    PENDING_W3 = "PENDING_W3"
    PENDING_RETRY = "PENDING_RETRY"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


class RuntimeEffectStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PENDING_RETRY = "PENDING_RETRY"
    FAILED = "FAILED"


class RuntimeTechnicalStatus(StrEnum):
    OK = "OK"
    PENDING_RETRY = "PENDING_RETRY"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


class DailyRecordStatus(StrEnum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"


class DailyCloseStage(StrEnum):
    PREPARED = "PREPARED"
    O2_COMPLETED = "O2_COMPLETED"
    FEED_ASSEMBLED = "FEED_ASSEMBLED"
    O3_COMPLETED = "O3_COMPLETED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class SourceMessageSnapshot(RuntimeV2Model):
    """Only business fields made visible to W1/W2.

    URL, timestamps, source identifiers, hashes and message identifiers are
    deliberately held in :class:`SourceMessageEnvelope` and never serialized
    into this LLM snapshot.
    """

    ticker: str = Field(min_length=1)
    source_type: SourceType
    interface_type: str = Field(min_length=1)
    title: str | None = None
    body: str | None = None
    author: str | None = None
    username: str | None = None
    symbols: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("symbols", "keywords")
    @classmethod
    def unique_nonempty_values(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @model_validator(mode="after")
    def has_business_content(self) -> SourceMessageSnapshot:
        if not (self.title and self.title.strip()) and not (self.body and self.body.strip()):
            raise ValueError("SourceMessageSnapshot requires title or body")
        return self


class SourceMessageEnvelope(RuntimeV2Model):
    """Operational/audit fields kept outside the model-visible snapshot."""

    source_message_id: str = Field(min_length=1)
    raw_message_id: str | None = None
    source_id: str = Field(min_length=1)
    binding_id: str | None = None
    url: str | None = None
    published_at: datetime | None = None
    collected_at: datetime
    normalized_at: datetime | None = None
    message_bus_event_time: datetime
    provider_message_id: str | None = None
    metadata: JsonObject = Field(default_factory=dict)
    snapshot: SourceMessageSnapshot

    @classmethod
    def from_standard_message(
        cls,
        message: StandardMessage,
        *,
        message_bus_event_time: datetime | None = None,
    ) -> SourceMessageEnvelope:
        return cls(
            source_message_id=message.standard_message_id,
            raw_message_id=message.raw_message_id,
            source_id=message.source_id,
            binding_id=message.binding_id,
            url=message.url,
            published_at=message.published_at,
            collected_at=message.collected_at,
            normalized_at=message.normalized_at,
            message_bus_event_time=message_bus_event_time or message.collected_at,
            provider_message_id=message.provider_message_id,
            metadata=dict(message.metadata),
            snapshot=SourceMessageSnapshot(
                ticker=message.ticker,
                source_type=message.source_type,
                interface_type=message.interface_type.value,
                title=message.title,
                body=message.body,
                author=message.author,
                username=message.username,
                symbols=list(message.symbols),
                keywords=list(message.keywords),
            ),
        )

    @classmethod
    def from_event(cls, event: EventStreamItem) -> SourceMessageEnvelope:
        payload = StandardMessage.model_validate(event.payload)
        return cls.from_standard_message(payload, message_bus_event_time=event.event_time)

    @property
    def occurrence_source_time(self) -> datetime:
        return self.published_at or self.message_bus_event_time or self.collected_at


class RuntimeVersionPin(RuntimeV2Model):
    event_library_version: int = Field(ge=1)
    provisional_snapshot_version: int = Field(ge=0)
    policy_set_version: int = Field(ge=1)
    runtime_projection_version: int = Field(ge=1)


class W1Round1Result(RuntimeV2Model):
    event_ids: list[str] = Field(default_factory=list)

    @field_validator("event_ids")
    @classmethod
    def normalized_event_ids(cls, value: list[str]) -> list[str]:
        normalized = [item.strip().upper() for item in value if item.strip()]
        if any(not item.startswith("E") or not item[1:].isdigit() for item in normalized):
            raise ValueError("event_ids must contain E# identifiers")
        return list(dict.fromkeys(normalized))


class W1NoveltyResult(RuntimeV2Model):
    result: W1NoveltyVerdict
    confidence: RuntimeConfidence
    reference_ids: list[str] = Field(default_factory=list, max_length=3)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reference_ids")
    @classmethod
    def normalized_reference_ids(cls, value: list[str]) -> list[str]:
        normalized = [item.strip().upper() for item in value if item.strip()]
        if any(not item.startswith("E") or not item[1:].isdigit() for item in normalized):
            raise ValueError("reference_ids must contain E# identifiers")
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def old_requires_reference(self) -> W1NoveltyResult:
        if self.result is W1NoveltyVerdict.OLD and not self.reference_ids:
            raise ValueError("OLD requires at least one loaded reference ID")
        return self


class RuntimeFactCandidate(RuntimeV2Model):
    proposition: str = Field(min_length=1, max_length=4000)
    assertion_state: CanonicalAssertionState
    subject_time: str | None = Field(default=None, max_length=500)
    occurrence_date: date | None = None
    entities: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("entities")
    @classmethod
    def normalize_entities(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @property
    def canonical_payload(self) -> JsonObject:
        return self.model_dump(mode="json", exclude_none=False)

    @property
    def signature(self) -> str:
        payload = json.dumps(
            self.canonical_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class W1FactExtractionResult(RuntimeV2Model):
    candidates: list[RuntimeFactCandidate] = Field(min_length=1, max_length=12)


class W2PolicyResult(RuntimeV2Model):
    policy_ids: list[str] = Field(default_factory=list, max_length=3)
    confidence: RuntimeConfidence
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("policy_ids")
    @classmethod
    def unique_policy_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class ProvisionalFactDetail(RuntimeV2Model):
    provisional_event_id: str = Field(pattern=r"^E[1-9][0-9]*$")
    ticker: str
    trading_date: date
    source_message_id: str
    candidate_index: int = Field(ge=0)
    candidate: RuntimeFactCandidate
    runtime_signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_version: int = Field(ge=1)
    created_at: datetime = Field(default_factory=utc_now)


class RuntimeEventDetailEnvelope(RuntimeV2Model):
    event_library_version: int = Field(ge=1)
    requested_event_ids: list[str]
    canonical_events: list[CanonicalEvent] = Field(default_factory=list)
    provisional_events: list[ProvisionalFactDetail] = Field(default_factory=list)
    missing_event_ids: list[str] = Field(default_factory=list)


class RuntimeRouteDecision(RuntimeV2Model):
    primary_route: RuntimePrimaryRoute
    side_effects: list[RuntimeSideEffect]
    reason: str = Field(min_length=1)

    @field_validator("side_effects")
    @classmethod
    def unique_side_effects(cls, value: list[RuntimeSideEffect]) -> list[RuntimeSideEffect]:
        return list(dict.fromkeys(value))


class RuntimeModelTurn(RuntimeV2Model):
    turn_id: str = Field(default_factory=lambda: new_runtime_v2_id("turn"))
    case_id: str
    lane: Literal["W1", "W2"]
    round_name: Literal["R1", "R2", "R3"]
    attempt_number: int = Field(ge=1)
    status: RuntimeTechnicalStatus
    response_id: str | None = None
    previous_response_id: str | None = None
    model: str
    provider: Literal["bailian"] = "bailian"
    reasoning_effort: Literal["medium"] = "medium"
    transport: Literal["responses_json_schema"] = "responses_json_schema"
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int = Field(ge=0)
    output: JsonObject | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class RuntimeEffect(RuntimeV2Model):
    effect_id: str = Field(default_factory=lambda: new_runtime_v2_id("effect"))
    case_id: str
    effect_type: RuntimeSideEffect
    idempotency_key: str
    status: RuntimeEffectStatus = RuntimeEffectStatus.PENDING
    attempt_count: int = Field(default=0, ge=0)
    available_at: datetime = Field(default_factory=utc_now)
    payload: JsonObject = Field(default_factory=dict)
    last_error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class RuntimeCase(RuntimeV2Model):
    contract_version: Literal["persistent-runtime.v2"] = RUNTIME_V2_CONTRACT_VERSION
    case_id: str = Field(default_factory=lambda: new_runtime_v2_id("case"))
    trading_date: date
    source: SourceMessageEnvelope
    version_pin: RuntimeVersionPin
    status: RuntimeCaseStatus = RuntimeCaseStatus.CREATED
    technical_status: RuntimeTechnicalStatus = RuntimeTechnicalStatus.OK
    w1_round1: W1Round1Result | None = None
    w1_final: W1NoveltyResult | None = None
    w1_extraction: W1FactExtractionResult | None = None
    w2_round1: W2PolicyResult | None = None
    w2_final: W2PolicyResult | None = None
    route: RuntimeRouteDecision | None = None
    hot_path_latency_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def ticker(self) -> str:
        return self.source.snapshot.ticker


class TradeRecord(RuntimeV2Model):
    trade_record_id: str = Field(default_factory=lambda: new_runtime_v2_id("trade"))
    case_id: str
    ticker: str
    trading_date: date
    source: SourceMessageEnvelope
    executed_policy_id: str
    candidate_policy_ids: list[str]
    policy_set_version: int = Field(ge=1)
    decision: PolicyDecision
    w1_result: W1NoveltyResult
    w2_result: W2PolicyResult
    daily_status: DailyRecordStatus = DailyRecordStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)


class BadcaseRecord(RuntimeV2Model):
    badcase_id: str = Field(default_factory=lambda: new_runtime_v2_id("badcase"))
    case_id: str
    ticker: str
    trading_date: date
    source: SourceMessageEnvelope
    matched_known_event_ids: list[str]
    hit_policy_ids: list[str]
    event_library_version: int = Field(ge=1)
    policy_set_version: int = Field(ge=1)
    w1_reason: str
    w2_reason: str
    daily_status: DailyRecordStatus = DailyRecordStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)


class O3MaintenanceFeed(RuntimeV2Model):
    contract_version: Literal["persistent-runtime.o3-maintenance-feed.v1"] = (
        "persistent-runtime.o3-maintenance-feed.v1"
    )
    ticker: str
    trading_date: date
    reference_view_delta: ReferenceViewDeltaSnapshot
    trade_records: list[TradeRecord] = Field(default_factory=list)
    badcase_records: list[BadcaseRecord] = Field(default_factory=list)


class DailyCloseRun(RuntimeV2Model):
    contract_version: Literal["persistent-runtime.daily-close.v1"] = (
        "persistent-runtime.daily-close.v1"
    )
    run_id: str
    ticker: str
    trading_date: date
    stage: DailyCloseStage
    base_library_version: int = Field(ge=0)
    published_library_version: int | None = Field(default=None, ge=0)
    candidate_keys: list[str] = Field(default_factory=list)
    trade_record_ids: list[str] = Field(default_factory=list)
    badcase_ids: list[str] = Field(default_factory=list)
    delta_batch_id: str | None = None
    o2_run_id: str
    o3_run_id: str
    o3_result: JsonObject | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class W3RouteCase(RuntimeV2Model):
    w3_case_id: str = Field(default_factory=lambda: new_runtime_v2_id("w3"))
    case_id: str
    ticker: str
    source: SourceMessageEnvelope
    w1_final: W1NoveltyResult
    w2_final: W2PolicyResult
    event_library_version: int = Field(ge=1)
    policy_set_version: int = Field(ge=1)
    route_reason: str
    status: Literal["PENDING_W3"] = "PENDING_W3"
    created_at: datetime = Field(default_factory=utc_now)


class ArchiveRecord(RuntimeV2Model):
    archive_id: str = Field(default_factory=lambda: new_runtime_v2_id("archive"))
    case_id: str
    ticker: str
    source_message_id: str
    reason: str
    created_at: datetime = Field(default_factory=utc_now)


def strict_json_schema(value: Any) -> Any:
    """Close a Pydantic schema for strict Responses structured output."""

    if isinstance(value, list):
        return [strict_json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    strict = {key: strict_json_schema(item) for key, item in value.items() if key != "default"}
    properties = strict.get("properties")
    if isinstance(properties, dict):
        strict["additionalProperties"] = False
        strict["required"] = list(properties)
    return strict
