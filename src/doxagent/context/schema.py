"""Agent-visible context snapshot models."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from doxagent.models import (
    AgentName,
    DelegationStatus,
    DocumentType,
    ObjectionSeverity,
    ObjectionStatus,
    TaskType,
)
from doxagent.prompts import PromptResourceSummary
from doxagent.skills import SkillSummary


class ContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkingMemorySummary(ContextModel):
    entry_id: str
    author_agent: AgentName
    content_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ObjectionSummary(ContextModel):
    objection_id: str
    source_agent: AgentName
    severity: ObjectionSeverity
    status: ObjectionStatus
    target_document_type: DocumentType
    target_field_path: str
    taxonomy: str = "general"
    dedupe_hash: str | None = None
    target_path: str | None = None
    merged_objection_ids: list[str] = Field(default_factory=list)
    reason: str


class BlockingDelegationSummary(ContextModel):
    delegation_id: str
    requester_agent: AgentName
    target_agent: AgentName
    status: DelegationStatus
    target_document_type: DocumentType
    target_field_path: str
    question: str


class AgentContextSnapshot(ContextModel):
    run_id: str
    ticker: str
    agent_name: AgentName
    task_type: TaskType
    workflow_state: str
    task_input: dict[str, Any] = Field(default_factory=dict)
    readable_scopes: list[str] = Field(default_factory=list)
    prompt_summaries: list[PromptResourceSummary] = Field(default_factory=list)
    skill_summaries: list[SkillSummary] = Field(default_factory=list)
    belief_state_summary: dict[str, dict[str, Any]] = Field(default_factory=dict)
    working_memory_summary: list[WorkingMemorySummary] = Field(default_factory=list)
    unresolved_objections: list[ObjectionSummary] = Field(default_factory=list)
    blocking_delegations: list[BlockingDelegationSummary] = Field(default_factory=list)
