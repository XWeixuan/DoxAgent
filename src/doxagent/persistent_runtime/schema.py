"""Contracts for Persistent Runtime Execution."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from doxagent.monitoring.schema import EventStreamItem, SourceType, StandardMessage

JsonObject = dict[str, Any]


class PersistentRuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuntimeWorkerTimeout(TimeoutError):
    """Raised when a bounded runtime worker exceeds its time budget."""


class W1NoveltyLabel(StrEnum):
    OLD_DUPLICATE = "old_duplicate"
    KNOWN_EVENT_RECAP = "known_event_recap"
    MATERIAL_UPDATE = "material_update"
    NEW_EVENT = "new_event"


class W1Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class W2Type(StrEnum):
    DIRECT_TRADE_CANDIDATE = "Direct Trade Candidate"
    ESCALATE_TO_BACKGROUND_AGENT = "Escalate to Background Agent"
    NULL = "NULL"
    IRRELEVANT = "Irrelevant"


class A2VerificationStatus(StrEnum):
    VERIFIED = "verified"
    LIKELY_TRUE = "likely_true"
    UNVERIFIED = "unverified"
    LIKELY_FALSE = "likely_false"
    DENIED = "denied"


class O3PrimaryAction(StrEnum):
    TRADING_RECORD = "trading_record"
    INGEST_QUEUE = "ingest_queue"
    ARCHIVE = "archive"
    OBJECTION = "objection"
    OBJECTION_NOTE = "objection_note"


class RuntimeRoute(StrEnum):
    TRADING_RECORD = "trading_record"
    A2 = "a2"
    O3 = "o3"
    INGEST_QUEUE = "ingest_queue"
    ARCHIVE = "archive"
    OBJECTION = "objection"
    OBJECTION_NOTE = "objection_note"
    FAILED_WITH_EXCEPTION = "failed_with_exception"


class TradeRecordStatus(StrEnum):
    RECORDED_ONLY = "recorded_only"
    RECORDED = "recorded_only"
    RECORDED_WITH_EXCEPTION = "recorded_with_exception"


class TradeSide(StrEnum):
    LONG = "long"
    SHORT = "short"
    EXIT = "exit"


class Conviction(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SizeBucket(StrEnum):
    SMALL = "small"
    NORMAL = "normal"
    AGGRESSIVE = "aggressive"


class RuntimeSourceMessage(PersistentRuntimeModel):
    source_message_id: str
    raw_message_id: str | None = None
    ticker: str
    source_type: SourceType
    source_id: str
    binding_id: str | None = None
    title: str | None = None
    body: str | None = None
    url: str | None = None
    author: str | None = None
    username: str | None = None
    symbols: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provider_message_id: str | None = None
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator("source_message_id", "ticker", "source_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("field must not be empty.")
        return cleaned

    @field_validator("ticker")
    @classmethod
    def _ticker_upper(cls, value: str) -> str:
        return value.strip().upper()

    @classmethod
    def from_standard_message(cls, message: StandardMessage) -> RuntimeSourceMessage:
        return cls(
            source_message_id=message.standard_message_id,
            raw_message_id=message.raw_message_id,
            ticker=message.ticker,
            source_type=message.source_type,
            source_id=message.source_id,
            binding_id=message.binding_id,
            title=message.title,
            body=message.body,
            url=message.url,
            author=message.author,
            username=message.username,
            symbols=list(message.symbols),
            keywords=list(message.keywords),
            published_at=message.published_at,
            collected_at=message.collected_at,
            provider_message_id=message.provider_message_id,
            metadata=dict(message.metadata),
        )

    @classmethod
    def from_event(cls, event: EventStreamItem) -> RuntimeSourceMessage:
        payload = dict(event.payload)
        return cls(
            source_message_id=str(
                payload.get("standard_message_id") or event.standard_message_id
            ),
            raw_message_id=_optional_str(payload.get("raw_message_id")),
            ticker=str(payload.get("ticker") or event.ticker),
            source_type=SourceType(str(payload.get("source_type") or SourceType.MEDIA.value)),
            source_id=str(payload.get("source_id") or event.source_id),
            binding_id=_optional_str(payload.get("binding_id")),
            title=_optional_str(payload.get("title")),
            body=_optional_str(payload.get("body")),
            url=_optional_str(payload.get("url")),
            author=_optional_str(payload.get("author")),
            username=_optional_str(payload.get("username")),
            symbols=[str(item) for item in payload.get("symbols") or []],
            keywords=[str(item) for item in payload.get("keywords") or []],
            published_at=_parse_datetime(payload.get("published_at")),
            collected_at=_parse_datetime(payload.get("collected_at")) or event.event_time,
            provider_message_id=_optional_str(payload.get("provider_message_id")),
            metadata=dict(payload.get("metadata") or {}),
        )


class SourceMessageBatch(PersistentRuntimeModel):
    batch_id: str = Field(default_factory=lambda: new_runtime_id("batch"))
    ticker: str
    source_type: SourceType = SourceType.SOCIAL
    messages: list[RuntimeSourceMessage]
    polling_window_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _all_messages_social_and_same_ticker(self) -> SourceMessageBatch:
        ticker = self.ticker.strip().upper()
        if not self.messages:
            raise ValueError("batch must contain at least one message.")
        for message in self.messages:
            if message.ticker != ticker:
                raise ValueError("all batch messages must use the same ticker.")
            if message.source_type is not SourceType.SOCIAL:
                raise ValueError("only social messages may be batch-routed.")
        self.ticker = ticker
        return self


class W1Result(PersistentRuntimeModel):
    is_new: bool
    novelty_label: W1NoveltyLabel
    matched_known_event_ids: list[str] = Field(default_factory=list)
    confidence: W1Confidence
    reasoning: str

    @model_validator(mode="after")
    def _novelty_label_matches_route_flag(self) -> W1Result:
        expected = self.novelty_label in {
            W1NoveltyLabel.MATERIAL_UPDATE,
            W1NoveltyLabel.NEW_EVENT,
        }
        if self.is_new is not expected:
            raise ValueError("is_new must follow the PRD novelty_label mapping.")
        return self


class W2Result(PersistentRuntimeModel):
    matched_policy_code: str | None = None
    type: W2Type
    reasoning: str

    @model_validator(mode="after")
    def _policy_code_matches_type(self) -> W2Result:
        if self.type in {
            W2Type.DIRECT_TRADE_CANDIDATE,
            W2Type.ESCALATE_TO_BACKGROUND_AGENT,
        }:
            if not self.matched_policy_code:
                raise ValueError("DTC/EBA W2 outputs must include matched_policy_code.")
        elif self.matched_policy_code is not None:
            raise ValueError("NULL/Irrelevant W2 outputs must not include matched_policy_code.")
        return self


class A2Result(PersistentRuntimeModel):
    is_new: bool
    verification_status: A2VerificationStatus
    reasoning: str
    evidence_refs: list[JsonObject] = Field(default_factory=list)

    @property
    def passed_for_runtime(self) -> bool:
        return self.is_new and self.verification_status in {
            A2VerificationStatus.VERIFIED,
            A2VerificationStatus.LIKELY_TRUE,
            A2VerificationStatus.UNVERIFIED,
        }


class TradeIntent(PersistentRuntimeModel):
    side: TradeSide
    conviction: Conviction
    size_bucket: SizeBucket
    reasoning: str


class KnownEventsPatch(PersistentRuntimeModel):
    event_id: str
    event_time_or_window: str | None = None
    core_fact: str
    duplicate_detection_keys: list[str] = Field(default_factory=list)


class RuntimeKnownEvent(PersistentRuntimeModel):
    event_id: str
    ticker: str
    event_time_or_window: str | None = None
    core_fact: str
    duplicate_detection_keys: list[str] = Field(default_factory=list)
    source_ref: str
    change_reason: str
    changed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_patch_log(cls, log: KnownEventsPatchLog) -> RuntimeKnownEvent:
        return cls(
            event_id=log.known_event_id,
            ticker=log.ticker,
            event_time_or_window=log.patch.event_time_or_window,
            core_fact=log.patch.core_fact,
            duplicate_detection_keys=list(log.patch.duplicate_detection_keys),
            source_ref=log.source_ref,
            change_reason=log.change_reason,
            changed_at=log.changed_at,
        )


class O3RuntimeBudget(PersistentRuntimeModel):
    target_seconds: int = 120
    max_model_calls: int = 2
    max_parallel_tool_call_batches: int = 1


class O3Result(PersistentRuntimeModel):
    primary_action: O3PrimaryAction
    confidence: W1Confidence | None = None
    side_effects: list[str] = Field(default_factory=list)
    trade_intent: TradeIntent | None = None
    known_events_patch: KnownEventsPatch | None = None
    blackboard_target: str | None = None
    objection_type: O3PrimaryAction | None = None
    reasoning: str
    evidence_refs: list[JsonObject] = Field(default_factory=list)

    @model_validator(mode="after")
    def _required_fields_for_action(self) -> O3Result:
        if self.primary_action is O3PrimaryAction.TRADING_RECORD and self.trade_intent is None:
            raise ValueError("O3 trading_record action requires trade_intent.")
        if self.primary_action in {
            O3PrimaryAction.OBJECTION,
            O3PrimaryAction.OBJECTION_NOTE,
        }:
            if self.blackboard_target is None:
                raise ValueError("O3 objection actions require blackboard_target.")
        if "known_events_update" in self.side_effects and self.known_events_patch is None:
            raise ValueError("known_events_update side effect requires known_events_patch.")
        return self


class RouteDecision(PersistentRuntimeModel):
    source_message_id: str
    ticker: str
    route: RuntimeRoute
    reason: str
    upstream_trade_path: bool = False
    requires_o3_known_events_update: bool = False
    o3_must_check_novelty_first: bool = False
    batch_id: str | None = None
    duplicate_of_source_message_id: str | None = None
    duplicate_key: str | None = None


class RuntimeNodeTrace(PersistentRuntimeModel):
    node: str
    status: str
    duration_ms: int
    attempts: int = 1
    exception_id: str | None = None
    timeout_budget_ms: int | None = None
    source_message_bytes: int | None = None
    runtime_context_bytes: int | None = None
    prompt_input_bytes: int | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RuntimeExecutionRecord(PersistentRuntimeModel):
    execution_id: str = Field(default_factory=lambda: new_runtime_id("pre"))
    source_message: RuntimeSourceMessage
    route_decision: RouteDecision
    w1_result: W1Result | None = None
    w2_result: W2Result | None = None
    a2_result: A2Result | None = None
    o3_result: O3Result | None = None
    status: str = "completed"
    message_statuses: list[str] = Field(default_factory=list)
    node_traces: list[RuntimeNodeTrace] = Field(default_factory=list)
    exception_ids: list[str] = Field(default_factory=list)
    timing: JsonObject = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None


class TradingRecord(PersistentRuntimeModel):
    record_id: str = Field(default_factory=lambda: new_runtime_id("trd"))
    source_message_id: str
    ticker: str
    source_type: SourceType | None = None
    route: str | None = None
    matched_policy_code: str | None = None
    trade_intent: TradeIntent
    status: TradeRecordStatus = TradeRecordStatus.RECORDED_ONLY
    exception_type: str | None = None
    w1_result: W1Result | None = None
    w2_result: W2Result | None = None
    a2_result: A2Result | None = None
    o3_result: O3Result | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RuntimeExecutionObservation(PersistentRuntimeModel):
    source_message_id: str
    ticker: str
    source_type: SourceType
    final_route: RuntimeRoute
    message_statuses: list[str] = Field(default_factory=list)
    w1_result: W1Result | None = None
    w2_result: W2Result | None = None
    a2_result: A2Result | None = None
    o3_result: O3Result | None = None
    entered_trading_records: bool = False
    entered_ingest_queue: bool = False
    entered_archive: bool = False
    known_events_updated: bool = False
    objection_created: bool = False
    objection_note_created: bool = False
    exception_types: list[str] = Field(default_factory=list)
    node_durations_ms: dict[str, int] = Field(default_factory=dict)
    created_at: datetime


class IngestQueueItem(PersistentRuntimeModel):
    item_id: str = Field(default_factory=lambda: new_runtime_id("inq"))
    source_message_id: str
    ticker: str
    reason: str
    queue_type: str = "runtime_ingest"
    available_for_doxatlas: bool = True
    available_for_research_agent: bool = True
    available_after: datetime | None = None
    payload: JsonObject = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ArchiveItem(PersistentRuntimeModel):
    item_id: str = Field(default_factory=lambda: new_runtime_id("arc"))
    source_message_id: str
    ticker: str
    reason: str
    payload: JsonObject = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class KnownEventsPatchLog(PersistentRuntimeModel):
    log_id: str = Field(default_factory=lambda: new_runtime_id("kel"))
    source_message_id: str
    ticker: str
    known_event_id: str
    source_ref: str
    change_reason: str
    patch: KnownEventsPatch
    changed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RuntimeObjectionRecord(PersistentRuntimeModel):
    objection_id: str = Field(default_factory=lambda: new_runtime_id("obj"))
    source_message_id: str
    ticker: str
    objection_type: O3PrimaryAction
    blackboard_target: str
    reason: str
    evidence_refs: list[JsonObject] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExecutionExceptionLog(PersistentRuntimeModel):
    exception_id: str = Field(default_factory=lambda: new_runtime_id("exc"))
    source_message_id: str
    ticker: str
    node: str
    exception_type: str
    message: str
    payload: JsonObject = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def new_runtime_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def runtime_duplicate_keys(message: RuntimeSourceMessage) -> set[str]:
    keys: set[str] = set()
    if message.url:
        normalized_url = message.url.strip().lower().rstrip("/")
        if normalized_url:
            keys.add(f"url:{normalized_url}")
    url_hash = message.metadata.get("url_hash")
    if isinstance(url_hash, str) and url_hash.strip():
        keys.add(f"url_hash:{url_hash.strip().lower()}")
    for key in ("content_hash", "payload_hash", "body_hash"):
        value = message.metadata.get(key)
        if isinstance(value, str) and value.strip():
            keys.add(f"content_hash:{value.strip().lower()}")
    if message.published_at is not None:
        keys.add(
            "source_time:"
            f"{message.source_type.value}:{message.source_id}:{message.published_at.isoformat()}"
        )
    batch_window_id = _metadata_text(
        message.metadata,
        ("batch_window_id", "polling_window_id", "poll_window_id"),
    )
    batch_item_id = _metadata_text(
        message.metadata,
        ("batch_item_id", "item_id", "provider_message_id"),
    )
    if batch_window_id and batch_item_id:
        keys.add(f"batch_item:{batch_window_id}:{batch_item_id}")
    return keys


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _metadata_text(metadata: JsonObject, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return None


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text).astimezone(UTC)
