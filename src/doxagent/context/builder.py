"""Build bounded agent context from Blackboard state."""

from typing import Any

from doxagent.blackboard import BlackboardService
from doxagent.context.schema import (
    AgentContextSnapshot,
    BlockingDelegationSummary,
    ObjectionSummary,
    WorkingMemorySummary,
)
from doxagent.models import AgentTask, DocumentType, EvidenceRef


class ContextBuilder:
    def __init__(self, blackboard: BlackboardService) -> None:
        self.blackboard = blackboard

    def build(self, task: AgentTask, run_id: str) -> AgentContextSnapshot:
        run = self.blackboard.get_run(run_id)
        scopes = set(task.permissions.readable_context_scopes)
        belief_state_summary = self._belief_state_summary(run.belief_state.documents, scopes)
        working_memory_summary = [
            WorkingMemorySummary(
                entry_id=entry.entry_id,
                author_agent=entry.author_agent,
                content_type=entry.content_type,
                payload=entry.payload,
                evidence_refs=entry.evidence_refs,
            )
            for entry in run.working_memory
            if "working_memory" in scopes or task.permissions.can_access_private_memory
        ]
        unresolved_objections = [
            ObjectionSummary(
                objection_id=objection.objection_id,
                source_agent=objection.source_agent,
                severity=objection.severity,
                status=objection.status,
                target_document_type=objection.target.document_type,
                target_field_path=objection.target.field_path,
                reason=objection.reason,
                evidence_refs=objection.evidence_refs,
            )
            for objection in run.objections
            if objection.is_unresolved
        ]
        blocking_delegations = [
            BlockingDelegationSummary(
                delegation_id=delegation.delegation_id,
                requester_agent=delegation.requester_agent,
                target_agent=delegation.target_agent,
                status=delegation.status,
                target_document_type=delegation.blocking_scope.document_type,
                target_field_path=delegation.blocking_scope.field_path,
                question=delegation.question,
            )
            for delegation in run.delegations
            if delegation.is_blocking
        ]
        return AgentContextSnapshot(
            run_id=run.run_id,
            ticker=run.ticker,
            agent_name=task.agent_name,
            task_type=task.task_type,
            workflow_state=run.workflow_state.value,
            task_input=task.input_context,
            readable_scopes=list(task.permissions.readable_context_scopes),
            skill_summaries=task.skill_bundle.skills if task.skill_bundle is not None else [],
            belief_state_summary=belief_state_summary,
            working_memory_summary=working_memory_summary,
            evidence_refs=self._collect_evidence(
                working_memory_summary,
                unresolved_objections,
            ),
            unresolved_objections=unresolved_objections,
            blocking_delegations=blocking_delegations,
        )

    def _belief_state_summary(
        self,
        documents: dict[DocumentType, dict[str, Any]],
        scopes: set[str],
    ) -> dict[str, dict[str, Any]]:
        if "belief_state" in scopes or "all" in scopes:
            return {document_type.value: document for document_type, document in documents.items()}
        return {
            document_type.value: document
            for document_type, document in documents.items()
            if document_type.value in scopes
        }

    def _collect_evidence(
        self,
        working_memory: list[WorkingMemorySummary],
        objections: list[ObjectionSummary],
    ) -> list[EvidenceRef]:
        evidence_by_id: dict[str, EvidenceRef] = {}
        for entry in working_memory:
            for evidence in entry.evidence_refs:
                evidence_by_id[evidence.evidence_id] = evidence
        for objection in objections:
            for evidence in objection.evidence_refs:
                evidence_by_id[evidence.evidence_id] = evidence
        return list(evidence_by_id.values())
