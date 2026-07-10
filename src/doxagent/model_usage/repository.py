"""Persistence backends for model usage audit events."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol

from doxagent.model_usage.schema import ModelUsageEvent
from doxagent.monitoring.schema import canonical_json
from doxagent.postgres import (
    connect_postgres,
    postgres_database_error,
    record_postgres_failure,
    retry_postgres_operation,
)
from doxagent.settings import DoxAgentSettings


class ModelUsageRepository(Protocol):
    def save_event(self, event: ModelUsageEvent) -> ModelUsageEvent:
        ...

    def list_events(
        self,
        *,
        ticker: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        node: str | None = None,
        model: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        newest_first: bool = False,
    ) -> list[ModelUsageEvent]:
        ...

    def count_events(
        self,
        *,
        ticker: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        node: str | None = None,
        model: str | None = None,
        status: str | None = None,
    ) -> int:
        ...


class InMemoryModelUsageRepository:
    """In-process repository for tests and isolated local runs."""

    def __init__(self, events: Iterable[ModelUsageEvent] | None = None) -> None:
        self._events: dict[str, ModelUsageEvent] = {}
        for event in events or []:
            self.save_event(event)

    def save_event(self, event: ModelUsageEvent) -> ModelUsageEvent:
        self._events[event.event_id] = event.model_copy(deep=True)
        return event.model_copy(deep=True)

    def list_events(
        self,
        *,
        ticker: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        node: str | None = None,
        model: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        newest_first: bool = False,
    ) -> list[ModelUsageEvent]:
        rows = list(self._events.values())
        rows = _filter_events(
            rows,
            ticker=ticker,
            start_time=start_time,
            end_time=end_time,
            node=node,
            model=model,
            status=status,
        )
        rows = sorted(rows, key=lambda event: _aware(event.created_at), reverse=newest_first)
        rows = rows[max(0, offset) :]
        if limit is not None:
            rows = rows[: max(0, limit)]
        return [event.model_copy(deep=True) for event in rows]

    def count_events(
        self,
        *,
        ticker: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        node: str | None = None,
        model: str | None = None,
        status: str | None = None,
    ) -> int:
        return len(
            _filter_events(
                list(self._events.values()),
                ticker=ticker,
                start_time=start_time,
                end_time=end_time,
                node=node,
                model=model,
                status=status,
            )
        )


class SQLiteModelUsageRepository:
    """SQLite-backed repository shared by runtime-scheduler and dashboard API."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if self.path.parent != Path("."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def save_event(self, event: ModelUsageEvent) -> ModelUsageEvent:
        payload = event.model_dump(mode="json")
        with self._connect() as conn:
            conn.execute(
                """
                insert or replace into model_usage_events (
                    event_id,
                    created_at,
                    ticker,
                    run_id,
                    source_message_id,
                    workflow_node,
                    runtime_node,
                    agent_name,
                    task_type,
                    provider,
                    model,
                    status,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    retry_count,
                    fallback_used,
                    payload_json
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    _dt(event.created_at),
                    event.ticker,
                    event.run_id,
                    event.source_message_id,
                    event.workflow_node,
                    event.runtime_node,
                    event.agent_name,
                    event.task_type,
                    event.provider,
                    event.model,
                    event.status,
                    event.input_tokens,
                    event.output_tokens,
                    event.total_tokens,
                    event.retry_count,
                    1 if event.fallback_used else 0,
                    canonical_json(payload),
                ),
            )
        return event.model_copy(deep=True)

    def list_events(
        self,
        *,
        ticker: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        node: str | None = None,
        model: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        newest_first: bool = False,
    ) -> list[ModelUsageEvent]:
        sql = "select payload_json from model_usage_events"
        clauses: list[str] = []
        params: list[object] = []
        if ticker is not None:
            clauses.append("ticker = ?")
            params.append(ticker.strip().upper())
        if start_time is not None:
            clauses.append("created_at >= ?")
            params.append(_dt(start_time))
        if end_time is not None:
            clauses.append("created_at <= ?")
            params.append(_dt(end_time))
        if node is not None:
            clauses.append("(lower(runtime_node) = ? or lower(workflow_node) = ?)")
            normalized_node = node.strip().lower()
            params.extend([normalized_node, normalized_node])
        if model is not None:
            clauses.append("model = ?")
            params.append(model.strip())
        if status is not None:
            clauses.append("lower(status) = ?")
            params.append(status.strip().lower())
        if clauses:
            sql += " where " + " and ".join(clauses)
        direction = "desc" if newest_first else "asc"
        sql += f" order by created_at {direction}, rowid {direction}"
        if limit is not None:
            sql += " limit ?"
            params.append(max(0, limit))
        elif offset > 0:
            sql += " limit -1"
        if offset > 0:
            sql += " offset ?"
            params.append(max(0, offset))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [ModelUsageEvent.model_validate_json(str(row["payload_json"])) for row in rows]

    def count_events(
        self,
        *,
        ticker: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        node: str | None = None,
        model: str | None = None,
        status: str | None = None,
    ) -> int:
        sql, params = self._filtered_sql(
            "select count(*) from model_usage_events",
            ticker=ticker,
            start_time=start_time,
            end_time=end_time,
            node=node,
            model=model,
            status=status,
        )
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row[0]) if row is not None else 0

    @staticmethod
    def _filtered_sql(
        sql: str,
        *,
        ticker: str | None,
        start_time: datetime | None,
        end_time: datetime | None,
        node: str | None,
        model: str | None,
        status: str | None,
    ) -> tuple[str, list[object]]:
        clauses: list[str] = []
        params: list[object] = []
        if ticker is not None:
            clauses.append("ticker = ?")
            params.append(ticker.strip().upper())
        if start_time is not None:
            clauses.append("created_at >= ?")
            params.append(_dt(start_time))
        if end_time is not None:
            clauses.append("created_at <= ?")
            params.append(_dt(end_time))
        if node is not None:
            clauses.append("(lower(runtime_node) = ? or lower(workflow_node) = ?)")
            normalized_node = node.strip().lower()
            params.extend([normalized_node, normalized_node])
        if model is not None:
            clauses.append("model = ?")
            params.append(model.strip())
        if status is not None:
            clauses.append("lower(status) = ?")
            params.append(status.strip().lower())
        if clauses:
            sql += " where " + " and ".join(clauses)
        return sql, params

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists model_usage_events (
                    event_id text primary key,
                    created_at text not null,
                    ticker text,
                    run_id text,
                    source_message_id text,
                    workflow_node text,
                    runtime_node text,
                    agent_name text,
                    task_type text,
                    provider text not null,
                    model text not null,
                    status text not null,
                    input_tokens integer not null,
                    output_tokens integer not null,
                    total_tokens integer not null,
                    retry_count integer not null,
                    fallback_used integer not null,
                    payload_json text not null
                );
                create index if not exists idx_model_usage_events_ticker_created
                    on model_usage_events(ticker, created_at);
                create index if not exists idx_model_usage_events_model_created
                    on model_usage_events(model, created_at);
                create index if not exists idx_model_usage_events_status_created
                    on model_usage_events(status, created_at);
                create index if not exists idx_model_usage_events_run_id
                    on model_usage_events(run_id);
                create index if not exists idx_model_usage_events_source_message_id
                    on model_usage_events(source_message_id);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


class PostgresModelUsageRepository:
    """Supabase/Postgres-backed repository for durable model usage events."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    @classmethod
    def from_settings(
        cls,
        settings: DoxAgentSettings | None = None,
    ) -> PostgresModelUsageRepository:
        resolved = settings or DoxAgentSettings()
        return cls(resolved.require_database_url())

    def save_event(self, event: ModelUsageEvent) -> ModelUsageEvent:
        return self.save_events([event])[0]

    def save_events(self, events: Iterable[ModelUsageEvent]) -> list[ModelUsageEvent]:
        rows = [event.model_copy(deep=True) for event in events]
        if not rows:
            return []

        def operation() -> None:
            with self._connection() as conn:
                with conn.cursor() as cursor:
                    cursor.executemany(
                        """
                        insert into doxagent.model_usage_events (
                            event_id,
                            created_at,
                            ticker,
                            run_id,
                            source_message_id,
                            execution_id,
                            workflow_node,
                            runtime_node,
                            agent_name,
                            task_type,
                            provider,
                            model,
                            status,
                            input_tokens,
                            output_tokens,
                            total_tokens,
                            retry_count,
                            fallback_used,
                            latency_seconds,
                            error_code,
                            error_message
                        )
                        values (
                            %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s
                        )
                        on conflict (event_id) do update set
                            created_at = excluded.created_at,
                            ticker = excluded.ticker,
                            run_id = excluded.run_id,
                            source_message_id = excluded.source_message_id,
                            execution_id = excluded.execution_id,
                            workflow_node = excluded.workflow_node,
                            runtime_node = excluded.runtime_node,
                            agent_name = excluded.agent_name,
                            task_type = excluded.task_type,
                            provider = excluded.provider,
                            model = excluded.model,
                            status = excluded.status,
                            input_tokens = excluded.input_tokens,
                            output_tokens = excluded.output_tokens,
                            total_tokens = excluded.total_tokens,
                            retry_count = excluded.retry_count,
                            fallback_used = excluded.fallback_used,
                            latency_seconds = excluded.latency_seconds,
                            error_code = excluded.error_code,
                            error_message = excluded.error_message
                        """,
                        [
                            (
                                event.event_id,
                                _aware(event.created_at),
                                event.ticker,
                                event.run_id,
                                event.source_message_id,
                                event.execution_id,
                                event.workflow_node,
                                event.runtime_node,
                                event.agent_name,
                                event.task_type,
                                event.provider,
                                event.model,
                                event.status,
                                event.input_tokens,
                                event.output_tokens,
                                event.total_tokens,
                                event.retry_count,
                                event.fallback_used,
                                event.latency_seconds,
                                event.error_code,
                                (event.error_message or "")[:1000] or None,
                            )
                            for event in rows
                        ],
                    )

        self._retry(
            operation,
            operation_name="model_usage.save_events",
        )
        return [event.model_copy(deep=True) for event in rows]

    def list_events(
        self,
        *,
        ticker: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        node: str | None = None,
        model: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        newest_first: bool = False,
    ) -> list[ModelUsageEvent]:
        sql, params = self._filtered_sql(
            """
            select event_id, provider, model, status,
                   input_tokens, output_tokens, total_tokens,
                   retry_count, fallback_used, latency_seconds,
                   ticker, run_id, workflow_node, runtime_node,
                   agent_name, task_type, source_message_id,
                   execution_id, error_code, created_at
            from doxagent.model_usage_events
            """,
            ticker=ticker,
            start_time=start_time,
            end_time=end_time,
            node=node,
            model=model,
            status=status,
        )
        direction = "desc" if newest_first else "asc"
        sql += f" order by created_at {direction}, event_id {direction}"
        if limit is not None:
            sql += " limit %s"
            params.append(max(0, limit))
        if offset > 0:
            sql += " offset %s"
            params.append(max(0, offset))

        def operation() -> list[ModelUsageEvent]:
            with self._connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(sql, params)
                    rows = cursor.fetchall()
            return [self._event_from_row(row) for row in rows]

        return self._retry(
            operation,
            operation_name="model_usage.list_events",
        )

    def count_events(
        self,
        *,
        ticker: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        node: str | None = None,
        model: str | None = None,
        status: str | None = None,
    ) -> int:
        sql, params = self._filtered_sql(
            "select count(*) from doxagent.model_usage_events",
            ticker=ticker,
            start_time=start_time,
            end_time=end_time,
            node=node,
            model=model,
            status=status,
        )

        def operation() -> int:
            with self._connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(sql, params)
                    row = cursor.fetchone()
            return int(row[0]) if row is not None else 0

        return self._retry(
            operation,
            operation_name="model_usage.count_events",
        )

    @staticmethod
    def _filtered_sql(
        sql: str,
        *,
        ticker: str | None,
        start_time: datetime | None,
        end_time: datetime | None,
        node: str | None,
        model: str | None,
        status: str | None,
    ) -> tuple[str, list[object]]:
        clauses: list[str] = []
        params: list[object] = []
        if ticker is not None:
            clauses.append("ticker = %s")
            params.append(ticker.strip().upper())
        if start_time is not None:
            clauses.append("created_at >= %s")
            params.append(_aware(start_time))
        if end_time is not None:
            clauses.append("created_at <= %s")
            params.append(_aware(end_time))
        if node is not None:
            clauses.append("(lower(runtime_node) = %s or lower(workflow_node) = %s)")
            normalized_node = node.strip().lower()
            params.extend([normalized_node, normalized_node])
        if model is not None:
            clauses.append("model = %s")
            params.append(model.strip())
        if status is not None:
            clauses.append("lower(status) = %s")
            params.append(status.strip().lower())
        if clauses:
            sql += " where " + " and ".join(clauses)
        return sql, params

    @staticmethod
    def _event_from_row(row: Any) -> ModelUsageEvent:
        return ModelUsageEvent(
            event_id=row[0],
            provider=row[1],
            model=row[2],
            status=row[3],
            input_tokens=row[4],
            output_tokens=row[5],
            total_tokens=row[6],
            retry_count=row[7],
            fallback_used=row[8],
            latency_seconds=row[9],
            ticker=row[10],
            run_id=row[11],
            workflow_node=row[12],
            runtime_node=row[13],
            agent_name=row[14],
            task_type=row[15],
            source_message_id=row[16],
            execution_id=row[17],
            error_code=row[18],
            created_at=row[19],
        )

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        psycopg = self._psycopg()
        with connect_postgres(psycopg, self.database_url) as conn:
            yield conn

    def _retry(
        self,
        operation: Any,
        *,
        operation_name: str,
    ) -> Any:
        psycopg = self._psycopg()
        try:
            return retry_postgres_operation(psycopg, operation)
        except postgres_database_error(psycopg) as exc:
            record_postgres_failure(
                exc,
                database_url=self.database_url,
                operation=operation_name,
                table="doxagent.model_usage_events",
            )
            raise

    def _psycopg(self) -> Any:
        try:
            return import_module("psycopg")
        except ImportError as exc:
            raise RuntimeError(
                "psycopg is required for Postgres model usage persistence."
            ) from exc



def model_usage_repository_from_settings(
    settings: DoxAgentSettings | None = None,
) -> ModelUsageRepository:
    resolved = settings or DoxAgentSettings()
    if resolved.model_usage_storage_mode == "memory":
        return InMemoryModelUsageRepository()
    if resolved.model_usage_storage_mode == "sqlite":
        return SQLiteModelUsageRepository(resolved.model_usage_sqlite_path)
    return PostgresModelUsageRepository.from_settings(resolved)


def copy_model_usage_sqlite_to_postgres(
    *,
    sqlite_path: str | Path,
    database_url: str,
) -> int:
    """Idempotently copy existing local usage events into Supabase/Postgres."""

    source = SQLiteModelUsageRepository(sqlite_path)
    target = PostgresModelUsageRepository(database_url)
    events = source.list_events(newest_first=False)
    target.save_events(events)
    return len(events)


def _filter_events(
    rows: list[ModelUsageEvent],
    *,
    ticker: str | None,
    start_time: datetime | None,
    end_time: datetime | None,
    node: str | None,
    model: str | None,
    status: str | None,
) -> list[ModelUsageEvent]:
    filtered = rows
    if ticker is not None:
        normalized = ticker.strip().upper()
        filtered = [event for event in filtered if event.ticker == normalized]
    if start_time is not None:
        start = _aware(start_time)
        filtered = [event for event in filtered if _aware(event.created_at) >= start]
    if end_time is not None:
        end = _aware(end_time)
        filtered = [event for event in filtered if _aware(event.created_at) <= end]
    if node is not None:
        normalized_node = node.strip().lower()
        filtered = [
            event
            for event in filtered
            if (event.runtime_node or "").lower() == normalized_node
            or (event.workflow_node or "").lower() == normalized_node
        ]
    if model is not None:
        filtered = [event for event in filtered if event.model == model.strip()]
    if status is not None:
        normalized_status = status.strip().lower()
        filtered = [event for event in filtered if event.status.lower() == normalized_status]
    return filtered


def _dt(value: datetime) -> str:
    return _aware(value).isoformat()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
