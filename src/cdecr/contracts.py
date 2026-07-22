"""Strict domain contracts for the standalone CDECR module."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


class StrictModel(BaseModel):
    """Base class shared by every persisted/public CDECR contract."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


Confidence = Annotated[float, Field(ge=0.0, le=1.0)]
NonEmptyString = Annotated[str, Field(min_length=1)]


class SourceType(StrEnum):
    NEWS = "NEWS"
    SOCIAL = "SOCIAL"
    ANNOUNCEMENT = "ANNOUNCEMENT"
    FLASH = "FLASH"
    FILING = "FILING"
    OTHER = "OTHER"


class Language(StrEnum):
    EN = "en"
    ZH = "zh"
    UND = "und"


class EventFamily(StrEnum):
    FINANCIAL_PERFORMANCE = "FINANCIAL_PERFORMANCE"
    GUIDANCE_EXPECTATION = "GUIDANCE_EXPECTATION"
    ANALYST_ACTION = "ANALYST_ACTION"
    TRANSACTION_CAPITAL = "TRANSACTION_CAPITAL"
    COMMERCIAL_OPERATION = "COMMERCIAL_OPERATION"
    PRODUCTION_SUPPLY = "PRODUCTION_SUPPLY"
    REGULATORY_LEGAL_POLICY = "REGULATORY_LEGAL_POLICY"
    GOVERNANCE_PERSONNEL = "GOVERNANCE_PERSONNEL"
    PRODUCT_SCIENCE = "PRODUCT_SCIENCE"
    INCIDENT_GEOPOLITICAL = "INCIDENT_GEOPOLITICAL"
    MARKET_MOVEMENT = "MARKET_MOVEMENT"
    OTHER = "OTHER"


class ParticipantRole(StrEnum):
    ACTOR = "ACTOR"
    SUBJECT = "SUBJECT"
    TARGET = "TARGET"
    COUNTERPARTY = "COUNTERPARTY"
    AFFECTED = "AFFECTED"
    AUTHORITY = "AUTHORITY"
    OTHER = "OTHER"


class TimePrecision(StrEnum):
    TIMESTAMP = "TIMESTAMP"
    DAY = "DAY"
    MONTH = "MONTH"
    QUARTER = "QUARTER"
    YEAR = "YEAR"
    INTERVAL = "INTERVAL"
    UNKNOWN = "UNKNOWN"


class AssertionState(StrEnum):
    ACTUAL = "ACTUAL"
    ONGOING = "ONGOING"
    PLANNED = "PLANNED"
    EXPECTED = "EXPECTED"
    RUMORED = "RUMORED"
    DENIED = "DENIED"
    HYPOTHETICAL = "HYPOTHETICAL"
    UNKNOWN = "UNKNOWN"


class ComparisonBasis(StrEnum):
    ABSOLUTE = "ABSOLUTE"
    YOY = "YOY"
    QOQ = "QOQ"
    YTD = "YTD"
    VS_CONSENSUS = "VS_CONSENSUS"
    VS_GUIDANCE = "VS_GUIDANCE"
    UNKNOWN = "UNKNOWN"


class AccountingBasis(StrEnum):
    GAAP = "GAAP"
    NON_GAAP = "NON_GAAP"
    STATUTORY = "STATUTORY"
    UNKNOWN = "UNKNOWN"


class GuidanceAction(StrEnum):
    INITIATE = "INITIATE"
    RAISE = "RAISE"
    LOWER = "LOWER"
    REITERATE = "REITERATE"
    NARROW = "NARROW"
    WIDEN = "WIDEN"
    WITHDRAW = "WITHDRAW"
    UNKNOWN = "UNKNOWN"


class AnalystAction(StrEnum):
    UPGRADE = "UPGRADE"
    DOWNGRADE = "DOWNGRADE"
    INITIATE = "INITIATE"
    REITERATE = "REITERATE"
    MAINTAIN = "MAINTAIN"
    RAISE_TARGET = "RAISE_TARGET"
    LOWER_TARGET = "LOWER_TARGET"
    SUSPEND_COVERAGE = "SUSPEND_COVERAGE"
    RESUME_COVERAGE = "RESUME_COVERAGE"
    UNKNOWN = "UNKNOWN"


class AtomicSemanticRelation(StrEnum):
    SAME_EVENT = "SAME_EVENT"
    RELATED_NOT_SAME = "RELATED_NOT_SAME"
    UNRELATED = "UNRELATED"
    UNCERTAIN = "UNCERTAIN"


class AtomicAction(StrEnum):
    MERGE = "MERGE"
    CREATE_NEW = "CREATE_NEW"
    CREATE_AND_LINK = "CREATE_AND_LINK"
    HOLD = "HOLD"


class PackageKind(StrEnum):
    BOUNDED = "BOUNDED"
    EPISODE = "EPISODE"


class PackageFamily(StrEnum):
    EARNINGS_DISCLOSURE = "EARNINGS_DISCLOSURE"
    COMPANY_DISCLOSURE = "COMPANY_DISCLOSURE"
    ANALYST_REPORT = "ANALYST_REPORT"
    TRANSACTION = "TRANSACTION"
    REGULATORY_LEGAL = "REGULATORY_LEGAL"
    POLICY = "POLICY"
    OPERATIONAL_INCIDENT = "OPERATIONAL_INCIDENT"
    PRODUCT_SCIENCE = "PRODUCT_SCIENCE"
    OTHER = "OTHER"


class PackageStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


class MembershipRelation(StrEnum):
    DISCLOSED_IN = "DISCLOSED_IN"
    COMPONENT_OF = "COMPONENT_OF"
    STAGE_OF = "STAGE_OF"
    UPDATE_OF = "UPDATE_OF"
    CORRECTION_OF = "CORRECTION_OF"
    IMPLEMENTATION_OF = "IMPLEMENTATION_OF"


class ExternalRelationType(StrEnum):
    CAUSES = "CAUSES"
    MARKET_REACTION_TO = "MARKET_REACTION_TO"
    ANALYST_REACTION_TO = "ANALYST_REACTION_TO"
    CONFIRMS = "CONFIRMS"
    CONTRADICTS = "CONTRADICTS"
    RELATED_TO = "RELATED_TO"


class PackageAssignmentRelation(StrEnum):
    MEMBER = "MEMBER"
    EXTERNAL_RELATED = "EXTERNAL_RELATED"
    NOT_RELATED = "NOT_RELATED"
    UNCERTAIN = "UNCERTAIN"


class PackageMergeRelation(StrEnum):
    SAME_PACKAGE = "SAME_PACKAGE"
    DIFFERENT_PACKAGE = "DIFFERENT_PACKAGE"
    UNCERTAIN = "UNCERTAIN"


class PackageAction(StrEnum):
    ADD_TO_PACKAGE = "ADD_TO_PACKAGE"
    CREATE_NEW_PACKAGE = "CREATE_NEW_PACKAGE"
    LINK_EXTERNALLY = "LINK_EXTERNALLY"
    MERGE_PACKAGES = "MERGE_PACKAGES"
    HOLD = "HOLD"


class EvidenceSpan(StrictModel):
    field: Literal["title", "text"]
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    text: NonEmptyString

    @model_validator(mode="after")
    def validate_interval(self) -> EvidenceSpan:
        if self.end_char <= self.start_char:
            raise ValueError("end_char must be greater than start_char")
        return self

    def validate_source(self, source: SourceMessage) -> None:
        original = source.title if self.field == "title" else source.text
        if self.end_char > len(original):
            raise ValueError("evidence span is outside the source field")
        if original[self.start_char : self.end_char] != self.text:
            raise ValueError("evidence span text does not match the source slice")


class SourceMessage(StrictModel):
    message_id: NonEmptyString
    source_type: SourceType
    title: NonEmptyString
    text: NonEmptyString
    published_at: datetime
    source_name: NonEmptyString
    url: NonEmptyString
    ticker_hints: list[NonEmptyString] = Field(min_length=1)
    parent_message_id: str | None = None
    language: Language

    @field_validator("published_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("published_at must include a timezone")
        return value

    @field_validator("ticker_hints")
    @classmethod
    def unique_tickers(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().upper() for value in values]
        if any(not value for value in normalized):
            raise ValueError("ticker hints must not be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("ticker hints must be unique")
        return normalized


class Predicate(StrictModel):
    raw: NonEmptyString
    normalized: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]


class Participant(StrictModel):
    surface: NonEmptyString
    entity_id: str | None = None
    role: ParticipantRole


class EventTime(StrictModel):
    event_start: datetime | date | None = None
    event_end: datetime | date | None = None
    precision: TimePrecision
    reference_period_id: str | None = None

    @model_validator(mode="after")
    def validate_bounds(self) -> EventTime:
        if self.event_start is not None and self.event_end is not None:
            start = self.event_start
            end = self.event_end
            if isinstance(start, datetime) and isinstance(end, datetime) and end < start:
                raise ValueError("event_end must not precede event_start")
            if isinstance(start, date) and not isinstance(start, datetime):
                if isinstance(end, date) and not isinstance(end, datetime) and end < start:
                    raise ValueError("event_end must not precede event_start")
        return self


class Quantity(StrictModel):
    metric_id: NonEmptyString
    value: int | float
    unit: NonEmptyString
    raw_text: NonEmptyString


class OpenAttribute(StrictModel):
    key: NonEmptyString
    value: NonEmptyString
    evidence_span: EvidenceSpan


class FinancialMetricFields(StrictModel):
    issuer_id: NonEmptyString
    period_id: NonEmptyString
    metric_id: NonEmptyString
    value: int | float
    unit: NonEmptyString
    comparison_basis: ComparisonBasis
    change_value: int | float | None = None
    accounting_basis: AccountingBasis


class FinancialMetricProjection(StrictModel):
    schema_type: Literal["FINANCIAL_METRIC"] = "FINANCIAL_METRIC"
    fields: FinancialMetricFields


class GuidanceFields(StrictModel):
    issuer_id: NonEmptyString
    period_id: NonEmptyString
    metric_id: NonEmptyString
    action: GuidanceAction
    value_low: int | float | None = None
    value_high: int | float | None = None
    unit: NonEmptyString

    @model_validator(mode="after")
    def validate_range(self) -> GuidanceFields:
        if self.value_low is not None and self.value_high is not None:
            if self.value_high < self.value_low:
                raise ValueError("value_high must not be lower than value_low")
        return self


class GuidanceProjection(StrictModel):
    schema_type: Literal["GUIDANCE"] = "GUIDANCE"
    fields: GuidanceFields


class AnalystActionFields(StrictModel):
    institution_id: NonEmptyString
    analyst_id: str | None = None
    company_id: NonEmptyString
    action: AnalystAction
    rating_from: str | None = None
    rating_to: str | None = None
    target_from: int | float | None = None
    target_to: int | float | None = None
    currency: str | None = None
    report_date: date


class AnalystActionProjection(StrictModel):
    schema_type: Literal["ANALYST_ACTION"] = "ANALYST_ACTION"
    fields: AnalystActionFields


SchemaProjection = Annotated[
    FinancialMetricProjection | GuidanceProjection | AnalystActionProjection,
    Field(discriminator="schema_type"),
]


class LocalPackageHint(StrictModel):
    anchor: NonEmptyString
    relation_to_anchor: MembershipRelation


class EventMention(StrictModel):
    mention_id: NonEmptyString
    message_id: NonEmptyString
    evidence_spans: list[EvidenceSpan] = Field(min_length=1)
    canonical_proposition: NonEmptyString
    source_claim: str | None = None
    event_family: EventFamily
    predicate: Predicate
    participants: list[Participant]
    locations: list[NonEmptyString]
    time: EventTime
    assertion_state: AssertionState
    quantities: list[Quantity]
    open_attributes: list[OpenAttribute]
    schema_projection: SchemaProjection | None = None
    local_package_hint: LocalPackageHint | None = None

    def validate_evidence(self, source: SourceMessage) -> None:
        if source.message_id != self.message_id:
            raise ValueError("mention and source message_id do not match")
        for span in self.evidence_spans:
            span.validate_source(source)
        for attribute in self.open_attributes:
            attribute.evidence_span.validate_source(source)


class FinancialMetricIdentityFields(StrictModel):
    issuer_id: NonEmptyString
    period_id: NonEmptyString
    metric_id: NonEmptyString
    comparison_basis: ComparisonBasis
    accounting_basis: AccountingBasis


class FinancialMetricIdentityProfile(StrictModel):
    schema_type: Literal["FINANCIAL_METRIC"] = "FINANCIAL_METRIC"
    fields: FinancialMetricIdentityFields


class GuidanceIdentityFields(StrictModel):
    issuer_id: NonEmptyString
    period_id: NonEmptyString
    metric_id: NonEmptyString
    action: GuidanceAction


class GuidanceIdentityProfile(StrictModel):
    schema_type: Literal["GUIDANCE"] = "GUIDANCE"
    fields: GuidanceIdentityFields


class AnalystActionIdentityFields(StrictModel):
    institution_id: NonEmptyString
    company_id: NonEmptyString
    action: AnalystAction
    report_date: date | None = None
    report_id: str | None = None

    @model_validator(mode="after")
    def require_report_identity(self) -> AnalystActionIdentityFields:
        if self.report_date is None and not self.report_id:
            raise ValueError("analyst identity requires report_date or report_id")
        return self


class AnalystActionIdentityProfile(StrictModel):
    schema_type: Literal["ANALYST_ACTION"] = "ANALYST_ACTION"
    fields: AnalystActionIdentityFields


class OpenIdentityFields(StrictModel):
    normalized_predicate: NonEmptyString
    principal_participant_ids: list[NonEmptyString]
    event_time: EventTime
    reference_period_id: str | None = None
    location_or_asset_ids: list[NonEmptyString]
    assertion_state: AssertionState


class OpenIdentityProfile(StrictModel):
    schema_type: Literal["OPEN"] = "OPEN"
    fields: OpenIdentityFields


IdentityProfile = Annotated[
    FinancialMetricIdentityProfile
    | GuidanceIdentityProfile
    | AnalystActionIdentityProfile
    | OpenIdentityProfile,
    Field(discriminator="schema_type"),
]


class AtomicEvent(StrictModel):
    event_id: NonEmptyString
    canonical_proposition: NonEmptyString
    event_family: EventFamily
    identity_profile: IdentityProfile
    time: EventTime
    assertion_state: AssertionState
    mention_ids: list[NonEmptyString] = Field(min_length=1)
    representative_mention_ids: list[NonEmptyString] = Field(min_length=1)
    consensus_claims: dict[str, JsonValue]
    conflict_flags: list[NonEmptyString]
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_mentions(self) -> AtomicEvent:
        mention_ids = set(self.mention_ids)
        if len(mention_ids) != len(self.mention_ids):
            raise ValueError("mention_ids must be unique")
        if not set(self.representative_mention_ids).issubset(mention_ids):
            raise ValueError("representative mentions must be members")
        return self


class PackageTimeRange(StrictModel):
    start: datetime | date | None = None
    end: datetime | date | None = None


class EventPackage(StrictModel):
    package_id: NonEmptyString
    package_kind: PackageKind
    package_family: PackageFamily
    canonical_title: NonEmptyString
    anchor_entities: list[NonEmptyString]
    anchor_artifact_id: str | None = None
    anchor_period_id: str | None = None
    time_range: PackageTimeRange
    lifecycle_state: str | None = None
    member_event_ids: list[NonEmptyString] = Field(min_length=1)
    canonical_summary: str
    status: PackageStatus
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_members(self) -> EventPackage:
        if len(self.member_event_ids) != len(set(self.member_event_ids)):
            raise ValueError("member_event_ids must be unique")
        return self


class PackageMembership(StrictModel):
    membership_id: NonEmptyString
    event_id: NonEmptyString
    package_id: NonEmptyString
    relation: MembershipRelation
    role_detail: str | None = None
    version: int = Field(default=1, ge=1)


class ExternalEventRelation(StrictModel):
    relation_id: NonEmptyString
    source_event_id: NonEmptyString
    target_event_id: NonEmptyString
    relation: ExternalRelationType
    version: int = Field(default=1, ge=1)


class PackageExternalRelation(StrictModel):
    """Directed relation from an Atomic Event to a Package boundary."""

    relation_id: NonEmptyString
    source_event_id: NonEmptyString
    target_package_id: NonEmptyString
    relation: ExternalRelationType
    version: int = Field(default=1, ge=1)


class AtomicCoreferenceDecision(StrictModel):
    relation: AtomicSemanticRelation
    claim_conflict: bool
    identity_conflicts: list[NonEmptyString]


class PackageAssignmentDecision(StrictModel):
    relation: PackageAssignmentRelation


class PackageMergeDecision(StrictModel):
    relation: PackageMergeRelation
