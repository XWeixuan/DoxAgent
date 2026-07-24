"""Strict contracts for CDECR single-document discovery and normalization."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, JsonValue, model_validator
from pydantic.json_schema import SkipJsonSchema

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
    event_start: datetime | date | None = Field(
        description=(
            "Underlying event start time, not the publication date or a financial reporting period."
        )
    )
    event_end: datetime | date | None = Field(
        description=(
            "Underlying event end time, not the publication date or a financial reporting period."
        )
    )
    precision: TimePrecision
    reference_period_id: str | None = Field(
        default=None,
        description=(
            "Document-local reporting or financial-period expression awaiting later "
            "linking; never a canonical KB ID."
        ),
    )

    @model_validator(mode="after")
    def validate_bounds(self) -> EventTimeDraft:
        EventTime(**self.model_dump())
        return self


def validate_event_time_semantics(value: EventTimeDraft) -> None:
    if (
        value.event_start is None
        and value.event_end is None
        and value.precision is not TimePrecision.UNKNOWN
    ):
        raise ValueError("time without event bounds must use UNKNOWN precision")


class QuantityDraft(StrictModel):
    metric_id: Annotated[
        str,
        Field(
            pattern=r"^[a-z][a-z0-9_]*$",
            description=(
                "Document-local metric label awaiting later linking; never invent a "
                "canonical Metric KB ID."
            ),
        ),
    ]
    value: int | float
    unit: NonEmptyString
    raw_text: NonEmptyString


class MentionDraft(StrictModel):
    evidence_locations: list[EvidenceText] = Field(min_length=1)
    canonical_proposition: NonEmptyString = Field(
        description=("Self-contained underlying event proposition without reporting attribution.")
    )
    source_claim: str | None = Field(
        description=(
            "Explicit claimant or statement source in the text, not the publishing news "
            "outlet by default."
        )
    )
    event_family: EventFamily
    predicate: Predicate
    participants: list[ParticipantDraft] = Field(
        description=("Core participants in the underlying event, not every entity mentioned.")
    )
    locations: list[NonEmptyString]
    time: EventTimeDraft
    assertion_state: AssertionState = Field(
        description=("State of the underlying proposition, not the state of the reporting act.")
    )
    quantities: list[QuantityDraft]
    open_attributes: list[OpenAttributeDraft] = Field(
        description=("Evidence-backed modifiers that do not independently constitute events.")
    )
    local_package_hint: LocalPackageHint | None = None


class GroundedMentionDraft(StrictModel):
    draft_id: NonEmptyString
    source_candidate_ids: list[NonEmptyString] = Field(min_length=1)
    mention: MentionDraft


class GroundedMentionDraftInput(StrictModel):
    source_candidate_ids: list[Annotated[str, Field(pattern=r"^c[1-9][0-9]*$")]] = Field(
        min_length=1
    )
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
        if self.action is JudgeAction.ACCEPT:
            if self.split_mentions or self.target_mention_id or self.attribute is not None:
                raise ValueError("ACCEPT only permits revised_mention")
        elif self.action is JudgeAction.REJECT:
            if (
                self.revised_mention is not None
                or self.split_mentions
                or self.target_mention_id
                or self.attribute is not None
            ):
                raise ValueError("REJECT does not permit action payload fields")
        elif self.action is JudgeAction.SPLIT:
            if len(self.split_mentions) < 2:
                raise ValueError("SPLIT requires at least two replacement mentions")
            if (
                self.revised_mention is not None
                or self.target_mention_id
                or self.attribute is not None
            ):
                raise ValueError("SPLIT only permits split_mentions")
        elif self.action is JudgeAction.DUPLICATE:
            if not self.target_mention_id:
                raise ValueError("DUPLICATE requires target_mention_id")
            if (
                self.revised_mention is not None
                or self.split_mentions
                or self.attribute is not None
            ):
                raise ValueError("DUPLICATE only permits target_mention_id")
        elif self.action is JudgeAction.MERGE_AS_ATTRIBUTE:
            if not self.target_mention_id or self.attribute is None:
                raise ValueError("MERGE_AS_ATTRIBUTE requires target and attribute")
            if self.revised_mention is not None or self.split_mentions:
                raise ValueError("MERGE_AS_ATTRIBUTE only permits target_mention_id and attribute")
        return self


class JudgeMentionDraft(StrictModel):
    """N4-only Mention DTO; lineage and package fields remain outside the model."""

    evidence_locations: list[EvidenceText] = Field(min_length=1)
    canonical_proposition: NonEmptyString = Field(
        description=("Self-contained underlying event proposition without reporting attribution.")
    )
    source_claim: str | None = Field(
        description=(
            "Explicit claimant or statement source in the text, not the publishing news "
            "outlet by default."
        )
    )
    event_family: EventFamily = Field(
        description="Coarse routing family that N4 may correct when unsupported."
    )
    predicate: Predicate
    participants: list[ParticipantDraft] = Field(
        description=("Core participants in the underlying event, not every entity mentioned.")
    )
    locations: list[NonEmptyString]
    time: EventTimeDraft
    assertion_state: AssertionState = Field(
        description=("State of the underlying proposition, not the state of the reporting act.")
    )
    quantities: list[QuantityDraft]
    open_attributes: list[OpenAttributeDraft] = Field(
        description=("Evidence-backed modifiers that do not independently constitute events.")
    )


class JudgeDraftInput(StrictModel):
    id: Annotated[str, Field(pattern=r"^d[1-9][0-9]*$")]
    mention: JudgeMentionDraft


class JudgeMentionChanges(StrictModel):
    """Field-level replacements for ACCEPT; omitted fields retain their input values."""

    evidence_locations: list[EvidenceText] | SkipJsonSchema[None] = Field(
        default=None, min_length=1
    )
    canonical_proposition: NonEmptyString | SkipJsonSchema[None] = None
    source_claim: str | None = None
    event_family: EventFamily | SkipJsonSchema[None] = None
    predicate: Predicate | SkipJsonSchema[None] = None
    participants: list[ParticipantDraft] | SkipJsonSchema[None] = None
    locations: list[NonEmptyString] | SkipJsonSchema[None] = None
    time: EventTimeDraft | SkipJsonSchema[None] = None
    assertion_state: AssertionState | SkipJsonSchema[None] = None
    quantities: list[QuantityDraft] | SkipJsonSchema[None] = None
    open_attributes: list[OpenAttributeDraft] | SkipJsonSchema[None] = None

    @model_validator(mode="after")
    def validate_replacements(self) -> JudgeMentionChanges:
        if not self.model_fields_set:
            raise ValueError("changes must replace at least one Mention field")
        nullable_fields = {"source_claim"}
        for field_name in self.model_fields_set - nullable_fields:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be replaced with null")
        return self


JudgeShortId = Annotated[str, Field(pattern=r"^d[1-9][0-9]*$")]
JudgeReason = Annotated[str, Field(min_length=1, max_length=240)]


class JudgeAcceptedCommand(StrictModel):
    id: JudgeShortId
    reason: JudgeReason
    changes: JudgeMentionChanges | SkipJsonSchema[None] = None

    @model_validator(mode="after")
    def reject_explicit_null_changes(self) -> JudgeAcceptedCommand:
        if "changes" in self.model_fields_set and self.changes is None:
            raise ValueError("changes must be omitted when no fields are replaced")
        return self


class JudgeRejectedCommand(StrictModel):
    id: JudgeShortId
    reason: JudgeReason


class JudgeSplitCommand(StrictModel):
    id: JudgeShortId
    reason: JudgeReason
    mentions: list[JudgeMentionDraft] = Field(min_length=2)


class JudgeDuplicateCommand(StrictModel):
    id: JudgeShortId
    reason: JudgeReason
    keep_id: JudgeShortId = Field(
        description="Short ID in this batch whose final decision is ACCEPT."
    )


class JudgeAttributeMergeCommand(StrictModel):
    id: JudgeShortId
    reason: JudgeReason
    keep_id: JudgeShortId = Field(
        description="Short ID in this batch whose final decision is ACCEPT."
    )
    attribute: OpenAttributeDraft


class JudgeCommandOutput(StrictModel):
    accepted: list[JudgeAcceptedCommand] = Field(default_factory=list)
    rejected: list[JudgeRejectedCommand] = Field(default_factory=list)
    split: list[JudgeSplitCommand] = Field(default_factory=list)
    duplicates: list[JudgeDuplicateCommand] = Field(default_factory=list)
    attribute_merges: list[JudgeAttributeMergeCommand] = Field(default_factory=list)


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
    queue_wait_ms: int = Field(default=0, ge=0)
    request_item_count: int = Field(default=1, ge=0)
    candidate_count: int = Field(default=0, ge=0)
    request_payload_bytes: int = Field(default=0, ge=0)
    wire_ref_count: int = Field(default=0, ge=0)


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
