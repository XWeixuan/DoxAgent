"""Blackboard contract models.

These models describe state-change payloads only. They do not implement the
Blackboard Service or any persistence behavior.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from doxagent.models.common import (
    AgentName,
    DelegationStatus,
    DocumentType,
    EvidenceSourceType,
    ObjectionSeverity,
    ObjectionStatus,
    PatchOperation,
    ValidationStatus,
)
from doxagent.models.ids import NonEmptyStr


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class EvidenceRef(ContractModel):
    evidence_id: NonEmptyStr
    source_type: EvidenceSourceType
    source_id: NonEmptyStr
    title: NonEmptyStr
    summary: NonEmptyStr
    retrieval_metadata: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    citation_scope: NonEmptyStr


class BlackboardTarget(ContractModel):
    document_type: DocumentType
    field_path: NonEmptyStr
    ticker: NonEmptyStr | None = None
    document_id: NonEmptyStr | None = None
    expectation_id: NonEmptyStr | None = None


class BlackboardPatch(ContractModel):
    patch_id: NonEmptyStr
    target: BlackboardTarget
    operation: PatchOperation
    before: Any | None = None
    after: Any | None = None
    rationale: NonEmptyStr
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    author_agent: AgentName
    validation_status: ValidationStatus = ValidationStatus.PENDING


class Objection(ContractModel):
    objection_id: NonEmptyStr
    source_agent: AgentName
    target: BlackboardTarget
    severity: ObjectionSeverity
    reason: NonEmptyStr
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    status: ObjectionStatus = ObjectionStatus.OPEN
    resolution_note: NonEmptyStr | None = None

    @property
    def is_unresolved(self) -> bool:
        return self.status in {ObjectionStatus.OPEN, ObjectionStatus.UNRESOLVED}


class Delegation(ContractModel):
    delegation_id: NonEmptyStr
    requester_agent: AgentName
    target_agent: AgentName
    question: NonEmptyStr
    required_evidence: list[EvidenceSourceType] = Field(default_factory=list)
    blocking_scope: BlackboardTarget
    status: DelegationStatus = DelegationStatus.OPEN
    result_summary: NonEmptyStr | None = None

    @property
    def is_blocking(self) -> bool:
        return self.status in {DelegationStatus.OPEN, DelegationStatus.ASSIGNED}


class CommitLogEntry(ContractModel):
    commit_id: NonEmptyStr
    patch: BlackboardPatch
    triggered_by: AgentName
    trigger_reason: NonEmptyStr
    resolved_objection_ids: list[NonEmptyStr] = Field(default_factory=list)
    residual_disputes: list[NonEmptyStr] = Field(default_factory=list)
    created_at: datetime


class WorkingMemoryEntry(ContractModel):
    entry_id: NonEmptyStr
    ticker: NonEmptyStr
    author_agent: AgentName
    content_type: NonEmptyStr
    payload: dict[str, Any]
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    created_at: datetime


class BeliefStateSnapshot(ContractModel):
    snapshot_id: NonEmptyStr
    ticker: NonEmptyStr
    documents: dict[DocumentType, dict[str, Any]] = Field(default_factory=dict)
    commit_ids: list[NonEmptyStr] = Field(default_factory=list)
    created_at: datetime
