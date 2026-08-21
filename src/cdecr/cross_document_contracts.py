"""Strict contracts for incremental Atomic Event and Event Package processing."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, model_validator

from cdecr.atomic_identity_contracts import (
    AtomicIdentitySidecar,
    IdentityAxisAssessment,
)
from cdecr.contracts import (
    AtomicAction,
    AtomicEvent,
    AtomicSemanticRelation,
    Confidence,
    EventPackage,
    MembershipRelation,
    NonEmptyString,
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
    FIELD_ID = "FIELD_ID"


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


class HardCannotLinkMode(StrEnum):
    ENFORCE = "enforce"
    SHADOW = "shadow"
    OFF = "off"


class CrossDocumentStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL_ATOMIC_DECIDE_RETRYABLE = "PARTIAL_ATOMIC_DECIDE_RETRYABLE"
    PARTIAL_PARENT_RESOLUTION = "PARTIAL_PARENT_RESOLUTION"
    FAILED = "FAILED"


class AtomicCandidate(StrictModel):
    event: AtomicEvent
    recall_routes: list[RecallRoute] = Field(min_length=1)
    recall_score: Confidence
    hard_conflicts: list[HardConflictCode]
    identity_sidecar: AtomicIdentitySidecar | None = None
    candidate_root_id: str | None = None
    raw_embedding_similarity: float | None = Field(default=None, ge=-1.0, le=1.0)

    @model_validator(mode="after")
    def unique_routes_and_conflicts(self) -> AtomicCandidate:
        if len(self.recall_routes) != len(set(self.recall_routes)):
            raise ValueError("recall_routes must be unique")
        if len(self.hard_conflicts) != len(set(self.hard_conflicts)):
            raise ValueError("hard_conflicts must be unique")
        return self


class AtomicCandidateAssessment(StrictModel):
    candidate_event_id: NonEmptyString
    relation: AtomicSemanticRelation
    axis_assessments: list[IdentityAxisAssessment]
    claim_conflict: bool = False
    identity_differences: list[NonEmptyString] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_identity_axes(self) -> AtomicCandidateAssessment:
        axes = [item.axis for item in self.axis_assessments]
        if len(axes) != len(set(axes)):
            raise ValueError("identity axis assessments must be unique")
        return self


class AtomicAssignmentDecision(StrictModel):
    mention_id: NonEmptyString
    action: AtomicAction
    merge_target_event_id: str | None = None
    candidate_assessments: list[AtomicCandidateAssessment]
    related_candidate_event_ids: list[NonEmptyString] = Field(default_factory=list)
    possible_duplicate_atomic_ids: list[NonEmptyString] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_action(self) -> AtomicAssignmentDecision:
        if self.action is AtomicAction.MERGE and self.merge_target_event_id is None:
            raise ValueError("MERGE requires merge_target_event_id")
        if self.action is AtomicAction.CREATE_NEW and self.merge_target_event_id is not None:
            raise ValueError("CREATE_NEW must not include merge_target_event_id")
        assessment_ids = [item.candidate_event_id for item in self.candidate_assessments]
        if len(assessment_ids) != len(set(assessment_ids)):
            raise ValueError("candidate assessments must be unique")
        if len(self.related_candidate_event_ids) != len(set(self.related_candidate_event_ids)):
            raise ValueError("related candidate ids must be unique")
        if len(self.possible_duplicate_atomic_ids) != len(set(self.possible_duplicate_atomic_ids)):
            raise ValueError("possible duplicate atomic ids must be unique")
        return self


class AtomicDecisionBatch(StrictModel):
    decisions: list[AtomicAssignmentDecision]

    @model_validator(mode="after")
    def unique_mentions(self) -> AtomicDecisionBatch:
        mentions = [item.mention_id for item in self.decisions]
        if len(mentions) != len(set(mentions)):
            raise ValueError("atomic decisions must be unique per mention")
        return self


class AtomicLatePairDecision(StrictModel):
    pair_id: NonEmptyString
    relation: AtomicSemanticRelation


class AtomicLateDecisionBatch(StrictModel):
    decisions: list[AtomicLatePairDecision]

    @model_validator(mode="after")
    def unique_pairs(self) -> AtomicLateDecisionBatch:
        pair_ids = [item.pair_id for item in self.decisions]
        if len(pair_ids) != len(set(pair_ids)):
            raise ValueError("atomic late decisions must be unique per pair")
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
    identity_differences: list[NonEmptyString]
    related_candidate_event_ids: list[NonEmptyString] = Field(default_factory=list)
    possible_duplicate_atomic_ids: list[NonEmptyString] = Field(default_factory=list)
    identity_processing_key: NonEmptyString
    assignment_policy_version: NonEmptyString
    reason: NonEmptyString
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_action_target(self) -> AtomicAssignmentRecord:
        if self.resulting_event_id is None:
            raise ValueError("atomic assignment requires resulting_event_id")
        return self


class PackageAssignmentRecord(StrictModel):
    """Compact audit record for one membership projected from a frozen partition."""

    assignment_id: NonEmptyString
    run_id: NonEmptyString
    event_id: NonEmptyString
    resulting_package_id: NonEmptyString
    membership_relation: MembershipRelation
    supporting_proposal_ids: list[NonEmptyString] = Field(default_factory=list)
    partition_hash: NonEmptyString
    reason: NonEmptyString
    version: int = Field(default=1, ge=1)


class CrossDocumentResult(StrictModel):
    run_id: NonEmptyString
    processing_key: NonEmptyString
    message_id: NonEmptyString
    status: CrossDocumentStatus
    atomic_events: list[AtomicEvent]
    packages: list[EventPackage]
    atomic_assignments: list[AtomicAssignmentRecord]
    package_assignments: list[PackageAssignmentRecord]
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
