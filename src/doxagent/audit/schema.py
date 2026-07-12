"""Serializable business audit records."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from doxagent.models import AgentName, DelegationStatus, DocumentType, ObjectionStatus


class AuditModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CommitAuditRecord(AuditModel):
    commit_id: str
    patch_id: str
    author_agent: AgentName
    triggered_by: AgentName
    trigger_reason: str
    document_type: DocumentType
    object_id: str
    field_path: str
    resolved_objection_ids: list[str] = Field(default_factory=list)
    residual_disputes: list[str] = Field(default_factory=list)
    created_at: datetime


class FieldTrace(AuditModel):
    document_type: DocumentType
    object_id: str
    field_path: str
    value: Any
    commit_id: str
    patch_id: str
    author_agent: AgentName
    trigger_reason: str


class ObjectionAuditRecord(AuditModel):
    objection_id: str
    source_agent: AgentName
    status: ObjectionStatus
    document_type: DocumentType
    object_id: str
    field_path: str
    taxonomy: str = "general"
    dedupe_hash: str | None = None
    target_path: str | None = None
    merged_objection_ids: list[str] = Field(default_factory=list)
    reason: str
    resolution_note: str | None = None
    resolution_changed_paths: list[str] = Field(default_factory=list)


class DelegationAuditRecord(AuditModel):
    delegation_id: str
    requester_agent: AgentName
    target_agent: AgentName
    status: DelegationStatus
    document_type: DocumentType
    object_id: str
    field_path: str
    question: str
    result_summary: str | None = None
