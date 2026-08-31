"""Hot-path repositories for Persistent Runtime V2.

SQLite is the authoritative journal. Remote Postgres receives only compact
terminal projections through a transactional outbox implemented separately.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel

from .schema import (
    ArchiveRecord,
    BadcaseRecord,
    DailyCloseRun,
    DailyRecordStatus,
    ProvisionalFactDetail,
    RuntimeCase,
    RuntimeEffect,
    RuntimeEffectStatus,
    RuntimeFactCandidate,
    RuntimeModelTurn,
    TradeRecord,
    W3RouteCase,
    utc_now,
)

T = TypeVar("T", bound=BaseModel)
DailyT = TypeVar("DailyT", TradeRecord, BadcaseRecord)


class _ClosingSQLiteConnection(sqlite3.Connection):
    """Commit or roll back a context, then release the Windows file handle."""

    def __exit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any,
    ) -> Literal[False]:
        try:
            super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()
        return False


class PersistentRuntimeV2Repository(Protocol):
    def save_case(self, case: RuntimeCase) -> RuntimeCase: ...

    def get_case(self, case_id: str) -> RuntimeCase | None: ...

    def get_case_by_source(self, source_message_id: str) -> RuntimeCase | None: ...

    def append_turn(self, turn: RuntimeModelTurn) -> RuntimeModelTurn: ...

    def list_turns(self, case_id: str) -> list[RuntimeModelTurn]: ...

    def enqueue_effect(self, effect: RuntimeEffect) -> RuntimeEffect: ...

    def claim_effects(self, *, limit: int = 20) -> list[RuntimeEffect]: ...

    def save_effect(self, effect: RuntimeEffect) -> RuntimeEffect: ...

    def list_effects(self, case_id: str) -> list[RuntimeEffect]: ...

    def allocate_provisional(
        self,
        *,
        ticker: str,
        trading_date: date,
        source_message_id: str,
        candidate_index: int,
        candidate: RuntimeFactCandidate,
        published_max_event_numeric_id: int,
    ) -> ProvisionalFactDetail: ...

    def provisional_snapshot_version(self, ticker: str, trading_date: date) -> int: ...

    def list_provisional(
        self, ticker: str, trading_date: date
    ) -> list[ProvisionalFactDetail]: ...

    def get_provisional(
        self, ticker: str, trading_date: date, event_ids: list[str]
    ) -> list[ProvisionalFactDetail]: ...

    def save_archive(self, value: ArchiveRecord) -> ArchiveRecord: ...

    def save_trade(self, value: TradeRecord) -> TradeRecord: ...

    def save_badcase(self, value: BadcaseRecord) -> BadcaseRecord: ...

    def save_w3_case(self, value: W3RouteCase) -> W3RouteCase: ...

    def list_daily_candidates(
        self, ticker: str, trading_date: date
    ) -> list[ProvisionalFactDetail]: ...

    def list_daily_trades(self, ticker: str, trading_date: date) -> list[TradeRecord]: ...

    def list_daily_badcases(self, ticker: str, trading_date: date) -> list[BadcaseRecord]: ...

    def get_daily_close(self, ticker: str, trading_date: date) -> DailyCloseRun | None: ...

    def save_daily_close(self, value: DailyCloseRun) -> DailyCloseRun: ...

    def mark_daily_records_processed(
        self,
        *,
        candidate_keys: list[str],
        trade_record_ids: list[str],
        badcase_ids: list[str],
    ) -> None: ...


class InMemoryPersistentRuntimeV2Repository:
    def __init__(self) -> None:
        self._cases: dict[str, RuntimeCase] = {}
        self._source_cases: dict[str, str] = {}
        self._turns: dict[str, list[RuntimeModelTurn]] = {}
        self._effects: dict[str, RuntimeEffect] = {}
        self._effect_keys: dict[str, str] = {}
        self._provisional: dict[tuple[str, date], list[ProvisionalFactDetail]] = {}
        self._candidate_keys: dict[str, ProvisionalFactDetail] = {}
        self._counters: dict[tuple[str, date], tuple[int, int]] = {}
        self._archives: dict[str, ArchiveRecord] = {}
        self._trades: dict[str, TradeRecord] = {}
        self._badcases: dict[str, BadcaseRecord] = {}
        self._w3_cases: dict[str, W3RouteCase] = {}
        self._daily_closes: dict[tuple[str, date], DailyCloseRun] = {}
        self._processed_candidate_keys: set[str] = set()
        self._lock = threading.RLock()

    def save_case(self, case: RuntimeCase) -> RuntimeCase:
        with self._lock:
            existing_id = self._source_cases.get(case.source.source_message_id)
            if existing_id is not None and existing_id != case.case_id:
                return self._cases[existing_id]
            self._cases[case.case_id] = case
            self._source_cases[case.source.source_message_id] = case.case_id
            return case

    def get_case(self, case_id: str) -> RuntimeCase | None:
        with self._lock:
            return self._cases.get(case_id)

    def get_case_by_source(self, source_message_id: str) -> RuntimeCase | None:
        with self._lock:
            case_id = self._source_cases.get(source_message_id)
            return self._cases.get(case_id) if case_id else None

    def append_turn(self, turn: RuntimeModelTurn) -> RuntimeModelTurn:
        with self._lock:
            self._turns.setdefault(turn.case_id, []).append(turn)
        return turn

    def list_turns(self, case_id: str) -> list[RuntimeModelTurn]:
        with self._lock:
            return list(self._turns.get(case_id, []))

    def enqueue_effect(self, effect: RuntimeEffect) -> RuntimeEffect:
        with self._lock:
            existing_id = self._effect_keys.get(effect.idempotency_key)
            if existing_id:
                return self._effects[existing_id]
            self._effects[effect.effect_id] = effect
            self._effect_keys[effect.idempotency_key] = effect.effect_id
            return effect

    def claim_effects(self, *, limit: int = 20) -> list[RuntimeEffect]:
        now = utc_now()
        claimed: list[RuntimeEffect] = []
        with self._lock:
            for effect in sorted(self._effects.values(), key=lambda item: item.created_at):
                if len(claimed) >= limit:
                    break
                if effect.status not in {
                    RuntimeEffectStatus.PENDING,
                    RuntimeEffectStatus.PENDING_RETRY,
                } or effect.available_at > now:
                    continue
                running = effect.model_copy(
                    update={"status": RuntimeEffectStatus.RUNNING, "updated_at": now}
                )
                self._effects[effect.effect_id] = running
                claimed.append(running)
        return claimed

    def save_effect(self, effect: RuntimeEffect) -> RuntimeEffect:
        with self._lock:
            self._effects[effect.effect_id] = effect
            self._effect_keys[effect.idempotency_key] = effect.effect_id
        return effect

    def list_effects(self, case_id: str) -> list[RuntimeEffect]:
        with self._lock:
            return [item for item in self._effects.values() if item.case_id == case_id]

    def allocate_provisional(
        self,
        *,
        ticker: str,
        trading_date: date,
        source_message_id: str,
        candidate_index: int,
        candidate: RuntimeFactCandidate,
        published_max_event_numeric_id: int,
    ) -> ProvisionalFactDetail:
        identity = _candidate_identity(source_message_id, candidate_index)
        key = (ticker.upper(), trading_date)
        with self._lock:
            existing = self._candidate_keys.get(identity)
            if existing is not None:
                return existing
            next_numeric, snapshot_version = self._counters.get(
                key, (published_max_event_numeric_id + 1, 0)
            )
            snapshot_version += 1
            value = ProvisionalFactDetail(
                provisional_event_id=f"E{next_numeric}",
                ticker=key[0],
                trading_date=trading_date,
                source_message_id=source_message_id,
                candidate_index=candidate_index,
                candidate=candidate,
                runtime_signature=candidate.signature,
                snapshot_version=snapshot_version,
            )
            self._counters[key] = (next_numeric + 1, snapshot_version)
            self._candidate_keys[identity] = value
            self._provisional.setdefault(key, []).append(value)
            return value

    def provisional_snapshot_version(self, ticker: str, trading_date: date) -> int:
        with self._lock:
            return self._counters.get((ticker.upper(), trading_date), (0, 0))[1]

    def list_provisional(
        self, ticker: str, trading_date: date
    ) -> list[ProvisionalFactDetail]:
        with self._lock:
            return list(self._provisional.get((ticker.upper(), trading_date), []))

    def get_provisional(
        self, ticker: str, trading_date: date, event_ids: list[str]
    ) -> list[ProvisionalFactDetail]:
        wanted = set(event_ids)
        return [
            item for item in self.list_provisional(ticker, trading_date)
            if item.provisional_event_id in wanted
        ]

    def save_archive(self, value: ArchiveRecord) -> ArchiveRecord:
        with self._lock:
            return self._archives.setdefault(value.case_id, value)

    def save_trade(self, value: TradeRecord) -> TradeRecord:
        with self._lock:
            return self._trades.setdefault(value.case_id, value)

    def save_badcase(self, value: BadcaseRecord) -> BadcaseRecord:
        with self._lock:
            return self._badcases.setdefault(value.case_id, value)

    def save_w3_case(self, value: W3RouteCase) -> W3RouteCase:
        with self._lock:
            return self._w3_cases.setdefault(value.case_id, value)

    def list_daily_candidates(
        self, ticker: str, trading_date: date
    ) -> list[ProvisionalFactDetail]:
        return [
            item
            for item in self.list_provisional(ticker, trading_date)
            if _candidate_identity(item.source_message_id, item.candidate_index)
            not in self._processed_candidate_keys
        ]

    def list_daily_trades(self, ticker: str, trading_date: date) -> list[TradeRecord]:
        return [
            item for item in self._trades.values()
            if item.ticker == ticker.upper()
            and item.trading_date == trading_date
            and item.daily_status is DailyRecordStatus.PENDING
        ]

    def list_daily_badcases(self, ticker: str, trading_date: date) -> list[BadcaseRecord]:
        return [
            item for item in self._badcases.values()
            if item.ticker == ticker.upper()
            and item.trading_date == trading_date
            and item.daily_status is DailyRecordStatus.PENDING
        ]

    def get_daily_close(self, ticker: str, trading_date: date) -> DailyCloseRun | None:
        with self._lock:
            return self._daily_closes.get((ticker.upper(), trading_date))

    def save_daily_close(self, value: DailyCloseRun) -> DailyCloseRun:
        with self._lock:
            self._daily_closes[(value.ticker.upper(), value.trading_date)] = value
        return value

    def mark_daily_records_processed(
        self,
        *,
        candidate_keys: list[str],
        trade_record_ids: list[str],
        badcase_ids: list[str],
    ) -> None:
        with self._lock:
            self._processed_candidate_keys.update(candidate_keys)
            trade_ids = set(trade_record_ids)
            badcase_set = set(badcase_ids)
            self._trades = {
                key: (
                    value.model_copy(update={"daily_status": DailyRecordStatus.PROCESSED})
                    if value.trade_record_id in trade_ids
                    else value
                )
                for key, value in self._trades.items()
            }
            self._badcases = {
                key: (
                    value.model_copy(update={"daily_status": DailyRecordStatus.PROCESSED})
                    if value.badcase_id in badcase_set
                    else value
                )
                for key, value in self._badcases.items()
            }


class SQLitePersistentRuntimeV2Repository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=30,
            factory=_ClosingSQLiteConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runtime_v2_cases (
                    case_id TEXT PRIMARY KEY,
                    source_message_id TEXT NOT NULL UNIQUE,
                    ticker TEXT NOT NULL,
                    trading_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    technical_status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_v2_cases_status
                    ON runtime_v2_cases(status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_runtime_v2_cases_ticker_date
                    ON runtime_v2_cases(ticker, trading_date, created_at);

                CREATE TABLE IF NOT EXISTS runtime_v2_turns (
                    turn_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES runtime_v2_cases(case_id),
                    lane TEXT NOT NULL,
                    round_name TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    response_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(case_id, lane, round_name, attempt_number)
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_v2_turns_case
                    ON runtime_v2_turns(case_id, lane, round_name, attempt_number);

                CREATE TABLE IF NOT EXISTS runtime_v2_effects (
                    effect_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES runtime_v2_cases(case_id),
                    effect_type TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    available_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_v2_effects_pending
                    ON runtime_v2_effects(status, available_at)
                    WHERE status IN ('PENDING', 'PENDING_RETRY');

                CREATE TABLE IF NOT EXISTS runtime_v2_provisional_counters (
                    ticker TEXT NOT NULL,
                    trading_date TEXT NOT NULL,
                    base_max_numeric_id INTEGER NOT NULL,
                    next_numeric_id INTEGER NOT NULL,
                    snapshot_version INTEGER NOT NULL,
                    PRIMARY KEY(ticker, trading_date)
                );
                CREATE TABLE IF NOT EXISTS runtime_v2_candidates (
                    candidate_identity TEXT PRIMARY KEY,
                    provisional_event_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    trading_date TEXT NOT NULL,
                    source_message_id TEXT NOT NULL,
                    candidate_index INTEGER NOT NULL,
                    runtime_signature TEXT NOT NULL,
                    daily_status TEXT NOT NULL DEFAULT 'PENDING',
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(ticker, trading_date, provisional_event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_v2_candidates_daily
                    ON runtime_v2_candidates(ticker, trading_date, daily_status, candidate_index);

                CREATE TABLE IF NOT EXISTS runtime_v2_archives (
                    case_id TEXT PRIMARY KEY REFERENCES runtime_v2_cases(case_id),
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_v2_trade_records (
                    case_id TEXT PRIMARY KEY REFERENCES runtime_v2_cases(case_id),
                    ticker TEXT NOT NULL,
                    trading_date TEXT NOT NULL,
                    daily_status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_v2_trade_daily
                    ON runtime_v2_trade_records(ticker, trading_date, daily_status, created_at);
                CREATE TABLE IF NOT EXISTS runtime_v2_badcases (
                    case_id TEXT PRIMARY KEY REFERENCES runtime_v2_cases(case_id),
                    ticker TEXT NOT NULL,
                    trading_date TEXT NOT NULL,
                    daily_status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_v2_badcase_daily
                    ON runtime_v2_badcases(ticker, trading_date, daily_status, created_at);
                CREATE TABLE IF NOT EXISTS runtime_v2_w3_cases (
                    case_id TEXT PRIMARY KEY REFERENCES runtime_v2_cases(case_id),
                    ticker TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_v2_daily_close_runs (
                    ticker TEXT NOT NULL,
                    trading_date TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(ticker, trading_date)
                );
                """
            )

    def save_case(self, case: RuntimeCase) -> RuntimeCase:
        payload = case.model_dump_json()
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM runtime_v2_cases WHERE source_message_id = ?",
                (case.source.source_message_id,),
            ).fetchone()
            if existing is not None:
                saved = RuntimeCase.model_validate_json(existing[0])
                if saved.case_id != case.case_id:
                    return saved
            connection.execute(
                """
                INSERT INTO runtime_v2_cases
                    (case_id, source_message_id, ticker, trading_date, status,
                     technical_status, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(case_id) DO UPDATE SET
                    status=excluded.status,
                    technical_status=excluded.technical_status,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    case.case_id,
                    case.source.source_message_id,
                    case.ticker,
                    case.trading_date.isoformat(),
                    case.status.value,
                    case.technical_status.value,
                    payload,
                    case.created_at.isoformat(),
                    case.updated_at.isoformat(),
                ),
            )
        return case

    def get_case(self, case_id: str) -> RuntimeCase | None:
        return self._read_model(
            RuntimeCase,
            "SELECT payload_json FROM runtime_v2_cases WHERE case_id = ?",
            (case_id,),
        )

    def get_case_by_source(self, source_message_id: str) -> RuntimeCase | None:
        return self._read_model(
            RuntimeCase,
            "SELECT payload_json FROM runtime_v2_cases WHERE source_message_id = ?",
            (source_message_id,),
        )

    def append_turn(self, turn: RuntimeModelTurn) -> RuntimeModelTurn:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runtime_v2_turns
                    (turn_id, case_id, lane, round_name, attempt_number, status,
                     response_id, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(case_id, lane, round_name, attempt_number) DO UPDATE SET
                    status=excluded.status,
                    response_id=excluded.response_id,
                    payload_json=excluded.payload_json
                """,
                (
                    turn.turn_id,
                    turn.case_id,
                    turn.lane,
                    turn.round_name,
                    turn.attempt_number,
                    turn.status.value,
                    turn.response_id,
                    turn.model_dump_json(),
                    turn.created_at.isoformat(),
                ),
            )
        return turn

    def list_turns(self, case_id: str) -> list[RuntimeModelTurn]:
        return self._read_models(
            RuntimeModelTurn,
            "SELECT payload_json FROM runtime_v2_turns "
            "WHERE case_id = ? ORDER BY created_at, lane, round_name, attempt_number",
            (case_id,),
        )

    def enqueue_effect(self, effect: RuntimeEffect) -> RuntimeEffect:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM runtime_v2_effects WHERE idempotency_key = ?",
                (effect.idempotency_key,),
            ).fetchone()
            if row is not None:
                return RuntimeEffect.model_validate_json(row[0])
            connection.execute(
                """
                INSERT INTO runtime_v2_effects
                    (effect_id, case_id, effect_type, idempotency_key, status,
                     attempt_count, available_at, payload_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    effect.effect_id,
                    effect.case_id,
                    effect.effect_type.value,
                    effect.idempotency_key,
                    effect.status.value,
                    effect.attempt_count,
                    effect.available_at.isoformat(),
                    effect.model_dump_json(),
                    effect.updated_at.isoformat(),
                ),
            )
        return effect

    def claim_effects(self, *, limit: int = 20) -> list[RuntimeEffect]:
        if limit < 1:
            return []
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT effect_id, payload_json FROM runtime_v2_effects
                WHERE status IN ('PENDING', 'PENDING_RETRY') AND available_at <= ?
                ORDER BY available_at, rowid LIMIT ?
                """,
                (now.isoformat(), limit),
            ).fetchall()
            claimed: list[RuntimeEffect] = []
            for row in rows:
                effect = RuntimeEffect.model_validate_json(row["payload_json"])
                running = effect.model_copy(
                    update={"status": RuntimeEffectStatus.RUNNING, "updated_at": now}
                )
                connection.execute(
                    "UPDATE runtime_v2_effects SET status=?, payload_json=?, updated_at=? "
                    "WHERE effect_id=?",
                    (
                        running.status.value,
                        running.model_dump_json(),
                        now.isoformat(),
                        running.effect_id,
                    ),
                )
                claimed.append(running)
            connection.commit()
            return claimed

    def save_effect(self, effect: RuntimeEffect) -> RuntimeEffect:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE runtime_v2_effects SET
                    status=?, attempt_count=?, available_at=?, payload_json=?, updated_at=?
                WHERE effect_id=?
                """,
                (
                    effect.status.value,
                    effect.attempt_count,
                    effect.available_at.isoformat(),
                    effect.model_dump_json(),
                    effect.updated_at.isoformat(),
                    effect.effect_id,
                ),
            )
        return effect

    def list_effects(self, case_id: str) -> list[RuntimeEffect]:
        return self._read_models(
            RuntimeEffect,
            "SELECT payload_json FROM runtime_v2_effects WHERE case_id=? ORDER BY rowid",
            (case_id,),
        )

    def allocate_provisional(
        self,
        *,
        ticker: str,
        trading_date: date,
        source_message_id: str,
        candidate_index: int,
        candidate: RuntimeFactCandidate,
        published_max_event_numeric_id: int,
    ) -> ProvisionalFactDetail:
        key = ticker.upper()
        day = trading_date.isoformat()
        identity = _candidate_identity(source_message_id, candidate_index)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload_json FROM runtime_v2_candidates WHERE candidate_identity = ?",
                (identity,),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return ProvisionalFactDetail.model_validate_json(existing[0])
            counter = connection.execute(
                "SELECT next_numeric_id, snapshot_version "
                "FROM runtime_v2_provisional_counters WHERE ticker=? AND trading_date=?",
                (key, day),
            ).fetchone()
            if counter is None:
                next_numeric = published_max_event_numeric_id + 1
                snapshot_version = 1
                connection.execute(
                    "INSERT INTO runtime_v2_provisional_counters "
                    "(ticker, trading_date, base_max_numeric_id, next_numeric_id, "
                    "snapshot_version) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (key, day, published_max_event_numeric_id, next_numeric + 1, snapshot_version),
                )
            else:
                next_numeric = int(counter[0])
                snapshot_version = int(counter[1]) + 1
                connection.execute(
                    "UPDATE runtime_v2_provisional_counters "
                    "SET next_numeric_id=?, snapshot_version=? "
                    "WHERE ticker=? AND trading_date=?",
                    (next_numeric + 1, snapshot_version, key, day),
                )
            value = ProvisionalFactDetail(
                provisional_event_id=f"E{next_numeric}",
                ticker=key,
                trading_date=trading_date,
                source_message_id=source_message_id,
                candidate_index=candidate_index,
                candidate=candidate,
                runtime_signature=candidate.signature,
                snapshot_version=snapshot_version,
            )
            connection.execute(
                """
                INSERT INTO runtime_v2_candidates
                    (candidate_identity, provisional_event_id, ticker, trading_date,
                     source_message_id, candidate_index, runtime_signature, daily_status,
                     payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
                """,
                (
                    identity,
                    value.provisional_event_id,
                    key,
                    day,
                    source_message_id,
                    candidate_index,
                    value.runtime_signature,
                    value.model_dump_json(),
                    value.created_at.isoformat(),
                ),
            )
            connection.commit()
            return value

    def provisional_snapshot_version(self, ticker: str, trading_date: date) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT snapshot_version FROM runtime_v2_provisional_counters "
                "WHERE ticker=? AND trading_date=?",
                (ticker.upper(), trading_date.isoformat()),
            ).fetchone()
        return int(row[0]) if row else 0

    def list_provisional(
        self, ticker: str, trading_date: date
    ) -> list[ProvisionalFactDetail]:
        return self._read_models(
            ProvisionalFactDetail,
            "SELECT payload_json FROM runtime_v2_candidates "
            "WHERE ticker=? AND trading_date=? "
            "ORDER BY CAST(SUBSTR(provisional_event_id, 2) AS INTEGER)",
            (ticker.upper(), trading_date.isoformat()),
        )

    def get_provisional(
        self, ticker: str, trading_date: date, event_ids: list[str]
    ) -> list[ProvisionalFactDetail]:
        wanted = set(event_ids)
        return [
            item for item in self.list_provisional(ticker, trading_date)
            if item.provisional_event_id in wanted
        ]

    def save_archive(self, value: ArchiveRecord) -> ArchiveRecord:
        return self._insert_case_record("runtime_v2_archives", value, value.case_id)

    def save_trade(self, value: TradeRecord) -> TradeRecord:
        return self._insert_daily_record("runtime_v2_trade_records", value)

    def save_badcase(self, value: BadcaseRecord) -> BadcaseRecord:
        return self._insert_daily_record("runtime_v2_badcases", value)

    def save_w3_case(self, value: W3RouteCase) -> W3RouteCase:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM runtime_v2_w3_cases WHERE case_id=?",
                (value.case_id,),
            ).fetchone()
            if row:
                return W3RouteCase.model_validate_json(row[0])
            connection.execute(
                "INSERT INTO runtime_v2_w3_cases "
                "(case_id, ticker, status, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    value.case_id,
                    value.ticker,
                    value.status,
                    value.model_dump_json(),
                    value.created_at.isoformat(),
                ),
            )
        return value

    def list_daily_candidates(
        self, ticker: str, trading_date: date
    ) -> list[ProvisionalFactDetail]:
        return self._read_models(
            ProvisionalFactDetail,
            "SELECT payload_json FROM runtime_v2_candidates "
            "WHERE ticker=? AND trading_date=? AND daily_status='PENDING' "
            "ORDER BY candidate_index, created_at",
            (ticker.upper(), trading_date.isoformat()),
        )

    def list_daily_trades(self, ticker: str, trading_date: date) -> list[TradeRecord]:
        return self._read_models(
            TradeRecord,
            "SELECT payload_json FROM runtime_v2_trade_records "
            "WHERE ticker=? AND trading_date=? AND daily_status='PENDING' ORDER BY created_at",
            (ticker.upper(), trading_date.isoformat()),
        )

    def list_daily_badcases(self, ticker: str, trading_date: date) -> list[BadcaseRecord]:
        return self._read_models(
            BadcaseRecord,
            "SELECT payload_json FROM runtime_v2_badcases "
            "WHERE ticker=? AND trading_date=? AND daily_status='PENDING' ORDER BY created_at",
            (ticker.upper(), trading_date.isoformat()),
        )

    def get_daily_close(self, ticker: str, trading_date: date) -> DailyCloseRun | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM runtime_v2_daily_close_runs "
                "WHERE ticker=? AND trading_date=?",
                (ticker.upper(), trading_date.isoformat()),
            ).fetchone()
        return None if row is None else DailyCloseRun.model_validate_json(row[0])

    def save_daily_close(self, value: DailyCloseRun) -> DailyCloseRun:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runtime_v2_daily_close_runs(
                    ticker,trading_date,stage,payload_json,updated_at
                ) VALUES (?,?,?,?,?)
                ON CONFLICT(ticker,trading_date) DO UPDATE SET
                    stage=excluded.stage,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    value.ticker.upper(),
                    value.trading_date.isoformat(),
                    value.stage.value,
                    value.model_dump_json(),
                    value.updated_at.isoformat(),
                ),
            )
        return value

    def mark_daily_records_processed(
        self,
        *,
        candidate_keys: list[str],
        trade_record_ids: list[str],
        badcase_ids: list[str],
    ) -> None:
        with self._lock, self._connect() as connection:
            for key in candidate_keys:
                connection.execute(
                    "UPDATE runtime_v2_candidates SET daily_status='PROCESSED' "
                    "WHERE candidate_identity=?",
                    (key,),
                )
            self._mark_daily_payloads(
                connection,
                "runtime_v2_trade_records",
                "trade_record_id",
                trade_record_ids,
                TradeRecord,
            )
            self._mark_daily_payloads(
                connection,
                "runtime_v2_badcases",
                "badcase_id",
                badcase_ids,
                BadcaseRecord,
            )

    @staticmethod
    def _mark_daily_payloads(
        connection: sqlite3.Connection,
        table: str,
        id_field: str,
        record_ids: list[str],
        model: type[DailyT],
    ) -> None:
        wanted = set(record_ids)
        if not wanted:
            return
        rows = connection.execute(
            f"SELECT case_id,payload_json FROM {table} WHERE daily_status='PENDING'"
        ).fetchall()
        for row in rows:
            value = model.model_validate_json(row["payload_json"])
            if getattr(value, id_field) not in wanted:
                continue
            updated = value.model_copy(update={"daily_status": DailyRecordStatus.PROCESSED})
            connection.execute(
                f"UPDATE {table} SET daily_status='PROCESSED',payload_json=? WHERE case_id=?",
                (updated.model_dump_json(), row["case_id"]),
            )

    def _insert_case_record(self, table: str, value: T, case_id: str) -> T:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT payload_json FROM {table} WHERE case_id=?", (case_id,)
            ).fetchone()
            if row:
                return type(value).model_validate_json(row[0])
            connection.execute(
                f"INSERT INTO {table} (case_id, payload_json, created_at) VALUES (?, ?, ?)",
                (case_id, value.model_dump_json(), _created_at(value).isoformat()),
            )
        return value

    def _insert_daily_record(self, table: str, value: DailyT) -> DailyT:
        case_id = value.case_id
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT payload_json FROM {table} WHERE case_id=?", (case_id,)
            ).fetchone()
            if row:
                return type(value).model_validate_json(row[0])
            connection.execute(
                f"INSERT INTO {table} "
                "(case_id, ticker, trading_date, daily_status, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    case_id,
                    value.ticker,
                    value.trading_date.isoformat(),
                    value.daily_status.value,
                    value.model_dump_json(),
                    _created_at(value).isoformat(),
                ),
            )
        return value

    def _read_model(
        self,
        model: type[T],
        query: str,
        args: tuple[Any, ...],
    ) -> T | None:
        with self._connect() as connection:
            row = connection.execute(query, args).fetchone()
        return model.model_validate_json(row[0]) if row else None

    def _read_models(
        self,
        model: type[T],
        query: str,
        args: tuple[Any, ...],
    ) -> list[T]:
        with self._connect() as connection:
            rows = connection.execute(query, args).fetchall()
        return [model.model_validate_json(row[0]) for row in rows]


def _candidate_identity(source_message_id: str, candidate_index: int) -> str:
    return f"runtime-v2-msg:{source_message_id}:{candidate_index}"


def _created_at(value: BaseModel) -> datetime:
    created_at = getattr(value, "created_at", None)
    return created_at if isinstance(created_at, datetime) else datetime.now(UTC)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def dedupe_candidates(
    candidates: Iterable[ProvisionalFactDetail],
) -> list[ProvisionalFactDetail]:
    """Deterministically retain the first exact canonical candidate signature."""

    by_signature: dict[str, ProvisionalFactDetail] = {}
    for candidate in sorted(
        candidates,
        key=lambda item: (
            item.created_at,
            item.source_message_id,
            item.candidate_index,
        ),
    ):
        by_signature.setdefault(candidate.runtime_signature, candidate)
    return list(by_signature.values())
