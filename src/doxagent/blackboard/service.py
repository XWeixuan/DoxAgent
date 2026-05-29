"""Blackboard Service minimum viable business state layer."""

from datetime import UTC, datetime
from typing import Any

from doxagent.blackboard.errors import PatchValidationError, StateTransitionError
from doxagent.blackboard.repository import InMemoryBlackboardRepository
from doxagent.blackboard.state import BlackboardRun, create_empty_run
from doxagent.models import (
    AgentName,
    AgentPermissions,
    BeliefStateSnapshot,
    BlackboardPatch,
    BlackboardTarget,
    CommitLogEntry,
    Delegation,
    DelegationStatus,
    EvidenceRef,
    Objection,
    ObjectionStatus,
    WorkingMemoryEntry,
    can_promote_target,
    new_id,
)


class BlackboardService:
    def __init__(self, repository: InMemoryBlackboardRepository | None = None) -> None:
        self.repository = repository or InMemoryBlackboardRepository()

    def start_run(self, ticker: str, created_by: AgentName) -> BlackboardRun:
        return self.repository.add(create_empty_run(ticker=ticker, created_by=created_by))

    def get_run(self, run_id: str) -> BlackboardRun:
        return self.repository.get(run_id)

    def get_belief_state(self, run_id: str) -> BeliefStateSnapshot:
        return self.repository.get(run_id).belief_state

    def add_working_memory_entry(
        self,
        run_id: str,
        *,
        author_agent: AgentName,
        content_type: str,
        payload: dict[str, Any],
        evidence_refs: list[EvidenceRef] | None = None,
    ) -> WorkingMemoryEntry:
        run = self.repository.get(run_id)
        entry = WorkingMemoryEntry(
            entry_id=new_id("wm"),
            ticker=run.ticker,
            author_agent=author_agent,
            content_type=content_type,
            payload=payload,
            evidence_refs=evidence_refs or [],
            created_at=datetime.now(UTC),
        )
        run.working_memory.append(entry)
        self.repository.save(run)
        return entry

    def submit_patch(
        self,
        run_id: str,
        patch: BlackboardPatch,
        *,
        permissions: AgentPermissions,
        trigger_reason: str,
    ) -> CommitLogEntry:
        run = self.repository.get(run_id)
        self._validate_patch(run, patch, permissions)
        self._apply_patch(run.belief_state, patch)
        commit = CommitLogEntry(
            commit_id=new_id("commit"),
            patch=patch,
            triggered_by=patch.author_agent,
            trigger_reason=trigger_reason,
            resolved_objection_ids=[
                item.objection_id for item in run.objections if not item.is_unresolved
            ],
            residual_disputes=[item.objection_id for item in run.objections if item.is_unresolved],
            created_at=datetime.now(UTC),
        )
        run.commit_log.append(commit)
        run.belief_state.commit_ids.append(commit.commit_id)
        self.repository.save(run)
        return commit

    def create_objection(self, run_id: str, objection: Objection) -> Objection:
        run = self.repository.get(run_id)
        self._validate_target_matches_run(run, objection.target)
        run.objections.append(objection)
        self.repository.save(run)
        return objection

    def resolve_objection(self, run_id: str, objection_id: str, note: str) -> Objection:
        return self._set_objection_status(run_id, objection_id, ObjectionStatus.RESOLVED, note)

    def accept_objection(self, run_id: str, objection_id: str, note: str) -> Objection:
        return self._set_objection_status(run_id, objection_id, ObjectionStatus.ACCEPTED, note)

    def partially_accept_objection(self, run_id: str, objection_id: str, note: str) -> Objection:
        return self._set_objection_status(
            run_id,
            objection_id,
            ObjectionStatus.PARTIALLY_ACCEPTED,
            note,
        )

    def reject_objection(self, run_id: str, objection_id: str, note: str) -> Objection:
        return self._set_objection_status(run_id, objection_id, ObjectionStatus.REJECTED, note)

    def mark_objection_unresolved(self, run_id: str, objection_id: str, note: str) -> Objection:
        return self._set_objection_status(run_id, objection_id, ObjectionStatus.UNRESOLVED, note)

    def create_delegation(self, run_id: str, delegation: Delegation) -> Delegation:
        run = self.repository.get(run_id)
        self._validate_target_matches_run(run, delegation.blocking_scope)
        run.delegations.append(delegation)
        self.repository.save(run)
        return delegation

    def assign_delegation(self, run_id: str, delegation_id: str) -> Delegation:
        return self._set_delegation_status(run_id, delegation_id, DelegationStatus.ASSIGNED)

    def complete_delegation(self, run_id: str, delegation_id: str, summary: str) -> Delegation:
        return self._set_delegation_status(
            run_id,
            delegation_id,
            DelegationStatus.COMPLETED,
            summary,
        )

    def fail_delegation(self, run_id: str, delegation_id: str, summary: str) -> Delegation:
        return self._set_delegation_status(run_id, delegation_id, DelegationStatus.FAILED, summary)

    def retry_delegation(self, run_id: str, delegation_id: str) -> Delegation:
        return self._set_delegation_status(run_id, delegation_id, DelegationStatus.ASSIGNED)

    def cancel_delegation(self, run_id: str, delegation_id: str, summary: str) -> Delegation:
        return self._set_delegation_status(
            run_id,
            delegation_id,
            DelegationStatus.CANCELLED,
            summary,
        )

    def _validate_patch(
        self,
        run: BlackboardRun,
        patch: BlackboardPatch,
        permissions: AgentPermissions,
    ) -> None:
        self._validate_target_matches_run(run, patch.target)
        if not permissions.can_propose_patch:
            raise PatchValidationError("Agent permissions do not allow proposing patches.")
        if patch.target.document_type.value not in permissions.writable_targets:
            raise PatchValidationError(
                f"Agent cannot write document type: {patch.target.document_type.value}"
            )
        if not patch.evidence_refs:
            raise PatchValidationError("Patch must include at least one evidence reference.")
        if not can_promote_target(patch.target, run.objections, run.delegations):
            raise PatchValidationError("Patch target is blocked by objection or delegation.")

    def _validate_target_matches_run(self, run: BlackboardRun, target: BlackboardTarget) -> None:
        if target.ticker is not None and target.ticker != run.ticker:
            raise PatchValidationError(
                f"Target ticker {target.ticker} does not match run ticker {run.ticker}."
            )

    def _apply_patch(self, belief_state: BeliefStateSnapshot, patch: BlackboardPatch) -> None:
        document_bucket = belief_state.documents.setdefault(patch.target.document_type, {})
        object_key = (
            patch.target.document_id
            or patch.target.expectation_id
            or f"{patch.target.document_type.value}:default"
        )
        target_document = document_bucket.setdefault(object_key, {})
        self._set_dot_path(target_document, patch.target.field_path, patch.after)

    def _set_dot_path(self, document: dict[str, Any], field_path: str, value: Any) -> None:
        keys = field_path.split(".")
        if any(not key for key in keys):
            raise PatchValidationError(f"Invalid field path: {field_path}")
        cursor = document
        for key in keys[:-1]:
            existing = cursor.setdefault(key, {})
            if not isinstance(existing, dict):
                raise PatchValidationError(f"Cannot write through non-dict path segment: {key}")
            cursor = existing
        cursor[keys[-1]] = value

    def _set_objection_status(
        self,
        run_id: str,
        objection_id: str,
        status: ObjectionStatus,
        note: str,
    ) -> Objection:
        run = self.repository.get(run_id)
        for index, objection in enumerate(run.objections):
            if objection.objection_id == objection_id:
                updated = objection.model_copy(
                    update={"status": status, "resolution_note": note},
                    deep=True,
                )
                run.objections[index] = updated
                self.repository.save(run)
                return updated
        raise StateTransitionError(f"Objection not found: {objection_id}")

    def _set_delegation_status(
        self,
        run_id: str,
        delegation_id: str,
        status: DelegationStatus,
        summary: str | None = None,
    ) -> Delegation:
        run = self.repository.get(run_id)
        for index, delegation in enumerate(run.delegations):
            if delegation.delegation_id == delegation_id:
                updated = delegation.model_copy(
                    update={"status": status, "result_summary": summary},
                    deep=True,
                )
                run.delegations[index] = updated
                self.repository.save(run)
                return updated
        raise StateTransitionError(f"Delegation not found: {delegation_id}")
