"""Structured outputs for real O1/A1/A2 workflow execution."""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from doxagent.models.blackboard import (
    BlackboardPatch,
    BlackboardTarget,
    Delegation,
    Objection,
)
from doxagent.models.common import AgentName, ExpectationDirection
from doxagent.models.contracts import ContractModel, ToolCallSummary
from doxagent.models.documents import (
    EventMonitoringDirection,
    ExpectationUnitDocument,
    RealizedFact,
    ResearchSection,
    VariableStatus,
)
from doxagent.models.ids import NonEmptyStr, new_id


class ExpectationShell(ContractModel):
    """Partial expectation draft produced before detail completion."""

    expectation_id: NonEmptyStr
    expectation_name: NonEmptyStr
    direction: Literal["bullish", "bearish", "neutral", "risk"]
    why_it_matters: NonEmptyStr
    market_view: ResearchSection
    unknowns: list[NonEmptyStr] = Field(default_factory=list)
    rationale: NonEmptyStr


class ExpectationShellConstructionResult(ContractModel):
    """O1 output for the construction phase: I/II only, no stable patches."""

    shells: list[ExpectationShell] = Field(default_factory=list)
    delegations: list[Delegation] = Field(default_factory=list)
    unknowns: list[NonEmptyStr] = Field(default_factory=list)
    rationale: NonEmptyStr


class ObjectionResolutionDecision(ContractModel):
    """Structured O1 decision for closing or rebutting one objection."""

    objection_id: NonEmptyStr
    decision: Literal["resolved", "accepted", "partially_accepted", "rejected"]
    resolution_note: NonEmptyStr
    changed_paths: list[NonEmptyStr] = Field(default_factory=list)


class ExpectationConstructionResult(ContractModel):
    """O1 output for full expectation patches and objection revisions."""

    proposed_patches: list[BlackboardPatch] = Field(default_factory=list)
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

    candidate: "ExpectationUnitCandidateBody"
    delegations: list[Delegation] = Field(default_factory=list)
    unknowns: list[NonEmptyStr] = Field(default_factory=list)
    rationale: NonEmptyStr


class ExpectationUnitCandidateBody(ContractModel):
    """Model-authored business body; workflow supplies document identity and timestamps."""

    @model_validator(mode="before")
    @classmethod
    def _discard_runtime_identity(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for key in ("document_id", "document_type", "ticker", "created_at", "updated_at"):
            normalized.pop(key, None)
        return normalized

    expectation_id: NonEmptyStr
    expectation_name: NonEmptyStr
    direction: ExpectationDirection
    why_it_matters: NonEmptyStr
    market_view: ResearchSection
    realized_facts: list[RealizedFact] = Field(min_length=1)
    realized_facts_summary: NonEmptyStr
    key_variables: list[VariableStatus] = Field(min_length=1)
    event_monitoring_direction: EventMonitoringDirection

    def to_document(
        self,
        *,
        document_id: str,
        ticker: str,
        created_at: datetime,
        updated_at: datetime | None = None,
    ) -> ExpectationUnitDocument:
        return ExpectationUnitDocument(
            document_id=document_id,
            ticker=ticker,
            created_at=created_at,
            updated_at=updated_at,
            **self.model_dump(mode="python"),
        )


class Document2ResolutionDecisionOutput(ContractModel):
    """O1 resolver decision item before transaction-layer application."""

    objection_id: NonEmptyStr | None = None
    finding_id: NonEmptyStr | None = None
    decision: Literal["resolved", "accepted", "partially_accepted", "rejected", "deferred"]
    resolution_note: NonEmptyStr
    changed_paths: list[NonEmptyStr] = Field(default_factory=list)


class Document2FieldRepairDecisionOutput(ContractModel):
    """The sole model-authored decision source for one routed review finding."""

    finding_id: NonEmptyStr
    decision: Literal["resolved", "accepted", "partially_accepted", "rejected", "deferred"]
    resolution_note: NonEmptyStr
    changed_paths: list[NonEmptyStr] = Field(default_factory=list)


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
    unresolved_finding_ids: list[NonEmptyStr] = Field(default_factory=list)
    unresolved_reason: NonEmptyStr | None = None
    rationale: NonEmptyStr


class Document2FieldRepairResultOutput(ContractModel):
    """O1 resolver output for exactly one Document2 field repair task."""

    task_id: NonEmptyStr
    expectation_id: NonEmptyStr
    field_family: Literal[
        "realized_facts",
        "key_variables",
        "event_monitoring_direction",
        "market_view",
        "market_evidence",
        "cross_field",
    ]
    decisions: list[Document2FieldRepairDecisionOutput] = Field(min_length=1)
    realized_facts: list[RealizedFact] | None = Field(default=None, min_length=1)
    key_variables: list[VariableStatus] | None = Field(default=None, min_length=1)
    event_monitoring_direction: EventMonitoringDirection | None = None
    market_view: ResearchSection | None = None
    market_evidence: ResearchSection | None = None
    revised_candidate: ExpectationUnitCandidateBody | None = None
    unresolved_reason: NonEmptyStr | None = None
    rationale: NonEmptyStr

    @model_validator(mode="after")
    def decision_and_field_shape_match(self) -> "Document2FieldRepairResultOutput":
        typed = {
            "realized_facts": self.realized_facts,
            "key_variables": self.key_variables,
            "event_monitoring_direction": self.event_monitoring_direction,
            "market_view": self.market_view,
            "market_evidence": self.market_evidence,
        }
        accepted = any(
            item.decision in {"accepted", "partially_accepted"} for item in self.decisions
        )
        present = [key for key, value in typed.items() if value is not None]
        if self.field_family == "cross_field":
            if present:
                raise ValueError("cross_field repair must not return typed field updates")
            if accepted and self.revised_candidate is None:
                raise ValueError("cross_field accepted repair requires revised_candidate")
            return self
        if self.revised_candidate is not None:
            raise ValueError("single-field repair must not return revised_candidate")
        if accepted and typed.get(self.field_family) is None:
            raise ValueError("single-field accepted repair requires its typed field update")
        if present and present != [self.field_family]:
            raise ValueError("typed field update does not match field_family")
        return self


class DoxAtlasAuditFinding(ContractModel):
    expectation_id: NonEmptyStr | None = None
    field_path: NonEmptyStr
    status: Literal[
        "supported",
        "unsupported",
        "needs_more_evidence",
        "contradicted",
        "not_checked",
    ]
    rationale: NonEmptyStr
    recommended_statement: NonEmptyStr | None = None


class DoxAtlasAuditResult(ContractModel):
    """A1 output for field-level DoxAtlas audit."""

    verdict: Literal["pass", "pass_with_warnings", "needs_revision", "blocked"] = "pass"
    revision_required: bool = False
    findings: list[DoxAtlasAuditFinding] = Field(default_factory=list)
    objections: list[Objection] = Field(default_factory=list)
    delegations: list[Delegation] = Field(default_factory=list)
    unknowns: list[NonEmptyStr] = Field(default_factory=list)
    rationale: NonEmptyStr


class ExpectationFieldReviewFinding(ContractModel):
    """Field-level review finding from C1/C3/O4 expectation reviewers."""

    expectation_id: NonEmptyStr | None = None
    field_path: NonEmptyStr
    target_paths: list[NonEmptyStr] = Field(default_factory=list)
    status: Literal["supported", "unsupported", "needs_more_evidence", "contradicted"]
    rationale: NonEmptyStr
    recommended_statement: NonEmptyStr | None = None


class ExpectationFieldReviewResult(ContractModel):
    """Generic non-DoxAtlas expectation-field review output."""

    findings: list[ExpectationFieldReviewFinding] = Field(default_factory=list)
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
        blocking_scope=request.blocking_scope,
    )
