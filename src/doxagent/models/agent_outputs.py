"""Structured outputs for real O1/A1/A2 workflow execution."""

from typing import Any, Literal

from pydantic import Field

from doxagent.models.blackboard import (
    BlackboardPatch,
    BlackboardTarget,
    Delegation,
    EvidenceRef,
    Objection,
)
from doxagent.models.common import AgentName, EvidenceSourceType
from doxagent.models.contracts import ContractModel, ToolCallSummary
from doxagent.models.documents import ExpectationUnitDocument, ResearchSection
from doxagent.models.ids import NonEmptyStr, new_id


class ExpectationShell(ContractModel):
    """Partial expectation draft produced before detail completion."""

    expectation_id: NonEmptyStr
    expectation_name: NonEmptyStr
    direction: Literal["bullish", "bearish", "neutral", "risk"]
    why_it_matters: NonEmptyStr
    market_view: ResearchSection
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    unknowns: list[NonEmptyStr] = Field(default_factory=list)
    rationale: NonEmptyStr


class ExpectationShellConstructionResult(ContractModel):
    """O1 output for the construction phase: I/II only, no stable patches."""

    shells: list[ExpectationShell] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    delegations: list[Delegation] = Field(default_factory=list)
    unknowns: list[NonEmptyStr] = Field(default_factory=list)
    rationale: NonEmptyStr


class ObjectionResolutionDecision(ContractModel):
    """Structured O1 decision for closing or rebutting one objection."""

    objection_id: NonEmptyStr
    decision: Literal["resolved", "accepted", "partially_accepted", "rejected"]
    resolution_note: NonEmptyStr
    changed_paths: list[NonEmptyStr] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class ExpectationConstructionResult(ContractModel):
    """O1 output for full expectation patches and objection revisions."""

    proposed_patches: list[BlackboardPatch] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    delegations: list[Delegation] = Field(default_factory=list)
    unknowns: list[NonEmptyStr] = Field(default_factory=list)
    rationale: NonEmptyStr
    resolved_objection_ids: list[NonEmptyStr] = Field(default_factory=list)
    accepted_objection_ids: list[NonEmptyStr] = Field(default_factory=list)
    partially_accepted_objection_ids: list[NonEmptyStr] = Field(default_factory=list)
    rejected_objection_ids: list[NonEmptyStr] = Field(default_factory=list)
    objection_resolutions: list[ObjectionResolutionDecision] = Field(default_factory=list)


class ExpectationDetailResult(ExpectationConstructionResult):
    """O1 output for one completed expectation-unit patch."""


class ExpectationDetailCandidateResult(ContractModel):
    """O1 output for one complete expectation-unit candidate, without patches."""

    candidate: ExpectationUnitDocument
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    delegations: list[Delegation] = Field(default_factory=list)
    unknowns: list[NonEmptyStr] = Field(default_factory=list)
    rationale: NonEmptyStr


class Document2ResolutionDecisionOutput(ContractModel):
    """O1 resolver decision item before transaction-layer application."""

    objection_id: NonEmptyStr | None = None
    finding_id: NonEmptyStr | None = None
    decision: Literal["resolved", "accepted", "partially_accepted", "rejected", "deferred"]
    resolution_note: NonEmptyStr
    changed_paths: list[NonEmptyStr] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class Document2ResolutionPlanOutput(ContractModel):
    """O1 resolver output: advisory plan only, never BlackboardPatch."""

    plan_id: NonEmptyStr | None = None
    expectation_id: NonEmptyStr
    decision: Literal[
        "resolved",
        "accepted",
        "partially_accepted",
        "rejected",
        "deferred",
    ] = "deferred"
    decisions: list[Document2ResolutionDecisionOutput] = Field(default_factory=list)
    target_finding_ids: list[NonEmptyStr] = Field(default_factory=list)
    proposed_revision: dict[str, Any] | None = None
    revised_candidate: ExpectationUnitDocument | None = None
    evidence_requests: list[NonEmptyStr] = Field(default_factory=list)
    unresolved_finding_ids: list[NonEmptyStr] = Field(default_factory=list)
    unresolved_reason: NonEmptyStr | None = None
    rationale: NonEmptyStr


class DoxAtlasAuditFinding(ContractModel):
    field_path: NonEmptyStr
    status: Literal[
        "supported",
        "unsupported",
        "needs_more_evidence",
        "contradicted",
        "not_checked",
    ]
    rationale: NonEmptyStr
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class DoxAtlasAuditResult(ContractModel):
    """A1 output for field-level DoxAtlas audit."""

    verdict: Literal["pass", "pass_with_warnings", "needs_revision", "blocked"] = "pass"
    revision_required: bool = False
    findings: list[DoxAtlasAuditFinding] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    objections: list[Objection] = Field(default_factory=list)
    delegations: list[Delegation] = Field(default_factory=list)
    unknowns: list[NonEmptyStr] = Field(default_factory=list)
    rationale: NonEmptyStr


class ExpectationFieldReviewFinding(ContractModel):
    """Field-level review finding from C1/C3/O4 expectation reviewers."""

    field_path: NonEmptyStr
    status: Literal["supported", "unsupported", "needs_more_evidence", "contradicted"]
    rationale: NonEmptyStr
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class ExpectationFieldReviewResult(ContractModel):
    """Generic non-DoxAtlas expectation-field review output."""

    findings: list[ExpectationFieldReviewFinding] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    objections: list[Objection] = Field(default_factory=list)
    delegations: list[Delegation] = Field(default_factory=list)
    unknowns: list[NonEmptyStr] = Field(default_factory=list)
    rationale: NonEmptyStr


class DelegatedRetrievalRequest(ContractModel):
    """Standard request shape for routing information gaps to A2."""

    requester_agent: AgentName
    question: NonEmptyStr
    blocking_scope: BlackboardTarget
    purpose: Literal["fact_check", "delegated_retrieval"] = "delegated_retrieval"
    required_evidence: list[EvidenceSourceType] = Field(
        default_factory=lambda: [EvidenceSourceType.EXTERNAL_REPORT]
    )
    completion_criteria: NonEmptyStr = (
        "Return a concise public-search answer or record the gap as inconclusive."
    )
    query_hints: list[NonEmptyStr] = Field(default_factory=list)


class DelegatedRetrievalResult(ContractModel):
    """A2 output for concise public-search and verification tasks."""

    answer: NonEmptyStr
    claim_verdict: Literal[
        "supported",
        "unsupported",
        "partially_supported",
        "inconclusive",
        "unknown",
        "not_applicable",
    ] = "not_applicable"
    retrieval_summary: NonEmptyStr
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    source_refs: list[EvidenceRef] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    unknowns: list[NonEmptyStr] = Field(default_factory=list)
    query_log: list[NonEmptyStr] = Field(default_factory=list)
    tool_calls: list[ToolCallSummary] = Field(default_factory=list)
    delegation_id: NonEmptyStr | None = None
    can_complete_delegation: bool = False


def create_a2_retrieval_delegation(
    request: DelegatedRetrievalRequest,
    *,
    delegation_id: str | None = None,
) -> Delegation:
    """Create a standard blocking delegation to A2's search verification path."""

    return Delegation(
        delegation_id=delegation_id or new_id("delegation"),
        requester_agent=request.requester_agent,
        target_agent=AgentName.A2_FACT_CHECK,
        question=request.question,
        required_evidence=list(request.required_evidence),
        blocking_scope=request.blocking_scope,
    )
