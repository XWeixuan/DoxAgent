"""Persistence backends for unified runtime scheduler state."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Protocol, TypeVar

from doxagent.monitoring.schema import canonical_json
from doxagent.runtime_scheduler.schema import (
    DocumentRefreshRequest,
    RuntimeAuditEvent,
    TickerRunState,
)

T = TypeVar("T")


class RuntimeSchedulerRepository(Protocol):
    def get_state(self, ticker: str) -> TickerRunState | None:
        ...

    def list_states(self) -> list[TickerRunState]:
        ...

    def upsert_state(self, state: TickerRunState) -> TickerRunState:
        ...

    def append_audit_event(self, event: RuntimeAuditEvent) -> RuntimeAuditEvent:
        ...

    def list_audit_events(
        self,
        *,
        ticker: str | None = None,
        limit: int = 100,
    ) -> list[RuntimeAuditEvent]:
        ...

    def save_refresh_request(
        self,
        request: DocumentRefreshRequest,
    ) -> DocumentRefreshRequest:
        ...

    def list_refresh_requests(
        self,
        *,
        ticker: str | None = None,
        limit: int = 100,
    ) -> list[DocumentRefreshRequest]:
        ...


class InMemoryRuntimeSchedulerRepository:
    def __init__(self) -> None:
        self._states: dict[str, TickerRunState] = {}
        self._audit_events: dict[str, RuntimeAuditEvent] = {}
        self._refresh_requests: dict[str, DocumentRefreshRequest] = {}

    def get_state(self, ticker: str) -> TickerRunState | None:
        state = self._states.get(ticker.strip().upper())
        return state.model_copy(deep=True) if state is not None else None

    def list_states(self) -> list[TickerRunState]:
        states = sorted(
            self._states.values(),
            key=lambda item: item.updated_at,
            reverse=True,
        )
        return [state.model_copy(deep=True) for state in states]

    def upsert_state(self, state: TickerRunState) -> TickerRunState:
        self._states[state.ticker] = state.model_copy(deep=True)
        return state.model_copy(deep=True)

    def append_audit_event(self, event: RuntimeAuditEvent) -> RuntimeAuditEvent:
        self._audit_events[event.audit_id] = event.model_copy(deep=True)
        return event.model_copy(deep=True)

    def list_audit_events(
        self,
        *,
        ticker: str | None = None,
        limit: int = 100,
    ) -> list[RuntimeAuditEvent]:
        normalized = ticker.strip().upper() if ticker else None
        events = [
            event
            for event in self._audit_events.values()
            if normalized is None or event.ticker == normalized
        ]
        events.sort(key=lambda item: item.created_at, reverse=True)
        return [event.model_copy(deep=True) for event in events[:limit]]

    def save_refresh_request(
        self,
        request: DocumentRefreshRequest,
    ) -> DocumentRefreshRequest:
        self._refresh_requests[request.request_id] = request.model_copy(deep=True)
        return request.model_copy(deep=True)

    def list_refresh_requests(
        self,
        *,
        ticker: str | None = None,
        limit: int = 100,
    ) -> list[DocumentRefreshRequest]:
        normalized = ticker.strip().upper() if ticker else None
        requests = [
            request
            for request in self._refresh_requests.values()
            if normalized is None or request.ticker == normalized
        ]
        requests.sort(key=lambda item: item.created_at, reverse=True)
        return [request.model_copy(deep=True) for request in requests[:limit]]


class SQLiteRuntimeSchedulerRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if self.path.parent != Path("."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def get_state(self, ticker: str) -> TickerRunState | None:
        with self._connect() as conn:
            row = conn.execute(
                "select payload_json from runtime_scheduler_states where ticker = ?",
                (ticker.strip().upper(),),
            ).fetchone()
        return _model_from_json(TickerRunState, str(row["payload_json"])) if row else None

    def list_states(self) -> list[TickerRunState]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select payload_json
                from runtime_scheduler_states
                order by updated_at desc
                """
            ).fetchall()
        return [_model_from_json(TickerRunState, str(row["payload_json"])) for row in rows]

    def upsert_state(self, state: TickerRunState) -> TickerRunState:
        with self._connect() as conn:
            conn.execute(
                """
                insert into runtime_scheduler_states (ticker, payload_json)
                values (?, ?)
                on conflict(ticker) do update set
                    payload_json = excluded.payload_json,
                    updated_at = current_timestamp
                """,
                (state.ticker, canonical_json(state.model_dump(mode="json"))),
            )
        resolved = self.get_state(state.ticker)
        if resolved is None:
            raise RuntimeError(f"scheduler state was not persisted for {state.ticker}.")
        return resolved

    def append_audit_event(self, event: RuntimeAuditEvent) -> RuntimeAuditEvent:
        with self._connect() as conn:
            conn.execute(
                """
                insert into runtime_scheduler_audit_events
                    (audit_id, ticker, event_type, severity, payload_json)
                values (?, ?, ?, ?, ?)
                """,
                (
                    event.audit_id,
                    event.ticker,
                    event.event_type,
                    event.severity.value,
                    canonical_json(event.model_dump(mode="json")),
                ),
            )
        return event

    def list_audit_events(
        self,
        *,
        ticker: str | None = None,
        limit: int = 100,
    ) -> list[RuntimeAuditEvent]:
        params: list[object] = []
        where = ""
        if ticker is not None:
            where = "where ticker = ?"
            params.append(ticker.strip().upper())
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                select payload_json
                from runtime_scheduler_audit_events
                {where}
                order by created_at desc, rowid desc
                limit ?
                """,
                params,
            ).fetchall()
        return [_model_from_json(RuntimeAuditEvent, str(row["payload_json"])) for row in rows]

    def save_refresh_request(
        self,
        request: DocumentRefreshRequest,
    ) -> DocumentRefreshRequest:
        with self._connect() as conn:
            conn.execute(
                """
                insert into runtime_scheduler_refresh_requests
                    (request_id, ticker, status, payload_json)
                values (?, ?, ?, ?)
                on conflict(request_id) do update set
                    status = excluded.status,
                    payload_json = excluded.payload_json
                """,
                (
                    request.request_id,
                    request.ticker,
                    request.status.value,
                    canonical_json(request.model_dump(mode="json")),
                ),
            )
        return request

    def list_refresh_requests(
        self,
        *,
        ticker: str | None = None,
        limit: int = 100,
    ) -> list[DocumentRefreshRequest]:
        params: list[object] = []
        where = ""
        if ticker is not None:
            where = "where ticker = ?"
            params.append(ticker.strip().upper())
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                select payload_json
                from runtime_scheduler_refresh_requests
                {where}
                order by created_at desc, rowid desc
                limit ?
                """,
                params,
            ).fetchall()
        return [
            _model_from_json(DocumentRefreshRequest, str(row["payload_json"]))
            for row in rows
        ]

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists runtime_scheduler_states (
                    ticker text primary key,
                    payload_json text not null,
                    updated_at text not null default current_timestamp
                );

                create table if not exists runtime_scheduler_audit_events (
                    audit_id text primary key,
                    ticker text not null,
                    event_type text not null,
                    severity text not null,
                    payload_json text not null,
                    created_at text not null default current_timestamp
                );

                create table if not exists runtime_scheduler_refresh_requests (
                    request_id text primary key,
                    ticker text not null,
                    status text not null,
                    payload_json text not null,
                    created_at text not null default current_timestamp
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


def _model_from_json(model: type[T], payload: str) -> T:
    if not hasattr(model, "model_validate_json"):
        raise TypeError("model must be a Pydantic model type.")
    return model.model_validate_json(payload)  # type: ignore[attr-defined, no-any-return]
