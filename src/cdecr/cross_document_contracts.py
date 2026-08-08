"""Strict contracts for incremental Atomic Event and Event Package processing."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

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
    ExternalRelationType,
    MembershipRelation,
    NonEmptyString,
    PackageAction,
    PackageAssignmentRelation,
    PackageBoundaryAction,
    PackageFamily,
    PackageKind,
    PackageMergeRelation,
    PackageQualityState,
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
    CANONICAL_ARTIFACT = "CANONICAL_ARTIFACT"
    PACKAGE_KIND_FAMILY = "PACKAGE_KIND_FAMILY"
    SHARED_ATOMIC_EVENT = "SHARED_ATOMIC_EVENT"
    MEMBER_IDENTITY = "MEMBER_IDENTITY"
    MEMBER_EMBEDDING = "MEMBER_EMBEDDING"
    INCUMBENT_MEMBERSHIP = "INCUMBENT_MEMBERSHIP"
    PARENT_CONTEXT = "PARENT_CONTEXT"
    SAME_SOURCE_MEMBER = "SAME_SOURCE_MEMBER"
    LIFECYCLE_COMPATIBILITY = "LIFECYCLE_COMPATIBILITY"
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
    PACKAGE_KIND = "PACKAGE_KIND"
    PACKAGE_FAMILY = "PACKAGE_FAMILY"
    PACKAGE_ANCHOR = "PACKAGE_ANCHOR"
    PACKAGE_ARTIFACT = "PACKAGE_ARTIFACT"
    PACKAGE_PERIOD = "PACKAGE_PERIOD"
    PACKAGE_MATTER = "PACKAGE_MATTER"


class HardCannotLinkMode(StrEnum):
    ENFORCE = "enforce"
    SHADOW = "shadow"
    OFF = "off"


class PackageConflictMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"
    ENFORCE = "enforce"


class CrossDocumentStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
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


class PackageCandidate(StrictModel):
    package: EventPackage
    recall_routes: list[RecallRoute] = Field(min_length=1)
    recall_score: Confidence
    embedding_similarity: float | None = Field(default=None, ge=-1.0, le=1.0)
    member_embedding_similarity: float | None = Field(default=None, ge=-1.0, le=1.0)
    hard_conflicts: list[HardConflictCode] = Field(default_factory=list)


class PackageSeed(StrictModel):
    package_kind: PackageKind
    package_family: PackageFamily
    canonical_title: NonEmptyString
    anchor_entities: list[NonEmptyString]
    local_anchor_hint: str | None = None
    local_anchor_hints: list[NonEmptyString] = Field(default_factory=list)
    package_anchor_ids: list[NonEmptyString] = Field(default_factory=list)
    artifact_candidate_ids: list[NonEmptyString] = Field(default_factory=list)
    anchor_conflict: bool = False
    anchor_artifact_id: str | None = None
    anchor_period_id: str | None = None
    time_range: PackageTimeRange
    membership_relation: MembershipRelation


class PackageAnchorView(StrictModel):
    canonical_id: NonEmptyString
    external_id: str | None = None
    trust: Literal["KB_EXTERNAL", "PROVISIONAL"]
    canonical_text: NonEmptyString


class SurfaceCanonicalEvidence(StrictModel):
    surfaces: list[NonEmptyString] = Field(max_length=3)
    canonical_ids: list[NonEmptyString] = Field(max_length=3)


class SurfaceParticipantEvidence(SurfaceCanonicalEvidence):
    role: NonEmptyString


class AtomicSurfaceEvidence(StrictModel):
    participants: list[SurfaceParticipantEvidence] | None = None
    artifacts: SurfaceCanonicalEvidence | None = None
    periods: SurfaceCanonicalEvidence | None = None
    object_locations: SurfaceCanonicalEvidence | None = None


class PackageRepresentativeMember(StrictModel):
    event_id: NonEmptyString
    canonical_proposition: NonEmptyString
    event_family: NonEmptyString
    identity_profile: dict[str, object]
    time: dict[str, object]
    assertion_state: NonEmptyString
    surface_evidence: AtomicSurfaceEvidence
    source_ids: list[NonEmptyString] = Field(max_length=3)


class PackageRetrievalSignals(StrictModel):
    routes: list[RecallRoute]
    embedding_similarity: float | None = Field(default=None, ge=-1.0, le=1.0)


class PackageDecisionView(StrictModel):
    package: EventPackage
    package_anchors: list[PackageAnchorView]
    representative_members: list[PackageRepresentativeMember] = Field(max_length=5)
    retrieval_signals: PackageRetrievalSignals


class PackageCandidateAssessment(StrictModel):
    candidate_package_id: NonEmptyString
    relation: PackageAssignmentRelation
    membership_relation: MembershipRelation | None = None
    external_relation: ExternalRelationType | None = None
    reason: NonEmptyString

    @model_validator(mode="after")
    def validate_relation_detail(self) -> PackageCandidateAssessment:
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


class PackagePairDecision(StrictModel):
    """Compatibility DTO for the current pair-wise N11 executor.

    The newer joint package-assignment contract remains the production-facing
    target.  Keeping this DTO separate prevents the legacy executor from
    changing that contract while it is migrated.
    """

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


class PackagePairDecisionBatch(StrictModel):
    decisions: list[PackagePairDecision]

    @model_validator(mode="after")
    def unique_pairs(self) -> PackagePairDecisionBatch:
        pairs = [(item.event_id, item.candidate_package_id) for item in self.decisions]
        if len(pairs) != len(set(pairs)):
            raise ValueError("package decisions must be unique per event/candidate pair")
        return self


class PackageAssignmentDecision(StrictModel):
    event_id: NonEmptyString
    candidate_assessments: list[PackageCandidateAssessment]
    ranked_member_package_ids: list[NonEmptyString]
    selected_member_package_id: str | None = None
    selection_reason: str | None = None

    @model_validator(mode="after")
    def validate_ranking_and_selection(self) -> PackageAssignmentDecision:
        assessment_ids = [item.candidate_package_id for item in self.candidate_assessments]
        if len(assessment_ids) != len(set(assessment_ids)):
            raise ValueError("candidate assessments must be unique")
        member_ids = {
            item.candidate_package_id
            for item in self.candidate_assessments
            if item.relation is PackageAssignmentRelation.MEMBER
        }
        if len(self.ranked_member_package_ids) != len(set(self.ranked_member_package_ids)):
            raise ValueError("ranked_member_package_ids must be unique")
        if set(self.ranked_member_package_ids) != member_ids:
            raise ValueError(
                "ranking must contain every MEMBER candidate and only MEMBER candidates"
            )
        if member_ids:
            if self.selected_member_package_id is None:
                raise ValueError("MEMBER candidates require one selected target")
            if self.selected_member_package_id != self.ranked_member_package_ids[0]:
                raise ValueError("selected target must be the first ranked MEMBER")
            if not self.selection_reason:
                raise ValueError("selected target requires selection_reason")
        elif self.selected_member_package_id is not None:
            raise ValueError("selected target must be null without MEMBER candidates")
        elif self.selection_reason is not None:
            raise ValueError("selection_reason must be null without MEMBER candidates")
        return self


class PackageDecisionBatch(StrictModel):
    decisions: list[PackageAssignmentDecision]

    @model_validator(mode="after")
    def unique_events(self) -> PackageDecisionBatch:
        event_ids = [item.event_id for item in self.decisions]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("package assignment decisions must be unique per event")
        return self


class PackageAssignmentRecord(StrictModel):
    assignment_id: NonEmptyString
    run_id: NonEmptyString
    event_id: NonEmptyString
    candidate_package_id: str | None = None
    resulting_package_id: str | None = None
    action: PackageAction
    relation: PackageAssignmentRelation | None = None
    candidate_assessments: list[PackageCandidateAssessment] = Field(default_factory=list)
    ranked_member_package_ids: list[NonEmptyString] = Field(default_factory=list)
    selected_member_package_id: str | None = None
    selection_reason: str | None = None
    package_assignment_key: NonEmptyString = "legacy"
    package_seed_hash: NonEmptyString = "legacy"
    package_field_links_hash: NonEmptyString = "legacy"
    assignment_policy_version: NonEmptyString = "legacy"
    reason: NonEmptyString
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_action_target(self) -> PackageAssignmentRecord:
        if self.resulting_package_id is None:
            raise ValueError("package assignment requires resulting_package_id")
        if (
            self.selected_member_package_id is not None
            and self.selected_member_package_id != self.candidate_package_id
        ):
            raise ValueError("selected member must equal candidate_package_id")
        return self


class PackagePairMergeDecision(StrictModel):
    source_package_id: NonEmptyString
    target_package_id: NonEmptyString
    relation: PackageMergeRelation
    reason: NonEmptyString


class PackagePairEvaluation(StrictModel):
    evaluation_id: NonEmptyString
    run_id: NonEmptyString
    left_package_id: NonEmptyString
    right_package_id: NonEmptyString
    left_version: int = Field(ge=1)
    right_version: int = Field(ge=1)
    left_profile_hash: NonEmptyString
    right_profile_hash: NonEmptyString
    routes: list[RecallRoute]
    relation: PackageMergeRelation
    decision_source: Literal["M0", "GUARD", "M3", "REUSED"]
    deterministic_rule: str | None = None
    membership_changed: bool = False


class PackagePairMergeWireDecision(StrictModel):
    pair_id: NonEmptyString
    relation: PackageMergeRelation
    reason: NonEmptyString


class PackageMergeWireDecisionBatch(StrictModel):
    decisions: list[PackagePairMergeWireDecision]

    @model_validator(mode="after")
    def unique_pairs(self) -> PackageMergeWireDecisionBatch:
        pair_ids = [item.pair_id for item in self.decisions]
        if len(pair_ids) != len(set(pair_ids)):
            raise ValueError("package merge wire decisions must be unique per pair")
        return self


class PackageLatePairDecision(StrictModel):
    pair_id: NonEmptyString
    relation: Literal["SAME_PARENT", "DIFFERENT_PARENT", "UNCERTAIN"]


class PackageLateDecisionBatch(StrictModel):
    decisions: list[PackageLatePairDecision]

    @model_validator(mode="after")
    def unique_pairs(self) -> PackageLateDecisionBatch:
        pair_ids = [item.pair_id for item in self.decisions]
        if len(pair_ids) != len(set(pair_ids)):
            raise ValueError("package late decisions must be unique per pair")
        return self


class PackagePairBoundary(StrictModel):
    shared_artifact_ids: list[NonEmptyString] = Field(default_factory=list)
    shared_anchor_ids: list[NonEmptyString] = Field(default_factory=list)
    conflicting_artifact_ids: list[NonEmptyString] = Field(default_factory=list)
    shared_parent_context: bool = False
    shared_source_member: bool = False
    member_identity_support: bool = False
    time_support: bool = False
    issuer_conflict: bool = False
    reaction_boundary: bool = False
    analyst_boundary: bool = False
    period_boundary: bool = False
    session_boundary: bool = False
    instrument_conflict: bool = False
    market_measure_conflict: bool = False
    object_scope_difference: bool = False
    left_member_count: int = Field(ge=0)
    right_member_count: int = Field(ge=0)

    @property
    def hard_blocked(self) -> bool:
        return bool(
            self.conflicting_artifact_ids
            or self.issuer_conflict
            or self.reaction_boundary
            or self.analyst_boundary
            or self.period_boundary
            or self.session_boundary
            or self.instrument_conflict
            or self.market_measure_conflict
        )

    def compact_signals(
        self, *, include_object_scope: bool = True
    ) -> dict[str, list[str]]:
        """Return only positive pair-boundary signals for model payloads."""

        same: list[str] = []
        different: list[str] = []
        for matched, label in (
            (bool(self.shared_artifact_ids), "artifact"),
            (bool(self.shared_anchor_ids), "parent"),
            (self.shared_parent_context, "parent_context"),
            (self.shared_source_member, "source"),
            (self.member_identity_support, "member_identity"),
            (self.time_support, "time"),
        ):
            if matched:
                same.append(label)
        for conflict, label in (
            (bool(self.conflicting_artifact_ids), "artifact"),
            (self.issuer_conflict, "issuer"),
            (self.reaction_boundary, "reaction_parent"),
            (self.analyst_boundary, "analyst_institution"),
            (self.period_boundary, "period"),
            (self.session_boundary, "market_session"),
            (self.instrument_conflict, "instrument"),
            (self.market_measure_conflict, "market_measure"),
            (self.object_scope_difference and include_object_scope, "object_scope"),
        ):
            if conflict:
                different.append(label)
        output: dict[str, list[str]] = {}
        if same:
            output["same"] = same
        if different:
            output["diff"] = different
        return output

    @property
    def independent_positive_count(self) -> int:
        return sum(
            (
                bool(self.shared_artifact_ids),
                bool(self.shared_anchor_ids),
                self.shared_parent_context,
                self.shared_source_member,
                self.member_identity_support,
                self.time_support,
            )
        )


class PackageMergeDecisionBatch(StrictModel):
    decisions: list[PackagePairMergeDecision]

    @model_validator(mode="after")
    def unique_pairs(self) -> PackageMergeDecisionBatch:
        pairs = [
            tuple(sorted((item.source_package_id, item.target_package_id)))
            for item in self.decisions
        ]
        if len(pairs) != len(set(pairs)):
            raise ValueError("package merge decisions must be unique per pair")
        return self


class PackageMergePlan(StrictModel):
    plan_id: NonEmptyString
    target_package_id: NonEmptyString
    source_package_ids: list[NonEmptyString] = Field(min_length=1)
    decision_ids: list[NonEmptyString] = Field(min_length=1)
    reason: NonEmptyString

    @model_validator(mode="after")
    def validate_plan(self) -> PackageMergePlan:
        if len(self.source_package_ids) != len(set(self.source_package_ids)):
            raise ValueError("merge sources must be unique")
        if self.target_package_id in self.source_package_ids:
            raise ValueError("merge target cannot also be a source")
        if len(self.decision_ids) != len(set(self.decision_ids)):
            raise ValueError("merge decision ids must be unique")
        return self


class PackageBoundaryFinding(StrictModel):
    package_id: NonEmptyString
    quality_state: PackageQualityState
    severity: Literal["WARNING", "REVIEW_REQUIRED", "BLOCKING_CONFLICT"] | None = None
    reasons: list[NonEmptyString]
    actions: list[PackageBoundaryAction]


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
