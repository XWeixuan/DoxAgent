"""Persistence for Persistent Runtime Execution artifacts."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Protocol, TypeVar, cast

from doxagent.monitoring.schema import canonical_json
from doxagent.persistent_runtime.schema import (
    ArchiveItem,
    ExecutionExceptionLog,
    IngestQueueItem,
    KnownEventsPatchLog,
    RuntimeExecutionRecord,
    RuntimeKnownEvent,
    RuntimeObjectionRecord,
    RuntimeSourceMessage,
    TradingRecord,
    runtime_duplicate_keys,
)

T = TypeVar("T")


class PersistentRuntimeRepository(Protocol):
    def save_execution(self, record: RuntimeExecutionRecord) -> RuntimeExecutionRecord:
        ...

    def save_trading_record(self, record: TradingRecord) -> TradingRecord:
        ...

    def save_ingest_queue_item(self, item: IngestQueueItem) -> IngestQueueItem:
        ...

    def save_archive_item(self, item: ArchiveItem) -> ArchiveItem:
        ...

    def save_known_events_patch_log(self, log: KnownEventsPatchLog) -> KnownEventsPatchLog:
        ...

    def save_objection(self, objection: RuntimeObjectionRecord) -> RuntimeObjectionRecord:
        ...

    def save_exception(self, exception: ExecutionExceptionLog) -> ExecutionExceptionLog:
        ...

    def trading_record_for_source(self, source_message_id: str) -> TradingRecord | None:
        ...

    def execution_for_source(self, source_message_id: str) -> RuntimeExecutionRecord | None:
        ...

    def execution_for_duplicate(
        self,
        message: RuntimeSourceMessage,
    ) -> RuntimeExecutionRecord | None:
        ...

    def list_executions(self, *, ticker: str | None = None) -> list[RuntimeExecutionRecord]:
        ...

    def list_trading_records(self, *, ticker: str | None = None) -> list[TradingRecord]:
        ...

    def list_ingest_queue(self, *, ticker: str | None = None) -> list[IngestQueueItem]:
        ...

    def list_archive(self, *, ticker: str | None = None) -> list[ArchiveItem]:
        ...

    def list_known_events_patch_logs(
        self,
        *,
        ticker: str | None = None,
    ) -> list[KnownEventsPatchLog]:
        ...

    def list_known_events(self, *, ticker: str | None = None) -> list[RuntimeKnownEvent]:
        ...

    def list_objections(self, *, ticker: str | None = None) -> list[RuntimeObjectionRecord]:
        ...

    def list_exceptions(self, *, ticker: str | None = None) -> list[ExecutionExceptionLog]:
        ...


class InMemoryPersistentRuntimeRepository:
    """In-process runtime repository for unit tests and dry runs."""

    def __init__(self) -> None:
        self._executions: dict[str, RuntimeExecutionRecord] = {}
        self._trading_records: dict[str, TradingRecord] = {}
        self._ingest_queue: dict[str, IngestQueueItem] = {}
        self._archive: dict[str, ArchiveItem] = {}
        self._known_events: dict[str, RuntimeKnownEvent] = {}
        self._known_event_logs: dict[str, KnownEventsPatchLog] = {}
        self._objections: dict[str, RuntimeObjectionRecord] = {}
        self._exceptions: dict[str, ExecutionExceptionLog] = {}

    def save_execution(self, record: RuntimeExecutionRecord) -> RuntimeExecutionRecord:
        self._executions[record.source_message.source_message_id] = record.model_copy(deep=True)
        return record.model_copy(deep=True)

    def save_trading_record(self, record: TradingRecord) -> TradingRecord:
        existing = self._trading_records.get(record.source_message_id)
        if existing is not None:
            return existing.model_copy(deep=True)
        self._trading_records[record.source_message_id] = record.model_copy(deep=True)
        return record.model_copy(deep=True)

    def save_ingest_queue_item(self, item: IngestQueueItem) -> IngestQueueItem:
        existing = self._ingest_queue.get(item.source_message_id)
        if existing is not None:
            return existing.model_copy(deep=True)
        self._ingest_queue[item.source_message_id] = item.model_copy(deep=True)
        return item.model_copy(deep=True)

    def save_archive_item(self, item: ArchiveItem) -> ArchiveItem:
        existing = self._archive.get(item.source_message_id)
        if existing is not None:
            return existing.model_copy(deep=True)
        self._archive[item.source_message_id] = item.model_copy(deep=True)
        return item.model_copy(deep=True)

    def save_known_events_patch_log(self, log: KnownEventsPatchLog) -> KnownEventsPatchLog:
        key = f"{log.source_message_id}:{log.known_event_id}"
        existing = self._known_event_logs.get(key)
        if existing is not None:
            return existing.model_copy(deep=True)
        self._known_event_logs[key] = log.model_copy(deep=True)
        self._known_events[log.known_event_id] = RuntimeKnownEvent.from_patch_log(log)
        return log.model_copy(deep=True)

    def save_objection(self, objection: RuntimeObjectionRecord) -> RuntimeObjectionRecord:
        key = f"{objection.source_message_id}:{objection.objection_type.value}"
        existing = self._objections.get(key)
        if existing is not None:
            return existing.model_copy(deep=True)
        self._objections[key] = objection.model_copy(deep=True)
        return objection.model_copy(deep=True)

    def save_exception(self, exception: ExecutionExceptionLog) -> ExecutionExceptionLog:
        self._exceptions[exception.exception_id] = exception.model_copy(deep=True)
        return exception.model_copy(deep=True)

    def trading_record_for_source(self, source_message_id: str) -> TradingRecord | None:
        return _copy_optional(self._trading_records.get(source_message_id))

    def execution_for_source(self, source_message_id: str) -> RuntimeExecutionRecord | None:
        return _copy_optional(self._executions.get(source_message_id))

    def execution_for_duplicate(
        self,
        message: RuntimeSourceMessage,
    ) -> RuntimeExecutionRecord | None:
        keys = runtime_duplicate_keys(message)
        if not keys:
            return None
        for record in self._executions.values():
            if record.source_message.source_message_id == message.source_message_id:
                continue
            if runtime_duplicate_keys(record.source_message) & keys:
                return record.model_copy(deep=True)
        return None

    def list_executions(self, *, ticker: str | None = None) -> list[RuntimeExecutionRecord]:
        rows = list(self._executions.values())
        if ticker is not None:
            normalized = ticker.strip().upper()
            rows = [row for row in rows if row.source_message.ticker == normalized]
        return [row.model_copy(deep=True) for row in rows]

    def list_trading_records(self, *, ticker: str | None = None) -> list[TradingRecord]:
        return _filter_ticker(list(self._trading_records.values()), ticker)

    def list_ingest_queue(self, *, ticker: str | None = None) -> list[IngestQueueItem]:
        return _filter_ticker(list(self._ingest_queue.values()), ticker)

    def list_archive(self, *, ticker: str | None = None) -> list[ArchiveItem]:
        return _filter_ticker(list(self._archive.values()), ticker)

    def list_known_events_patch_logs(
        self,
        *,
        ticker: str | None = None,
    ) -> list[KnownEventsPatchLog]:
        return _filter_ticker(list(self._known_event_logs.values()), ticker)

    def list_known_events(self, *, ticker: str | None = None) -> list[RuntimeKnownEvent]:
        return _filter_ticker(list(self._known_events.values()), ticker)

    def list_objections(self, *, ticker: str | None = None) -> list[RuntimeObjectionRecord]:
        return _filter_ticker(list(self._objections.values()), ticker)

    def list_exceptions(self, *, ticker: str | None = None) -> list[ExecutionExceptionLog]:
        return _filter_ticker(list(self._exceptions.values()), ticker)


class SQLitePersistentRuntimeRepository:
    """SQLite-backed repository for durable runtime replay and audit."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if self.path.parent != Path("."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def save_execution(self, record: RuntimeExecutionRecord) -> RuntimeExecutionRecord:
        self._upsert_payload(
            table="persistent_runtime_executions",
            id_column="execution_id",
            id_value=record.execution_id,
            source_message_id=record.source_message.source_message_id,
            ticker=record.source_message.ticker,
            payload=record.model_dump(mode="json"),
        )
        resolved = self.execution_for_source(record.source_message.source_message_id)
        if resolved is None:
            raise RuntimeError("runtime execution was not persisted.")
        return resolved

    def save_trading_record(self, record: TradingRecord) -> TradingRecord:
        self._insert_unique_payload(
            table="persistent_trading_records",
            id_column="record_id",
            id_value=record.record_id,
            source_message_id=record.source_message_id,
            ticker=record.ticker,
            payload=record.model_dump(mode="json"),
        )
        resolved = self.trading_record_for_source(record.source_message_id)
        if resolved is None:
            raise RuntimeError("trading record was not persisted.")
        return resolved

    def save_ingest_queue_item(self, item: IngestQueueItem) -> IngestQueueItem:
        self._insert_unique_payload(
            table="persistent_ingest_queue",
            id_column="item_id",
            id_value=item.item_id,
            source_message_id=item.source_message_id,
            ticker=item.ticker,
            payload=item.model_dump(mode="json"),
        )
        return self._get_unique_payload(
            "persistent_ingest_queue",
            item.source_message_id,
            IngestQueueItem,
        )

    def save_archive_item(self, item: ArchiveItem) -> ArchiveItem:
        self._insert_unique_payload(
            table="persistent_archive",
            id_column="item_id",
            id_value=item.item_id,
            source_message_id=item.source_message_id,
            ticker=item.ticker,
            payload=item.model_dump(mode="json"),
        )
        return self._get_unique_payload("persistent_archive", item.source_message_id, ArchiveItem)

    def save_known_events_patch_log(self, log: KnownEventsPatchLog) -> KnownEventsPatchLog:
        source_key = f"{log.source_message_id}:{log.known_event_id}"
        self._insert_unique_payload(
            table="persistent_known_event_patch_logs",
            id_column="log_id",
            id_value=log.log_id,
            source_message_id=source_key,
            ticker=log.ticker,
            payload=log.model_dump(mode="json"),
        )
        resolved = self._get_unique_payload(
            "persistent_known_event_patch_logs",
            source_key,
            KnownEventsPatchLog,
        )
        self._upsert_known_event(RuntimeKnownEvent.from_patch_log(log))
        return resolved

    def save_objection(self, objection: RuntimeObjectionRecord) -> RuntimeObjectionRecord:
        source_key = f"{objection.source_message_id}:{objection.objection_type.value}"
        self._insert_unique_payload(
            table="persistent_objections",
            id_column="objection_id",
            id_value=objection.objection_id,
            source_message_id=source_key,
            ticker=objection.ticker,
            payload=objection.model_dump(mode="json"),
        )
        return self._get_unique_payload(
            "persistent_objections",
            source_key,
            RuntimeObjectionRecord,
        )

    def save_exception(self, exception: ExecutionExceptionLog) -> ExecutionExceptionLog:
        with self._connect() as conn:
            conn.execute(
                """
                insert into persistent_execution_exceptions
                    (exception_id, source_message_id, ticker, payload_json)
                values (?, ?, ?, ?)
                """,
                (
                    exception.exception_id,
                    exception.source_message_id,
                    exception.ticker,
                    canonical_json(exception.model_dump(mode="json")),
                ),
            )
        return exception

    def trading_record_for_source(self, source_message_id: str) -> TradingRecord | None:
        return self._get_optional_payload(
            "persistent_trading_records",
            source_message_id,
            TradingRecord,
        )

    def execution_for_source(self, source_message_id: str) -> RuntimeExecutionRecord | None:
        return self._get_optional_payload(
            "persistent_runtime_executions",
            source_message_id,
            RuntimeExecutionRecord,
        )

    def execution_for_duplicate(
        self,
        message: RuntimeSourceMessage,
    ) -> RuntimeExecutionRecord | None:
        keys = runtime_duplicate_keys(message)
        if not keys:
            return None
        for record in self._list_payloads(
            "persistent_runtime_executions",
            RuntimeExecutionRecord,
            ticker=None,
        ):
            if record.source_message.source_message_id == message.source_message_id:
                continue
            if runtime_duplicate_keys(record.source_message) & keys:
                return record
        return None

    def list_executions(self, *, ticker: str | None = None) -> list[RuntimeExecutionRecord]:
        return self._list_payloads(
            "persistent_runtime_executions",
            RuntimeExecutionRecord,
            ticker=ticker,
        )

    def list_trading_records(self, *, ticker: str | None = None) -> list[TradingRecord]:
        return self._list_payloads("persistent_trading_records", TradingRecord, ticker=ticker)

    def list_ingest_queue(self, *, ticker: str | None = None) -> list[IngestQueueItem]:
        return self._list_payloads("persistent_ingest_queue", IngestQueueItem, ticker=ticker)

    def list_archive(self, *, ticker: str | None = None) -> list[ArchiveItem]:
        return self._list_payloads("persistent_archive", ArchiveItem, ticker=ticker)

    def list_known_events_patch_logs(
        self,
        *,
        ticker: str | None = None,
    ) -> list[KnownEventsPatchLog]:
        return self._list_payloads(
            "persistent_known_event_patch_logs",
            KnownEventsPatchLog,
            ticker=ticker,
        )

    def list_known_events(self, *, ticker: str | None = None) -> list[RuntimeKnownEvent]:
        return self._list_payloads("persistent_known_events", RuntimeKnownEvent, ticker=ticker)

    def list_objections(self, *, ticker: str | None = None) -> list[RuntimeObjectionRecord]:
        return self._list_payloads("persistent_objections", RuntimeObjectionRecord, ticker=ticker)

    def list_exceptions(self, *, ticker: str | None = None) -> list[ExecutionExceptionLog]:
        return self._list_payloads(
            "persistent_execution_exceptions",
            ExecutionExceptionLog,
            ticker=ticker,
        )

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists persistent_runtime_executions (
                    execution_id text primary key,
                    source_message_id text not null unique,
                    ticker text not null,
                    payload_json text not null,
                    updated_at text not null default current_timestamp
                );

                create table if not exists persistent_trading_records (
                    record_id text primary key,
                    source_message_id text not null unique,
                    ticker text not null,
                    payload_json text not null,
                    created_at text not null default current_timestamp
                );

                create table if not exists persistent_ingest_queue (
                    item_id text primary key,
                    source_message_id text not null unique,
                    ticker text not null,
                    payload_json text not null,
                    created_at text not null default current_timestamp
                );

                create table if not exists persistent_archive (
                    item_id text primary key,
                    source_message_id text not null unique,
                    ticker text not null,
                    payload_json text not null,
                    created_at text not null default current_timestamp
                );

                create table if not exists persistent_known_event_patch_logs (
                    log_id text primary key,
                    source_message_id text not null unique,
                    ticker text not null,
                    payload_json text not null,
                    created_at text not null default current_timestamp
                );

                create table if not exists persistent_known_events (
                    event_id text primary key,
                    ticker text not null,
                    payload_json text not null,
                    updated_at text not null default current_timestamp
                );

                create table if not exists persistent_objections (
                    objection_id text primary key,
                    source_message_id text not null unique,
                    ticker text not null,
                    payload_json text not null,
                    created_at text not null default current_timestamp
                );

                create table if not exists persistent_execution_exceptions (
                    exception_id text primary key,
                    source_message_id text not null,
                    ticker text not null,
                    payload_json text not null,
                    created_at text not null default current_timestamp
                );
                """
            )

    def _upsert_known_event(self, event: RuntimeKnownEvent) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                insert into persistent_known_events
                    (event_id, ticker, payload_json)
                values (?, ?, ?)
                on conflict(event_id) do update set
                    payload_json = excluded.payload_json,
                    ticker = excluded.ticker,
                    updated_at = current_timestamp
                """,
                (
                    event.event_id,
                    event.ticker,
                    canonical_json(event.model_dump(mode="json")),
                ),
            )

    def _insert_unique_payload(
        self,
        *,
        table: str,
        id_column: str,
        id_value: str,
        source_message_id: str,
        ticker: str,
        payload: dict[str, Any],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                f"""
                insert or ignore into {table}
                    ({id_column}, source_message_id, ticker, payload_json)
                values (?, ?, ?, ?)
                """,
                (id_value, source_message_id, ticker, canonical_json(payload)),
            )

    def _upsert_payload(
        self,
        *,
        table: str,
        id_column: str,
        id_value: str,
        source_message_id: str,
        ticker: str,
        payload: dict[str, Any],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                f"""
                insert into {table}
                    ({id_column}, source_message_id, ticker, payload_json)
                values (?, ?, ?, ?)
                on conflict(source_message_id) do update set
                    payload_json = excluded.payload_json,
                    ticker = excluded.ticker,
                    updated_at = current_timestamp
                """,
                (id_value, source_message_id, ticker, canonical_json(payload)),
            )

    def _get_unique_payload(
        self,
        table: str,
        source_message_id: str,
        model: type[T],
    ) -> T:
        resolved = self._get_optional_payload(table, source_message_id, model)
        if resolved is None:
            raise RuntimeError(f"row was not persisted in {table}: {source_message_id}")
        return resolved

    def _get_optional_payload(
        self,
        table: str,
        source_message_id: str,
        model: type[T],
    ) -> T | None:
        with self._connect() as conn:
            row = conn.execute(
                f"select payload_json from {table} where source_message_id = ?",
                (source_message_id,),
            ).fetchone()
        if row is None:
            return None
        return _model_from_json(model, str(row["payload_json"]))

    def _list_payloads(
        self,
        table: str,
        model: type[T],
        *,
        ticker: str | None,
    ) -> list[T]:
        sql = f"select payload_json from {table}"
        params: tuple[str, ...] = ()
        if ticker is not None:
            sql += " where ticker = ?"
            params = (ticker.strip().upper(),)
        sql += " order by rowid"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_model_from_json(model, str(row["payload_json"])) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


def _copy_optional(item: T | None) -> T | None:
    if item is None:
        return None
    if hasattr(item, "model_copy"):
        return item.model_copy(deep=True)  # type: ignore[no-any-return]
    return item


def _filter_ticker(items: list[T], ticker: str | None) -> list[T]:
    rows = items
    if ticker is not None:
        normalized = ticker.strip().upper()
        rows = [item for item in rows if cast(Any, item).ticker == normalized]
    copied: list[T] = []
    for item in rows:
        copied.append(_copy_required(item))
    return copied


def _copy_required(item: T) -> T:
    if hasattr(item, "model_copy"):
        return item.model_copy(deep=True)  # type: ignore[no-any-return]
    return item


def _model_from_json(model: type[T], value: str) -> T:
    if not hasattr(model, "model_validate_json"):
        raise TypeError("model must be a Pydantic model type.")
    return model.model_validate_json(value)  # type: ignore[attr-defined, no-any-return]
