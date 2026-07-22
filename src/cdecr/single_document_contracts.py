"""Strict contracts for CDECR single-document discovery and normalization."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, JsonValue, model_validator

from cdecr.contracts import (
    AssertionState,
    Confidence,
    EventFamily,
    EventMention,
    EventTime,
    LocalPackageHint,
    NonEmptyString,
    ParticipantRole,
    Predicate,
    StrictModel,
    TimePrecision,
)


class SegmentKind(StrEnum):
    TITLE = "TITLE"
    LEAD = "LEAD"
    PARAGRAPH = "PARAGRAPH"


class DuplicateRelationType(StrEnum):
    EXACT = "EXACT"
    NORMALIZED = "NORMALIZED"
    NEAR = "NEAR"
    URL_REPRINT = "URL_REPRINT"


class JudgeAction(StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    SPLIT = "SPLIT"
    DUPLICATE = "DUPLICATE"
    MERGE_AS_ATTRIBUTE = "MERGE_AS_ATTRIBUTE"


class NormalizationKind(StrEnum):
    ENTITY = "ENTITY"
    TIME_PERIOD = "TIME_PERIOD"
    METRIC = "METRIC"
    QUANTITY = "QUANTITY"
    PROJECTION = "PROJECTION"


class NormalizationMethod(StrEnum):
    M0_EXACT = "M0_EXACT"
    M1_EMBEDDING = "M1_EMBEDDING"
    M2_CONSTRAINED = "M2_CONSTRAINED"
    UNRESOLVED = "UNRESOLVED"


class ProcessingStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class EvidenceLocator(StrictModel):
    """Model-facing evidence pointer using a segment-local half-open interval."""

    segment_id: NonEmptyString
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    text: NonEmptyString

    @model_validator(mode="after")
    def validate_interval(self) -> EvidenceLocator:
        if self.end_char <= self.start_char:
            raise ValueError("end_char must be greater than start_char")
        return self


class EvidenceText(StrictModel):
    """LLM-facing evidence quote; character positions are computed by the program."""

    segment_id: NonEmptyString
    text: NonEmptyString


class SourceSegment(StrictModel):
    segment_id: NonEmptyString
    kind: SegmentKind
    field: Literal["title", "text"]
    text: NonEmptyString
    original_start: int = Field(ge=0)
    original_end: int = Field(gt=0)
    paragraph_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_interval(self) -> SourceSegment:
        if self.original_end <= self.original_start:
            raise ValueError("original_end must be greater than original_start")
        if self.original_end - self.original_start != len(self.text):
            raise ValueError("segment text must preserve a one-to-one original offset mapping")
        return self


class DocumentBlock(StrictModel):
    block_id: NonEmptyString
    segment_ids: list[NonEmptyString] = Field(min_length=1)
    text: NonEmptyString
    common_context: str = ""
    overlap_segment_ids: list[NonEmptyString] = Field(default_factory=list)


class PreprocessedDocument(StrictModel):
    message_id: NonEmptyString
    pipeline_version: NonEmptyString
    source_fingerprint: NonEmptyString
    normalized_fingerprint: NonEmptyString
    normalized_url: NonEmptyString
    segments: list[SourceSegment] = Field(min_length=1)
    cleaned_text: NonEmptyString
    document_blocks: list[DocumentBlock] = Field(min_length=1)
    minhash64: list[int] = Field(min_length=64, max_length=64)
    is_long_document: bool
    is_complex_document: bool
    removed_span_count: int = Field(ge=0)


class DuplicateRelation(StrictModel):
    relation_id: NonEmptyString
    source_message_id: NonEmptyString
    target_message_id: NonEmptyString
    relation_type: DuplicateRelationType
    score: Confidence


class PreprocessingResult(StrictModel):
    document: PreprocessedDocument
    duplicate_relations: list[DuplicateRelation]
    reusable_message_id: str | None = None


class DreamCandidate(StrictModel):
    candidate_id: NonEmptyString
    statement: NonEmptyString
    evidence_locations: list[EvidenceLocator] = Field(min_length=1)


class DreamCandidateDraft(StrictModel):
    statement: NonEmptyString
    evidence_locations: list[EvidenceLocator] = Field(min_length=1)


class DreamerModelOutput(StrictModel):
    candidates: list[DreamCandidateDraft] = Field(max_length=24)


class DreamerOutput(StrictModel):
    candidates: list[DreamCandidate]


class OpenAttributeDraft(StrictModel):
    key: NonEmptyString
    value: NonEmptyString
    evidence_location: EvidenceText


class ParticipantDraft(StrictModel):
    surface: NonEmptyString
    role: ParticipantRole


class EventTimeDraft(StrictModel):
    event_start: datetime | date | None
    event_end: datetime | date | None
    precision: TimePrecision
    reference_period_id: str | None = None

    @model_validator(mode="after")
    def validate_bounds(self) -> EventTimeDraft:
        EventTime(**self.model_dump())
        return self


class QuantityDraft(StrictModel):
    metric_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    value: int | float
    unit: NonEmptyString
    raw_text: NonEmptyString


class MentionDraft(StrictModel):
    evidence_locations: list[EvidenceText] = Field(min_length=1)
    canonical_proposition: NonEmptyString
    source_claim: str | None
    event_family: EventFamily
    predicate: Predicate
    participants: list[ParticipantDraft]
    locations: list[NonEmptyString]
    time: EventTimeDraft
    assertion_state: AssertionState
    quantities: list[QuantityDraft]
    open_attributes: list[OpenAttributeDraft]
    local_package_hint: LocalPackageHint | None = None


class GroundedMentionDraft(StrictModel):
    draft_id: NonEmptyString
    source_candidate_ids: list[NonEmptyString] = Field(min_length=1)
    mention: MentionDraft


class GroundedMentionDraftInput(StrictModel):
    source_candidate_ids: list[
        Annotated[str, Field(pattern=r"^c[1-9][0-9]*$")]
    ] = Field(min_length=1)
    mention: MentionDraft


class GrounderModelOutput(StrictModel):
    drafts: list[GroundedMentionDraftInput]
    issue_flags: list[NonEmptyString]


class GrounderOutput(StrictModel):
    drafts: list[GroundedMentionDraft]
    issue_flags: list[NonEmptyString]


class JudgeDecisionRecord(StrictModel):
    decision_id: NonEmptyString
    target_draft_id: NonEmptyString
    action: JudgeAction
    reason: NonEmptyString
    revised_mention: MentionDraft | None = None
    split_mentions: list[MentionDraft] = Field(default_factory=list)
    target_mention_id: str | None = None
    attribute: OpenAttributeDraft | None = None

    @model_validator(mode="after")
    def validate_action_payload(self) -> JudgeDecisionRecord:
        if self.action is JudgeAction.SPLIT and len(self.split_mentions) < 2:
            raise ValueError("SPLIT requires at least two replacement mentions")
        if self.action is not JudgeAction.SPLIT and self.split_mentions:
            raise ValueError("split_mentions is only valid for SPLIT")
        if self.action is JudgeAction.DUPLICATE and not self.target_mention_id:
            raise ValueError("DUPLICATE requires target_mention_id")
        if self.action is JudgeAction.MERGE_AS_ATTRIBUTE:
            if not self.target_mention_id or self.attribute is None:
                raise ValueError("MERGE_AS_ATTRIBUTE requires target and attribute")
        return self


class JudgeDecisionDraft(StrictModel):
    target_draft_id: NonEmptyString
    action: JudgeAction
    reason: NonEmptyString
    revised_mention: MentionDraft | None = None
    split_mentions: list[MentionDraft] = Field(default_factory=list)
    target_mention_id: str | None = None
    attribute: OpenAttributeDraft | None = None

    @model_validator(mode="after")
    def validate_action_payload(self) -> JudgeDecisionDraft:
        if self.action is JudgeAction.SPLIT and len(self.split_mentions) < 2:
            raise ValueError("SPLIT requires at least two replacement mentions")
        if self.action is not JudgeAction.SPLIT and self.split_mentions:
            raise ValueError("split_mentions is only valid for SPLIT")
        if self.action is JudgeAction.DUPLICATE and not self.target_mention_id:
            raise ValueError("DUPLICATE requires target_mention_id")
        if self.action is JudgeAction.MERGE_AS_ATTRIBUTE:
            if not self.target_mention_id or self.attribute is None:
                raise ValueError("MERGE_AS_ATTRIBUTE requires target and attribute")
        return self


class JudgeModelOutput(StrictModel):
    decisions: list[JudgeDecisionDraft]


class JudgeOutput(StrictModel):
    decisions: list[JudgeDecisionRecord]


class NormalizationCandidate(StrictModel):
    canonical_id: NonEmptyString
    score: Confidence


class NormalizationDecision(StrictModel):
    decision_id: NonEmptyString
    mention_id: NonEmptyString
    field_path: NonEmptyString
    kind: NormalizationKind
    raw_value: JsonValue
    normalized_value: JsonValue
    method: NormalizationMethod
    candidates: list[NormalizationCandidate]
    unresolved_reason: str | None = None

    @model_validator(mode="after")
    def validate_unresolved(self) -> NormalizationDecision:
        if self.method is NormalizationMethod.UNRESOLVED and not self.unresolved_reason:
            raise ValueError("unresolved decisions require a reason")
        return self


class ModelCallSummary(StrictModel):
    stage: NonEmptyString
    tier: Literal["m1", "m2", "m3", "m4"]
    model: NonEmptyString
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int = Field(ge=0)
    status: Literal["SUCCEEDED", "FAILED"] = "SUCCEEDED"
    error_code: str | None = None
    repaired: bool = False


class JudgeRouting(StrictModel):
    invoked: bool
    reasons: list[NonEmptyString]


class FailureSummary(StrictModel):
    stage: NonEmptyString
    error_code: NonEmptyString


class SingleDocumentResult(StrictModel):
    run_id: NonEmptyString
    message_id: NonEmptyString
    processing_key: NonEmptyString
    status: ProcessingStatus
    mentions: list[EventMention]
    model_calls: list[ModelCallSummary]
    judge_routing: JudgeRouting
    normalization_decisions: list[NormalizationDecision]
    failures: list[FailureSummary]
    started_at: datetime
    finished_at: datetime | None = None
    reused: bool = False


class FiscalPeriod(StrictModel):
    period_id: NonEmptyString
    company_id: NonEmptyString
    fiscal_year: int = Field(ge=1900, le=2200)
    fiscal_quarter: int | None = Field(default=None, ge=1, le=4)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_period(self) -> FiscalPeriod:
        if self.end_date < self.start_date:
            raise ValueError("fiscal period end precedes start")
        return self
