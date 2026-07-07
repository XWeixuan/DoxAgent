"""Persistence backends for model usage audit events."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from doxagent.model_usage.schema import ModelUsageEvent
from doxagent.monitoring.schema import canonical_json
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
        newest_first: bool = False,
    ) -> list[ModelUsageEvent]:
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
        if limit is not None:
            rows = rows[: max(0, limit)]
        return [event.model_copy(deep=True) for event in rows]


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
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [ModelUsageEvent.model_validate_json(str(row["payload_json"])) for row in rows]

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


def model_usage_repository_from_settings(
    settings: DoxAgentSettings | None = None,
) -> ModelUsageRepository:
    resolved = settings or DoxAgentSettings()
    if resolved.model_usage_storage_mode == "memory":
        return InMemoryModelUsageRepository()
    return SQLiteModelUsageRepository(resolved.model_usage_sqlite_path)


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
