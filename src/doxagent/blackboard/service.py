"""Blackboard Service minimum viable business state layer."""

from datetime import UTC, datetime
from typing import Any

from doxagent.blackboard.errors import (
    PatchValidationError,
    RunNotFoundError,
    StateTransitionError,
)
from doxagent.blackboard.repository import BlackboardRepository, InMemoryBlackboardRepository
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
    Objection,
    ObjectionSeverity,
    ObjectionStatus,
    WorkingMemoryEntry,
    can_promote_target,
    new_id,
)


class BlackboardService:
    def __init__(self, repository: BlackboardRepository | None = None) -> None:
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
    ) -> WorkingMemoryEntry:
        header_loader = getattr(self.repository, "get_run_header", None)
        inserter = getattr(self.repository, "insert_working_memory_entry", None)
        if callable(header_loader) and callable(inserter):
            header = header_loader(run_id)
            entry = WorkingMemoryEntry(
                entry_id=new_id("wm"),
                ticker=header.ticker,
                author_agent=author_agent,
                content_type=content_type,
                payload=payload,
                created_at=datetime.now(UTC),
            )
            inserter(run_id, entry)
            return entry

        entry: WorkingMemoryEntry | None = None

        def mutate(run: BlackboardRun) -> BlackboardRun:
            nonlocal entry
            if entry is None:
                entry = WorkingMemoryEntry(
                    entry_id=new_id("wm"),
                    ticker=run.ticker,
                    author_agent=author_agent,
                    content_type=content_type,
                    payload=payload,
                    created_at=datetime.now(UTC),
                )
            if any(existing.entry_id == entry.entry_id for existing in run.working_memory):
                return run
            run.working_memory.append(entry)
            return run

        self.repository.mutate(run_id, mutate)
        if entry is None:
            raise StateTransitionError("Working memory entry was not created.")
        return entry

    def list_runs_by_ticker(self, ticker: str, *, limit: int = 20) -> list[BlackboardRun]:
        return self.repository.list_by_ticker(ticker, limit=limit)

    def list_unresolved_objections(self, run_id: str) -> list[Objection]:
        return self.repository.list_unresolved_objections(run_id)

    def list_blocking_delegations(
        self,
        run_id: str,
        *,
        target_agent: AgentName | None = None,
    ) -> list[Delegation]:
        return self.repository.list_blocking_delegations(
            run_id,
            target_agent=target_agent,
        )

    def summary_counts(self, run_id: str) -> dict[str, int]:
        return self.repository.summary_counts(run_id)

    def submit_patch(
        self,
        run_id: str,
        patch: BlackboardPatch,
        *,
        permissions: AgentPermissions,
        trigger_reason: str,
    ) -> CommitLogEntry:
        header_loader = getattr(self.repository, "get_run_header", None)
        document_loader = getattr(self.repository, "get_document_bundle_by_run_id", None)
        apply_patch_commit = getattr(self.repository, "apply_patch_commit", None)
        if callable(header_loader) and callable(document_loader) and callable(apply_patch_commit):
            header = header_loader(run_id)
            run = document_loader(header.ticker, run_id, [patch.target.document_type])
            list_objections = getattr(self.repository, "list_objections", None)
            run.objections = (
                list_objections(run_id)
                if callable(list_objections)
                else self.repository.list_unresolved_objections(run_id)
            )
            run.delegations = self.repository.list_blocking_delegations(run_id)
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
                residual_disputes=[
                    item.objection_id for item in run.objections if item.is_unresolved
                ],
                created_at=datetime.now(UTC),
            )
            apply_patch_commit(run_id, run.belief_state, commit)
            return commit

        commit: CommitLogEntry | None = None

        def mutate(run: BlackboardRun) -> BlackboardRun:
            nonlocal commit
            if commit is not None and any(
                existing.commit_id == commit.commit_id for existing in run.commit_log
            ):
                return run
            self._validate_patch(run, patch, permissions)
            self._apply_patch(run.belief_state, patch)
            if commit is None:
                commit = CommitLogEntry(
                    commit_id=new_id("commit"),
                    patch=patch,
                    triggered_by=patch.author_agent,
                    trigger_reason=trigger_reason,
                    resolved_objection_ids=[
                        item.objection_id for item in run.objections if not item.is_unresolved
                    ],
                    residual_disputes=[
                        item.objection_id for item in run.objections if item.is_unresolved
                    ],
                    created_at=datetime.now(UTC),
                )
            run.commit_log.append(commit)
            run.belief_state.commit_ids.append(commit.commit_id)
            return run

        self.repository.mutate(run_id, mutate)
        if commit is None:
            raise StateTransitionError("Commit log entry was not created.")
        return commit

    def create_objection(self, run_id: str, objection: Objection) -> Objection:
        header_loader = getattr(self.repository, "get_run_header", None)
        getter = getattr(self.repository, "get_objections_by_ids", None)
        upserter = getattr(self.repository, "upsert_objection", None)
        if callable(header_loader) and callable(getter) and callable(upserter):
            header = header_loader(run_id)
            self._validate_target_matches_ticker(header.ticker, objection.target)
            enriched = _enrich_objection(objection)
            existing_by_id = getter(run_id, [enriched.objection_id])
            if existing_by_id:
                updated = _merge_objections(existing_by_id[0], enriched)
                upserter(run_id, updated)
                return updated
            for existing in self.repository.list_unresolved_objections(run_id):
                if _objection_dedupe_key(existing) != _objection_dedupe_key(enriched):
                    continue
                updated = _merge_objections(existing, enriched)
                upserter(run_id, updated)
                return updated
            upserter(run_id, enriched)
            return enriched

        updated: Objection | None = None

        def mutate(run: BlackboardRun) -> BlackboardRun:
            nonlocal updated
            self._validate_target_matches_run(run, objection.target)
            enriched = _enrich_objection(objection)
            for index, existing in enumerate(run.objections):
                if existing.objection_id == enriched.objection_id:
                    updated = _merge_objections(existing, enriched)
                    run.objections[index] = updated
                    return run
            for index, existing in enumerate(run.objections):
                if not existing.is_unresolved:
                    continue
                if _objection_dedupe_key(existing) != _objection_dedupe_key(enriched):
                    continue
                updated = _merge_objections(existing, enriched)
                run.objections[index] = updated
                return run
            run.objections.append(enriched)
            updated = enriched
            return run

        self.repository.mutate(run_id, mutate)
        if updated is None:
            raise StateTransitionError("Objection was not created or merged.")
        return updated

    def create_delegation(self, run_id: str, delegation: Delegation) -> Delegation:
        header_loader = getattr(self.repository, "get_run_header", None)
        getter = getattr(self.repository, "get_delegations_by_ids", None)
        inserter = getattr(self.repository, "insert_delegation", None)
        if callable(header_loader) and callable(getter) and callable(inserter):
            header = header_loader(run_id)
            self._validate_target_matches_ticker(header.ticker, delegation.blocking_scope)
            existing = getter(run_id, [delegation.delegation_id])
            if existing:
                return existing[0]
            inserter(run_id, delegation)
            return delegation

        def mutate(run: BlackboardRun) -> BlackboardRun:
            self._validate_target_matches_run(run, delegation.blocking_scope)
            if any(
                existing.delegation_id == delegation.delegation_id
                for existing in run.delegations
            ):
                return run
            run.delegations.append(delegation)
            return run

        self.repository.mutate(run_id, mutate)
        return delegation

    def resolve_objection(
        self,
        run_id: str,
        objection_id: str,
        note: str,
        *,
        changed_paths: list[str] | None = None,
    ) -> Objection:
        return self._set_objection_status(
            run_id,
            objection_id,
            ObjectionStatus.RESOLVED,
            note,
            changed_paths=changed_paths,
        )

    def accept_objection(
        self,
        run_id: str,
        objection_id: str,
        note: str,
        *,
        changed_paths: list[str] | None = None,
    ) -> Objection:
        return self._set_objection_status(
            run_id,
            objection_id,
            ObjectionStatus.ACCEPTED,
            note,
            changed_paths=changed_paths,
        )

    def partially_accept_objection(
        self,
        run_id: str,
        objection_id: str,
        note: str,
        *,
        changed_paths: list[str] | None = None,
    ) -> Objection:
        return self._set_objection_status(
            run_id,
            objection_id,
            ObjectionStatus.PARTIALLY_ACCEPTED,
            note,
            changed_paths=changed_paths,
        )

    def reject_objection(
        self,
        run_id: str,
        objection_id: str,
        note: str,
        *,
        changed_paths: list[str] | None = None,
    ) -> Objection:
        return self._set_objection_status(
            run_id,
            objection_id,
            ObjectionStatus.REJECTED,
            note,
            changed_paths=changed_paths,
        )

    def mark_objection_unresolved(self, run_id: str, objection_id: str, note: str) -> Objection:
        return self._set_objection_status(run_id, objection_id, ObjectionStatus.UNRESOLVED, note)

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

    def _legacy_add_working_memory_entry(
        self,
        run_id: str,
        *,
        author_agent: AgentName,
        content_type: str,
        payload: dict[str, Any],
    ) -> WorkingMemoryEntry:
        run = self.repository.get(run_id)
        entry = WorkingMemoryEntry(
            entry_id=new_id("wm"),
            ticker=run.ticker,
            author_agent=author_agent,
            content_type=content_type,
            payload=payload,
            created_at=datetime.now(UTC),
        )
        run.working_memory.append(entry)
        self.repository.save(run)
        return entry

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
        if not can_promote_target(patch.target, run.objections, run.delegations):
            raise PatchValidationError("Patch target is blocked by objection or delegation.")

    def _validate_target_matches_run(self, run: BlackboardRun, target: BlackboardTarget) -> None:
        self._validate_target_matches_ticker(run.ticker, target)

    def _validate_target_matches_ticker(self, ticker: str, target: BlackboardTarget) -> None:
        if target.ticker is not None and target.ticker != ticker:
            raise PatchValidationError(
                f"Target ticker {target.ticker} does not match run ticker {ticker}."
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
        *,
        changed_paths: list[str] | None = None,
    ) -> Objection:
        getter = getattr(self.repository, "get_objections_by_ids", None)
        updater = getattr(self.repository, "update_objection", None)
        if callable(getter) and callable(updater):
            objections = getter(run_id, [objection_id])
            if not objections:
                raise StateTransitionError(f"Objection not found: {objection_id}")
            objection = objections[0]
            updated = objection.model_copy(
                update={
                    "status": status,
                    "resolution_note": note,
                    "resolution_changed_paths": changed_paths
                    if changed_paths is not None
                    else objection.resolution_changed_paths,
                },
                deep=True,
            )
            try:
                updater(run_id, updated)
            except RunNotFoundError as exc:
                raise StateTransitionError(f"Objection not found: {objection_id}") from exc
            return updated

        updated: Objection | None = None

        def mutate(run: BlackboardRun) -> BlackboardRun:
            nonlocal updated
            for index, objection in enumerate(run.objections):
                if objection.objection_id == objection_id:
                    updated = objection.model_copy(
                        update={
                            "status": status,
                            "resolution_note": note,
                            "resolution_changed_paths": changed_paths
                            if changed_paths is not None
                            else objection.resolution_changed_paths,
                        },
                        deep=True,
                    )
                    run.objections[index] = updated
                    return run
            raise StateTransitionError(f"Objection not found: {objection_id}")

        self.repository.mutate(run_id, mutate)
        if updated is None:
            raise StateTransitionError(f"Objection not found: {objection_id}")
        return updated

    def _set_delegation_status(
        self,
        run_id: str,
        delegation_id: str,
        status: DelegationStatus,
        summary: str | None = None,
    ) -> Delegation:
        getter = getattr(self.repository, "get_delegations_by_ids", None)
        updater = getattr(self.repository, "update_delegation", None)
        if callable(getter) and callable(updater):
            delegations = getter(run_id, [delegation_id])
            if not delegations:
                raise StateTransitionError(f"Delegation not found: {delegation_id}")
            delegation = delegations[0]
            updated = delegation.model_copy(
                update={"status": status, "result_summary": summary},
                deep=True,
            )
            try:
                updater(run_id, updated)
            except RunNotFoundError as exc:
                raise StateTransitionError(f"Delegation not found: {delegation_id}") from exc
            return updated

        updated: Delegation | None = None

        def mutate(run: BlackboardRun) -> BlackboardRun:
            nonlocal updated
            for index, delegation in enumerate(run.delegations):
                if delegation.delegation_id == delegation_id:
                    updated = delegation.model_copy(
                        update={"status": status, "result_summary": summary},
                        deep=True,
                    )
                    run.delegations[index] = updated
                    return run
            raise StateTransitionError(f"Delegation not found: {delegation_id}")

        self.repository.mutate(run_id, mutate)
        if updated is None:
            raise StateTransitionError(f"Delegation not found: {delegation_id}")
        return updated


_SEVERITY_RANK: dict[ObjectionSeverity, int] = {
    ObjectionSeverity.LOW: 1,
    ObjectionSeverity.MEDIUM: 2,
    ObjectionSeverity.HIGH: 3,
    ObjectionSeverity.BLOCKING: 4,
}


def _enrich_objection(objection: Objection) -> Objection:
    target_path = objection.target_path or _target_path(objection.target)
    dedupe_hash = objection.dedupe_hash or _default_objection_hash(
        target_path,
        objection.taxonomy,
        objection.reason,
    )
    return objection.model_copy(
        update={
            "target_path": target_path,
            "dedupe_hash": dedupe_hash,
        },
        deep=True,
    )


def _target_path(target: BlackboardTarget) -> str:
    object_id = target.document_id or target.expectation_id or "default"
    return f"{target.document_type.value}:{object_id}:{target.field_path}"


def _default_objection_hash(target_path: str, taxonomy: str, reason: str) -> str:
    reason_key = " ".join(reason.lower().split())[:120]
    return f"{target_path}|{taxonomy.lower()}|{reason_key}"


def _objection_dedupe_key(objection: Objection) -> tuple[str, str, str]:
    target_path = objection.target_path or _target_path(objection.target)
    dedupe_hash = objection.dedupe_hash or _default_objection_hash(
        target_path,
        objection.taxonomy,
        objection.reason,
    )
    return (target_path, objection.taxonomy.lower(), dedupe_hash)


def _merge_objections(existing: Objection, incoming: Objection) -> Objection:
    merged_ids = [
        *existing.merged_objection_ids,
        incoming.objection_id,
        *incoming.merged_objection_ids,
    ]
    severity = (
        incoming.severity
        if _SEVERITY_RANK[incoming.severity] > _SEVERITY_RANK[existing.severity]
        else existing.severity
    )
    reason = existing.reason
    if incoming.reason != existing.reason:
        reason = f"{existing.reason} | duplicate: {incoming.reason}"
    return existing.model_copy(
        update={
            "severity": severity,
            "reason": reason,
            "merged_objection_ids": list(dict.fromkeys(merged_ids)),
            "target_path": existing.target_path or incoming.target_path,
            "dedupe_hash": existing.dedupe_hash or incoming.dedupe_hash,
        },
        deep=True,
    )
