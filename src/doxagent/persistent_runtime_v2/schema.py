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
from doxagent.message_bus_v2.compiler import compile_stream_item
from doxagent.message_bus_v2.schema import MaterializedStreamItem, PublicationMode
from doxagent.workflows.codex_document3.schema import PolicyDecision

JsonObject = dict[str, Any]
RUNTIME_V2_CONTRACT_VERSION: Final[Literal["persistent-runtime.v2"]] = "persistent-runtime.v2"


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


class W3Mode(StrEnum):
    UNCOVERED_NEW = "UNCOVERED_NEW"
    REVALIDATE_THEN_EVALUATE = "REVALIDATE_THEN_EVALUATE"


class W3CaseStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    RESOLVED = "RESOLVED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED = "FAILED"


class W3ThreadKind(StrEnum):
    MAIN = "MAIN"
    FALLBACK = "FALLBACK"


class TradeDecisionOrigin(StrEnum):
    POLICY = "POLICY"
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
    title: str | None = None
    body: str | None = None

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def has_business_content(self) -> SourceMessageSnapshot:
        if not (self.title and self.title.strip()) and not (self.body and self.body.strip()):
            raise ValueError("SourceMessageSnapshot requires title or body")
        return self


class SourceMessageEnvelope(RuntimeV2Model):
    """Operational/audit fields kept outside the model-visible snapshot."""

    source_message_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    binding_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    published_at: datetime
    collected_at: datetime
    normalized_at: datetime | None = None
    message_bus_event_time: datetime
    stream_item_id: str = Field(min_length=1)
    member_count: int = Field(ge=1)
    snapshot: SourceMessageSnapshot

    @classmethod
    def from_stream_item(cls, value: MaterializedStreamItem) -> SourceMessageEnvelope:
        compiled = compile_stream_item(value)
        latest = compiled.latest
        is_buffered = value.item.publication_mode is PublicationMode.BUFFERED
        return cls(
            source_message_id=latest.standard_message_id,
            source_id=latest.source_id,
            binding_id=latest.binding_id,
            url=latest.url,
            published_at=latest.published_at,
            collected_at=value.item.published_at,
            message_bus_event_time=value.item.published_at,
            stream_item_id=value.item.stream_item_id,
            member_count=value.item.member_count,
            snapshot=SourceMessageSnapshot(
                ticker=value.item.ticker,
                title=latest.title if not is_buffered else None,
                body=compiled.body if is_buffered else latest.body,
            ),
        )

    @property
    def occurrence_source_time(self) -> datetime:
        return self.published_at


class RuntimeVersionPin(RuntimeV2Model):
    event_library_root: str | None = None
    activation_revision_id: str | None = None
    document1_run_id: str | None = None
    document2_run_id: str | None = None
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


class W2MatchedConditions(RuntimeV2Model):
    policy_id: str = Field(min_length=1)
    condition_ids: list[str] = Field(default_factory=list)

    @field_validator("condition_ids")
    @classmethod
    def unique_condition_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class W2PolicyResult(RuntimeV2Model):
    policy_ids: list[str] = Field(default_factory=list, max_length=3)
    matched_condition_ids: list[W2MatchedConditions] = Field(default_factory=list)
    confidence: RuntimeConfidence
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("policy_ids")
    @classmethod
    def unique_policy_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @model_validator(mode="after")
    def condition_attribution_is_advisory(self) -> W2PolicyResult:
        selected = set(self.policy_ids)
        # Condition-level attribution improves auditability but malformed or
        # surplus attribution must never block the Runtime decision.
        by_policy: dict[str, W2MatchedConditions] = {}
        for item in self.matched_condition_ids:
            if item.policy_id in selected:
                by_policy[item.policy_id] = item
        self.matched_condition_ids = list(by_policy.values())
        return self


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
    prefix_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    latency_ms: int = Field(ge=0)
    output: JsonObject | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class RuntimeEffect(RuntimeV2Model):
    lease_token: int = 0
    lease_until: datetime | None = None
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
    time_semantics_version: int = 1
    runtime_mode: Literal["REALTIME", "CLOSED"] = "REALTIME"
    sweep_id: str | None = None
    closed_cycle_id: str | None = None
    execution_bundle_id: str | None = None
    frozen_inputs: JsonObject = Field(default_factory=dict)
    trade_expired: bool = False
    w2_skipped: bool = False
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
    resolved_route: RuntimeRouteDecision | None = None
    w3_result: W3CaseResult | None = None
    hot_path_latency_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def ticker(self) -> str:
        return self.source.snapshot.ticker


class PolicyActivationRecord(RuntimeV2Model):
    activation_record_id: str = Field(default_factory=lambda: new_runtime_v2_id("activation"))
    case_id: str
    source_message_id: str
    ticker: str
    policy_id: str
    activation_revision: str = Field(pattern=r"^ar_[0-9a-f]{24}$")
    policy_set_version: int = Field(ge=1)
    matched_condition_ids: list[str] = Field(default_factory=list)
    activated_at: datetime = Field(default_factory=utc_now)

    @field_validator("ticker")
    @classmethod
    def normalize_activation_ticker(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("matched_condition_ids")
    @classmethod
    def unique_activation_condition_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class TradeRecord(RuntimeV2Model):
    trade_record_id: str = Field(default_factory=lambda: new_runtime_v2_id("trade"))
    case_id: str
    ticker: str
    trading_date: date
    source: SourceMessageEnvelope
    decision_origin: TradeDecisionOrigin = TradeDecisionOrigin.POLICY
    executed_policy_id: str | None = None
    activation_revision: str | None = Field(default=None, pattern=r"^ar_[0-9a-f]{24}$")
    matched_condition_ids: list[str] = Field(default_factory=list)
    w3_case_id: str | None = None
    candidate_policy_ids: list[str] = Field(default_factory=list)
    policy_set_version: int = Field(ge=1)
    decision: PolicyDecision
    w1_result: W1NoveltyResult
    w2_result: W2PolicyResult
    w3_result: W3CaseResult | None = None
    daily_status: DailyRecordStatus = DailyRecordStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def origin_reference_is_consistent(self) -> TradeRecord:
        if self.decision_origin is TradeDecisionOrigin.POLICY:
            if not self.executed_policy_id or self.w3_case_id is not None:
                raise ValueError("POLICY trade requires policy_id and forbids w3_case_id")
        elif self.executed_policy_id is not None or not self.w3_case_id:
            raise ValueError("W3 trade requires w3_case_id and forbids policy_id")
        return self


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
    trade_candidates: list[JsonObject] = Field(default_factory=list)
    contract_version: Literal["persistent-runtime.o3-maintenance-feed.v1"] = (
        "persistent-runtime.o3-maintenance-feed.v1"
    )
    ticker: str
    trading_date: date
    reference_view_delta: ReferenceViewDeltaSnapshot
    trade_records: list[TradeRecord] = Field(default_factory=list)
    badcase_records: list[BadcaseRecord] = Field(default_factory=list)
    w3_coverage_gaps: list[W3CoverageGapRecord] = Field(default_factory=list)


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
    w3_coverage_gap_ids: list[str] = Field(default_factory=list)
    delta_batch_id: str | None = None
    o2_run_id: str
    o3_run_id: str
    o3_result: JsonObject | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class W3NoveltyResult(RuntimeV2Model):
    result: W1NoveltyVerdict
    reference_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=4000)

    @field_validator("reference_ids")
    @classmethod
    def normalized_reference_ids(cls, value: list[str]) -> list[str]:
        normalized = [item.strip().upper() for item in value if item.strip()]
        if any(not item.startswith("E") or not item[1:].isdigit() for item in normalized):
            raise ValueError("reference_ids must contain E# identifiers")
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def old_requires_reference(self) -> W3NoveltyResult:
        if self.result is W1NoveltyVerdict.OLD and not self.reference_ids:
            raise ValueError("OLD requires at least one Reference View event ID")
        return self


class W3PolicyResult(RuntimeV2Model):
    policy_ids: list[str] = Field(default_factory=list)
    matched_condition_ids: list[W2MatchedConditions] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=4000)

    @field_validator("policy_ids")
    @classmethod
    def unique_policy_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @model_validator(mode="after")
    def condition_attribution_is_advisory(self) -> W3PolicyResult:
        selected = set(self.policy_ids)
        by_policy: dict[str, W2MatchedConditions] = {}
        for item in self.matched_condition_ids:
            if item.policy_id in selected:
                by_policy[item.policy_id] = item
        self.matched_condition_ids = list(by_policy.values())
        return self


class W3ExpertTradeResult(RuntimeV2Model):
    evaluated: bool
    trade: bool
    direction: PolicyDecision | None = None
    prior_expectation: str | None = Field(default=None, max_length=4000)
    expectation_delta: str | None = Field(default=None, max_length=4000)
    reason: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def evaluation_shape_is_consistent(self) -> W3ExpertTradeResult:
        if not self.evaluated:
            if self.trade or self.direction is not None:
                raise ValueError("non-evaluated expert_trade cannot trade or set direction")
            return self
        if not self.prior_expectation or not self.expectation_delta:
            raise ValueError("evaluated expert_trade requires prior expectation and delta")
        if self.trade != (self.direction is not None):
            raise ValueError("trade=true requires direction; trade=false forbids direction")
        return self


class W3CaseResult(RuntimeV2Model):
    w3_case_id: str = Field(min_length=1, max_length=160)
    novelty: W3NoveltyResult
    policy: W3PolicyResult
    expert_trade: W3ExpertTradeResult
    delta_candidates: list[RuntimeFactCandidate] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def final_route_shape_is_consistent(self) -> W3CaseResult:
        if self.novelty.result is W1NoveltyVerdict.OLD:
            if self.delta_candidates or self.expert_trade.evaluated:
                raise ValueError("OLD result forbids delta and expert trade evaluation")
        elif self.policy.policy_ids:
            if self.expert_trade.evaluated:
                raise ValueError("existing Policy hit forbids expert trade evaluation")
        elif not self.expert_trade.evaluated:
            raise ValueError("NEW without Policy requires expert trade evaluation")
        if self.novelty.result is W1NoveltyVerdict.NEW and not self.delta_candidates:
            raise ValueError("NEW result requires at least one RuntimeFactCandidate")
        return self


class W3ContextVersionPin(RuntimeV2Model):
    document1_run_id: str
    document2_run_id: str
    event_library_version: int = Field(ge=1)
    policy_set_version: int = Field(ge=1)


class W3ThreadSlot(RuntimeV2Model):
    ticker: str
    case_id: str
    kind: W3ThreadKind
    thread_id: str | None = None
    acquired_at: datetime = Field(default_factory=utc_now)


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
    mode: W3Mode
    status: W3CaseStatus = W3CaseStatus.PENDING
    context_version_pin: W3ContextVersionPin | None = None
    result: W3CaseResult | None = None
    resolved_route: RuntimeRouteDecision | None = None
    thread_kind: W3ThreadKind | None = None
    thread_id: str | None = None
    attempt_count: int = Field(default=0, ge=0)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class W3CoverageGapRecord(RuntimeV2Model):
    coverage_gap_id: str = Field(default_factory=lambda: new_runtime_v2_id("w3gap"))
    case_id: str
    w3_case_id: str
    ticker: str
    trading_date: date
    source: SourceMessageEnvelope
    policy_set_version: int = Field(ge=1)
    result: W3CaseResult
    daily_status: DailyRecordStatus = DailyRecordStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)


class ArchiveRecord(RuntimeV2Model):
    archive_id: str = Field(default_factory=lambda: new_runtime_v2_id("archive"))
    case_id: str
    ticker: str
    source_message_id: str
    reason: str
    created_at: datetime = Field(default_factory=utc_now)


RuntimeCase.model_rebuild()
TradeRecord.model_rebuild()
O3MaintenanceFeed.model_rebuild()


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
