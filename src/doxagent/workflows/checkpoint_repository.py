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
        with self._connection() as conn:
            with conn.cursor() as cursor:
                if is_latest:
                    cursor.execute(
                        """
                        update doxagent.workflow_checkpoints
                        set is_latest = false
                        where run_id = %s
                        """,
                        (checkpoint.run_id,),
                    )
                cursor.execute(
                    """
                    insert into doxagent.workflow_checkpoints
                        (checkpoint_id, run_id, ticker, status, next_node, completed_nodes,
                         checkpoint_json, is_latest, created_at)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        record.checkpoint_id,
                        checkpoint.run_id,
                        checkpoint.ticker,
                        checkpoint.status.value,
                        checkpoint.next_node.value if checkpoint.next_node is not None else None,
                        self._jsonb(dumped["completed_nodes"]),
                        self._jsonb(checkpoint_dump),
                        is_latest,
                        record.created_at,
                    ),
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
    def _connection(self) -> Iterator[Any]:
        psycopg = self._psycopg()
        with psycopg.connect(self.database_url) as conn:
            yield conn

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
