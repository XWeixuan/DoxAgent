"""Persistence backends for the standalone Stocktwits crawler."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol, cast

from doxagent.postgres import (
    connect_postgres,
    estimate_json_payload_bytes,
    postgres_database_error,
    record_postgres_failure,
    record_postgres_payload,
    retry_postgres_operation,
)
from doxagent.settings import DoxAgentSettings
from doxagent.stocktwits.schema import (
    BootstrapEventPolicy,
    CoverageStatus,
    CrawlRunStatus,
    JsonObject,
    StocktwitsCrawlRun,
    StocktwitsIngestResult,
    StocktwitsMessage,
    StocktwitsStatusSnapshot,
    StocktwitsTickerState,
    TickerMode,
    normalize_symbol,
    normalize_symbols,
)


class StocktwitsRepository(Protocol):
    def ensure_schema(self) -> None:
        ...

    def upsert_ticker_state(self, state: StocktwitsTickerState) -> StocktwitsTickerState:
        ...

    def get_ticker_state(self, symbol: str) -> StocktwitsTickerState | None:
        ...

    def list_ticker_states(
        self,
        *,
        symbol: str | None = None,
        enabled_only: bool = False,
    ) -> list[StocktwitsTickerState]:
        ...

    def save_messages(
        self,
        *,
        requested_symbol: str,
        messages: list[StocktwitsMessage],
    ) -> StocktwitsIngestResult:
        ...

    def record_crawl_run(self, run: StocktwitsCrawlRun) -> StocktwitsCrawlRun:
        ...

    def recent_crawl_runs(
        self,
        *,
        symbol: str | None = None,
        limit: int = 20,
    ) -> list[StocktwitsCrawlRun]:
        ...

    def status_snapshot(
        self,
        *,
        symbol: str | None = None,
        limit: int = 20,
    ) -> StocktwitsStatusSnapshot:
        ...


class InMemoryStocktwitsRepository:
    """In-process repository for focused tests and dry-run experiments."""

    def __init__(self) -> None:
        self._states: dict[str, StocktwitsTickerState] = {}
        self._messages: dict[str, StocktwitsMessage] = {}
        self._message_symbols: set[tuple[str, str]] = set()
        self._runs: list[StocktwitsCrawlRun] = []

    def ensure_schema(self) -> None:
        return None

    def upsert_ticker_state(self, state: StocktwitsTickerState) -> StocktwitsTickerState:
        now = datetime.now(UTC)
        existing = self._states.get(state.symbol)
        created_at = existing.created_at if existing is not None else state.created_at
        updated = state.model_copy(update={"created_at": created_at, "updated_at": now}, deep=True)
        self._states[updated.symbol] = updated
        return updated.model_copy(deep=True)

    def get_ticker_state(self, symbol: str) -> StocktwitsTickerState | None:
        state = self._states.get(normalize_symbol(symbol))
        return state.model_copy(deep=True) if state is not None else None

    def list_ticker_states(
        self,
        *,
        symbol: str | None = None,
        enabled_only: bool = False,
    ) -> list[StocktwitsTickerState]:
        rows = list(self._states.values())
        if symbol is not None:
            normalized = normalize_symbol(symbol)
            rows = [row for row in rows if row.symbol == normalized]
        if enabled_only:
            rows = [row for row in rows if row.enabled]
        rows = sorted(rows, key=lambda row: (row.next_due_at, row.symbol))
        return [row.model_copy(deep=True) for row in rows]

    def save_messages(
        self,
        *,
        requested_symbol: str,
        messages: list[StocktwitsMessage],
    ) -> StocktwitsIngestResult:
        result = StocktwitsIngestResult()
        symbol_relations = normalize_symbols([requested_symbol])
        now = datetime.now(UTC)
        for message in messages:
            all_symbols = normalize_symbols([*symbol_relations, *message.symbols])
            if message.message_id in self._messages:
                result.duplicate_count += 1
            else:
                result.inserted_count += 1
                self._messages[message.message_id] = message.model_copy(deep=True)
            for symbol in all_symbols:
                self._message_symbols.add((message.message_id, symbol))
            stored = self._messages[message.message_id]
            self._messages[message.message_id] = stored.model_copy(
                update={"raw_payload": {**stored.raw_payload, "_last_seen_at": now.isoformat()}},
                deep=True,
            )
        return result

    def record_crawl_run(self, run: StocktwitsCrawlRun) -> StocktwitsCrawlRun:
        self._runs.append(run.model_copy(deep=True))
        return run.model_copy(deep=True)

    def recent_crawl_runs(
        self,
        *,
        symbol: str | None = None,
        limit: int = 20,
    ) -> list[StocktwitsCrawlRun]:
        rows = self._runs
        if symbol is not None:
            normalized = normalize_symbol(symbol)
            rows = [row for row in rows if row.symbol == normalized]
        rows = sorted(rows, key=lambda row: row.started_at, reverse=True)
        return [row.model_copy(deep=True) for row in rows[:limit]]

    def status_snapshot(
        self,
        *,
        symbol: str | None = None,
        limit: int = 20,
    ) -> StocktwitsStatusSnapshot:
        return StocktwitsStatusSnapshot(
            ticker_states=self.list_ticker_states(symbol=symbol),
            recent_runs=self.recent_crawl_runs(symbol=symbol, limit=limit),
        )


class SQLiteStocktwitsRepository:
    """Local SQLite-backed repository for server-local Stocktwits persistence."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @classmethod
    def from_settings(
        cls,
        settings: DoxAgentSettings | None = None,
    ) -> SQLiteStocktwitsRepository:
        resolved = settings or DoxAgentSettings()
        return cls(resolved.stocktwits_sqlite_path)

    def ensure_schema(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SQLITE_SCHEMA_SQL)

    def upsert_ticker_state(self, state: StocktwitsTickerState) -> StocktwitsTickerState:
        self.ensure_schema()
        existing = self.get_ticker_state(state.symbol)
        now = datetime.now(UTC)
        created_at = existing.created_at if existing is not None else state.created_at
        updated = state.model_copy(
            update={"created_at": created_at, "updated_at": now},
            deep=True,
        )
        with self._connect() as conn:
            conn.execute(
                """
                insert into stocktwits_ticker_states (
                    symbol, enabled, target_cadence_seconds, hot_cadence_seconds,
                    next_due_at, last_successful_crawl_at, last_seen_message_id,
                    last_seen_message_created_at, current_mode, latest_coverage_status,
                    consecutive_gap_count, consecutive_complete_count, hot_started_at,
                    hot_until, created_at, updated_at, page_size, max_pages_per_crawl,
                    hot_message_threshold, hot_cooldown_successes, bootstrap_event_policy
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(symbol) do update set
                    enabled = excluded.enabled,
                    target_cadence_seconds = excluded.target_cadence_seconds,
                    hot_cadence_seconds = excluded.hot_cadence_seconds,
                    next_due_at = excluded.next_due_at,
                    last_successful_crawl_at = excluded.last_successful_crawl_at,
                    last_seen_message_id = excluded.last_seen_message_id,
                    last_seen_message_created_at = excluded.last_seen_message_created_at,
                    current_mode = excluded.current_mode,
                    latest_coverage_status = excluded.latest_coverage_status,
                    consecutive_gap_count = excluded.consecutive_gap_count,
                    consecutive_complete_count = excluded.consecutive_complete_count,
                    hot_started_at = excluded.hot_started_at,
                    hot_until = excluded.hot_until,
                    updated_at = excluded.updated_at,
                    page_size = excluded.page_size,
                    max_pages_per_crawl = excluded.max_pages_per_crawl,
                    hot_message_threshold = excluded.hot_message_threshold,
                    hot_cooldown_successes = excluded.hot_cooldown_successes,
                    bootstrap_event_policy = excluded.bootstrap_event_policy
                """,
                _sqlite_state_row(updated),
            )
        resolved = self.get_ticker_state(state.symbol)
        if resolved is None:
            raise RuntimeError(f"Stocktwits ticker state was not persisted: {state.symbol}")
        return resolved

    def get_ticker_state(self, symbol: str) -> StocktwitsTickerState | None:
        self.ensure_schema()
        with self._connect() as conn:
            row = conn.execute(
                f"{_SQLITE_STATE_SELECT} where symbol = ?",
                (normalize_symbol(symbol),),
            ).fetchone()
        return _state_from_row(row) if row is not None else None

    def list_ticker_states(
        self,
        *,
        symbol: str | None = None,
        enabled_only: bool = False,
    ) -> list[StocktwitsTickerState]:
        self.ensure_schema()
        clauses: list[str] = []
        params: list[object] = []
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(normalize_symbol(symbol))
        if enabled_only:
            clauses.append("enabled = 1")
        where = " where " + " and ".join(clauses) if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"{_SQLITE_STATE_SELECT}{where} order by next_due_at asc, symbol asc",
                tuple(params),
            ).fetchall()
        return [_state_from_row(row) for row in rows]

    def save_messages(
        self,
        *,
        requested_symbol: str,
        messages: list[StocktwitsMessage],
    ) -> StocktwitsIngestResult:
        self.ensure_schema()
        normalized_requested = normalize_symbol(requested_symbol)
        result = StocktwitsIngestResult()
        now = datetime.now(UTC)
        now_text = _dt_text(now)
        with self._connect() as conn:
            for message in messages:
                dumped = message.model_dump(mode="json")
                cursor = conn.execute(
                    """
                    insert into stocktwits_messages (
                        message_id, body, created_at, user_id, username, user_name,
                        user_avatar_url, sentiment, symbols_json, source_url,
                        raw_payload_json, first_seen_at, last_seen_at, inserted_at, updated_at
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    on conflict(message_id) do nothing
                    """,
                    (
                        message.message_id,
                        message.body,
                        _dt_text(message.created_at),
                        message.user.user_id,
                        message.user.username,
                        message.user.name,
                        message.user.avatar_url,
                        message.sentiment,
                        _json_text(dumped["symbols"]),
                        message.source_url,
                        _json_text(dumped["raw_payload"]),
                        now_text,
                        now_text,
                        now_text,
                        now_text,
                    ),
                )
                if cursor.rowcount:
                    result.inserted_count += 1
                else:
                    result.duplicate_count += 1
                    conn.execute(
                        """
                        update stocktwits_messages
                        set body = coalesce(?, body),
                            created_at = coalesce(?, created_at),
                            user_id = coalesce(?, user_id),
                            username = coalesce(?, username),
                            user_name = coalesce(?, user_name),
                            user_avatar_url = coalesce(?, user_avatar_url),
                            sentiment = coalesce(?, sentiment),
                            symbols_json = ?,
                            source_url = coalesce(?, source_url),
                            raw_payload_json = ?,
                            last_seen_at = ?,
                            updated_at = ?
                        where message_id = ?
                        """,
                        (
                            message.body,
                            _dt_text(message.created_at),
                            message.user.user_id,
                            message.user.username,
                            message.user.name,
                            message.user.avatar_url,
                            message.sentiment,
                            _json_text(dumped["symbols"]),
                            message.source_url,
                            _json_text(dumped["raw_payload"]),
                            now_text,
                            now_text,
                            message.message_id,
                        ),
                    )
                for symbol in normalize_symbols([normalized_requested, *message.symbols]):
                    conn.execute(
                        """
                        insert into stocktwits_message_symbols (
                            message_id, symbol, first_seen_at, last_seen_at
                        )
                        values (?, ?, ?, ?)
                        on conflict(message_id, symbol) do update set
                            last_seen_at = excluded.last_seen_at
                        """,
                        (message.message_id, symbol, now_text, now_text),
                    )
        return result

    def record_crawl_run(self, run: StocktwitsCrawlRun) -> StocktwitsCrawlRun:
        self.ensure_schema()
        created_at = _dt_text(datetime.now(UTC))
        with self._connect() as conn:
            conn.execute(
                """
                insert into stocktwits_crawl_runs (
                    run_id, symbol, started_at, finished_at, status, fetched_count,
                    inserted_count, duplicate_count, request_count, pages_fetched,
                    newest_message_id, newest_message_time, oldest_message_time,
                    checkpoint_message_id, checkpoint_found, coverage_status,
                    gap_reason, error_code, error_message, mode, rate_limited,
                    metadata_json, created_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(run_id) do update set
                    finished_at = excluded.finished_at,
                    status = excluded.status,
                    fetched_count = excluded.fetched_count,
                    inserted_count = excluded.inserted_count,
                    duplicate_count = excluded.duplicate_count,
                    request_count = excluded.request_count,
                    pages_fetched = excluded.pages_fetched,
                    newest_message_id = excluded.newest_message_id,
                    newest_message_time = excluded.newest_message_time,
                    oldest_message_time = excluded.oldest_message_time,
                    checkpoint_message_id = excluded.checkpoint_message_id,
                    checkpoint_found = excluded.checkpoint_found,
                    coverage_status = excluded.coverage_status,
                    gap_reason = excluded.gap_reason,
                    error_code = excluded.error_code,
                    error_message = excluded.error_message,
                    mode = excluded.mode,
                    rate_limited = excluded.rate_limited,
                    metadata_json = excluded.metadata_json
                """,
                (*_sqlite_run_row(run), created_at),
            )
        with self._connect() as conn:
            row = conn.execute(
                f"{_SQLITE_RUN_SELECT} where run_id = ?",
                (run.run_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError(f"Stocktwits crawl run was not persisted: {run.run_id}")
        return _run_from_row(row)

    def recent_crawl_runs(
        self,
        *,
        symbol: str | None = None,
        limit: int = 20,
    ) -> list[StocktwitsCrawlRun]:
        self.ensure_schema()
        params: list[object] = []
        where = ""
        if symbol is not None:
            where = " where symbol = ?"
            params.append(normalize_symbol(symbol))
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"{_SQLITE_RUN_SELECT}{where} order by started_at desc limit ?",
                tuple(params),
            ).fetchall()
        return [_run_from_row(row) for row in rows]

    def status_snapshot(
        self,
        *,
        symbol: str | None = None,
        limit: int = 20,
    ) -> StocktwitsStatusSnapshot:
        return StocktwitsStatusSnapshot(
            ticker_states=self.list_ticker_states(symbol=symbol),
            recent_runs=self.recent_crawl_runs(symbol=symbol, limit=limit),
        )

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma foreign_keys = on")
        conn.execute("pragma busy_timeout = 30000")
        conn.execute("pragma journal_mode = wal")
        return conn


class PostgresStocktwitsRepository:
    """Supabase/Postgres-backed repository for durable Stocktwits polling state."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    @classmethod
    def from_settings(
        cls,
        settings: DoxAgentSettings | None = None,
    ) -> PostgresStocktwitsRepository:
        resolved = settings or DoxAgentSettings()
        return cls(resolved.require_database_url())

    def ensure_schema(self) -> None:
        def operation() -> None:
            with self._connection(autocommit=True) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(_schema_sql())

        self._retry(operation, operation_name="stocktwits.ensure_schema")

    def upsert_ticker_state(self, state: StocktwitsTickerState) -> StocktwitsTickerState:
        payload_bytes = estimate_json_payload_bytes(state.model_dump(mode="json"))
        record_postgres_payload(
            operation="stocktwits.upsert_ticker_state",
            table="stocktwits_ticker_states",
            run_id=None,
            payload_bytes=payload_bytes,
            item_count=1,
        )

        def operation() -> StocktwitsTickerState:
            with self._connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        insert into doxagent.stocktwits_ticker_states (
                            symbol, enabled, target_cadence_seconds, hot_cadence_seconds,
                            page_size, max_pages_per_crawl, hot_message_threshold,
                            hot_cooldown_successes, bootstrap_event_policy,
                            next_due_at, last_successful_crawl_at, last_seen_message_id,
                            last_seen_message_created_at, current_mode, latest_coverage_status,
                            consecutive_gap_count, consecutive_complete_count, hot_started_at,
                            hot_until, created_at, updated_at
                        )
                        values (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, now()
                        )
                        on conflict (symbol) do update set
                            enabled = excluded.enabled,
                            target_cadence_seconds = excluded.target_cadence_seconds,
                            hot_cadence_seconds = excluded.hot_cadence_seconds,
                            page_size = excluded.page_size,
                            max_pages_per_crawl = excluded.max_pages_per_crawl,
                            hot_message_threshold = excluded.hot_message_threshold,
                            hot_cooldown_successes = excluded.hot_cooldown_successes,
                            bootstrap_event_policy = excluded.bootstrap_event_policy,
                            next_due_at = excluded.next_due_at,
                            last_successful_crawl_at = excluded.last_successful_crawl_at,
                            last_seen_message_id = excluded.last_seen_message_id,
                            last_seen_message_created_at = excluded.last_seen_message_created_at,
                            current_mode = excluded.current_mode,
                            latest_coverage_status = excluded.latest_coverage_status,
                            consecutive_gap_count = excluded.consecutive_gap_count,
                            consecutive_complete_count = excluded.consecutive_complete_count,
                            hot_started_at = excluded.hot_started_at,
                            hot_until = excluded.hot_until,
                            updated_at = now()
                        returning
                            symbol, enabled, target_cadence_seconds, hot_cadence_seconds,
                            next_due_at, last_successful_crawl_at, last_seen_message_id,
                            last_seen_message_created_at, current_mode,
                            latest_coverage_status, consecutive_gap_count,
                            consecutive_complete_count, hot_started_at, hot_until,
                            created_at, updated_at, page_size, max_pages_per_crawl,
                            hot_message_threshold, hot_cooldown_successes,
                            bootstrap_event_policy
                        """,
                        _state_row(state),
                    )
                    row = cursor.fetchone()
            if row is None:
                raise RuntimeError(f"Stocktwits ticker state was not persisted: {state.symbol}")
            return _state_from_row(row)

        return cast(
            StocktwitsTickerState,
            self._retry(
                operation,
                operation_name="stocktwits.upsert_ticker_state",
                table="stocktwits_ticker_states",
                payload_bytes=payload_bytes,
            ),
        )

    def get_ticker_state(self, symbol: str) -> StocktwitsTickerState | None:
        def operation() -> StocktwitsTickerState | None:
            with self._connection(autocommit=True) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "select * from doxagent.stocktwits_ticker_states where symbol = %s",
                        (normalize_symbol(symbol),),
                    )
                    row = cursor.fetchone()
            return _state_from_row(row) if row is not None else None

        return cast(StocktwitsTickerState | None, self._retry(operation))

    def list_ticker_states(
        self,
        *,
        symbol: str | None = None,
        enabled_only: bool = False,
    ) -> list[StocktwitsTickerState]:
        def operation() -> list[StocktwitsTickerState]:
            clauses: list[str] = []
            params: list[object] = []
            if symbol is not None:
                clauses.append("symbol = %s")
                params.append(normalize_symbol(symbol))
            if enabled_only:
                clauses.append("enabled = true")
            where = " where " + " and ".join(clauses) if clauses else ""
            with self._connection(autocommit=True) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"""
                        select * from doxagent.stocktwits_ticker_states
                        {where}
                        order by next_due_at asc, symbol asc
                        """,
                        tuple(params),
                    )
                    rows = cursor.fetchall()
            return [_state_from_row(row) for row in rows]

        return cast(list[StocktwitsTickerState], self._retry(operation))

    def save_messages(
        self,
        *,
        requested_symbol: str,
        messages: list[StocktwitsMessage],
    ) -> StocktwitsIngestResult:
        normalized_requested = normalize_symbol(requested_symbol)
        payload_bytes = estimate_json_payload_bytes(
            [message.model_dump(mode="json") for message in messages]
        )
        record_postgres_payload(
            operation="stocktwits.save_messages",
            table="stocktwits_messages",
            run_id=normalized_requested,
            payload_bytes=payload_bytes,
            item_count=len(messages),
        )

        def operation() -> StocktwitsIngestResult:
            result = StocktwitsIngestResult()
            with self._connection() as conn:
                with conn.cursor() as cursor:
                    for message in messages:
                        dumped = message.model_dump(mode="json")
                        cursor.execute(
                            """
                            insert into doxagent.stocktwits_messages (
                                message_id, body, created_at, user_id, username, user_name,
                                user_avatar_url, sentiment, symbols, source_url, raw_payload,
                                first_seen_at, last_seen_at
                            )
                            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
                            on conflict (message_id) do nothing
                            returning message_id
                            """,
                            (
                                message.message_id,
                                message.body,
                                message.created_at,
                                message.user.user_id,
                                message.user.username,
                                message.user.name,
                                message.user.avatar_url,
                                message.sentiment,
                                self._jsonb(dumped["symbols"]),
                                message.source_url,
                                self._jsonb(dumped["raw_payload"]),
                            ),
                        )
                        inserted = cursor.fetchone() is not None
                        if inserted:
                            result.inserted_count += 1
                        else:
                            result.duplicate_count += 1
                            cursor.execute(
                                """
                                update doxagent.stocktwits_messages
                                set body = coalesce(%s, body),
                                    created_at = coalesce(%s, created_at),
                                    user_id = coalesce(%s, user_id),
                                    username = coalesce(%s, username),
                                    user_name = coalesce(%s, user_name),
                                    user_avatar_url = coalesce(%s, user_avatar_url),
                                    sentiment = coalesce(%s, sentiment),
                                    symbols = %s,
                                    source_url = coalesce(%s, source_url),
                                    raw_payload = %s,
                                    last_seen_at = now(),
                                    updated_at = now()
                                where message_id = %s
                                """,
                                (
                                    message.body,
                                    message.created_at,
                                    message.user.user_id,
                                    message.user.username,
                                    message.user.name,
                                    message.user.avatar_url,
                                    message.sentiment,
                                    self._jsonb(dumped["symbols"]),
                                    message.source_url,
                                    self._jsonb(dumped["raw_payload"]),
                                    message.message_id,
                                ),
                            )
                        symbols = normalize_symbols([normalized_requested, *message.symbols])
                        for symbol in symbols:
                            cursor.execute(
                                """
                                insert into doxagent.stocktwits_message_symbols (
                                    message_id, symbol, first_seen_at, last_seen_at
                                )
                                values (%s, %s, now(), now())
                                on conflict (message_id, symbol) do update set
                                    last_seen_at = now()
                                """,
                                (message.message_id, symbol),
                            )
            return result

        return cast(
            StocktwitsIngestResult,
            self._retry(
                operation,
                operation_name="stocktwits.save_messages",
                table="stocktwits_messages",
                payload_bytes=payload_bytes,
            ),
        )

    def record_crawl_run(self, run: StocktwitsCrawlRun) -> StocktwitsCrawlRun:
        payload_bytes = estimate_json_payload_bytes(run.model_dump(mode="json"))
        record_postgres_payload(
            operation="stocktwits.record_crawl_run",
            table="stocktwits_crawl_runs",
            run_id=run.run_id,
            payload_bytes=payload_bytes,
            item_count=1,
        )

        def operation() -> StocktwitsCrawlRun:
            with self._connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        insert into doxagent.stocktwits_crawl_runs (
                            run_id, symbol, started_at, finished_at, status, fetched_count,
                            inserted_count, duplicate_count, request_count, pages_fetched,
                            newest_message_id, newest_message_time, oldest_message_time,
                            checkpoint_message_id, checkpoint_found, coverage_status,
                            gap_reason, error_code, error_message, mode, rate_limited,
                            metadata
                        )
                        values (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s
                        )
                        on conflict (run_id) do update set
                            finished_at = excluded.finished_at,
                            status = excluded.status,
                            fetched_count = excluded.fetched_count,
                            inserted_count = excluded.inserted_count,
                            duplicate_count = excluded.duplicate_count,
                            request_count = excluded.request_count,
                            pages_fetched = excluded.pages_fetched,
                            newest_message_id = excluded.newest_message_id,
                            newest_message_time = excluded.newest_message_time,
                            oldest_message_time = excluded.oldest_message_time,
                            checkpoint_message_id = excluded.checkpoint_message_id,
                            checkpoint_found = excluded.checkpoint_found,
                            coverage_status = excluded.coverage_status,
                            gap_reason = excluded.gap_reason,
                            error_code = excluded.error_code,
                            error_message = excluded.error_message,
                            mode = excluded.mode,
                            rate_limited = excluded.rate_limited,
                            metadata = excluded.metadata
                        returning
                            run_id, symbol, started_at, finished_at, status, fetched_count,
                            inserted_count, duplicate_count, request_count, pages_fetched,
                            newest_message_id, newest_message_time, oldest_message_time,
                            checkpoint_message_id, checkpoint_found, coverage_status,
                            gap_reason, error_code, error_message, mode, rate_limited,
                            metadata
                        """,
                        _run_row(run, self._jsonb(run.metadata)),
                    )
                    row = cursor.fetchone()
            if row is None:
                raise RuntimeError(f"Stocktwits crawl run was not persisted: {run.run_id}")
            return _run_from_row(row)

        return cast(
            StocktwitsCrawlRun,
            self._retry(
                operation,
                operation_name="stocktwits.record_crawl_run",
                table="stocktwits_crawl_runs",
                payload_bytes=payload_bytes,
            ),
        )

    def recent_crawl_runs(
        self,
        *,
        symbol: str | None = None,
        limit: int = 20,
    ) -> list[StocktwitsCrawlRun]:
        def operation() -> list[StocktwitsCrawlRun]:
            params: list[object] = []
            where = ""
            if symbol is not None:
                where = "where symbol = %s"
                params.append(normalize_symbol(symbol))
            params.append(limit)
            with self._connection(autocommit=True) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"""
                        select * from doxagent.stocktwits_crawl_runs
                        {where}
                        order by started_at desc
                        limit %s
                        """,
                        tuple(params),
                    )
                    rows = cursor.fetchall()
            return [_run_from_row(row) for row in rows]

        return cast(list[StocktwitsCrawlRun], self._retry(operation))

    def status_snapshot(
        self,
        *,
        symbol: str | None = None,
        limit: int = 20,
    ) -> StocktwitsStatusSnapshot:
        return StocktwitsStatusSnapshot(
            ticker_states=self.list_ticker_states(symbol=symbol),
            recent_runs=self.recent_crawl_runs(symbol=symbol, limit=limit),
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
        operation_name: str = "stocktwits.postgres_operation",
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

    def _psycopg(self) -> Any:
        try:
            return import_module("psycopg")
        except ImportError as exc:  # pragma: no cover - depends on optional install state
            raise RuntimeError("psycopg is required for PostgresStocktwitsRepository.") from exc

    def _jsonb_type(self) -> Any:
        try:
            json_module = import_module("psycopg.types.json")
        except ImportError as exc:  # pragma: no cover - depends on optional install state
            raise RuntimeError("psycopg is required for Stocktwits JSONB persistence.") from exc
        return json_module.Jsonb


def repository_from_settings(
    settings: DoxAgentSettings | None = None,
    *,
    storage_mode: str | None = None,
) -> StocktwitsRepository:
    resolved = settings or DoxAgentSettings()
    mode = storage_mode or resolved.stocktwits_storage_mode
    if mode == "memory":
        return InMemoryStocktwitsRepository()
    if mode == "sqlite":
        return SQLiteStocktwitsRepository(resolved.stocktwits_sqlite_path)
    if mode == "postgres":
        if not resolved.stocktwits_allow_postgres:
            raise RuntimeError(
                "Stocktwits Postgres/Supabase persistence is disabled. "
                "Use DOXAGENT_STOCKTWITS_STORAGE_MODE=sqlite for server-local "
                "persistence, or set DOXAGENT_STOCKTWITS_ALLOW_POSTGRES=1 only "
                "for one-off migration reads."
            )
        return PostgresStocktwitsRepository(resolved.require_database_url())
    raise ValueError(f"Unsupported Stocktwits storage mode: {mode}")


def load_schema_sql() -> str:
    migration_path = (
        Path(__file__).resolve().parents[3]
        / "supabase"
        / "migrations"
        / "202606260001_stocktwits_polling_crawler.sql"
    )
    if migration_path.exists():
        return migration_path.read_text(encoding="utf-8")
    return _FALLBACK_SCHEMA_SQL


def _schema_sql() -> str:
    return load_schema_sql()


def migrate_postgres_stocktwits_to_sqlite(
    *,
    source_database_url: str,
    sqlite_path: str | Path,
    batch_size: int = 1000,
) -> dict[str, int | str]:
    """Copy existing Stocktwits Supabase/Postgres rows into local SQLite."""

    psycopg = import_module("psycopg")
    target = SQLiteStocktwitsRepository(sqlite_path)
    target.ensure_schema()
    counts = {
        "ticker_states": 0,
        "messages": 0,
        "message_symbols": 0,
        "crawl_runs": 0,
    }
    with connect_postgres(psycopg, source_database_url, autocommit=True) as pg_conn:
        with target._connect() as sqlite_conn:
            with pg_conn.cursor() as cursor:
                cursor.execute(_POSTGRES_STATE_SELECT)
                for row in cursor.fetchall():
                    state = _state_from_row(row)
                    sqlite_conn.execute(
                        """
                        insert into stocktwits_ticker_states (
                            symbol, enabled, target_cadence_seconds, hot_cadence_seconds,
                            next_due_at, last_successful_crawl_at, last_seen_message_id,
                            last_seen_message_created_at, current_mode, latest_coverage_status,
                            consecutive_gap_count, consecutive_complete_count, hot_started_at,
                            hot_until, created_at, updated_at, page_size, max_pages_per_crawl,
                            hot_message_threshold, hot_cooldown_successes,
                            bootstrap_event_policy
                        )
                        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        on conflict(symbol) do update set
                            enabled = excluded.enabled,
                            target_cadence_seconds = excluded.target_cadence_seconds,
                            hot_cadence_seconds = excluded.hot_cadence_seconds,
                            next_due_at = excluded.next_due_at,
                            last_successful_crawl_at = excluded.last_successful_crawl_at,
                            last_seen_message_id = excluded.last_seen_message_id,
                            last_seen_message_created_at = excluded.last_seen_message_created_at,
                            current_mode = excluded.current_mode,
                            latest_coverage_status = excluded.latest_coverage_status,
                            consecutive_gap_count = excluded.consecutive_gap_count,
                            consecutive_complete_count = excluded.consecutive_complete_count,
                            hot_started_at = excluded.hot_started_at,
                            hot_until = excluded.hot_until,
                            updated_at = excluded.updated_at,
                            page_size = excluded.page_size,
                            max_pages_per_crawl = excluded.max_pages_per_crawl,
                            hot_message_threshold = excluded.hot_message_threshold,
                            hot_cooldown_successes = excluded.hot_cooldown_successes,
                            bootstrap_event_policy = excluded.bootstrap_event_policy
                        """,
                        _sqlite_state_row(state),
                    )
                    counts["ticker_states"] += 1

                cursor.execute(
                    """
                    select message_id, body, created_at, user_id, username, user_name,
                           user_avatar_url, sentiment, symbols, source_url, raw_payload,
                           first_seen_at, last_seen_at, inserted_at, updated_at
                    from doxagent.stocktwits_messages
                    """
                )
                while rows := cursor.fetchmany(batch_size):
                    sqlite_conn.executemany(
                        """
                        insert into stocktwits_messages (
                            message_id, body, created_at, user_id, username, user_name,
                            user_avatar_url, sentiment, symbols_json, source_url,
                            raw_payload_json, first_seen_at, last_seen_at, inserted_at,
                            updated_at
                        )
                        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        on conflict(message_id) do update set
                            body = excluded.body,
                            created_at = excluded.created_at,
                            user_id = excluded.user_id,
                            username = excluded.username,
                            user_name = excluded.user_name,
                            user_avatar_url = excluded.user_avatar_url,
                            sentiment = excluded.sentiment,
                            symbols_json = excluded.symbols_json,
                            source_url = excluded.source_url,
                            raw_payload_json = excluded.raw_payload_json,
                            last_seen_at = excluded.last_seen_at,
                            updated_at = excluded.updated_at
                        """,
                        [_postgres_message_sqlite_row(row) for row in rows],
                    )
                    counts["messages"] += len(rows)

                cursor.execute(
                    """
                    select message_id, symbol, first_seen_at, last_seen_at
                    from doxagent.stocktwits_message_symbols
                    """
                )
                while rows := cursor.fetchmany(batch_size):
                    sqlite_conn.executemany(
                        """
                        insert into stocktwits_message_symbols (
                            message_id, symbol, first_seen_at, last_seen_at
                        )
                        values (?, ?, ?, ?)
                        on conflict(message_id, symbol) do update set
                            last_seen_at = excluded.last_seen_at
                        """,
                        [
                            (
                                row[0],
                                row[1],
                                _dt_text(_optional_dt(row[2])) or _dt_text(datetime.now(UTC)),
                                _dt_text(_optional_dt(row[3])) or _dt_text(datetime.now(UTC)),
                            )
                            for row in rows
                        ],
                    )
                    counts["message_symbols"] += len(rows)

                cursor.execute(_POSTGRES_RUN_SELECT)
                while rows := cursor.fetchmany(batch_size):
                    sqlite_conn.executemany(
                        """
                        insert into stocktwits_crawl_runs (
                            run_id, symbol, started_at, finished_at, status, fetched_count,
                            inserted_count, duplicate_count, request_count, pages_fetched,
                            newest_message_id, newest_message_time, oldest_message_time,
                            checkpoint_message_id, checkpoint_found, coverage_status,
                            gap_reason, error_code, error_message, mode, rate_limited,
                            metadata_json, created_at
                        )
                        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        on conflict(run_id) do update set
                            finished_at = excluded.finished_at,
                            status = excluded.status,
                            fetched_count = excluded.fetched_count,
                            inserted_count = excluded.inserted_count,
                            duplicate_count = excluded.duplicate_count,
                            request_count = excluded.request_count,
                            pages_fetched = excluded.pages_fetched,
                            newest_message_id = excluded.newest_message_id,
                            newest_message_time = excluded.newest_message_time,
                            oldest_message_time = excluded.oldest_message_time,
                            checkpoint_message_id = excluded.checkpoint_message_id,
                            checkpoint_found = excluded.checkpoint_found,
                            coverage_status = excluded.coverage_status,
                            gap_reason = excluded.gap_reason,
                            error_code = excluded.error_code,
                            error_message = excluded.error_message,
                            mode = excluded.mode,
                            rate_limited = excluded.rate_limited,
                            metadata_json = excluded.metadata_json
                        """,
                        [_postgres_run_sqlite_row(row) for row in rows],
                    )
                    counts["crawl_runs"] += len(rows)
    return {**counts, "sqlite_path": str(Path(sqlite_path))}


def _state_row(state: StocktwitsTickerState) -> tuple[object, ...]:
    return (
        state.symbol,
        state.enabled,
        state.target_cadence_seconds,
        state.hot_cadence_seconds,
        state.page_size,
        state.max_pages_per_crawl,
        state.hot_message_threshold,
        state.hot_cooldown_successes,
        state.bootstrap_event_policy.value,
        state.next_due_at,
        state.last_successful_crawl_at,
        state.last_seen_message_id,
        state.last_seen_message_created_at,
        state.current_mode.value,
        state.latest_coverage_status.value if state.latest_coverage_status is not None else None,
        state.consecutive_gap_count,
        state.consecutive_complete_count,
        state.hot_started_at,
        state.hot_until,
        state.created_at,
    )


def _state_from_row(row: Any) -> StocktwitsTickerState:
    return StocktwitsTickerState(
        symbol=str(row[0]),
        enabled=bool(row[1]),
        target_cadence_seconds=int(row[2]),
        hot_cadence_seconds=int(row[3]),
        next_due_at=_dt(row[4]),
        last_successful_crawl_at=_optional_dt(row[5]),
        last_seen_message_id=row[6],
        last_seen_message_created_at=_optional_dt(row[7]),
        current_mode=TickerMode(str(row[8])),
        latest_coverage_status=CoverageStatus(str(row[9])) if row[9] is not None else None,
        consecutive_gap_count=int(row[10]),
        consecutive_complete_count=int(row[11]),
        hot_started_at=_optional_dt(row[12]),
        hot_until=_optional_dt(row[13]),
        created_at=_dt(row[14]),
        updated_at=_dt(row[15]),
        page_size=_row_int(row, 16, 30),
        max_pages_per_crawl=_row_int(row, 17, 10),
        hot_message_threshold=_row_int(row, 18, 80),
        hot_cooldown_successes=_row_int(row, 19, 3),
        bootstrap_event_policy=BootstrapEventPolicy(str(_row_value(row, 20, "live_only"))),
    )


def _run_row(run: StocktwitsCrawlRun, metadata_jsonb: Any) -> tuple[object, ...]:
    return (
        run.run_id,
        run.symbol,
        run.started_at,
        run.finished_at,
        run.status.value,
        run.fetched_count,
        run.inserted_count,
        run.duplicate_count,
        run.request_count,
        run.pages_fetched,
        run.newest_message_id,
        run.newest_message_time,
        run.oldest_message_time,
        run.checkpoint_message_id,
        run.checkpoint_found,
        run.coverage_status.value,
        run.gap_reason,
        run.error_code,
        run.error_message,
        run.mode.value,
        run.rate_limited,
        metadata_jsonb,
    )


def _run_from_row(row: Any) -> StocktwitsCrawlRun:
    return StocktwitsCrawlRun(
        run_id=str(row[0]),
        symbol=str(row[1]),
        started_at=_dt(row[2]),
        finished_at=_optional_dt(row[3]),
        status=CrawlRunStatus(str(row[4])),
        fetched_count=int(row[5]),
        inserted_count=int(row[6]),
        duplicate_count=int(row[7]),
        request_count=int(row[8]),
        pages_fetched=int(row[9]),
        newest_message_id=row[10],
        newest_message_time=_optional_dt(row[11]),
        oldest_message_time=_optional_dt(row[12]),
        checkpoint_message_id=row[13],
        checkpoint_found=bool(row[14]),
        coverage_status=CoverageStatus(str(row[15])),
        gap_reason=row[16],
        error_code=row[17],
        error_message=row[18],
        mode=TickerMode(str(row[19])),
        rate_limited=bool(row[20]),
        metadata=_coerce_json_object(row[21]),
    )


def _sqlite_state_row(state: StocktwitsTickerState) -> tuple[object, ...]:
    return (
        state.symbol,
        int(state.enabled),
        state.target_cadence_seconds,
        state.hot_cadence_seconds,
        _dt_text(state.next_due_at),
        _dt_text(state.last_successful_crawl_at),
        state.last_seen_message_id,
        _dt_text(state.last_seen_message_created_at),
        state.current_mode.value,
        state.latest_coverage_status.value if state.latest_coverage_status is not None else None,
        state.consecutive_gap_count,
        state.consecutive_complete_count,
        _dt_text(state.hot_started_at),
        _dt_text(state.hot_until),
        _dt_text(state.created_at),
        _dt_text(state.updated_at),
        state.page_size,
        state.max_pages_per_crawl,
        state.hot_message_threshold,
        state.hot_cooldown_successes,
        state.bootstrap_event_policy.value,
    )


def _sqlite_run_row(run: StocktwitsCrawlRun) -> tuple[object, ...]:
    return (
        run.run_id,
        run.symbol,
        _dt_text(run.started_at),
        _dt_text(run.finished_at),
        run.status.value,
        run.fetched_count,
        run.inserted_count,
        run.duplicate_count,
        run.request_count,
        run.pages_fetched,
        run.newest_message_id,
        _dt_text(run.newest_message_time),
        _dt_text(run.oldest_message_time),
        run.checkpoint_message_id,
        int(run.checkpoint_found),
        run.coverage_status.value,
        run.gap_reason,
        run.error_code,
        run.error_message,
        run.mode.value,
        int(run.rate_limited),
        _json_text(run.metadata),
    )


def _postgres_message_sqlite_row(row: Any) -> tuple[object, ...]:
    return (
        row[0],
        row[1],
        _dt_text(_optional_dt(row[2])),
        row[3],
        row[4],
        row[5],
        row[6],
        row[7],
        _json_db_text(row[8], "[]"),
        row[9],
        _json_db_text(row[10], "{}"),
        _dt_text(_optional_dt(row[11])) or _dt_text(datetime.now(UTC)),
        _dt_text(_optional_dt(row[12])) or _dt_text(datetime.now(UTC)),
        _dt_text(_optional_dt(row[13])) or _dt_text(datetime.now(UTC)),
        _dt_text(_optional_dt(row[14])) or _dt_text(datetime.now(UTC)),
    )


def _postgres_run_sqlite_row(row: Any) -> tuple[object, ...]:
    return (
        row[0],
        row[1],
        _dt_text(_optional_dt(row[2])) or _dt_text(datetime.now(UTC)),
        _dt_text(_optional_dt(row[3])),
        row[4],
        int(row[5]),
        int(row[6]),
        int(row[7]),
        int(row[8]),
        int(row[9]),
        row[10],
        _dt_text(_optional_dt(row[11])),
        _dt_text(_optional_dt(row[12])),
        row[13],
        int(bool(row[14])),
        row[15],
        row[16],
        row[17],
        row[18],
        row[19],
        int(bool(row[20])),
        _json_db_text(row[21], "{}"),
        _dt_text(_optional_dt(row[22])) or _dt_text(datetime.now(UTC)),
    )


def _dt_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_db_text(value: object, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return _json_text(value)


def _dt(value: object) -> datetime:
    parsed = _optional_dt(value)
    if parsed is None:
        return datetime.now(UTC)
    return parsed


def _optional_dt(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text).astimezone(UTC)


def _row_value(row: Any, index: int, default: object) -> object:
    try:
        value = row[index]
    except (IndexError, KeyError):
        return default
    return default if value is None else value


def _row_int(row: Any, index: int, default: int) -> int:
    return int(cast(Any, _row_value(row, index, default)))


def _coerce_json_object(value: object) -> JsonObject:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        return dict(parsed) if isinstance(parsed, dict) else {"value": parsed}
    return dict(value) if isinstance(value, Iterable) else {"value": value}


_FALLBACK_SCHEMA_SQL = """
create schema if not exists doxagent;

create table if not exists doxagent.stocktwits_ticker_states (
    symbol text primary key,
    enabled boolean not null default true,
    target_cadence_seconds integer not null default 300 check (target_cadence_seconds >= 30),
    hot_cadence_seconds integer not null default 90 check (hot_cadence_seconds >= 30),
    next_due_at timestamptz not null,
    last_successful_crawl_at timestamptz,
    last_seen_message_id text,
    last_seen_message_created_at timestamptz,
    current_mode text not null default 'normal'
        check (current_mode in ('normal', 'hot', 'paused')),
    latest_coverage_status text
        check (latest_coverage_status in (
            'complete', 'likely_complete', 'incomplete', 'gap_detected', 'failed'
        )),
    consecutive_gap_count integer not null default 0,
    consecutive_complete_count integer not null default 0,
    hot_started_at timestamptz,
    hot_until timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    page_size integer not null default 30 check (page_size >= 1),
    max_pages_per_crawl integer not null default 10 check (max_pages_per_crawl >= 1),
    hot_message_threshold integer not null default 80 check (hot_message_threshold >= 1),
    hot_cooldown_successes integer not null default 3 check (hot_cooldown_successes >= 1),
    bootstrap_event_policy text not null default 'live_only'
        check (bootstrap_event_policy in ('live_only', 'publish_all', 'suppress_initial'))
);

alter table if exists doxagent.stocktwits_ticker_states
    add column if not exists page_size integer not null default 30 check (page_size >= 1),
    add column if not exists max_pages_per_crawl integer not null default 10
        check (max_pages_per_crawl >= 1),
    add column if not exists hot_message_threshold integer not null default 80
        check (hot_message_threshold >= 1),
    add column if not exists hot_cooldown_successes integer not null default 3
        check (hot_cooldown_successes >= 1),
    add column if not exists bootstrap_event_policy text not null default 'live_only'
        check (bootstrap_event_policy in ('live_only', 'publish_all', 'suppress_initial'));

create table if not exists doxagent.stocktwits_messages (
    message_id text primary key,
    body text,
    created_at timestamptz,
    user_id text,
    username text,
    user_name text,
    user_avatar_url text,
    sentiment text,
    symbols jsonb not null default '[]'::jsonb,
    source_url text,
    raw_payload jsonb not null,
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    inserted_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists doxagent.stocktwits_message_symbols (
    message_id text not null references doxagent.stocktwits_messages(message_id) on delete cascade,
    symbol text not null,
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    primary key (message_id, symbol)
);

create table if not exists doxagent.stocktwits_crawl_runs (
    run_id text primary key,
    symbol text not null,
    started_at timestamptz not null,
    finished_at timestamptz,
    status text not null check (status in ('succeeded', 'failed', 'skipped')),
    fetched_count integer not null default 0,
    inserted_count integer not null default 0,
    duplicate_count integer not null default 0,
    request_count integer not null default 0,
    pages_fetched integer not null default 0,
    newest_message_id text,
    newest_message_time timestamptz,
    oldest_message_time timestamptz,
    checkpoint_message_id text,
    checkpoint_found boolean not null default false,
    coverage_status text not null check (
        coverage_status in ('complete', 'likely_complete', 'incomplete', 'gap_detected', 'failed')
    ),
    gap_reason text,
    error_code text,
    error_message text,
    mode text not null check (mode in ('normal', 'hot', 'paused')),
    rate_limited boolean not null default false,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists stocktwits_ticker_states_due_idx
on doxagent.stocktwits_ticker_states (enabled, next_due_at);

create index if not exists stocktwits_messages_created_at_idx
on doxagent.stocktwits_messages (created_at desc);

create index if not exists stocktwits_message_symbols_symbol_seen_idx
on doxagent.stocktwits_message_symbols (symbol, last_seen_at desc);

create index if not exists stocktwits_crawl_runs_symbol_started_idx
on doxagent.stocktwits_crawl_runs (symbol, started_at desc);
"""


_SQLITE_STATE_SELECT = """
select
    symbol, enabled, target_cadence_seconds, hot_cadence_seconds,
    next_due_at, last_successful_crawl_at, last_seen_message_id,
    last_seen_message_created_at, current_mode, latest_coverage_status,
    consecutive_gap_count, consecutive_complete_count, hot_started_at,
    hot_until, created_at, updated_at, page_size, max_pages_per_crawl,
    hot_message_threshold, hot_cooldown_successes, bootstrap_event_policy
from stocktwits_ticker_states
"""


_SQLITE_RUN_SELECT = """
select
    run_id, symbol, started_at, finished_at, status, fetched_count,
    inserted_count, duplicate_count, request_count, pages_fetched,
    newest_message_id, newest_message_time, oldest_message_time,
    checkpoint_message_id, checkpoint_found, coverage_status, gap_reason,
    error_code, error_message, mode, rate_limited, metadata_json
from stocktwits_crawl_runs
"""


_POSTGRES_STATE_SELECT = """
select
    symbol, enabled, target_cadence_seconds, hot_cadence_seconds,
    next_due_at, last_successful_crawl_at, last_seen_message_id,
    last_seen_message_created_at, current_mode, latest_coverage_status,
    consecutive_gap_count, consecutive_complete_count, hot_started_at,
    hot_until, created_at, updated_at, page_size, max_pages_per_crawl,
    hot_message_threshold, hot_cooldown_successes, bootstrap_event_policy
from doxagent.stocktwits_ticker_states
order by symbol asc
"""


_POSTGRES_RUN_SELECT = """
select
    run_id, symbol, started_at, finished_at, status, fetched_count,
    inserted_count, duplicate_count, request_count, pages_fetched,
    newest_message_id, newest_message_time, oldest_message_time,
    checkpoint_message_id, checkpoint_found, coverage_status, gap_reason,
    error_code, error_message, mode, rate_limited, metadata, created_at
from doxagent.stocktwits_crawl_runs
"""


_SQLITE_SCHEMA_SQL = """
create table if not exists stocktwits_ticker_states (
    symbol text primary key,
    enabled integer not null default 1,
    target_cadence_seconds integer not null default 300 check (target_cadence_seconds >= 30),
    hot_cadence_seconds integer not null default 90 check (hot_cadence_seconds >= 30),
    next_due_at text not null,
    last_successful_crawl_at text,
    last_seen_message_id text,
    last_seen_message_created_at text,
    current_mode text not null default 'normal'
        check (current_mode in ('normal', 'hot', 'paused')),
    latest_coverage_status text
        check (latest_coverage_status in (
            'complete', 'likely_complete', 'incomplete', 'gap_detected', 'failed'
        )),
    consecutive_gap_count integer not null default 0,
    consecutive_complete_count integer not null default 0,
    hot_started_at text,
    hot_until text,
    created_at text not null,
    updated_at text not null,
    page_size integer not null default 30 check (page_size >= 1),
    max_pages_per_crawl integer not null default 10 check (max_pages_per_crawl >= 1),
    hot_message_threshold integer not null default 80 check (hot_message_threshold >= 1),
    hot_cooldown_successes integer not null default 3 check (hot_cooldown_successes >= 1),
    bootstrap_event_policy text not null default 'live_only'
        check (bootstrap_event_policy in ('live_only', 'publish_all', 'suppress_initial'))
);

create table if not exists stocktwits_messages (
    message_id text primary key,
    body text,
    created_at text,
    user_id text,
    username text,
    user_name text,
    user_avatar_url text,
    sentiment text,
    symbols_json text not null default '[]',
    source_url text,
    raw_payload_json text not null,
    first_seen_at text not null,
    last_seen_at text not null,
    inserted_at text not null,
    updated_at text not null
);

create table if not exists stocktwits_message_symbols (
    message_id text not null references stocktwits_messages(message_id) on delete cascade,
    symbol text not null,
    first_seen_at text not null,
    last_seen_at text not null,
    primary key (message_id, symbol)
);

create table if not exists stocktwits_crawl_runs (
    run_id text primary key,
    symbol text not null,
    started_at text not null,
    finished_at text,
    status text not null check (status in ('succeeded', 'failed', 'skipped')),
    fetched_count integer not null default 0,
    inserted_count integer not null default 0,
    duplicate_count integer not null default 0,
    request_count integer not null default 0,
    pages_fetched integer not null default 0,
    newest_message_id text,
    newest_message_time text,
    oldest_message_time text,
    checkpoint_message_id text,
    checkpoint_found integer not null default 0,
    coverage_status text not null check (
        coverage_status in ('complete', 'likely_complete', 'incomplete', 'gap_detected', 'failed')
    ),
    gap_reason text,
    error_code text,
    error_message text,
    mode text not null check (mode in ('normal', 'hot', 'paused')),
    rate_limited integer not null default 0,
    metadata_json text not null default '{}',
    created_at text not null
);

create index if not exists stocktwits_ticker_states_due_idx
on stocktwits_ticker_states (enabled, next_due_at);

create index if not exists stocktwits_messages_created_at_idx
on stocktwits_messages (created_at desc);

create index if not exists stocktwits_message_symbols_symbol_seen_idx
on stocktwits_message_symbols (symbol, last_seen_at desc);

create index if not exists stocktwits_crawl_runs_symbol_started_idx
on stocktwits_crawl_runs (symbol, started_at desc);
"""


__all__ = [
    "InMemoryStocktwitsRepository",
    "PostgresStocktwitsRepository",
    "SQLiteStocktwitsRepository",
    "StocktwitsRepository",
    "repository_from_settings",
]
