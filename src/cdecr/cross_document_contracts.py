"""Strict contracts for incremental Atomic Event and Event Package processing."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, model_validator

from cdecr.contracts import (
    AtomicAction,
    AtomicEvent,
    AtomicSemanticRelation,
    Confidence,
    EventPackage,
    ExternalRelationType,
    MembershipRelation,
    NonEmptyString,
    PackageAction,
    PackageAssignmentRelation,
    PackageFamily,
    PackageKind,
    PackageMergeRelation,
    PackageTimeRange,
    StrictModel,
)
from cdecr.single_document_contracts import ModelCallSummary


class RecallRoute(StrEnum):
    CORE_ENTITY = "CORE_ENTITY"
    TIME_WINDOW = "TIME_WINDOW"
    EVENT_FAMILY = "EVENT_FAMILY"
    SCHEMA_IDENTITY = "SCHEMA_IDENTITY"
    PROPOSITION_EMBEDDING = "PROPOSITION_EMBEDDING"
    SOURCE_FINGERPRINT = "SOURCE_FINGERPRINT"
    LOCAL_PACKAGE_HINT = "LOCAL_PACKAGE_HINT"
    PACKAGE_ANCHOR = "PACKAGE_ANCHOR"
    SHARED_ATOMIC_EVENT = "SHARED_ATOMIC_EVENT"


class HardConflictCode(StrEnum):
    SCHEMA_TYPE = "SCHEMA_TYPE"
    CORE_SUBJECT = "CORE_SUBJECT"
    COUNTERPARTY = "COUNTERPARTY"
    NORMALIZED_PREDICATE = "NORMALIZED_PREDICATE"
    EVENT_TIME = "EVENT_TIME"
    REFERENCE_PERIOD = "REFERENCE_PERIOD"
    ASSERTION_STATE = "ASSERTION_STATE"
    LIFECYCLE_STAGE = "LIFECYCLE_STAGE"
    LOCATION_ASSET = "LOCATION_ASSET"
    ISSUER = "ISSUER"
    METRIC = "METRIC"
    COMPARISON_BASIS = "COMPARISON_BASIS"
    ACCOUNTING_BASIS = "ACCOUNTING_BASIS"
    GUIDANCE_ACTION = "GUIDANCE_ACTION"
    ANALYST_INSTITUTION = "ANALYST_INSTITUTION"
    ANALYST_COMPANY = "ANALYST_COMPANY"
    ANALYST_ACTION = "ANALYST_ACTION"
    REPORT_IDENTITY = "REPORT_IDENTITY"
    TRANSACTION_PARTIES = "TRANSACTION_PARTIES"
    PACKAGE_KIND = "PACKAGE_KIND"
    PACKAGE_FAMILY = "PACKAGE_FAMILY"
    PACKAGE_ANCHOR = "PACKAGE_ANCHOR"
    PACKAGE_ARTIFACT = "PACKAGE_ARTIFACT"
    PACKAGE_PERIOD = "PACKAGE_PERIOD"
    PACKAGE_MATTER = "PACKAGE_MATTER"


class HoldKind(StrEnum):
    ATOMIC_ASSIGNMENT = "ATOMIC_ASSIGNMENT"
    ATOMIC_CORRECTION = "ATOMIC_CORRECTION"
    PACKAGE_ASSIGNMENT = "PACKAGE_ASSIGNMENT"
    PACKAGE_MERGE = "PACKAGE_MERGE"
    PACKAGE_CORRECTION = "PACKAGE_CORRECTION"


class HoldStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class CrossDocumentStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class AtomicCandidate(StrictModel):
    event: AtomicEvent
    recall_routes: list[RecallRoute] = Field(min_length=1)
    recall_score: Confidence
    hard_conflicts: list[HardConflictCode]

    @model_validator(mode="after")
    def unique_routes_and_conflicts(self) -> AtomicCandidate:
        if len(self.recall_routes) != len(set(self.recall_routes)):
            raise ValueError("recall_routes must be unique")
        if len(self.hard_conflicts) != len(set(self.hard_conflicts)):
            raise ValueError("hard_conflicts must be unique")
        return self


class AtomicPairDecision(StrictModel):
    mention_id: NonEmptyString
    candidate_event_id: NonEmptyString
    relation: AtomicSemanticRelation
    claim_conflict: bool
    identity_conflicts: list[NonEmptyString]


class AtomicDecisionBatch(StrictModel):
    decisions: list[AtomicPairDecision]

    @model_validator(mode="after")
    def unique_pairs(self) -> AtomicDecisionBatch:
        pairs = [(item.mention_id, item.candidate_event_id) for item in self.decisions]
        if len(pairs) != len(set(pairs)):
            raise ValueError("mention/candidate decision pairs must be unique")
        return self


class AtomicAssignmentRecord(StrictModel):
    assignment_id: NonEmptyString
    run_id: NonEmptyString
    mention_id: NonEmptyString
    candidate_event_id: str | None = None
    resulting_event_id: str | None = None
    action: AtomicAction
    relation: AtomicSemanticRelation | None = None
    hard_conflicts: list[HardConflictCode]
    claim_conflict: bool = False
    identity_conflicts: list[NonEmptyString]
    reason: NonEmptyString
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_action_target(self) -> AtomicAssignmentRecord:
        if self.action is AtomicAction.HOLD and self.resulting_event_id is not None:
            raise ValueError("HOLD must not assign a resulting event")
        if self.action is not AtomicAction.HOLD and self.resulting_event_id is None:
            raise ValueError("non-HOLD atomic action requires resulting_event_id")
        return self


class PackageCandidate(StrictModel):
    package: EventPackage
    recall_routes: list[RecallRoute] = Field(min_length=1)
    recall_score: Confidence
    hard_conflicts: list[HardConflictCode]


class PackageSeed(StrictModel):
    package_kind: PackageKind
    package_family: PackageFamily
    canonical_title: NonEmptyString
    anchor_entities: list[NonEmptyString]
    local_anchor_hint: str | None = None
    anchor_artifact_id: str | None = None
    anchor_period_id: str | None = None
    time_range: PackageTimeRange
    membership_relation: MembershipRelation


class PackagePairDecision(StrictModel):
    event_id: NonEmptyString
    candidate_package_id: NonEmptyString
    relation: PackageAssignmentRelation
    membership_relation: MembershipRelation | None = None
    external_relation: ExternalRelationType | None = None

    @model_validator(mode="after")
    def validate_relation_detail(self) -> PackagePairDecision:
        if self.relation is PackageAssignmentRelation.EXTERNAL_RELATED:
            if self.external_relation is None:
                raise ValueError("EXTERNAL_RELATED requires external_relation")
        elif self.external_relation is not None:
            raise ValueError("external_relation is only valid for EXTERNAL_RELATED")
        if self.relation is PackageAssignmentRelation.MEMBER and self.membership_relation is None:
            raise ValueError("MEMBER requires membership_relation")
        if (
            self.relation is not PackageAssignmentRelation.MEMBER
            and self.membership_relation is not None
        ):
            raise ValueError("membership_relation is only valid for MEMBER")
        return self


class PackageDecisionBatch(StrictModel):
    decisions: list[PackagePairDecision]


class PackageAssignmentRecord(StrictModel):
    assignment_id: NonEmptyString
    run_id: NonEmptyString
    event_id: NonEmptyString
    candidate_package_id: str | None = None
    resulting_package_id: str | None = None
    action: PackageAction
    relation: PackageAssignmentRelation | None = None
    reason: NonEmptyString
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_action_target(self) -> PackageAssignmentRecord:
        if self.action is PackageAction.HOLD and self.resulting_package_id is not None:
            raise ValueError("HOLD must not assign a resulting package")
        if self.action is not PackageAction.HOLD and self.resulting_package_id is None:
            raise ValueError("non-HOLD package action requires resulting_package_id")
        return self


class PackagePairMergeDecision(StrictModel):
    source_package_id: NonEmptyString
    target_package_id: NonEmptyString
    relation: PackageMergeRelation


class PackageMergeDecisionBatch(StrictModel):
    decisions: list[PackagePairMergeDecision]


class HoldRecord(StrictModel):
    hold_id: NonEmptyString
    run_id: NonEmptyString
    kind: HoldKind
    subject_id: NonEmptyString
    candidate_ids: list[NonEmptyString]
    reason_codes: list[NonEmptyString] = Field(min_length=1)
    payload: dict[str, object]
    status: HoldStatus = HoldStatus.OPEN
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CrossDocumentResult(StrictModel):
    run_id: NonEmptyString
    processing_key: NonEmptyString
    message_id: NonEmptyString
    status: CrossDocumentStatus
    atomic_events: list[AtomicEvent]
    packages: list[EventPackage]
    atomic_assignments: list[AtomicAssignmentRecord]
    package_assignments: list[PackageAssignmentRecord]
    hold_ids: list[NonEmptyString]
    model_calls: list[ModelCallSummary]
    candidate_counts: dict[str, int]
    reused: bool = False
    failure_stage: str | None = None
    error_code: str | None = None
    started_at: datetime
    finished_at: datetime | None = None

    @model_validator(mode="after")
    def validate_failure(self) -> CrossDocumentResult:
        if self.status is CrossDocumentStatus.FAILED and not self.error_code:
            raise ValueError("failed result requires error_code")
        return self
