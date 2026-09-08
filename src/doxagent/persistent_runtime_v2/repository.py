"""Hot-path repositories for Persistent Runtime V2.

SQLite is the authoritative journal. Remote Postgres receives only compact
terminal projections through a transactional outbox implemented separately.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel

from .fencing import FencedConnection
from .schema import (
    ArchiveRecord,
    BadcaseRecord,
    DailyCloseRun,
    DailyRecordStatus,
    PolicyActivationRecord,
    ProvisionalFactDetail,
    RuntimeCase,
    RuntimeEffect,
    RuntimeEffectStatus,
    RuntimeFactCandidate,
    RuntimeModelTurn,
    TradeRecord,
    W3CoverageGapRecord,
    W3RouteCase,
    W3ThreadKind,
    W3ThreadSlot,
    utc_now,
)

T = TypeVar("T", bound=BaseModel)
DailyT = TypeVar("DailyT", TradeRecord, BadcaseRecord, W3CoverageGapRecord)


class _ClosingSQLiteConnection(FencedConnection):
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
    def list_cases(self, ticker: str) -> list[RuntimeCase]: ...

    def save_case(self, case: RuntimeCase) -> RuntimeCase: ...

    def save_case_with_effects(
        self,
        case: RuntimeCase,
        effects: list[RuntimeEffect],
        w3_case: W3RouteCase | None = None,
    ) -> RuntimeCase: ...

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
        case_id: str | None = None,
        originating_node: Literal["W1_R3", "W3"] | None = None,
    ) -> ProvisionalFactDetail: ...

    def provisional_snapshot_version(self, ticker: str, trading_date: date) -> int: ...

    def list_provisional(self, ticker: str, trading_date: date) -> list[ProvisionalFactDetail]: ...

    def get_provisional(
        self, ticker: str, trading_date: date, event_ids: list[str]
    ) -> list[ProvisionalFactDetail]: ...

    def save_archive(self, value: ArchiveRecord) -> ArchiveRecord: ...

    def save_trade(self, value: TradeRecord) -> TradeRecord: ...

    def claim_policy_activation(self, value: PolicyActivationRecord) -> bool: ...

    def list_consumed_policy_revisions(self, ticker: str) -> set[tuple[str, str]]: ...

    def save_badcase(self, value: BadcaseRecord) -> BadcaseRecord: ...

    def save_w3_case(self, value: W3RouteCase) -> W3RouteCase: ...

    def get_w3_case(self, case_id: str) -> W3RouteCase | None: ...

    def acquire_w3_slot(
        self,
        *,
        ticker: str,
        case_id: str,
        max_concurrency: int = 5,
        lease_seconds: int = 1200,
    ) -> W3ThreadSlot | None: ...

    def release_w3_slot(
        self,
        slot: W3ThreadSlot,
        *,
        thread_id: str | None = None,
        clear_main_thread: bool = False,
    ) -> None: ...

    def save_w3_coverage_gap(self, value: W3CoverageGapRecord) -> W3CoverageGapRecord: ...

    def list_daily_candidates(
        self, ticker: str, trading_date: date
    ) -> list[ProvisionalFactDetail]: ...

    def list_daily_trades(self, ticker: str, trading_date: date) -> list[TradeRecord]: ...

    def list_daily_badcases(self, ticker: str, trading_date: date) -> list[BadcaseRecord]: ...

    def list_daily_w3_coverage_gaps(
        self, ticker: str, trading_date: date
    ) -> list[W3CoverageGapRecord]: ...

    def get_daily_close(self, ticker: str, trading_date: date) -> DailyCloseRun | None: ...

    def save_daily_close(self, value: DailyCloseRun) -> DailyCloseRun: ...

    def mark_daily_records_processed(
        self,
        *,
        candidate_keys: list[str],
        trade_record_ids: list[str],
        badcase_ids: list[str],
        w3_coverage_gap_ids: list[str],
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
        self._policy_activations: dict[tuple[str, str, str], PolicyActivationRecord] = {}
        self._badcases: dict[str, BadcaseRecord] = {}
        self._w3_cases: dict[str, W3RouteCase] = {}
        self._w3_main_threads: dict[str, str] = {}
        self._w3_main_cases: dict[str, str] = {}
        self._w3_slots: dict[str, W3ThreadSlot] = {}
        self._w3_coverage_gaps: dict[str, W3CoverageGapRecord] = {}
        self._daily_closes: dict[tuple[str, date], DailyCloseRun] = {}
        self._processed_candidate_keys: set[str] = set()
        self._lock = threading.RLock()

    def list_cases(self, ticker: str) -> list[RuntimeCase]:
        return [case for case in self._cases.values() if case.ticker == ticker]

    def save_case(self, case: RuntimeCase) -> RuntimeCase:
        with self._lock:
            existing_id = self._source_cases.get(case.source.source_message_id)
            if existing_id is not None and existing_id != case.case_id:
                return self._cases[existing_id]
            self._cases[case.case_id] = case
            self._source_cases[case.source.source_message_id] = case.case_id
            return case

    def save_case_with_effects(
        self,
        case: RuntimeCase,
        effects: list[RuntimeEffect],
        w3_case: W3RouteCase | None = None,
    ) -> RuntimeCase:
        with self._lock:
            saved = self.save_case(case)
            for effect in effects:
                self.enqueue_effect(effect)
            if w3_case is not None:
                self.save_w3_case(w3_case)
            return saved

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
                if (
                    effect.status
                    not in {
                        RuntimeEffectStatus.PENDING,
                        RuntimeEffectStatus.PENDING_RETRY,
                    }
                    or effect.available_at > now
                ):
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
        case_id: str | None = None,
        originating_node: Literal["W1_R3", "W3"] | None = None,
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
                case_id=case_id,
                originating_node=originating_node,
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

    def list_provisional(self, ticker: str, trading_date: date) -> list[ProvisionalFactDetail]:
        with self._lock:
            return list(self._provisional.get((ticker.upper(), trading_date), []))

    def get_provisional(
        self, ticker: str, trading_date: date, event_ids: list[str]
    ) -> list[ProvisionalFactDetail]:
        wanted = set(event_ids)
        return [
            item
            for item in self.list_provisional(ticker, trading_date)
            if item.provisional_event_id in wanted
        ]

    def save_archive(self, value: ArchiveRecord) -> ArchiveRecord:
        with self._lock:
            return self._archives.setdefault(value.case_id, value)

    def save_trade(self, value: TradeRecord) -> TradeRecord:
        with self._lock:
            return self._trades.setdefault(value.case_id, value)

    def claim_policy_activation(self, value: PolicyActivationRecord) -> bool:
        key = (value.ticker.upper(), value.policy_id, value.activation_revision)
        with self._lock:
            existing = self._policy_activations.get(key)
            if existing is not None:
                return existing.case_id == value.case_id
            self._policy_activations[key] = value
            return True

    def list_consumed_policy_revisions(self, ticker: str) -> set[tuple[str, str]]:
        key = ticker.upper()
        with self._lock:
            return {
                (policy_id, revision)
                for (record_ticker, policy_id, revision) in self._policy_activations
                if record_ticker == key
            }

    def save_badcase(self, value: BadcaseRecord) -> BadcaseRecord:
        with self._lock:
            return self._badcases.setdefault(value.case_id, value)

    def save_w3_case(self, value: W3RouteCase) -> W3RouteCase:
        with self._lock:
            self._w3_cases[value.case_id] = value
            return value

    def get_w3_case(self, case_id: str) -> W3RouteCase | None:
        with self._lock:
            return self._w3_cases.get(case_id)

    def acquire_w3_slot(
        self,
        *,
        ticker: str,
        case_id: str,
        max_concurrency: int = 5,
        lease_seconds: int = 1200,
    ) -> W3ThreadSlot | None:
        del lease_seconds
        normalized = ticker.upper()
        with self._lock:
            existing = self._w3_slots.get(case_id)
            if existing is not None:
                return existing
            active = [slot for slot in self._w3_slots.values() if slot.ticker == normalized]
            if len(active) >= max_concurrency:
                return None
            if normalized not in self._w3_main_cases:
                kind = W3ThreadKind.MAIN
                self._w3_main_cases[normalized] = case_id
                thread_id = self._w3_main_threads.get(normalized)
            else:
                kind = W3ThreadKind.FALLBACK
                thread_id = None
            slot = W3ThreadSlot(
                ticker=normalized,
                case_id=case_id,
                kind=kind,
                thread_id=thread_id,
            )
            self._w3_slots[case_id] = slot
            return slot

    def release_w3_slot(
        self,
        slot: W3ThreadSlot,
        *,
        thread_id: str | None = None,
        clear_main_thread: bool = False,
    ) -> None:
        with self._lock:
            self._w3_slots.pop(slot.case_id, None)
            if slot.kind is W3ThreadKind.MAIN:
                self._w3_main_cases.pop(slot.ticker, None)
                if clear_main_thread:
                    self._w3_main_threads.pop(slot.ticker, None)
                elif thread_id:
                    self._w3_main_threads[slot.ticker] = thread_id

    def save_w3_coverage_gap(self, value: W3CoverageGapRecord) -> W3CoverageGapRecord:
        with self._lock:
            return self._w3_coverage_gaps.setdefault(value.case_id, value)

    def list_daily_candidates(self, ticker: str, trading_date: date) -> list[ProvisionalFactDetail]:
        return [
            item
            for item in self.list_provisional(ticker, trading_date)
            if _candidate_identity(item.source_message_id, item.candidate_index)
            not in self._processed_candidate_keys
        ]

    def list_daily_trades(self, ticker: str, trading_date: date) -> list[TradeRecord]:
        return [
            item
            for item in self._trades.values()
            if item.ticker == ticker.upper()
            and item.trading_date == trading_date
            and item.daily_status is DailyRecordStatus.PENDING
        ]

    def list_daily_badcases(self, ticker: str, trading_date: date) -> list[BadcaseRecord]:
        return [
            item
            for item in self._badcases.values()
            if item.ticker == ticker.upper()
            and item.trading_date == trading_date
            and item.daily_status is DailyRecordStatus.PENDING
        ]

    def list_daily_w3_coverage_gaps(
        self, ticker: str, trading_date: date
    ) -> list[W3CoverageGapRecord]:
        return [
            item
            for item in self._w3_coverage_gaps.values()
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
        w3_coverage_gap_ids: list[str],
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
            gap_ids = set(w3_coverage_gap_ids)
            self._w3_coverage_gaps = {
                key: (
                    value.model_copy(update={"daily_status": DailyRecordStatus.PROCESSED})
                    if value.coverage_gap_id in gap_ids
                    else value
                )
                for key, value in self._w3_coverage_gaps.items()
            }


class SQLitePersistentRuntimeV2Repository:
    def visible_provisional(
        self, ticker: str, trading_date: date, *, current_only: bool = False
    ) -> list[ProvisionalFactDetail]:
        items = self._read_models(
            ProvisionalFactDetail,
            "SELECT payload_json FROM runtime_v2_candidates WHERE ticker=? "
            "AND (trading_date=? OR (?=0 AND trading_date<? AND daily_status!='PROCESSED')) "
            "ORDER BY trading_date,created_at,candidate_identity",
            (ticker.upper(), trading_date.isoformat(), int(current_only), trading_date.isoformat()),
        )
        maximum = max((int(item.provisional_event_id[1:]) for item in items), default=0)
        seen: set[str] = set()
        result = []
        for item in items:
            alias = item.provisional_event_id
            if alias in seen:
                maximum += 1
                alias = f"E{maximum}"
            seen.add(alias)
            result.append(item.model_copy(update={"provisional_event_id": alias}))
        return result

    def list_cases(self, ticker: str) -> list[RuntimeCase]:
        return self._read_models(
            RuntimeCase,
            "SELECT payload_json FROM runtime_v2_cases WHERE ticker=? ORDER BY created_at",
            (ticker.upper(),),
        )

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
                CREATE TABLE IF NOT EXISTS runtime_v2_policy_activations (
                    ticker TEXT NOT NULL,
                    policy_id TEXT NOT NULL,
                    activation_revision TEXT NOT NULL,
                    case_id TEXT NOT NULL UNIQUE REFERENCES runtime_v2_cases(case_id),
                    source_message_id TEXT NOT NULL,
                    policy_set_version INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    activated_at TEXT NOT NULL,
                    PRIMARY KEY(ticker, policy_id, activation_revision)
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_v2_policy_activation_case
                    ON runtime_v2_policy_activations(case_id);
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
                    created_at TEXT NOT NULL,
                    updated_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_v2_w3_cases_status
                    ON runtime_v2_w3_cases(ticker, status, created_at);
                CREATE TABLE IF NOT EXISTS runtime_v2_w3_thread_bindings (
                    ticker TEXT PRIMARY KEY,
                    main_thread_id TEXT,
                    main_case_id TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_v2_w3_thread_slots (
                    case_id TEXT PRIMARY KEY REFERENCES runtime_v2_cases(case_id),
                    ticker TEXT NOT NULL,
                    slot_kind TEXT NOT NULL,
                    acquired_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_v2_w3_slots_ticker
                    ON runtime_v2_w3_thread_slots(ticker, acquired_at);
                CREATE TABLE IF NOT EXISTS runtime_v2_w3_coverage_gaps (
                    case_id TEXT PRIMARY KEY REFERENCES runtime_v2_cases(case_id),
                    ticker TEXT NOT NULL,
                    trading_date TEXT NOT NULL,
                    daily_status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_v2_w3_gap_daily
                    ON runtime_v2_w3_coverage_gaps(
                        ticker, trading_date, daily_status, created_at
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
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(runtime_v2_w3_cases)")
            }
            if "updated_at" not in columns:
                connection.execute("ALTER TABLE runtime_v2_w3_cases ADD COLUMN updated_at TEXT")

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

    def save_case_with_effects(
        self,
        case: RuntimeCase,
        effects: list[RuntimeEffect],
        w3_case: W3RouteCase | None = None,
    ) -> RuntimeCase:
        payload = case.model_dump_json()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO runtime_v2_cases
                    (case_id,source_message_id,ticker,trading_date,status,technical_status,
                     payload_json,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)
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
            for effect in effects:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO runtime_v2_effects
                        (effect_id,case_id,effect_type,idempotency_key,status,attempt_count,
                         available_at,payload_json,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
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
            if w3_case is not None:
                connection.execute(
                    """
                    INSERT INTO runtime_v2_w3_cases
                        (case_id,ticker,status,payload_json,created_at,updated_at)
                    VALUES (?,?,?,?,?,?)
                    ON CONFLICT(case_id) DO NOTHING
                    """,
                    (
                        w3_case.case_id,
                        w3_case.ticker,
                        w3_case.status.value,
                        w3_case.model_dump_json(),
                        w3_case.created_at.isoformat(),
                        w3_case.updated_at.isoformat(),
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
                WHERE (status IN ('PENDING', 'PENDING_RETRY') AND available_at <= ?)
                   OR (status='RUNNING' AND updated_at <= ?)
                ORDER BY available_at, rowid LIMIT ?
                """,
                (now.isoformat(), (now - timedelta(seconds=120)).isoformat(), limit),
            ).fetchall()
            claimed: list[RuntimeEffect] = []
            for row in rows:
                effect = RuntimeEffect.model_validate_json(row["payload_json"])
                running = effect.model_copy(
                    update={
                        "status": RuntimeEffectStatus.RUNNING,
                        "updated_at": now,
                        "lease_token": effect.lease_token + 1,
                        "lease_until": now + timedelta(seconds=120),
                    }
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

    def renew_effect(self, effect: RuntimeEffect) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload_json FROM runtime_v2_effects WHERE effect_id=?", (effect.effect_id,)
            ).fetchone()
            current = RuntimeEffect.model_validate_json(row[0])
            if (
                current.lease_token != effect.lease_token
                or current.status is not RuntimeEffectStatus.RUNNING
                or current.lease_until is None
                or current.lease_until <= utc_now()
            ):
                raise RuntimeError("effect lease lost")
            current.lease_until = utc_now() + timedelta(seconds=120)
            current.updated_at = utc_now()
            db.execute(
                "UPDATE runtime_v2_effects SET payload_json=?,updated_at=? WHERE effect_id=?",
                (current.model_dump_json(), current.updated_at.isoformat(), effect.effect_id),
            )

    def save_effect(self, effect: RuntimeEffect) -> RuntimeEffect:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute(
                "SELECT payload_json FROM runtime_v2_effects WHERE effect_id=?", (effect.effect_id,)
            ).fetchone()
            if old and RuntimeEffect.model_validate_json(old[0]).lease_token != effect.lease_token:
                raise RuntimeError("effect lease lost")
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
            if (
                effect.status in {RuntimeEffectStatus.COMPLETED, RuntimeEffectStatus.FAILED}
                and connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='runtime_values'"
                ).fetchone()
            ):
                from .journal import encode

                row = connection.execute(
                    "SELECT ticker FROM runtime_v2_cases WHERE case_id=?", (effect.case_id,)
                ).fetchone()
                if row:
                    value = {
                        "case_id": effect.case_id,
                        "ticker": row[0],
                        "effect_id": effect.effect_id,
                        "attempt": effect.attempt_count,
                        "at": effect.updated_at.isoformat(),
                    }
                    connection.execute(
                        "INSERT INTO runtime_values VALUES('dirty_case',?,?) "
                        "ON CONFLICT(namespace,key) DO UPDATE SET payload=excluded.payload",
                        (effect.case_id, encode(value)),
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
        case_id: str | None = None,
        originating_node: Literal["W1_R3", "W3"] | None = None,
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
                case_id=case_id,
                originating_node=originating_node,
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

    def list_provisional(self, ticker: str, trading_date: date) -> list[ProvisionalFactDetail]:
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
            item
            for item in self.list_provisional(ticker, trading_date)
            if item.provisional_event_id in wanted
        ]

    def save_archive(self, value: ArchiveRecord) -> ArchiveRecord:
        return self._insert_case_record("runtime_v2_archives", value, value.case_id)

    def save_trade(self, value: TradeRecord) -> TradeRecord:
        return self._insert_daily_record("runtime_v2_trade_records", value)

    def claim_policy_activation(self, value: PolicyActivationRecord) -> bool:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO runtime_v2_policy_activations(
                    ticker,policy_id,activation_revision,case_id,source_message_id,
                    policy_set_version,payload_json,activated_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    value.ticker.upper(),
                    value.policy_id,
                    value.activation_revision,
                    value.case_id,
                    value.source_message_id,
                    value.policy_set_version,
                    value.model_dump_json(),
                    value.activated_at.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT case_id FROM runtime_v2_policy_activations "
                "WHERE ticker=? AND policy_id=? AND activation_revision=?",
                (value.ticker.upper(), value.policy_id, value.activation_revision),
            ).fetchone()
            connection.commit()
        return row is not None and str(row[0]) == value.case_id

    def list_consumed_policy_revisions(self, ticker: str) -> set[tuple[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT policy_id,activation_revision FROM runtime_v2_policy_activations "
                "WHERE ticker=?",
                (ticker.upper(),),
            ).fetchall()
        return {(str(row[0]), str(row[1])) for row in rows}

    def save_badcase(self, value: BadcaseRecord) -> BadcaseRecord:
        return self._insert_daily_record("runtime_v2_badcases", value)

    def save_w3_case(self, value: W3RouteCase) -> W3RouteCase:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runtime_v2_w3_cases
                    (case_id,ticker,status,payload_json,created_at,updated_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(case_id) DO UPDATE SET
                    status=excluded.status,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    value.case_id,
                    value.ticker,
                    value.status.value,
                    value.model_dump_json(),
                    value.created_at.isoformat(),
                    value.updated_at.isoformat(),
                ),
            )
        return value

    def get_w3_case(self, case_id: str) -> W3RouteCase | None:
        return self._read_model(
            W3RouteCase,
            "SELECT payload_json FROM runtime_v2_w3_cases WHERE case_id=?",
            (case_id,),
        )

    def acquire_w3_slot(
        self,
        *,
        ticker: str,
        case_id: str,
        max_concurrency: int = 5,
        lease_seconds: int = 1200,
    ) -> W3ThreadSlot | None:
        normalized = ticker.upper()
        cutoff = utc_now() - timedelta(seconds=lease_seconds)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            stale = connection.execute(
                "SELECT case_id FROM runtime_v2_w3_thread_slots WHERE acquired_at < ?",
                (cutoff.isoformat(),),
            ).fetchall()
            stale_ids = {str(row[0]) for row in stale}
            if stale_ids:
                connection.execute(
                    "DELETE FROM runtime_v2_w3_thread_slots WHERE acquired_at < ?",
                    (cutoff.isoformat(),),
                )
                row = connection.execute(
                    "SELECT main_case_id FROM runtime_v2_w3_thread_bindings WHERE ticker=?",
                    (normalized,),
                ).fetchone()
                if row and row[0] in stale_ids:
                    connection.execute(
                        "UPDATE runtime_v2_w3_thread_bindings "
                        "SET main_case_id=NULL,updated_at=? WHERE ticker=?",
                        (utc_now().isoformat(), normalized),
                    )
            existing = connection.execute(
                "SELECT slot_kind,acquired_at FROM runtime_v2_w3_thread_slots WHERE case_id=?",
                (case_id,),
            ).fetchone()
            binding = connection.execute(
                "SELECT main_thread_id,main_case_id FROM runtime_v2_w3_thread_bindings "
                "WHERE ticker=?",
                (normalized,),
            ).fetchone()
            if existing is not None:
                return W3ThreadSlot(
                    ticker=normalized,
                    case_id=case_id,
                    kind=W3ThreadKind(str(existing[0])),
                    thread_id=(str(binding[0]) if binding and binding[0] else None),
                    acquired_at=datetime.fromisoformat(str(existing[1])),
                )
            active = int(
                connection.execute(
                    "SELECT COUNT(*) FROM runtime_v2_w3_thread_slots "
                    "WHERE ticker=? OR ticker LIKE ?",
                    (normalized.split("@", 1)[0], normalized.split("@", 1)[0] + "@%"),
                ).fetchone()[0]
            )
            if active >= max_concurrency:
                return None
            now = utc_now()
            if binding is None:
                connection.execute(
                    "INSERT INTO runtime_v2_w3_thread_bindings"
                    "(ticker,main_thread_id,main_case_id,updated_at) VALUES (?,?,?,?)",
                    (normalized, None, case_id, now.isoformat()),
                )
                kind = W3ThreadKind.MAIN
                thread_id = None
            elif binding[1] is None:
                connection.execute(
                    "UPDATE runtime_v2_w3_thread_bindings "
                    "SET main_case_id=?,updated_at=? WHERE ticker=?",
                    (case_id, now.isoformat(), normalized),
                )
                kind = W3ThreadKind.MAIN
                thread_id = str(binding[0]) if binding[0] else None
            else:
                kind = W3ThreadKind.FALLBACK
                thread_id = None
            connection.execute(
                "INSERT INTO runtime_v2_w3_thread_slots"
                "(case_id,ticker,slot_kind,acquired_at) VALUES (?,?,?,?)",
                (case_id, normalized, kind.value, now.isoformat()),
            )
            return W3ThreadSlot(
                ticker=normalized,
                case_id=case_id,
                kind=kind,
                thread_id=thread_id,
                acquired_at=now,
            )

    def release_w3_slot(
        self,
        slot: W3ThreadSlot,
        *,
        thread_id: str | None = None,
        clear_main_thread: bool = False,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM runtime_v2_w3_thread_slots WHERE case_id=?",
                (slot.case_id,),
            )
            if slot.kind is not W3ThreadKind.MAIN:
                return
            if clear_main_thread:
                connection.execute(
                    "UPDATE runtime_v2_w3_thread_bindings "
                    "SET main_thread_id=NULL,main_case_id=NULL,updated_at=? WHERE ticker=?",
                    (utc_now().isoformat(), slot.ticker),
                )
            else:
                connection.execute(
                    "UPDATE runtime_v2_w3_thread_bindings "
                    "SET main_thread_id=COALESCE(?,main_thread_id),main_case_id=NULL,updated_at=? "
                    "WHERE ticker=?",
                    (thread_id, utc_now().isoformat(), slot.ticker),
                )

    def save_w3_coverage_gap(self, value: W3CoverageGapRecord) -> W3CoverageGapRecord:
        return self._insert_daily_record("runtime_v2_w3_coverage_gaps", value)

    def list_daily_candidates(self, ticker: str, trading_date: date) -> list[ProvisionalFactDetail]:
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

    def list_daily_w3_coverage_gaps(
        self, ticker: str, trading_date: date
    ) -> list[W3CoverageGapRecord]:
        return self._read_models(
            W3CoverageGapRecord,
            "SELECT payload_json FROM runtime_v2_w3_coverage_gaps "
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
        w3_coverage_gap_ids: list[str],
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
            self._mark_daily_payloads(
                connection,
                "runtime_v2_w3_coverage_gaps",
                "coverage_gap_id",
                w3_coverage_gap_ids,
                W3CoverageGapRecord,
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
