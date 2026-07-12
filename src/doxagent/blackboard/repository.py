"""Blackboard repository contracts and in-memory implementation."""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from doxagent.blackboard.errors import RunNotFoundError
from doxagent.blackboard.state import BlackboardRun, WorkflowState
from doxagent.models import AgentName, Delegation, DocumentType, Objection

RunMutator = Callable[[BlackboardRun], BlackboardRun]


@dataclass(frozen=True)
class BlackboardRunHeader:
    run_id: str
    ticker: str
    created_by: AgentName
    workflow_state: WorkflowState
    created_at: datetime
    updated_at: datetime | None = None


@dataclass(frozen=True)
class WorkingMemoryEntrySummary:
    entry_id: str
    author_agent: AgentName
    content_type: str
    payload: dict[str, Any] | None = None


class BlackboardRepository(Protocol):
    def add(self, run: BlackboardRun) -> BlackboardRun: ...

    def get(self, run_id: str) -> BlackboardRun: ...

    def save(self, run: BlackboardRun) -> BlackboardRun: ...

    def list_by_ticker(self, ticker: str, *, limit: int = 20) -> list[BlackboardRun]: ...

    def mutate(self, run_id: str, mutator: RunMutator) -> BlackboardRun: ...

    def get_run_header(self, run_id: str) -> BlackboardRunHeader: ...

    def get_document_bundle_by_run_id(
        self,
        ticker: str,
        run_id: str,
        document_types: list[DocumentType],
    ) -> BlackboardRun: ...

    def list_document_bundle_candidates(
        self,
        ticker: str,
        document_types: list[DocumentType],
        *,
        limit: int = 3,
    ) -> list[BlackboardRun]: ...

    def list_document_keys(self, run_id: str) -> dict[DocumentType, list[str]]: ...

    def list_working_memory_summaries(
        self,
        run_id: str,
        *,
        include_payload: bool = False,
    ) -> list[WorkingMemoryEntrySummary]: ...

    def get_objections_by_ids(self, run_id: str, ids: list[str]) -> list[Objection]: ...

    def list_objections(self, run_id: str) -> list[Objection]: ...

    def get_delegations_by_ids(self, run_id: str, ids: list[str]) -> list[Delegation]: ...

    def insert_working_memory_entry(
        self,
        run_id: str,
        entry: Any,
    ) -> Any: ...

    def upsert_objection(self, run_id: str, objection: Objection) -> Objection: ...

    def insert_delegation(self, run_id: str, delegation: Delegation) -> Delegation: ...

    def update_objection(self, run_id: str, objection: Objection) -> Objection: ...

    def update_delegation(self, run_id: str, delegation: Delegation) -> Delegation: ...

    def apply_patch_commit(
        self,
        run_id: str,
        belief_state: Any,
        commit: Any,
    ) -> Any: ...

    def list_unresolved_objections(self, run_id: str) -> list[Objection]: ...

    def list_blocking_delegations(
        self,
        run_id: str,
        *,
        target_agent: AgentName | None = None,
    ) -> list[Delegation]: ...

    def summary_counts(self, run_id: str) -> dict[str, int]: ...


class InMemoryBlackboardRepository:
    def __init__(self) -> None:
        self._runs: dict[str, BlackboardRun] = {}

    def add(self, run: BlackboardRun) -> BlackboardRun:
        self._runs[run.run_id] = run.model_copy(deep=True)
        return run.model_copy(deep=True)

    def get(self, run_id: str) -> BlackboardRun:
        try:
            return self._runs[run_id].model_copy(deep=True)
        except KeyError as exc:
            raise RunNotFoundError(f"Blackboard run not found: {run_id}") from exc

    def save(self, run: BlackboardRun) -> BlackboardRun:
        if run.run_id not in self._runs:
            raise RunNotFoundError(f"Blackboard run not found: {run.run_id}")
        self._runs[run.run_id] = run.model_copy(deep=True)
        return run.model_copy(deep=True)

    def list_by_ticker(self, ticker: str, *, limit: int = 20) -> list[BlackboardRun]:
        runs = [run for run in self._runs.values() if run.ticker == ticker]
        runs.sort(key=lambda item: item.created_at, reverse=True)
        return [run.model_copy(deep=True) for run in runs[:limit]]

    def mutate(self, run_id: str, mutator: RunMutator) -> BlackboardRun:
        run = self.get(run_id)
        updated = mutator(run)
        return self.save(updated)

    def get_run_header(self, run_id: str) -> BlackboardRunHeader:
        run = self.get(run_id)
        return BlackboardRunHeader(
            run_id=run.run_id,
            ticker=run.ticker,
            created_by=run.created_by,
            workflow_state=run.workflow_state,
            created_at=run.created_at,
            updated_at=None,
        )

    def get_document_bundle_by_run_id(
        self,
        ticker: str,
        run_id: str,
        document_types: list[DocumentType],
    ) -> BlackboardRun:
        run = self.get(run_id)
        if run.ticker != ticker:
            raise RunNotFoundError(f"Blackboard run not found: {run_id}")
        return _document_only_run(run, document_types)

    def list_document_bundle_candidates(
        self,
        ticker: str,
        document_types: list[DocumentType],
        *,
        limit: int = 3,
    ) -> list[BlackboardRun]:
        runs = [run for run in self._runs.values() if run.ticker == ticker]
        runs.sort(key=lambda item: item.created_at, reverse=True)
        return [_document_only_run(run, document_types) for run in runs[:limit]]

    def list_document_keys(self, run_id: str) -> dict[DocumentType, list[str]]:
        run = self.get(run_id)
        return {
            document_type: list(bucket.keys())
            for document_type, bucket in run.belief_state.documents.items()
        }

    def list_working_memory_summaries(
        self,
        run_id: str,
        *,
        include_payload: bool = False,
    ) -> list[WorkingMemoryEntrySummary]:
        return [
            WorkingMemoryEntrySummary(
                entry_id=entry.entry_id,
                author_agent=entry.author_agent,
                content_type=entry.content_type,
                payload=entry.payload if include_payload else None,
            )
            for entry in self.get(run_id).working_memory
        ]

    def get_objections_by_ids(self, run_id: str, ids: list[str]) -> list[Objection]:
        requested = set(ids)
        return [
            item
            for item in self.get(run_id).objections
            if item.objection_id in requested
        ]

    def list_objections(self, run_id: str) -> list[Objection]:
        return list(self.get(run_id).objections)

    def get_delegations_by_ids(self, run_id: str, ids: list[str]) -> list[Delegation]:
        requested = set(ids)
        return [
            item
            for item in self.get(run_id).delegations
            if item.delegation_id in requested
        ]

    def insert_working_memory_entry(
        self,
        run_id: str,
        entry: Any,
    ) -> Any:
        run = self.get(run_id)
        if any(existing.entry_id == entry.entry_id for existing in run.working_memory):
            return entry
        run.working_memory.append(entry)
        self.save(run)
        return entry

    def upsert_objection(self, run_id: str, objection: Objection) -> Objection:
        run = self.get(run_id)
        for index, existing in enumerate(run.objections):
            if existing.objection_id == objection.objection_id:
                run.objections[index] = objection
                self.save(run)
                return objection
        run.objections.append(objection)
        self.save(run)
        return objection

    def insert_delegation(self, run_id: str, delegation: Delegation) -> Delegation:
        run = self.get(run_id)
        if not any(
            existing.delegation_id == delegation.delegation_id
            for existing in run.delegations
        ):
            run.delegations.append(delegation)
            self.save(run)
        return delegation

    def update_objection(self, run_id: str, objection: Objection) -> Objection:
        run = self.get(run_id)
        for index, existing in enumerate(run.objections):
            if existing.objection_id == objection.objection_id:
                run.objections[index] = objection
                self.save(run)
                return objection
        raise RunNotFoundError(f"Objection not found: {objection.objection_id}")

    def update_delegation(self, run_id: str, delegation: Delegation) -> Delegation:
        run = self.get(run_id)
        for index, existing in enumerate(run.delegations):
            if existing.delegation_id == delegation.delegation_id:
                run.delegations[index] = delegation
                self.save(run)
                return delegation
        raise RunNotFoundError(f"Delegation not found: {delegation.delegation_id}")

    def apply_patch_commit(
        self,
        run_id: str,
        belief_state: Any,
        commit: Any,
    ) -> Any:
        run = self.get(run_id)
        document_type = commit.patch.target.document_type
        run.belief_state.documents[document_type] = belief_state.documents.get(
            document_type,
            {},
        )
        run.belief_state.commit_ids.append(commit.commit_id)
        run.commit_log.append(commit)
        self.save(run)
        return commit

    def list_unresolved_objections(self, run_id: str) -> list[Objection]:
        return [item for item in self.get(run_id).objections if item.is_unresolved]

    def list_blocking_delegations(
        self,
        run_id: str,
        *,
        target_agent: AgentName | None = None,
    ) -> list[Delegation]:
        return [
            item
            for item in self.get(run_id).delegations
            if item.is_blocking and (target_agent is None or item.target_agent is target_agent)
        ]

    def summary_counts(self, run_id: str) -> dict[str, int]:
        run = self.get(run_id)
        return {
            "commit_count": len(run.commit_log),
            "working_memory_count": len(run.working_memory),
            "unresolved_objection_count": sum(
                1 for objection in run.objections if objection.is_unresolved
            ),
            "blocking_delegation_count": sum(
                1 for delegation in run.delegations if delegation.is_blocking
            ),
        }

    def unsafe_get_mutable(self, run_id: str) -> BlackboardRun:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise RunNotFoundError(f"Blackboard run not found: {run_id}") from exc

    def snapshot(self) -> dict[str, BlackboardRun]:
        return deepcopy(self._runs)


def _document_only_run(
    run: BlackboardRun,
    document_types: list[DocumentType],
) -> BlackboardRun:
    copied = run.model_copy(deep=True)
    allowed = set(document_types)
    copied.belief_state.documents = {
        document_type: bucket
        for document_type, bucket in copied.belief_state.documents.items()
        if document_type in allowed
    }
    copied.working_memory = []
    copied.commit_log = []
    copied.objections = []
    copied.delegations = []
    return copied
