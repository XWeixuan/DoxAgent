"""Postgres-backed Blackboard repository for Supabase direct connections."""

from __future__ import annotations

import json
import logging
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import import_module
from typing import Any

from doxagent.blackboard.errors import RunNotFoundError
from doxagent.blackboard.repository import (
    BlackboardRunHeader,
    RunMutator,
    WorkingMemoryEntrySummary,
)
from doxagent.blackboard.state import BlackboardRun, WorkflowState
from doxagent.models import (
    AgentName,
    BeliefStateSnapshot,
    CommitLogEntry,
    Delegation,
    DocumentType,
    Objection,
    WorkingMemoryEntry,
)
from doxagent.postgres import (
    connect_postgres,
    estimate_json_payload_bytes,
    postgres_database_error,
    record_postgres_failure,
    record_postgres_payload,
    retry_postgres_operation,
)
from doxagent.settings import DoxAgentSettings

logger = logging.getLogger(__name__)

_FULL_READ_WARNING_BYTES = 512 * 1024
_FULL_READ_WARNING_PER_MINUTE = 20
_AGENT_CONTEXT_PAYLOAD_SQL = (
    "payload #- '{payload,react_audit}' #- '{payload,model_audits}'"
)


class PostgresBlackboardRepository:
    """Persist Blackboard business state in Postgres.

    The implementation intentionally talks to Postgres directly instead of the
    Supabase client API. Supabase credentials stay in environment variables.
    """

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._full_read_window_started_at = time.monotonic()
        self._full_read_count_in_window = 0

    @classmethod
    def from_settings(
        cls,
        settings: DoxAgentSettings | None = None,
    ) -> PostgresBlackboardRepository:
        resolved = settings or DoxAgentSettings()
        return cls(resolved.require_database_url())

    def add(self, run: BlackboardRun) -> BlackboardRun:
        payload_bytes = estimate_json_payload_bytes(run.model_dump(mode="json"))

        def operation() -> None:
            with self._connection() as conn:
                self._insert_run(conn, run)

        self._retry(
            operation,
            operation_name="blackboard.add",
            table="blackboard_runs",
            payload_bytes=payload_bytes,
        )
        return run.model_copy(deep=True)

    def get(self, run_id: str) -> BlackboardRun:
        def operation() -> BlackboardRun:
            with self._read_connection() as conn:
                return self._get_run(conn, run_id, lock=False)

        return self._retry(operation)

    def save(self, run: BlackboardRun) -> BlackboardRun:
        def operation() -> None:
            with self._connection() as conn:
                self._ensure_run_exists(conn, run.run_id)
                self._replace_run(conn, run, bump_version=True)

        self._retry(
            operation,
            operation_name="blackboard.save",
            table="blackboard_runs",
            payload_bytes=estimate_json_payload_bytes(run.model_dump(mode="json")),
        )
        return run.model_copy(deep=True)

    def list_by_ticker(self, ticker: str, *, limit: int = 20) -> list[BlackboardRun]:
        def operation() -> list[BlackboardRun]:
            with self._read_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        select run_id
                        from doxagent.blackboard_runs
                        where ticker = %s
                        order by created_at desc
                        limit %s
                        """,
                        (ticker, limit),
                    )
                    run_ids = [row[0] for row in cursor.fetchall()]
                if run_ids:
                    self._record_full_read_event(
                        operation="blackboard.full_read.list_by_ticker",
                        ticker=ticker,
                        run_id=None,
                        estimated_payload_bytes=None,
                        child_counts={"run_count": len(run_ids), "limit": limit},
                    )
                return [self._get_run(conn, run_id, lock=False) for run_id in run_ids]

        return self._retry(operation)

    def get_run_header(self, run_id: str) -> BlackboardRunHeader:
        def operation() -> BlackboardRunHeader:
            with self._read_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        select run_id, ticker, created_by, workflow_state, created_at, updated_at
                        from doxagent.blackboard_runs
                        where run_id = %s
                        """,
                        (run_id,),
                    )
                    row = cursor.fetchone()
            if row is None:
                raise RunNotFoundError(f"Blackboard run not found: {run_id}")
            return BlackboardRunHeader(
                run_id=row[0],
                ticker=row[1],
                created_by=AgentName(row[2]),
                workflow_state=WorkflowState(row[3]),
                created_at=row[4],
                updated_at=row[5],
            )

        return self._retry(operation)

    def get_document_bundle_by_run_id(
        self,
        ticker: str,
        run_id: str,
        document_types: list[DocumentType],
    ) -> BlackboardRun:
        def operation() -> BlackboardRun:
            with self._read_connection() as conn:
                row = self._document_bundle_row(
                    conn,
                    ticker=ticker,
                    run_id=run_id,
                    document_types=document_types,
                )
            if row is None:
                raise RunNotFoundError(f"Blackboard run not found: {run_id}")
            return self._document_run_from_row(row, document_types)

        return self._retry(operation)

    def list_document_bundle_candidates(
        self,
        ticker: str,
        document_types: list[DocumentType],
        *,
        limit: int = 3,
    ) -> list[BlackboardRun]:
        def operation() -> list[BlackboardRun]:
            with self._read_connection() as conn:
                rows = self._document_bundle_rows(
                    conn,
                    ticker=ticker,
                    document_types=document_types,
                    limit=limit,
                )
            return [self._document_run_from_row(row, document_types) for row in rows]

        return self._retry(operation)

    def list_document_keys(self, run_id: str) -> dict[DocumentType, list[str]]:
        def operation() -> dict[DocumentType, list[str]]:
            with self._read_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        select key, jsonb_object_keys(value)
                        from doxagent.belief_state_snapshots,
                             jsonb_each(documents)
                        where run_id = %s
                        """,
                        (run_id,),
                    )
                    rows = cursor.fetchall()
            keys: dict[DocumentType, list[str]] = {}
            for raw_type, document_id in rows:
                try:
                    document_type = DocumentType(str(raw_type))
                except ValueError:
                    continue
                keys.setdefault(document_type, []).append(str(document_id))
            return keys

        return self._retry(operation)

    def list_working_memory_summaries(
        self,
        run_id: str,
        *,
        include_payload: bool = False,
    ) -> list[WorkingMemoryEntrySummary]:
        def operation() -> list[WorkingMemoryEntrySummary]:
            payload_sql = (
                _AGENT_CONTEXT_PAYLOAD_SQL
                if include_payload
                else "null"
            )
            with self._read_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"""
                        select entry_id, author_agent, content_type, {payload_sql}
                        from doxagent.working_memory_entries
                        where run_id = %s
                        order by created_at asc, entry_id asc
                        """,
                        (run_id,),
                    )
                    rows = cursor.fetchall()
            return [
                WorkingMemoryEntrySummary(
                    entry_id=row[0],
                    author_agent=AgentName(row[1]),
                    content_type=row[2],
                    payload=self._coerce_json(row[3]) if include_payload else None,
                )
                for row in rows
            ]

        return self._retry(operation)

    def get_objections_by_ids(self, run_id: str, ids: list[str]) -> list[Objection]:
        if not ids:
            return []

        def operation() -> list[Objection]:
            with self._read_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        select objection_json
                        from doxagent.objections
                        where run_id = %s
                          and objection_id = any(%s)
                        order by created_at asc, objection_id asc
                        """,
                        (run_id, ids),
                    )
                    rows = cursor.fetchall()
            return [Objection.model_validate(self._coerce_json(row[0])) for row in rows]

        return self._retry(operation)

    def list_objections(self, run_id: str) -> list[Objection]:
        def operation() -> list[Objection]:
            with self._read_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        select objection_json
                        from doxagent.objections
                        where run_id = %s
                        order by created_at asc, objection_id asc
                        """,
                        (run_id,),
                    )
                    rows = cursor.fetchall()
            return [Objection.model_validate(self._coerce_json(row[0])) for row in rows]

        return self._retry(operation)

    def get_delegations_by_ids(self, run_id: str, ids: list[str]) -> list[Delegation]:
        if not ids:
            return []

        def operation() -> list[Delegation]:
            with self._read_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        select delegation_json
                        from doxagent.delegations
                        where run_id = %s
                          and delegation_id = any(%s)
                        order by created_at asc, delegation_id asc
                        """,
                        (run_id, ids),
                    )
                    rows = cursor.fetchall()
            return [Delegation.model_validate(self._coerce_json(row[0])) for row in rows]

        return self._retry(operation)

    def list_unresolved_objections(self, run_id: str) -> list[Objection]:
        def operation() -> list[Objection]:
            with self._read_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        select objection_json
                        from doxagent.objections
                        where run_id = %s
                          and status in ('open', 'unresolved')
                        order by created_at asc, objection_id asc
                        """,
                        (run_id,),
                    )
                    rows = cursor.fetchall()
            return [Objection.model_validate(self._coerce_json(row[0])) for row in rows]

        return self._retry(operation)

    def list_blocking_delegations(
        self,
        run_id: str,
        *,
        target_agent: AgentName | None = None,
    ) -> list[Delegation]:
        def operation() -> list[Delegation]:
            params: tuple[Any, ...]
            target_clause = ""
            if target_agent is None:
                params = (run_id,)
            else:
                target_clause = "and target_agent = %s"
                params = (run_id, target_agent.value)
            with self._read_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"""
                        select delegation_json
                        from doxagent.delegations
                        where run_id = %s
                          and status in ('open', 'assigned')
                          {target_clause}
                        order by created_at asc, delegation_id asc
                        """,
                        params,
                    )
                    rows = cursor.fetchall()
            return [Delegation.model_validate(self._coerce_json(row[0])) for row in rows]

        return self._retry(operation)

    def summary_counts(self, run_id: str) -> dict[str, int]:
        def operation() -> dict[str, int]:
            with self._read_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        select
                            (select count(*) from doxagent.commit_log_entries where run_id = %s),
                            (select count(*)
                             from doxagent.working_memory_entries
                             where run_id = %s),
                            (select count(*) from doxagent.objections
                             where run_id = %s and status in ('open', 'unresolved')),
                            (select count(*) from doxagent.delegations
                             where run_id = %s and status in ('open', 'assigned'))
                        """,
                        (run_id, run_id, run_id, run_id),
                    )
                    row = cursor.fetchone()
            return {
                "commit_count": int(row[0]),
                "working_memory_count": int(row[1]),
                "unresolved_objection_count": int(row[2]),
                "blocking_delegation_count": int(row[3]),
            }

        return self._retry(operation)

    def insert_working_memory_entry(
        self,
        run_id: str,
        entry: WorkingMemoryEntry,
    ) -> WorkingMemoryEntry:
        def operation() -> None:
            with self._connection() as conn:
                self._ensure_run_exists(conn, run_id)
                with conn.cursor() as cursor:
                    dumped = entry.model_dump(mode="json")
                    cursor.execute(
                        """
                        insert into doxagent.working_memory_entries
                            (entry_id, run_id, ticker, author_agent, content_type, payload,
                             evidence_refs, entry_json, created_at)
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        on conflict (entry_id) do nothing
                        """,
                        (
                            entry.entry_id,
                            run_id,
                            entry.ticker,
                            entry.author_agent.value,
                            entry.content_type,
                            self._jsonb(dumped["payload"]),
                            self._jsonb([]),
                            self._jsonb(dumped),
                            entry.created_at,
                        ),
                    )
                self._touch_run(conn, run_id)
                self._refresh_run_summary_counts(conn, run_id)

        self._retry(
            operation,
            operation_name="blackboard.insert_working_memory_entry",
            table="working_memory_entries",
            payload_bytes=estimate_json_payload_bytes(entry.model_dump(mode="json")),
        )
        return entry

    def upsert_objection(self, run_id: str, objection: Objection) -> Objection:
        def operation() -> None:
            with self._connection() as conn:
                self._ensure_run_exists(conn, run_id)
                with conn.cursor() as cursor:
                    self._execute_upsert_objection(cursor, run_id, objection)
                self._touch_run(conn, run_id)
                self._refresh_run_summary_counts(conn, run_id)

        self._retry(
            operation,
            operation_name="blackboard.upsert_objection",
            table="objections",
            payload_bytes=estimate_json_payload_bytes(objection.model_dump(mode="json")),
        )
        return objection

    def insert_delegation(self, run_id: str, delegation: Delegation) -> Delegation:
        def operation() -> None:
            with self._connection() as conn:
                self._ensure_run_exists(conn, run_id)
                with conn.cursor() as cursor:
                    dumped = delegation.model_dump(mode="json")
                    scope = delegation.blocking_scope
                    cursor.execute(
                        """
                        insert into doxagent.delegations
                            (delegation_id, run_id, requester_agent, target_agent, status,
                             document_type, object_id, field_path, blocking_scope_json,
                             delegation_json)
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        on conflict (delegation_id) do nothing
                        """,
                        (
                            delegation.delegation_id,
                            run_id,
                            delegation.requester_agent.value,
                            delegation.target_agent.value,
                            delegation.status.value,
                            scope.document_type.value,
                            scope.document_id or scope.expectation_id,
                            scope.field_path,
                            self._jsonb(dumped["blocking_scope"]),
                            self._jsonb(dumped),
                        ),
                    )
                self._touch_run(conn, run_id)
                self._refresh_run_summary_counts(conn, run_id)

        self._retry(
            operation,
            operation_name="blackboard.insert_delegation",
            table="delegations",
            payload_bytes=estimate_json_payload_bytes(delegation.model_dump(mode="json")),
        )
        return delegation

    def update_objection(self, run_id: str, objection: Objection) -> Objection:
        def operation() -> None:
            with self._connection() as conn:
                with conn.cursor() as cursor:
                    self._execute_update_objection(cursor, run_id, objection)
                    if cursor.rowcount == 0:
                        raise RunNotFoundError(f"Objection not found: {objection.objection_id}")
                self._touch_run(conn, run_id)
                self._refresh_run_summary_counts(conn, run_id)

        self._retry(
            operation,
            operation_name="blackboard.update_objection",
            table="objections",
            payload_bytes=estimate_json_payload_bytes(objection.model_dump(mode="json")),
        )
        return objection

    def update_delegation(self, run_id: str, delegation: Delegation) -> Delegation:
        def operation() -> None:
            with self._connection() as conn:
                with conn.cursor() as cursor:
                    dumped = delegation.model_dump(mode="json")
                    scope = delegation.blocking_scope
                    cursor.execute(
                        """
                        update doxagent.delegations
                        set requester_agent = %s,
                            target_agent = %s,
                            status = %s,
                            document_type = %s,
                            object_id = %s,
                            field_path = %s,
                            blocking_scope_json = %s,
                            delegation_json = %s
                        where run_id = %s
                          and delegation_id = %s
                        """,
                        (
                            delegation.requester_agent.value,
                            delegation.target_agent.value,
                            delegation.status.value,
                            scope.document_type.value,
                            scope.document_id or scope.expectation_id,
                            scope.field_path,
                            self._jsonb(dumped["blocking_scope"]),
                            self._jsonb(dumped),
                            run_id,
                            delegation.delegation_id,
                        ),
                    )
                    if cursor.rowcount == 0:
                        raise RunNotFoundError(f"Delegation not found: {delegation.delegation_id}")
                self._touch_run(conn, run_id)
                self._refresh_run_summary_counts(conn, run_id)

        self._retry(
            operation,
            operation_name="blackboard.update_delegation",
            table="delegations",
            payload_bytes=estimate_json_payload_bytes(delegation.model_dump(mode="json")),
        )
        return delegation

    def apply_patch_commit(
        self,
        run_id: str,
        belief_state: BeliefStateSnapshot,
        commit: CommitLogEntry,
    ) -> CommitLogEntry:
        patch = commit.patch
        document_type = patch.target.document_type
        bucket = belief_state.documents.get(document_type, {})

        def operation() -> None:
            with self._connection() as conn:
                self._ensure_run_exists(conn, run_id)
                with conn.cursor() as cursor:
                    dumped_commit = commit.model_dump(mode="json")
                    cursor.execute(
                        """
                        update doxagent.belief_state_snapshots
                        set documents = jsonb_set(
                                coalesce(documents, '{}'::jsonb),
                                %s,
                                %s,
                                true
                            ),
                            commit_ids = coalesce(commit_ids, '[]'::jsonb) || %s
                        where run_id = %s
                        """,
                        (
                            [document_type.value],
                            self._jsonb(bucket),
                            self._jsonb([commit.commit_id]),
                            run_id,
                        ),
                    )
                    if cursor.rowcount == 0:
                        raise RunNotFoundError(f"Belief state not found for run: {run_id}")
                    cursor.execute(
                        """
                        insert into doxagent.commit_log_entries
                            (commit_id, run_id, patch_id, document_type, object_id,
                             field_path, author_agent, trigger_reason, evidence_refs,
                             commit_json, created_at)
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        on conflict (commit_id) do nothing
                        """,
                        (
                            commit.commit_id,
                            run_id,
                            patch.patch_id,
                            document_type.value,
                            patch.target.document_id or patch.target.expectation_id,
                            patch.target.field_path,
                            patch.author_agent.value,
                            commit.trigger_reason,
                            self._jsonb([]),
                            self._jsonb(dumped_commit),
                            commit.created_at,
                        ),
                    )
                self._touch_run(conn, run_id)
                self._refresh_run_summary_counts(conn, run_id)

        self._retry(
            operation,
            operation_name="blackboard.apply_patch_commit",
            table="belief_state_snapshots",
            payload_bytes=estimate_json_payload_bytes(
                {
                    document_type.value: bucket,
                    "commit": commit.model_dump(mode="json"),
                }
            ),
        )
        return commit

    def mutate(self, run_id: str, mutator: RunMutator) -> BlackboardRun:
        updated: BlackboardRun | None = None

        def operation() -> None:
            nonlocal updated
            with self._read_connection() as conn:
                run = self._get_run(conn, run_id, lock=False)
            updated = mutator(run)
            with self._connection() as conn:
                self._lock_run(conn, run_id)
                self._replace_run(conn, updated, bump_version=True)

        self._retry(
            operation,
            operation_name="blackboard.mutate",
            table="blackboard_runs",
        )
        if updated is None:
            raise RuntimeError(f"Blackboard mutate did not produce an updated run: {run_id}")
        return updated.model_copy(deep=True)

    @contextmanager
    def _connection(self, *, autocommit: bool = False) -> Iterator[Any]:
        psycopg = self._psycopg()
        kwargs = {"autocommit": True} if autocommit else {}
        with connect_postgres(psycopg, self.database_url, **kwargs) as conn:
            yield conn

    @contextmanager
    def _read_connection(self) -> Iterator[Any]:
        with self._connection(autocommit=True) as conn:
            yield conn

    def _retry(
        self,
        operation: Any,
        *,
        operation_name: str = "blackboard.postgres_operation",
        table: str | None = None,
        payload_bytes: int | None = None,
    ) -> Any:
        psycopg = self._psycopg()
        try:
            return retry_postgres_operation(psycopg, operation)
        except postgres_database_error(psycopg) as exc:
            record_postgres_failure(
                exc,
                database_url=self.database_url,
                operation=operation_name,
                table=table,
                payload_bytes=payload_bytes,
                read_only_status=self._read_only_status(),
            )
            raise

    def _insert_run(self, conn: Any, run: BlackboardRun) -> None:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                insert into doxagent.blackboard_runs
                    (run_id, ticker, created_by, workflow_state, created_at)
                values (%s, %s, %s, %s, %s)
                """,
                (
                    run.run_id,
                    run.ticker,
                    run.created_by.value,
                    run.workflow_state.value,
                    run.created_at,
                ),
            )
        self._replace_children(conn, run)

    def _replace_run(self, conn: Any, run: BlackboardRun, *, bump_version: bool) -> None:
        with conn.cursor() as cursor:
            version_sql = "version = version + 1," if bump_version else ""
            cursor.execute(
                f"""
                update doxagent.blackboard_runs
                set ticker = %s,
                    created_by = %s,
                    workflow_state = %s,
                    {version_sql}
                    updated_at = now()
                where run_id = %s
                """,
                (
                    run.ticker,
                    run.created_by.value,
                    run.workflow_state.value,
                    run.run_id,
                ),
            )
        self._replace_children(conn, run)

    def _replace_children(self, conn: Any, run: BlackboardRun) -> None:
        with conn.cursor() as cursor:
            for table in (
                "working_memory_entries",
                "commit_log_entries",
                "objections",
                "delegations",
                "belief_state_snapshots",
            ):
                cursor.execute(f"delete from doxagent.{table} where run_id = %s", (run.run_id,))
        self._record_payload_sizes(run)
        self._insert_belief_state(conn, run)
        self._insert_working_memory(conn, run)
        self._insert_commit_log(conn, run)
        self._insert_objections(conn, run)
        self._insert_delegations(conn, run)
        self._upsert_run_summary(conn, run)

    def _insert_belief_state(self, conn: Any, run: BlackboardRun) -> None:
        belief = run.belief_state
        with conn.cursor() as cursor:
            cursor.execute(
                """
                insert into doxagent.belief_state_snapshots
                    (snapshot_id, run_id, ticker, documents, commit_ids, created_at)
                values (%s, %s, %s, %s, %s, %s)
                """,
                (
                    belief.snapshot_id,
                    run.run_id,
                    belief.ticker,
                    self._jsonb(belief.model_dump(mode="json")["documents"]),
                    self._jsonb(belief.model_dump(mode="json")["commit_ids"]),
                    belief.created_at,
                ),
            )

    def _insert_working_memory(self, conn: Any, run: BlackboardRun) -> None:
        with conn.cursor() as cursor:
            for entry in run.working_memory:
                dumped = entry.model_dump(mode="json")
                cursor.execute(
                    """
                    insert into doxagent.working_memory_entries
                        (entry_id, run_id, ticker, author_agent, content_type, payload,
                         evidence_refs, entry_json, created_at)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        entry.entry_id,
                        run.run_id,
                        entry.ticker,
                        entry.author_agent.value,
                        entry.content_type,
                        self._jsonb(dumped["payload"]),
                        self._jsonb([]),
                        self._jsonb(dumped),
                        entry.created_at,
                    ),
                )

    def _insert_commit_log(self, conn: Any, run: BlackboardRun) -> None:
        with conn.cursor() as cursor:
            for commit in run.commit_log:
                patch = commit.patch
                dumped = commit.model_dump(mode="json")
                cursor.execute(
                    """
                    insert into doxagent.commit_log_entries
                        (commit_id, run_id, patch_id, document_type, object_id, field_path,
                         author_agent, trigger_reason, evidence_refs, commit_json, created_at)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        commit.commit_id,
                        run.run_id,
                        patch.patch_id,
                        patch.target.document_type.value,
                        patch.target.document_id or patch.target.expectation_id,
                        patch.target.field_path,
                        patch.author_agent.value,
                        commit.trigger_reason,
                        self._jsonb([]),
                        self._jsonb(dumped),
                        commit.created_at,
                    ),
                )

    def _insert_objections(self, conn: Any, run: BlackboardRun) -> None:
        with conn.cursor() as cursor:
            for objection in run.objections:
                dumped = objection.model_dump(mode="json")
                cursor.execute(
                    """
                    insert into doxagent.objections
                        (objection_id, run_id, source_agent, status, severity,
                         taxonomy, dedupe_hash, target_path, merged_objection_ids,
                         document_type, object_id, field_path, target_json, objection_json)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        objection.objection_id,
                        run.run_id,
                        objection.source_agent.value,
                        objection.status.value,
                        objection.severity.value,
                        objection.taxonomy,
                        objection.dedupe_hash,
                        objection.target_path,
                        self._jsonb(dumped["merged_objection_ids"]),
                        objection.target.document_type.value,
                        objection.target.document_id or objection.target.expectation_id,
                        objection.target.field_path,
                        self._jsonb(dumped["target"]),
                        self._jsonb(dumped),
                    ),
                )

    def _insert_delegations(self, conn: Any, run: BlackboardRun) -> None:
        with conn.cursor() as cursor:
            for delegation in run.delegations:
                dumped = delegation.model_dump(mode="json")
                scope = delegation.blocking_scope
                cursor.execute(
                    """
                    insert into doxagent.delegations
                        (delegation_id, run_id, requester_agent, target_agent, status,
                         document_type, object_id, field_path, blocking_scope_json,
                         delegation_json)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        delegation.delegation_id,
                        run.run_id,
                        delegation.requester_agent.value,
                        delegation.target_agent.value,
                        delegation.status.value,
                        scope.document_type.value,
                        scope.document_id or scope.expectation_id,
                        scope.field_path,
                        self._jsonb(dumped["blocking_scope"]),
                        self._jsonb(dumped),
                    ),
                )

    def _upsert_run_summary(
        self,
        conn: Any,
        run: BlackboardRun,
    ) -> None:
        with conn.cursor() as cursor:
            if not self._relation_exists(cursor, "doxagent.run_summaries"):
                return
            belief_dump = run.belief_state.model_dump(mode="json")
            documents = belief_dump.get("documents", {})
            stable_document_types = (
                sorted(str(key) for key in documents)
                if isinstance(documents, dict)
                else []
            )
            full_payload_ref = {
                "storage": "supabase_child_tables",
                "tables": [
                    "belief_state_snapshots",
                    "working_memory_entries",
                    "commit_log_entries",
                    "objections",
                    "delegations",
                ],
            }
            cursor.execute(
                """
                insert into doxagent.run_summaries
                    (run_id, ticker, workflow_state, stable_document_types,
                     working_memory_count, commit_count, unresolved_objection_count,
                     blocking_delegation_count, evidence_ref_count, full_payload_ref)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (run_id) do update set
                    ticker = excluded.ticker,
                    workflow_state = excluded.workflow_state,
                    stable_document_types = excluded.stable_document_types,
                    working_memory_count = excluded.working_memory_count,
                    commit_count = excluded.commit_count,
                    unresolved_objection_count = excluded.unresolved_objection_count,
                    blocking_delegation_count = excluded.blocking_delegation_count,
                    evidence_ref_count = excluded.evidence_ref_count,
                    full_payload_ref = excluded.full_payload_ref,
                    updated_at = now()
                """,
                (
                    run.run_id,
                    run.ticker,
                    run.workflow_state.value,
                    self._jsonb(stable_document_types),
                    len(run.working_memory),
                    len(run.commit_log),
                    sum(
                        1
                        for item in run.objections
                        if item.status.value in {"open", "unresolved"}
                    ),
                    sum(
                        1
                        for item in run.delegations
                        if item.status.value in {"open", "assigned"}
                    ),
                    0,
                    self._jsonb(full_payload_ref),
                ),
            )

    def _document_bundle_row(
        self,
        conn: Any,
        *,
        ticker: str,
        run_id: str,
        document_types: list[DocumentType],
    ) -> Any | None:
        bucket_select = ", ".join(
            f"s.documents -> %s as document_bucket_{index}"
            for index, _document_type in enumerate(document_types)
        )
        if not bucket_select:
            bucket_select = "'{}'::jsonb as no_document_buckets"
        sql = f"""
            select b.run_id, b.ticker, b.created_by, b.workflow_state,
                   b.created_at, s.snapshot_id, s.commit_ids, s.created_at,
                   {bucket_select}
            from doxagent.blackboard_runs b
            join doxagent.belief_state_snapshots s on s.run_id = b.run_id
            where b.ticker = %s
              and b.run_id = %s
            limit 1
        """
        params: list[Any] = [
            *(document_type.value for document_type in document_types),
            ticker,
            run_id,
        ]
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone()

    def _document_bundle_rows(
        self,
        conn: Any,
        *,
        ticker: str,
        document_types: list[DocumentType],
        limit: int,
    ) -> list[Any]:
        bucket_select = ", ".join(
            f"s.documents -> %s as document_bucket_{index}"
            for index, _document_type in enumerate(document_types)
        )
        if not bucket_select:
            bucket_select = "'{}'::jsonb as no_document_buckets"
        sql = f"""
            select b.run_id, b.ticker, b.created_by, b.workflow_state,
                   b.created_at, s.snapshot_id, s.commit_ids, s.created_at,
                   {bucket_select}
            from doxagent.blackboard_runs b
            join doxagent.belief_state_snapshots s on s.run_id = b.run_id
            where b.ticker = %s
            order by b.created_at desc
            limit %s
        """
        params: list[Any] = [
            *(document_type.value for document_type in document_types),
            ticker,
            limit,
        ]
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()

    def _document_run_from_row(
        self,
        row: Any,
        document_types: list[DocumentType],
    ) -> BlackboardRun:
        documents: dict[DocumentType, dict[str, Any]] = {}
        for index, document_type in enumerate(document_types):
            bucket = self._coerce_json(row[8 + index])
            documents[document_type] = bucket if isinstance(bucket, dict) else {}
        return BlackboardRun(
            run_id=row[0],
            ticker=row[1],
            created_by=AgentName(row[2]),
            workflow_state=WorkflowState(row[3]),
            created_at=row[4],
            belief_state=BeliefStateSnapshot(
                snapshot_id=row[5],
                ticker=row[1],
                documents=documents,
                commit_ids=self._coerce_json(row[6]) or [],
                created_at=row[7],
            ),
            working_memory=[],
            objections=[],
            delegations=[],
            commit_log=[],
        )

    def _execute_upsert_objection(
        self,
        cursor: Any,
        run_id: str,
        objection: Objection,
    ) -> None:
        dumped = objection.model_dump(mode="json")
        target = objection.target
        cursor.execute(
            """
            insert into doxagent.objections
                (objection_id, run_id, source_agent, status, severity,
                 taxonomy, dedupe_hash, target_path, merged_objection_ids,
                 document_type, object_id, field_path, target_json, objection_json)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (objection_id) do update set
                source_agent = excluded.source_agent,
                status = excluded.status,
                severity = excluded.severity,
                taxonomy = excluded.taxonomy,
                dedupe_hash = excluded.dedupe_hash,
                target_path = excluded.target_path,
                merged_objection_ids = excluded.merged_objection_ids,
                document_type = excluded.document_type,
                object_id = excluded.object_id,
                field_path = excluded.field_path,
                target_json = excluded.target_json,
                objection_json = excluded.objection_json
            """,
            (
                objection.objection_id,
                run_id,
                objection.source_agent.value,
                objection.status.value,
                objection.severity.value,
                objection.taxonomy,
                objection.dedupe_hash,
                objection.target_path,
                self._jsonb(dumped["merged_objection_ids"]),
                target.document_type.value,
                target.document_id or target.expectation_id,
                target.field_path,
                self._jsonb(dumped["target"]),
                self._jsonb(dumped),
            ),
        )

    def _execute_update_objection(
        self,
        cursor: Any,
        run_id: str,
        objection: Objection,
    ) -> None:
        dumped = objection.model_dump(mode="json")
        target = objection.target
        cursor.execute(
            """
            update doxagent.objections
            set source_agent = %s,
                status = %s,
                severity = %s,
                taxonomy = %s,
                dedupe_hash = %s,
                target_path = %s,
                merged_objection_ids = %s,
                document_type = %s,
                object_id = %s,
                field_path = %s,
                target_json = %s,
                objection_json = %s
            where run_id = %s
              and objection_id = %s
            """,
            (
                objection.source_agent.value,
                objection.status.value,
                objection.severity.value,
                objection.taxonomy,
                objection.dedupe_hash,
                objection.target_path,
                self._jsonb(dumped["merged_objection_ids"]),
                target.document_type.value,
                target.document_id or target.expectation_id,
                target.field_path,
                self._jsonb(dumped["target"]),
                self._jsonb(dumped),
                run_id,
                objection.objection_id,
            ),
        )

    def _touch_run(self, conn: Any, run_id: str) -> None:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                update doxagent.blackboard_runs
                set version = version + 1,
                    updated_at = now()
                where run_id = %s
                """,
                (run_id,),
            )

    def _refresh_run_summary_counts(self, conn: Any, run_id: str) -> None:
        with conn.cursor() as cursor:
            if not self._relation_exists(cursor, "doxagent.run_summaries"):
                return
            cursor.execute(
                """
                update doxagent.run_summaries
                set working_memory_count = (
                        select count(*) from doxagent.working_memory_entries where run_id = %s
                    ),
                    commit_count = (
                        select count(*) from doxagent.commit_log_entries where run_id = %s
                    ),
                    unresolved_objection_count = (
                        select count(*) from doxagent.objections
                        where run_id = %s and status in ('open', 'unresolved')
                    ),
                    blocking_delegation_count = (
                        select count(*) from doxagent.delegations
                        where run_id = %s and status in ('open', 'assigned')
                    ),
                    updated_at = now()
                where run_id = %s
                """,
                (run_id, run_id, run_id, run_id, run_id),
            )

    def _get_run(self, conn: Any, run_id: str, *, lock: bool) -> BlackboardRun:
        lock_clause = " for update" if lock else ""
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                select run_id, ticker, created_by, workflow_state, created_at
                from doxagent.blackboard_runs
                where run_id = %s
                {lock_clause}
                """,
                (run_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise RunNotFoundError(f"Blackboard run not found: {run_id}")
        run = BlackboardRun(
            run_id=row[0],
            ticker=row[1],
            created_by=AgentName(row[2]),
            workflow_state=WorkflowState(row[3]),
            created_at=row[4],
            belief_state=self._get_belief_state(conn, run_id),
            working_memory=self._get_json_models(
                conn,
                "working_memory_entries",
                "entry_json",
                run_id,
                WorkingMemoryEntry,
            ),
            commit_log=self._get_json_models(
                conn,
                "commit_log_entries",
                "commit_json",
                run_id,
                CommitLogEntry,
            ),
            objections=self._get_json_models(
                conn,
                "objections",
                "objection_json",
                run_id,
                Objection,
            ),
            delegations=self._get_json_models(
                conn,
                "delegations",
                "delegation_json",
                run_id,
                Delegation,
            ),
        )
        self._record_full_read(run)
        return run

    def _get_belief_state(self, conn: Any, run_id: str) -> BeliefStateSnapshot:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                select snapshot_id, ticker, documents, commit_ids, created_at
                from doxagent.belief_state_snapshots
                where run_id = %s
                """,
                (run_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise RunNotFoundError(f"Belief state not found for run: {run_id}")
        return BeliefStateSnapshot(
            snapshot_id=row[0],
            ticker=row[1],
            documents=self._coerce_json(row[2]),
            commit_ids=self._coerce_json(row[3]),
            created_at=row[4],
        )

    def _get_json_models(
        self,
        conn: Any,
        table: str,
        column: str,
        run_id: str,
        model_type: Any,
    ) -> list[Any]:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                select {column}
                from doxagent.{table}
                where run_id = %s
                order by created_at asc, {column}->>'{self._id_key(table)}' asc
                """,
                (run_id,),
            )
            rows = cursor.fetchall()
        return [model_type.model_validate(self._coerce_json(row[0])) for row in rows]

    def _ensure_run_exists(self, conn: Any, run_id: str) -> None:
        with conn.cursor() as cursor:
            cursor.execute(
                "select 1 from doxagent.blackboard_runs where run_id = %s",
                (run_id,),
            )
            exists = cursor.fetchone()
        if exists is None:
            raise RunNotFoundError(f"Blackboard run not found: {run_id}")

    def _lock_run(self, conn: Any, run_id: str) -> None:
        with conn.cursor() as cursor:
            cursor.execute(
                "select 1 from doxagent.blackboard_runs where run_id = %s for update",
                (run_id,),
            )
            exists = cursor.fetchone()
        if exists is None:
            raise RunNotFoundError(f"Blackboard run not found: {run_id}")

    def _record_payload_sizes(
        self,
        run: BlackboardRun,
    ) -> None:
        self._record_payload(
            operation="blackboard.replace_children",
            table="belief_state_snapshots",
            run_id=run.run_id,
            payload=run.belief_state.model_dump(mode="json"),
            item_count=1,
        )
        self._record_payload(
            operation="blackboard.replace_children",
            table="working_memory_entries",
            run_id=run.run_id,
            payload=[entry.model_dump(mode="json") for entry in run.working_memory],
            item_count=len(run.working_memory),
        )
        self._record_payload(
            operation="blackboard.replace_children",
            table="commit_log_entries",
            run_id=run.run_id,
            payload=[entry.model_dump(mode="json") for entry in run.commit_log],
            item_count=len(run.commit_log),
        )
        self._record_payload(
            operation="blackboard.replace_children",
            table="objections",
            run_id=run.run_id,
            payload=[item.model_dump(mode="json") for item in run.objections],
            item_count=len(run.objections),
        )
        self._record_payload(
            operation="blackboard.replace_children",
            table="delegations",
            run_id=run.run_id,
            payload=[item.model_dump(mode="json") for item in run.delegations],
            item_count=len(run.delegations),
        )
    def _record_payload(
        self,
        *,
        operation: str,
        table: str,
        run_id: str,
        payload: Any,
        item_count: int,
    ) -> None:
        record_postgres_payload(
            operation=operation,
            table=table,
            run_id=run_id,
            payload_bytes=estimate_json_payload_bytes(payload),
            item_count=item_count,
        )

    def _record_full_read(self, run: BlackboardRun) -> None:
        child_counts = {
            "working_memory_count": len(run.working_memory),
            "commit_count": len(run.commit_log),
            "objection_count": len(run.objections),
            "delegation_count": len(run.delegations),
        }
        payload_bytes = estimate_json_payload_bytes(
            {
                "documents": run.belief_state.model_dump(mode="json").get("documents", {}),
                "working_memory": [
                    entry.model_dump(mode="json") for entry in run.working_memory
                ],
                "commit_log": [commit.model_dump(mode="json") for commit in run.commit_log],
                "objections": [item.model_dump(mode="json") for item in run.objections],
                "delegations": [item.model_dump(mode="json") for item in run.delegations],
            }
        )
        self._record_full_read_event(
            operation="blackboard.full_read.get",
            ticker=run.ticker,
            run_id=run.run_id,
            estimated_payload_bytes=payload_bytes,
            child_counts=child_counts,
        )

    def _record_full_read_event(
        self,
        *,
        operation: str,
        ticker: str | None,
        run_id: str | None,
        estimated_payload_bytes: int | None,
        child_counts: dict[str, int],
    ) -> None:
        now = time.monotonic()
        if now - self._full_read_window_started_at >= 60:
            self._full_read_window_started_at = now
            self._full_read_count_in_window = 0
        self._full_read_count_in_window += 1
        should_warn = (
            (
                estimated_payload_bytes is not None
                and estimated_payload_bytes >= _FULL_READ_WARNING_BYTES
            )
            or self._full_read_count_in_window == _FULL_READ_WARNING_PER_MINUTE
            or operation.endswith("list_by_ticker")
        )
        if not should_warn:
            return
        stack = "".join(traceback.format_stack(limit=8)[:-1]).strip()
        logger.warning(
            "blackboard full-read compatibility API used",
            extra={
                "operation": operation,
                "ticker": ticker,
                "run_id": run_id,
                "estimated_payload_bytes": estimated_payload_bytes,
                "child_counts": child_counts,
                "full_read_count_in_window": self._full_read_count_in_window,
                "stack": stack,
                "logged_at": datetime.now(UTC).isoformat(),
            },
        )

    def _relation_exists(self, cursor: Any, relation: str) -> bool:
        cursor.execute("select to_regclass(%s)", (relation,))
        row = cursor.fetchone()
        return bool(row and row[0])

    def _read_only_status(self) -> dict[str, Any]:
        try:
            with self._connection(autocommit=True) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        select current_setting('transaction_read_only', true),
                               current_setting('default_transaction_read_only', true),
                               pg_is_in_recovery()
                        """
                    )
                    row = cursor.fetchone()
            return {
                "transaction_read_only": row[0] if row else None,
                "default_transaction_read_only": row[1] if row else None,
                "pg_is_in_recovery": row[2] if row else None,
            }
        except Exception as exc:  # pragma: no cover - diagnostic best effort only
            return {"status_error": str(exc)[:500]}

    def _jsonb(self, value: Any) -> Any:
        return self._jsonb_type()(value)

    def _coerce_json(self, value: Any) -> Any:
        if isinstance(value, str):
            return json.loads(value)
        return value

    def _id_key(self, table: str) -> str:
        return {
            "working_memory_entries": "entry_id",
            "commit_log_entries": "commit_id",
            "objections": "objection_id",
            "delegations": "delegation_id",
        }[table]

    def _psycopg(self) -> Any:
        try:
            return import_module("psycopg")
        except ImportError as exc:  # pragma: no cover - depends on optional install state
            raise RuntimeError("psycopg is required for PostgresBlackboardRepository.") from exc

    def _jsonb_type(self) -> Any:
        try:
            json_module = import_module("psycopg.types.json")
        except ImportError as exc:  # pragma: no cover - depends on optional install state
            raise RuntimeError("psycopg is required for Postgres JSONB persistence.") from exc
        return json_module.Jsonb
