"""Workflow checkpoint persistence contracts and implementations."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import import_module
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from doxagent.models import NonEmptyStr, new_id
from doxagent.postgres import (
    connect_postgres,
    estimate_json_payload_bytes,
    postgres_database_error,
    record_postgres_failure,
    record_postgres_payload,
    retry_postgres_operation,
)
from doxagent.settings import DoxAgentSettings
from doxagent.workflows.schema import WorkflowCheckpoint, WorkflowNode, WorkflowRunStatus


class WorkflowCheckpointRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint_id: NonEmptyStr
    run_id: NonEmptyStr
    ticker: NonEmptyStr
    checkpoint: WorkflowCheckpoint
    status: WorkflowRunStatus
    next_node: WorkflowNode | None = None
    completed_nodes: list[WorkflowNode]
    is_latest: bool
    created_at: datetime


class WorkflowCheckpointRepository(Protocol):
    def save_checkpoint(
        self,
        checkpoint: WorkflowCheckpoint,
        *,
        is_latest: bool = True,
    ) -> WorkflowCheckpointRecord: ...

    def get_latest(self, run_id: str) -> WorkflowCheckpoint: ...

    def list_checkpoints(self, run_id: str) -> list[WorkflowCheckpointRecord]: ...


class InMemoryWorkflowCheckpointRepository:
    def __init__(self) -> None:
        self._records: dict[str, list[WorkflowCheckpointRecord]] = {}

    def save_checkpoint(
        self,
        checkpoint: WorkflowCheckpoint,
        *,
        is_latest: bool = True,
    ) -> WorkflowCheckpointRecord:
        if is_latest:
            self._records[checkpoint.run_id] = [
                record.model_copy(update={"is_latest": False}, deep=True)
                for record in self._records.get(checkpoint.run_id, [])
            ]
        record = WorkflowCheckpointRecord(
            checkpoint_id=new_id("checkpoint"),
            run_id=checkpoint.run_id,
            ticker=checkpoint.ticker,
            checkpoint=checkpoint.model_copy(deep=True),
            status=checkpoint.status,
            next_node=checkpoint.next_node,
            completed_nodes=list(checkpoint.completed_nodes),
            is_latest=is_latest,
            created_at=datetime.now(UTC),
        )
        self._records.setdefault(checkpoint.run_id, []).append(record)
        return record.model_copy(deep=True)

    def get_latest(self, run_id: str) -> WorkflowCheckpoint:
        for record in reversed(self._records.get(run_id, [])):
            if record.is_latest:
                return record.checkpoint.model_copy(deep=True)
        raise KeyError(f"Workflow checkpoint not found: {run_id}")

    def list_checkpoints(self, run_id: str) -> list[WorkflowCheckpointRecord]:
        return [record.model_copy(deep=True) for record in self._records.get(run_id, [])]


class PostgresWorkflowCheckpointRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    @classmethod
    def from_settings(
        cls,
        settings: DoxAgentSettings | None = None,
    ) -> PostgresWorkflowCheckpointRepository:
        resolved = settings or DoxAgentSettings()
        return cls(resolved.require_database_url())

    def save_checkpoint(
        self,
        checkpoint: WorkflowCheckpoint,
        *,
        is_latest: bool = True,
    ) -> WorkflowCheckpointRecord:
        record = WorkflowCheckpointRecord(
            checkpoint_id=new_id("checkpoint"),
            run_id=checkpoint.run_id,
            ticker=checkpoint.ticker,
            checkpoint=checkpoint.model_copy(deep=True),
            status=checkpoint.status,
            next_node=checkpoint.next_node,
            completed_nodes=list(checkpoint.completed_nodes),
            is_latest=is_latest,
            created_at=datetime.now(UTC),
        )
        dumped = record.model_dump(mode="json")
        checkpoint_dump = checkpoint.model_dump(mode="json")
        payload_bytes = estimate_json_payload_bytes(checkpoint_dump)
        record_postgres_payload(
            operation="workflow.save_checkpoint",
            table="workflow_checkpoints",
            run_id=checkpoint.run_id,
            payload_bytes=payload_bytes,
            item_count=1,
        )

        def operation() -> None:
            with self._connection() as conn:
                with conn.cursor() as cursor:
                    if is_latest:
                        cursor.execute(
                            """
                            update doxagent.workflow_checkpoints
                            set is_latest = false
                            where run_id = %s
                              and checkpoint_id <> %s
                            """,
                            (checkpoint.run_id, record.checkpoint_id),
                        )
                    cursor.execute(
                        """
                        insert into doxagent.workflow_checkpoints
                            (checkpoint_id, run_id, ticker, status, next_node,
                             completed_nodes, checkpoint_json, is_latest, created_at)
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        on conflict (checkpoint_id) do update set
                            ticker = excluded.ticker,
                            status = excluded.status,
                            next_node = excluded.next_node,
                            completed_nodes = excluded.completed_nodes,
                            checkpoint_json = excluded.checkpoint_json,
                            is_latest = excluded.is_latest,
                            created_at = excluded.created_at
                        """,
                        (
                            record.checkpoint_id,
                            checkpoint.run_id,
                            checkpoint.ticker,
                            checkpoint.status.value,
                            (
                                checkpoint.next_node.value
                                if checkpoint.next_node is not None
                                else None
                            ),
                            self._jsonb(dumped["completed_nodes"]),
                            self._jsonb(checkpoint_dump),
                            is_latest,
                            record.created_at,
                        ),
                    )
                    if is_latest:
                        self._upsert_run_summary_checkpoint(cursor, record, checkpoint_dump)
                        self._prune_checkpoint_history_if_available(cursor)

        self._retry(
            operation,
            operation_name="workflow.save_checkpoint",
            table="workflow_checkpoints",
            payload_bytes=payload_bytes,
        )
        return record

    def get_latest(self, run_id: str) -> WorkflowCheckpoint:
        records = self._select_records(run_id, latest_only=True)
        if not records:
            raise KeyError(f"Workflow checkpoint not found: {run_id}")
        return records[0].checkpoint

    def list_checkpoints(self, run_id: str) -> list[WorkflowCheckpointRecord]:
        return self._select_records(run_id, latest_only=False)

    def _select_records(
        self,
        run_id: str,
        *,
        latest_only: bool,
    ) -> list[WorkflowCheckpointRecord]:
        where_latest = "and is_latest = true" if latest_only else ""
        def operation() -> list[WorkflowCheckpointRecord]:
            with self._connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"""
                        select checkpoint_id, run_id, ticker, status, next_node, completed_nodes,
                               checkpoint_json, is_latest, created_at
                        from doxagent.workflow_checkpoints
                        where run_id = %s
                        {where_latest}
                        order by created_at desc
                        """,
                        (run_id,),
                    )
                    rows = cursor.fetchall()
            return [self._record_from_row(row) for row in rows]

        return self._retry(operation)

    def _record_from_row(self, row: Any) -> WorkflowCheckpointRecord:
        checkpoint = WorkflowCheckpoint.model_validate(self._coerce_json(row[6]))
        return WorkflowCheckpointRecord(
            checkpoint_id=row[0],
            run_id=row[1],
            ticker=row[2],
            status=WorkflowRunStatus(row[3]),
            next_node=WorkflowNode(row[4]) if row[4] is not None else None,
            completed_nodes=[WorkflowNode(item) for item in self._coerce_json(row[5])],
            checkpoint=checkpoint,
            is_latest=row[7],
            created_at=row[8],
        )

    @contextmanager
    def _connection(self, *, autocommit: bool = False) -> Iterator[Any]:
        psycopg = self._psycopg()
        kwargs = {"autocommit": True} if autocommit else {}
        with connect_postgres(psycopg, self.database_url, **kwargs) as conn:
            yield conn

    def _retry(
        self,
        operation: Any,
        *,
        operation_name: str = "workflow_checkpoint.postgres_operation",
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

    def _upsert_run_summary_checkpoint(
        self,
        cursor: Any,
        record: WorkflowCheckpointRecord,
        checkpoint_dump: dict[str, Any],
    ) -> None:
        if not self._relation_exists(cursor, "doxagent.run_summaries"):
            return
        error_code, error_message = self._checkpoint_error(checkpoint_dump)
        cursor.execute(
            """
            insert into doxagent.run_summaries
                (run_id, ticker, workflow_state, latest_checkpoint_id,
                 latest_checkpoint_status, latest_checkpoint_next_node,
                 latest_checkpoint_created_at, completed_nodes,
                 last_error_code, last_error_message_preview)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (run_id) do update set
                ticker = excluded.ticker,
                workflow_state = excluded.workflow_state,
                latest_checkpoint_id = excluded.latest_checkpoint_id,
                latest_checkpoint_status = excluded.latest_checkpoint_status,
                latest_checkpoint_next_node = excluded.latest_checkpoint_next_node,
                latest_checkpoint_created_at = excluded.latest_checkpoint_created_at,
                completed_nodes = excluded.completed_nodes,
                last_error_code = excluded.last_error_code,
                last_error_message_preview = excluded.last_error_message_preview,
                updated_at = now()
            """,
            (
                record.run_id,
                record.ticker,
                record.status.value,
                record.checkpoint_id,
                record.status.value,
                record.next_node.value if record.next_node is not None else None,
                record.created_at,
                self._jsonb([item.value for item in record.completed_nodes]),
                error_code,
                error_message,
            ),
        )

    def _prune_checkpoint_history_if_available(self, cursor: Any) -> None:
        cursor.execute(
            "select to_regprocedure('doxagent.prune_workflow_checkpoint_history(integer)')"
        )
        row = cursor.fetchone()
        if not row or not row[0]:
            return
        cursor.execute("select doxagent.prune_workflow_checkpoint_history(%s)", (3,))

    def _relation_exists(self, cursor: Any, relation: str) -> bool:
        cursor.execute("select to_regclass(%s)", (relation,))
        row = cursor.fetchone()
        return bool(row and row[0])

    def _checkpoint_error(self, checkpoint_dump: dict[str, Any]) -> tuple[str | None, str | None]:
        metadata = checkpoint_dump.get("metadata")
        if not isinstance(metadata, dict):
            return None, None
        code = metadata.get("last_error_code")
        message = metadata.get("last_error_message") or metadata.get("last_error")
        if not code:
            for summaries in _dict_values(metadata.get("last_agent_results")):
                for item in _list_of_dicts(summaries):
                    if item.get("error_code"):
                        code = item.get("error_code")
                        message = item.get("error_message") or item.get("output_summary")
                        break
                if code:
                    break
        return (
            str(code) if code else None,
            str(message)[:500] if message else None,
        )

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

    def _psycopg(self) -> Any:
        try:
            return import_module("psycopg")
        except ImportError as exc:  # pragma: no cover - depends on optional install state
            raise RuntimeError("psycopg is required for Postgres checkpoint persistence.") from exc

    def _jsonb_type(self) -> Any:
        try:
            json_module = import_module("psycopg.types.json")
        except ImportError as exc:  # pragma: no cover - depends on optional install state
            raise RuntimeError("psycopg is required for Postgres JSONB persistence.") from exc
        return json_module.Jsonb


def _dict_values(value: Any) -> list[Any]:
    return list(value.values()) if isinstance(value, dict) else []


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]
