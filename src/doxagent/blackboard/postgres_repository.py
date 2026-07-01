"""Postgres-backed Blackboard repository for Supabase direct connections."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import import_module
from typing import Any

from doxagent.blackboard.errors import RunNotFoundError
from doxagent.blackboard.repository import RunMutator
from doxagent.blackboard.state import BlackboardRun, WorkflowState
from doxagent.models import (
    AgentName,
    BeliefStateSnapshot,
    CommitLogEntry,
    Delegation,
    EvidenceRef,
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


class PostgresBlackboardRepository:
    """Persist Blackboard business state in Postgres.

    The implementation intentionally talks to Postgres directly instead of the
    Supabase client API. Supabase credentials stay in environment variables.
    """

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

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
                return [self._get_run(conn, run_id, lock=False) for run_id in run_ids]

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
        evidence_refs = self._collect_evidence_refs(run)
        self._record_payload_sizes(run, evidence_refs)
        self._upsert_evidence_refs(conn, evidence_refs)
        self._insert_belief_state(conn, run)
        self._insert_working_memory(conn, run)
        self._insert_commit_log(conn, run)
        self._insert_objections(conn, run)
        self._insert_delegations(conn, run)
        self._upsert_run_summary(conn, run, evidence_refs)

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
                        self._jsonb(dumped["evidence_refs"]),
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
                        self._jsonb(patch.model_dump(mode="json")["evidence_refs"]),
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

    def _upsert_evidence_refs(self, conn: Any, evidence_refs: dict[str, EvidenceRef]) -> None:
        with conn.cursor() as cursor:
            for evidence in evidence_refs.values():
                dumped = evidence.model_dump(mode="json")
                cursor.execute(
                    """
                    insert into doxagent.evidence_refs
                        (evidence_id, source_type, source_id, title, summary,
                         retrieval_metadata, confidence, citation_scope, evidence_json)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (evidence_id) do update set
                        source_type = excluded.source_type,
                        source_id = excluded.source_id,
                        title = excluded.title,
                        summary = excluded.summary,
                        retrieval_metadata = excluded.retrieval_metadata,
                        confidence = excluded.confidence,
                        citation_scope = excluded.citation_scope,
                        evidence_json = excluded.evidence_json
                    """,
                    (
                        evidence.evidence_id,
                        evidence.source_type.value,
                        evidence.source_id,
                        evidence.title,
                        evidence.summary,
                        self._jsonb(dumped["retrieval_metadata"]),
                        evidence.confidence,
                        evidence.citation_scope,
                        self._jsonb(dumped),
                    ),
                )

    def _upsert_run_summary(
        self,
        conn: Any,
        run: BlackboardRun,
        evidence_refs: dict[str, EvidenceRef],
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
                    "evidence_refs",
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
                    len(evidence_refs),
                    self._jsonb(full_payload_ref),
                ),
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
        return BlackboardRun(
            run_id=row[0],
            ticker=row[1],
            created_by=row[2],
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

    def _collect_evidence_refs(self, run: BlackboardRun) -> dict[str, EvidenceRef]:
        refs: dict[str, EvidenceRef] = {}
        for entry in run.working_memory:
            refs.update({item.evidence_id: item for item in entry.evidence_refs})
        for commit in run.commit_log:
            refs.update({item.evidence_id: item for item in commit.patch.evidence_refs})
        for objection in run.objections:
            refs.update({item.evidence_id: item for item in objection.evidence_refs})
        return refs

    def _record_payload_sizes(
        self,
        run: BlackboardRun,
        evidence_refs: dict[str, EvidenceRef],
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
        self._record_payload(
            operation="blackboard.replace_children",
            table="evidence_refs",
            run_id=run.run_id,
            payload=[item.model_dump(mode="json") for item in evidence_refs.values()],
            item_count=len(evidence_refs),
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
