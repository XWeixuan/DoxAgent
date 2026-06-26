"""Canonical Document 2 transaction contracts.

These models define the target protocol for the Document2 transaction layer.
They are intentionally not wired into the main workflow yet; Step 3 only creates
the typed boundary that later steps can switch to incrementally.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from doxagent.models import (
    AgentName,
    EvidenceRef,
    ExpectationUnitDocument,
    NonEmptyStr,
    new_id,
)

EvidenceAssessmentStatus = Literal[
    "sufficient",
    "insufficient",
    "unavailable",
    "stale",
    "contradictory",
]
Document2FindingSeverity = Literal["info", "warning", "blocking"]
Document2ResolutionDecision = Literal[
    "resolved",
    "accepted",
    "partially_accepted",
    "rejected",
    "deferred",
]
Document2RevisionSource = Literal[
    "candidate_generation",
    "resolution_plan",
]
Document2TransactionType = Literal[
    "candidate_generation",
    "construction_resolution",
    "review",
    "resolution",
    "promotion",
]
Document2TransactionStatus = Literal["accepted", "rejected", "failed"]
Document2PromotionBlockerType = Literal[
    "candidate_not_ready",
    "blocking_finding",
    "evidence_insufficient",
    "placeholder_text",
    "schema_validation",
]


class Document2ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ExpectationUnitCandidate(Document2ContractModel):
    candidate_id: NonEmptyStr = Field(default_factory=lambda: new_id("d2cand"))
    document: ExpectationUnitDocument
    source_agent: AgentName = AgentName.O1_EXPECTATION_OWNER
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    unknowns: list[NonEmptyStr] = Field(default_factory=list)
    rationale: NonEmptyStr

    @model_validator(mode="after")
    def evidence_defaults_to_document_refs(self) -> ExpectationUnitCandidate:
        if self.evidence_refs:
            return self
        refs = list(self.document.market_view.evidence_refs)
        for fact in self.document.realized_facts:
            refs.extend(fact.evidence_refs)
            refs.extend(fact.price_reaction.evidence_refs)
        for variable in self.document.key_variables:
            refs.extend(variable.evidence_refs)
        self.evidence_refs = _dedupe_evidence_refs(refs)
        return self


class EvidenceAssessment(Document2ContractModel):
    assessment_id: NonEmptyStr = Field(default_factory=lambda: new_id("d2evidence"))
    target_path: NonEmptyStr
    status: EvidenceAssessmentStatus
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    reason: NonEmptyStr
    blocks_promotion: bool = False

    @model_validator(mode="after")
    def non_sufficient_evidence_blocks_by_default(self) -> EvidenceAssessment:
        if self.status != "sufficient":
            self.blocks_promotion = True
        return self


class Document2ReviewFinding(Document2ContractModel):
    finding_id: NonEmptyStr = Field(default_factory=lambda: new_id("d2finding"))
    reviewer_agent: AgentName
    expectation_id: NonEmptyStr
    target_path: NonEmptyStr
    severity: Document2FindingSeverity
    reason: NonEmptyStr
    evidence_assessments: list[EvidenceAssessment] = Field(default_factory=list)
    supplemental_evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    supplemental_context: list[NonEmptyStr] = Field(default_factory=list)
    source_objection_id: NonEmptyStr | None = None
    blocks_promotion: bool = False

    @model_validator(mode="after")
    def blocking_severity_blocks_promotion(self) -> Document2ReviewFinding:
        if self.severity == "blocking" or any(
            assessment.blocks_promotion for assessment in self.evidence_assessments
        ):
            self.blocks_promotion = True
        return self


class Document2Revision(Document2ContractModel):
    revision_id: NonEmptyStr = Field(default_factory=lambda: new_id("d2rev"))
    expectation_id: NonEmptyStr
    before: ExpectationUnitDocument | None = None
    after: ExpectationUnitDocument
    source: Document2RevisionSource
    source_patch_id: NonEmptyStr | None = None
    rationale: NonEmptyStr
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    changed_paths: list[NonEmptyStr] = Field(default_factory=list)
    review_finding_ids: list[NonEmptyStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def expectation_ids_must_match(self) -> Document2Revision:
        if self.before is not None and self.before.expectation_id != self.after.expectation_id:
            raise ValueError("before and after expectation_id must match")
        if self.expectation_id != self.after.expectation_id:
            raise ValueError("revision expectation_id must match after document")
        if not self.evidence_refs:
            self.evidence_refs = _dedupe_evidence_refs(self.after.market_view.evidence_refs)
        return self


class Document2ResolutionDecisionRecord(Document2ContractModel):
    objection_id: NonEmptyStr | None = None
    finding_id: NonEmptyStr | None = None
    decision: Document2ResolutionDecision
    resolution_note: NonEmptyStr
    changed_paths: list[NonEmptyStr] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class Document2ResolutionPlan(Document2ContractModel):
    plan_id: NonEmptyStr = Field(default_factory=lambda: new_id("d2plan"))
    expectation_id: NonEmptyStr
    decision: Document2ResolutionDecision = "deferred"
    decisions: list[Document2ResolutionDecisionRecord] = Field(default_factory=list)
    target_finding_ids: list[NonEmptyStr] = Field(default_factory=list)
    proposed_revision: Document2Revision | None = None
    revised_candidate: ExpectationUnitDocument | None = None
    evidence_requests: list[NonEmptyStr] = Field(default_factory=list)
    unresolved_finding_ids: list[NonEmptyStr] = Field(default_factory=list)
    unresolved_reason: NonEmptyStr | None = None
    rationale: NonEmptyStr

    @model_validator(mode="after")
    def accepted_decisions_need_revision(self) -> Document2ResolutionPlan:
        accepted_decisions = {self.decision}
        accepted_decisions.update(item.decision for item in self.decisions)
        if accepted_decisions.intersection({"accepted", "partially_accepted"}) and (
            self.proposed_revision is None and self.revised_candidate is None
        ):
            raise ValueError(
                "accepted resolution decisions require a proposed_revision or revised_candidate"
            )
        if self.proposed_revision is not None and (
            self.proposed_revision.expectation_id != self.expectation_id
        ):
            raise ValueError("proposed_revision expectation_id must match resolution plan")
        if self.revised_candidate is not None and (
            self.revised_candidate.expectation_id != self.expectation_id
        ):
            raise ValueError("revised_candidate expectation_id must match resolution plan")
        return self


class Document2PromotionCandidate(Document2ContractModel):
    promotion_candidate_id: NonEmptyStr = Field(default_factory=lambda: new_id("d2promo"))
    document: ExpectationUnitDocument
    source_revision_id: NonEmptyStr | None = None
    evidence_assessments: list[EvidenceAssessment] = Field(default_factory=list)
    blocking_finding_ids: list[NonEmptyStr] = Field(default_factory=list)
    ready_for_promotion: bool
    rationale: NonEmptyStr

    @model_validator(mode="after")
    def ready_candidates_cannot_have_blockers(self) -> Document2PromotionCandidate:
        if self.ready_for_promotion and self.blocking_finding_ids:
            raise ValueError("promotion-ready candidate cannot carry blocking finding ids")
        if self.ready_for_promotion and any(
            assessment.blocks_promotion for assessment in self.evidence_assessments
        ):
            raise ValueError("promotion-ready candidate cannot carry blocking assessments")
        return self


class Document2PromotionBlocker(Document2ContractModel):
    blocker_type: Document2PromotionBlockerType
    target_path: NonEmptyStr
    reason: NonEmptyStr
    finding_id: NonEmptyStr | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class Document2TransactionAudit(Document2ContractModel):
    audit_id: NonEmptyStr = Field(default_factory=lambda: new_id("d2audit"))
    transaction_type: Document2TransactionType
    status: Document2TransactionStatus
    expectation_id: NonEmptyStr | None = None
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    notes: list[NonEmptyStr] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def _dedupe_evidence_refs(refs: list[EvidenceRef]) -> list[EvidenceRef]:
    deduped: dict[str, EvidenceRef] = {}
    for ref in refs:
        deduped.setdefault(ref.evidence_id, ref)
    return list(deduped.values())
