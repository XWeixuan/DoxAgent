"""Typed contracts for compiled, LLM-visible workflow memory."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from doxagent.models import AgentName, AgentPermissions, DocumentType, TaskType


class WorkflowMemoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TaskContractView(WorkflowMemoryModel):
    task_id: str
    run_id: str
    ticker: str
    agent_name: AgentName
    task_type: TaskType
    workflow_node: str | None = None
    required_output_schema: str
    permissions: AgentPermissions
    task_directives: dict[str, Any] = Field(default_factory=dict)


class AgentVisibleWorkflowMemory(WorkflowMemoryModel):
    documents: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    active_work_item: dict[str, Any] | None = None

    def model_view(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.documents:
            payload["documents"] = self.documents
        if self.active_work_item:
            payload["active_work_item"] = self.active_work_item
        return payload


class WorkflowMemoryPolicy(WorkflowMemoryModel):
    policy_id: str
    workflow_node: str
    task_type: TaskType
    required_output_schema: str | None = None
    agent_name: AgentName | None = None
    document_types: tuple[DocumentType, ...] = ()
    directive_fields: tuple[str, ...] = ()
    active_work_item_fields: tuple[str, ...] = ()
    max_input_tokens: int = Field(default=100_000, gt=0)


class SourceDocumentAudit(WorkflowMemoryModel):
    document_type: DocumentType
    document_id: str
    source_version: str | None = None
    body_chars: int
    content_hash: str


class ContextAssemblyAudit(WorkflowMemoryModel):
    schema_version: str = "workflow_memory_audit.v1"
    policy_id: str
    run_id: str
    workflow_node: str | None = None
    source_documents: list[SourceDocumentAudit] = Field(default_factory=list)
    included_document_types: list[DocumentType] = Field(default_factory=list)
    permission_excluded_document_types: list[DocumentType] = Field(default_factory=list)
    missing_document_types: list[DocumentType] = Field(default_factory=list)
    control_fields_selected: list[str] = Field(default_factory=list)
    excluded_field_categories: list[str] = Field(
        default_factory=lambda: [
            "evidence_and_retrieval_metadata",
            "patch_commit_and_validation_provenance",
            "working_memory_and_agent_result_history",
            "model_react_tool_and_transaction_audit",
            "runtime_execution_records",
        ]
    )
    task_contract_chars: int
    workflow_memory_chars: int
    estimated_tokens: int
    content_hash: str


class CompiledWorkflowInput(WorkflowMemoryModel):
    task_contract: TaskContractView
    workflow_memory: AgentVisibleWorkflowMemory
    audit: ContextAssemblyAudit


__all__ = [
    "AgentVisibleWorkflowMemory",
    "CompiledWorkflowInput",
    "ContextAssemblyAudit",
    "SourceDocumentAudit",
    "TaskContractView",
    "WorkflowMemoryPolicy",
]
