"""Compile stable documents and scoped control state into the sole workflow-memory view."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from doxagent.models import AgentTask, DocumentType
from doxagent.workflow_memory.errors import WorkflowMemoryOverBudget
from doxagent.workflow_memory.policies import (
    WorkflowMemoryPolicyRegistry,
    default_workflow_memory_policy_registry,
)
from doxagent.workflow_memory.projectors import (
    BlackboardDocumentBodyProjector,
    BlackboardStableDocumentReader,
    StableDocumentReader,
    StableDocumentRepository,
    WorkflowControlProjector,
    project_task_directives,
)
from doxagent.workflow_memory.schema import (
    AgentVisibleWorkflowMemory,
    CompiledWorkflowInput,
    ContextAssemblyAudit,
    SourceDocumentAudit,
    TaskContractView,
)

JsonDict = dict[str, Any]


class WorkflowMemoryCompiler:
    """Default-deny compiler with no dependency on the Blackboard Audit Plane."""

    def __init__(
        self,
        *,
        policy_registry: WorkflowMemoryPolicyRegistry | None = None,
        document_reader: StableDocumentReader | None = None,
        body_projector: BlackboardDocumentBodyProjector | None = None,
        control_projector: WorkflowControlProjector | None = None,
    ) -> None:
        self.policy_registry = (
            policy_registry or default_workflow_memory_policy_registry()
        )
        self.document_reader = document_reader
        self.body_projector = body_projector or BlackboardDocumentBodyProjector()
        self.control_projector = control_projector or WorkflowControlProjector(
            self.body_projector
        )

    @classmethod
    def from_repository(
        cls,
        repository: StableDocumentRepository,
        *,
        policy_registry: WorkflowMemoryPolicyRegistry | None = None,
        body_projector: BlackboardDocumentBodyProjector | None = None,
    ) -> WorkflowMemoryCompiler:
        return cls(
            policy_registry=policy_registry,
            document_reader=BlackboardStableDocumentReader(repository),
            body_projector=body_projector,
        )

    def compile(self, task: AgentTask) -> CompiledWorkflowInput:
        policy = self.policy_registry.resolve(task)
        directives = project_task_directives(task.input_context, policy)
        task_contract = TaskContractView(
            task_id=task.task_id,
            run_id=task.run_metadata.run_id,
            ticker=task.ticker,
            agent_name=task.agent_name,
            task_type=task.task_type,
            workflow_node=task.run_metadata.workflow_node,
            required_output_schema=task.required_output_schema,
            permissions=task.permissions,
            task_directives=directives,
        )

        requested = list(policy.document_types)
        allowed = [
            document_type
            for document_type in requested
            if _permission_allows(task, document_type)
        ]
        permission_excluded = [
            document_type for document_type in requested if document_type not in allowed
        ]
        raw_documents = (
            self.document_reader.read(
                run_id=task.run_metadata.run_id,
                ticker=task.ticker,
                document_types=tuple(allowed),
            )
            if self.document_reader is not None and allowed
            else {}
        )

        documents: dict[str, list[JsonDict]] = {}
        source_audits: list[SourceDocumentAudit] = []
        missing: list[DocumentType] = []
        document_chars: dict[str, int] = {}
        for document_type in allowed:
            raw_items = raw_documents.get(document_type, [])
            if not raw_items:
                missing.append(document_type)
                continue
            projected_items: list[JsonDict] = []
            for raw in raw_items:
                body = self.body_projector.project(document_type, raw)
                rendered = _canonical_json(body)
                projected_items.append(body)
                document_id = str(body.get("document_id") or "unknown")
                source_audits.append(
                    SourceDocumentAudit(
                        document_type=document_type,
                        document_id=document_id,
                        source_version=_source_version(body),
                        body_chars=len(rendered),
                        content_hash=_sha256(rendered),
                    )
                )
                document_chars[f"{document_type.value}:{document_id}"] = len(rendered)
            documents[document_type.value] = projected_items

        active_work_item, control_fields = self.control_projector.project(
            task.input_context,
            policy,
        )
        workflow_memory = AgentVisibleWorkflowMemory(
            documents=documents,
            active_work_item=active_work_item,
        )
        task_json = _canonical_json(task_contract.model_dump(mode="json"))
        memory_json = _canonical_json(workflow_memory.model_view())
        combined = _canonical_json(
            {
                "task_contract": task_contract.model_dump(mode="json"),
                "workflow_memory": workflow_memory.model_view(),
            }
        )
        estimated_tokens = max(1, len(combined) // 4)
        if estimated_tokens > policy.max_input_tokens:
            raise WorkflowMemoryOverBudget(
                policy_id=policy.policy_id,
                estimated_tokens=estimated_tokens,
                max_input_tokens=policy.max_input_tokens,
                document_chars=document_chars,
            )
        audit = ContextAssemblyAudit(
            policy_id=policy.policy_id,
            run_id=task.run_metadata.run_id,
            workflow_node=task.run_metadata.workflow_node,
            source_documents=source_audits,
            included_document_types=[
                item for item in allowed if item.value in documents
            ],
            permission_excluded_document_types=permission_excluded,
            missing_document_types=missing,
            control_fields_selected=control_fields,
            task_contract_chars=len(task_json),
            workflow_memory_chars=len(memory_json),
            estimated_tokens=estimated_tokens,
            content_hash=_sha256(combined),
        )
        return CompiledWorkflowInput(
            task_contract=task_contract,
            workflow_memory=workflow_memory,
            audit=audit,
        )


def _permission_allows(task: AgentTask, document_type: DocumentType) -> bool:
    scopes = set(task.permissions.readable_context_scopes)
    return bool(
        document_type.value in scopes
        or "belief_state" in scopes
        or "all" in scopes
    )


def _source_version(body: JsonDict) -> str | None:
    value = body.get("updated_at") or body.get("created_at")
    return str(value) if value is not None else None


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = ["WorkflowMemoryCompiler"]
