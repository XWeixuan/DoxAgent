"""In-memory audit query service."""

from doxagent.audit.schema import (
    CommitAuditRecord,
    DelegationAuditRecord,
    FieldTrace,
    ObjectionAuditRecord,
)
from doxagent.blackboard import BlackboardRun, BlackboardService
from doxagent.models import (
    BlackboardTarget,
    CommitLogEntry,
    Delegation,
    DocumentType,
    Objection,
)


class AuditQueryService:
    def __init__(self, run: BlackboardRun) -> None:
        self.run = run

    @classmethod
    def for_run(
        cls,
        run_or_run_id: BlackboardRun | str,
        blackboard: BlackboardService | None = None,
    ) -> "AuditQueryService":
        if isinstance(run_or_run_id, BlackboardRun):
            return cls(run_or_run_id)
        if blackboard is None:
            raise ValueError("blackboard is required when for_run receives a run id")
        return cls(blackboard.get_run(run_or_run_id))

    def list_commit_log(
        self,
        *,
        document_type: DocumentType | None = None,
        object_id: str | None = None,
        field_path: str | None = None,
    ) -> list[CommitAuditRecord]:
        records = [self._commit_record(commit) for commit in self.run.commit_log]
        if document_type is not None:
            records = [record for record in records if record.document_type is document_type]
        if object_id is not None:
            records = [record for record in records if record.object_id == object_id]
        if field_path is not None:
            records = [record for record in records if record.field_path == field_path]
        return records

    def trace_field(
        self,
        document_type: DocumentType,
        object_id: str,
        field_path: str,
    ) -> FieldTrace | None:
        value = self._read_field(document_type, object_id, field_path)
        if value is None:
            return None
        for commit in reversed(self.run.commit_log):
            record = self._commit_record(commit)
            if (
                record.document_type is document_type
                and record.object_id == object_id
                and record.field_path == field_path
            ):
                return FieldTrace(
                    document_type=document_type,
                    object_id=object_id,
                    field_path=field_path,
                    value=value,
                    commit_id=record.commit_id,
                    patch_id=record.patch_id,
                    author_agent=record.author_agent,
                    trigger_reason=record.trigger_reason,
                )
        return None

    def list_unresolved_objections(self) -> list[ObjectionAuditRecord]:
        return [
            self._objection_record(objection)
            for objection in self.run.objections
            if objection.is_unresolved
        ]

    def list_blocking_delegations(self) -> list[DelegationAuditRecord]:
        return [
            self._delegation_record(delegation)
            for delegation in self.run.delegations
            if delegation.is_blocking
        ]

    def _commit_record(self, commit: CommitLogEntry) -> CommitAuditRecord:
        target = commit.patch.target
        return CommitAuditRecord(
            commit_id=commit.commit_id,
            patch_id=commit.patch.patch_id,
            author_agent=commit.patch.author_agent,
            triggered_by=commit.triggered_by,
            trigger_reason=commit.trigger_reason,
            document_type=target.document_type,
            object_id=_object_id(target),
            field_path=target.field_path,
            resolved_objection_ids=list(commit.resolved_objection_ids),
            residual_disputes=list(commit.residual_disputes),
            created_at=commit.created_at,
        )

    def _objection_record(self, objection: Objection) -> ObjectionAuditRecord:
        target = objection.target
        return ObjectionAuditRecord(
            objection_id=objection.objection_id,
            source_agent=objection.source_agent,
            status=objection.status,
            document_type=target.document_type,
            object_id=_object_id(target),
            field_path=target.field_path,
            taxonomy=objection.taxonomy,
            dedupe_hash=objection.dedupe_hash,
            target_path=objection.target_path,
            merged_objection_ids=list(objection.merged_objection_ids),
            reason=objection.reason,
            resolution_note=objection.resolution_note,
            resolution_changed_paths=list(objection.resolution_changed_paths),
        )

    def _delegation_record(self, delegation: Delegation) -> DelegationAuditRecord:
        target = delegation.blocking_scope
        return DelegationAuditRecord(
            delegation_id=delegation.delegation_id,
            requester_agent=delegation.requester_agent,
            target_agent=delegation.target_agent,
            status=delegation.status,
            document_type=target.document_type,
            object_id=_object_id(target),
            field_path=target.field_path,
            question=delegation.question,
            result_summary=delegation.result_summary,
        )

    def _read_field(
        self,
        document_type: DocumentType,
        object_id: str,
        field_path: str,
    ) -> object | None:
        document_bucket = self.run.belief_state.documents.get(document_type, {})
        document = document_bucket.get(object_id)
        if not isinstance(document, dict):
            return None
        cursor: object = document
        for part in field_path.split("."):
            if not isinstance(cursor, dict) or part not in cursor:
                return None
            cursor = cursor[part]
        return cursor


def _object_id(target: BlackboardTarget) -> str:
    return (
        target.document_id
        or target.expectation_id
        or f"{target.document_type.value}:default"
    )
