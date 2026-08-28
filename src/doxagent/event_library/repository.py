"""SQLite copy-on-write repository for the Canonical Event Library."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from doxagent.event_library.contracts import (
    CanonicalEvent,
    CanonicalEventRevision,
    CanonicalObjectStatus,
    CanonicalRevisionBundle,
    DeltaBatch,
    DeltaBatchStatus,
    DeltaResolution,
    LibraryVersionStatus,
    PublicationResult,
    ReferenceReviewCandidate,
    ReferenceReviewDecision,
    ReferenceReviewMode,
    ReferenceReviewReason,
    ReferenceViewBasis,
)
from doxagent.event_library.reference_review import classify_review, event_review_anchor

EVENT_LIBRARY_SCHEMA_VERSION = 2


class EventLibraryError(RuntimeError):
    """Base error for deterministic Event Library operations."""


class StaleLibraryBaseError(EventLibraryError):
    """Raised when a Bundle targets an older Published head."""


class UnknownLibraryObjectError(EventLibraryError):
    """Raised when a requested Event, Fact, version, or Delta does not exist."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _bundle_hash(bundle: CanonicalRevisionBundle) -> str:
    return hashlib.sha256(_json(bundle.model_dump(mode="json")).encode("utf-8")).hexdigest()


def _numeric_id(value: str) -> int:
    digits = "".join(character for character in value if character.isdigit())
    return int(digits)


class EventLibraryRepository:
    """One local SQLite database with per-ticker Published heads and atomic writers."""

    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        self.path = Path(path)
        self.read_only = read_only
        self._lock = threading.RLock()
        if read_only:
            if not self.path.is_file():
                raise FileNotFoundError(self.path)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = (
            sqlite3.connect(
                f"{self.path.resolve().as_uri()}?mode=ro",
                timeout=30,
                uri=True,
            )
            if self.read_only
            else sqlite3.connect(self.path, timeout=30)
        )
        connection.row_factory = sqlite3.Row
        if not self.read_only:
            connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        if self.read_only:
            raise PermissionError("Event Library repository is read-only")
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def _initialize(self) -> None:
        with self._write() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > EVENT_LIBRARY_SCHEMA_VERSION:
                raise EventLibraryError(f"unsupported Event Library schema version {version}")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS library_heads (
                    ticker TEXT PRIMARY KEY,
                    published_version INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS library_versions (
                    ticker TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    base_version INTEGER NOT NULL,
                    source_delta_batches_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    published_at TEXT,
                    PRIMARY KEY (ticker, version)
                );
                CREATE TABLE IF NOT EXISTS id_counters (
                    ticker TEXT PRIMARY KEY,
                    next_event_no INTEGER NOT NULL DEFAULT 1,
                    next_fact_no INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS canonical_events (
                    ticker TEXT NOT NULL,
                    event_no INTEGER NOT NULL,
                    created_version INTEGER NOT NULL,
                    PRIMARY KEY (ticker, event_no)
                );
                CREATE TABLE IF NOT EXISTS canonical_event_revisions (
                    ticker TEXT NOT NULL,
                    event_no INTEGER NOT NULL,
                    revision_no INTEGER NOT NULL,
                    library_version INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (ticker, event_no, revision_no),
                    UNIQUE (ticker, event_no, library_version)
                );
                CREATE TABLE IF NOT EXISTS canonical_event_states (
                    ticker TEXT NOT NULL,
                    event_no INTEGER NOT NULL,
                    library_version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    redirect_to_event_no INTEGER,
                    PRIMARY KEY (ticker, event_no, library_version)
                );
                CREATE TABLE IF NOT EXISTS canonical_facts (
                    ticker TEXT NOT NULL,
                    fact_no INTEGER NOT NULL,
                    created_version INTEGER NOT NULL,
                    PRIMARY KEY (ticker, fact_no)
                );
                CREATE TABLE IF NOT EXISTS canonical_fact_revisions (
                    ticker TEXT NOT NULL,
                    fact_no INTEGER NOT NULL,
                    revision_no INTEGER NOT NULL,
                    library_version INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (ticker, fact_no, revision_no),
                    UNIQUE (ticker, fact_no, library_version)
                );
                CREATE TABLE IF NOT EXISTS canonical_fact_states (
                    ticker TEXT NOT NULL,
                    fact_no INTEGER NOT NULL,
                    library_version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    redirect_to_fact_no INTEGER,
                    PRIMARY KEY (ticker, fact_no, library_version)
                );
                CREATE TABLE IF NOT EXISTS event_fact_memberships (
                    ticker TEXT NOT NULL,
                    event_no INTEGER NOT NULL,
                    fact_no INTEGER NOT NULL,
                    valid_from_version INTEGER NOT NULL,
                    valid_to_version INTEGER,
                    PRIMARY KEY (ticker, event_no, fact_no, valid_from_version)
                );
                CREATE TABLE IF NOT EXISTS event_relations (
                    ticker TEXT NOT NULL,
                    source_event_no INTEGER NOT NULL,
                    relation_type TEXT NOT NULL,
                    target_event_no INTEGER NOT NULL,
                    valid_from_version INTEGER NOT NULL,
                    valid_to_version INTEGER,
                    PRIMARY KEY (
                        ticker, source_event_no, relation_type, target_event_no, valid_from_version
                    )
                );
                CREATE TABLE IF NOT EXISTS delta_batches (
                    batch_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    runtime_scope TEXT NOT NULL,
                    source_snapshot_id TEXT NOT NULL,
                    source_epoch_id TEXT NOT NULL,
                    base_version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    bundle_run_id TEXT,
                    bundle_hash TEXT,
                    source_bundle_hash TEXT,
                    published_version INTEGER,
                    publication_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS delta_items (
                    batch_id TEXT NOT NULL,
                    delta_id TEXT NOT NULL,
                    runtime_scope TEXT NOT NULL,
                    runtime_atomic_id TEXT NOT NULL,
                    runtime_atomic_version INTEGER NOT NULL,
                    runtime_signature TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    resolution TEXT,
                    target_event_no INTEGER,
                    target_fact_no INTEGER,
                    PRIMARY KEY (batch_id, delta_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_delta_runtime_identity
                    ON delta_items (
                        runtime_scope, runtime_atomic_id, runtime_atomic_version, runtime_signature
                    );
                CREATE TABLE IF NOT EXISTS runtime_atomic_mappings (
                    runtime_scope TEXT NOT NULL,
                    runtime_atomic_id TEXT NOT NULL,
                    runtime_atomic_version INTEGER NOT NULL,
                    runtime_signature TEXT NOT NULL,
                    disposition TEXT NOT NULL,
                    canonical_event_no INTEGER,
                    canonical_fact_no INTEGER,
                    last_delta_batch TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (runtime_scope, runtime_atomic_id)
                );
                CREATE TABLE IF NOT EXISTS maintenance_runs (
                    run_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    thread_id TEXT,
                    frozen_view_id TEXT NOT NULL,
                    base_version INTEGER NOT NULL,
                    bundle_path TEXT,
                    bundle_hash TEXT,
                    validator_status TEXT,
                    metadata_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reference_review_schedule (
                    ticker TEXT NOT NULL,
                    event_no INTEGER NOT NULL,
                    occurrence_anchor TEXT,
                    last_reviewed_at TEXT,
                    next_review_at TEXT,
                    review_mode TEXT NOT NULL,
                    candidate_reason TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (ticker,event_no)
                );
                CREATE TABLE IF NOT EXISTS reference_review_history (
                    ticker TEXT NOT NULL,
                    review_run_id TEXT NOT NULL,
                    event_no INTEGER NOT NULL,
                    reviewed_at TEXT NOT NULL,
                    review_mode TEXT NOT NULL,
                    candidate_reason TEXT NOT NULL,
                    changed INTEGER NOT NULL,
                    include_in_reference_view INTEGER NOT NULL,
                    decision_json TEXT NOT NULL,
                    PRIMARY KEY (ticker,review_run_id,event_no)
                );
                CREATE INDEX IF NOT EXISTS idx_event_revision_version
                    ON canonical_event_revisions (ticker, library_version, event_no);
                CREATE INDEX IF NOT EXISTS idx_fact_revision_version
                    ON canonical_fact_revisions (ticker, library_version, fact_no);
                CREATE INDEX IF NOT EXISTS idx_membership_version
                    ON event_fact_memberships (
                        ticker, valid_from_version, valid_to_version, event_no, fact_no
                    );
                CREATE INDEX IF NOT EXISTS idx_reference_review_due
                    ON reference_review_schedule (ticker,next_review_at,event_no);
                """
            )
            connection.execute(f"PRAGMA user_version={EVENT_LIBRARY_SCHEMA_VERSION}")

    def pragma_state(self) -> dict[str, int | str]:
        with self._read() as connection:
            return {
                "user_version": int(connection.execute("PRAGMA user_version").fetchone()[0]),
                "foreign_keys": int(connection.execute("PRAGMA foreign_keys").fetchone()[0]),
                "journal_mode": str(connection.execute("PRAGMA journal_mode").fetchone()[0]),
            }

    @staticmethod
    def _ticker(ticker: str) -> str:
        normalized = ticker.strip().upper()
        if not normalized:
            raise ValueError("ticker must not be empty")
        return normalized

    def published_version(self, ticker: str) -> int:
        normalized = self._ticker(ticker)
        with self._read() as connection:
            row = connection.execute(
                "SELECT published_version FROM library_heads WHERE ticker=?", (normalized,)
            ).fetchone()
        return 0 if row is None else int(row["published_version"])

    def published_metadata(
        self, ticker: str, version: int | None = None
    ) -> tuple[int, datetime | None]:
        normalized = self._ticker(ticker)
        selected = self.published_version(normalized) if version is None else version
        if selected == 0:
            return 0, None
        with self._read() as connection:
            row = connection.execute(
                "SELECT published_at FROM library_versions "
                "WHERE ticker=? AND version=? AND status='PUBLISHED'",
                (normalized, selected),
            ).fetchone()
        if row is None or row["published_at"] is None:
            return selected, None
        return selected, datetime.fromisoformat(str(row["published_at"]))

    def operational_quality_counts(self, ticker: str) -> dict[str, int]:
        """Return deterministic Delta/O2 counters without exposing row payloads."""

        normalized = self._ticker(ticker)
        with self._read() as connection:
            delta = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN i.status='PENDING' THEN 1 ELSE 0 END) AS pending
                FROM delta_items i
                JOIN delta_batches b ON b.batch_id=i.batch_id
                WHERE b.ticker=?
                """,
                (normalized,),
            ).fetchone()
            runs = connection.execute(
                "SELECT validator_status,metadata_json FROM maintenance_runs WHERE ticker=?",
                (normalized,),
            ).fetchall()
            review_due = connection.execute(
                "SELECT COUNT(*) AS count FROM reference_review_schedule "
                "WHERE ticker=? AND next_review_at IS NOT NULL",
                (normalized,),
            ).fetchone()
            review_history = connection.execute(
                "SELECT COUNT(*) AS count FROM reference_review_history WHERE ticker=?",
                (normalized,),
            ).fetchone()
        repair_runs = 0
        for row in runs:
            metadata = json.loads(str(row["metadata_json"]))
            completed = metadata.get("completed_attempt_ids", [])
            if isinstance(completed, list) and any(
                str(item).startswith("o2-repair-") for item in completed
            ):
                repair_runs += 1
        return {
            "delta_total": int(delta["total"] or 0),
            "delta_pending": int(delta["pending"] or 0),
            "o2_run_total": len(runs),
            "o2_invalid_bundle_runs": sum(str(row["validator_status"]) == "FAIL" for row in runs),
            "o2_repair_runs": repair_runs,
            "o2_partial_bundle_runs": sum(
                str(row["validator_status"]) == "PARTIAL" for row in runs
            ),
            "reference_review_scheduled": int(review_due["count"] or 0),
            "reference_review_history": int(review_history["count"] or 0),
        }

    def save_delta_batch(self, batch: DeltaBatch) -> bool:
        ticker = self._ticker(batch.ticker)
        payload = batch.model_dump(mode="json")
        now = _now()
        with self._write() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM delta_batches WHERE batch_id=?", (batch.batch_id,)
            ).fetchone()
            encoded = _json(payload)
            if existing is not None:
                if str(existing["payload_json"]) != encoded:
                    raise EventLibraryError(f"Delta batch {batch.batch_id!r} is immutable")
                return False
            connection.execute(
                """
                INSERT INTO delta_batches(
                    batch_id,ticker,runtime_scope,source_snapshot_id,source_epoch_id,
                    base_version,status,payload_json,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    batch.batch_id,
                    ticker,
                    batch.runtime_scope,
                    batch.source_snapshot_id,
                    batch.source_epoch_id,
                    batch.base_library_version,
                    batch.status.value,
                    encoded,
                    now,
                    now,
                ),
            )
            for item in batch.items:
                connection.execute(
                    """
                    INSERT INTO delta_items(
                        batch_id,delta_id,runtime_scope,runtime_atomic_id,
                        runtime_atomic_version,runtime_signature,payload_json,status
                    ) VALUES (?,?,?,?,?,?,?,'PENDING')
                    """,
                    (
                        batch.batch_id,
                        item.delta_id,
                        batch.runtime_scope,
                        item.runtime_atomic_id,
                        item.runtime_atomic_version,
                        item.runtime_signature,
                        _json(item.model_dump(mode="json")),
                    ),
                )
        return True

    def get_delta_batch(self, batch_id: str) -> DeltaBatch | None:
        with self._read() as connection:
            row = connection.execute(
                "SELECT payload_json,status FROM delta_batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row["payload_json"]))
        payload["status"] = str(row["status"])
        return DeltaBatch.model_validate(payload)

    def get_delta_batch_for_snapshot(
        self, runtime_scope: str, source_snapshot_id: str
    ) -> DeltaBatch | None:
        with self._read() as connection:
            row = connection.execute(
                """
                SELECT batch_id FROM delta_batches
                WHERE runtime_scope=? AND source_snapshot_id=?
                ORDER BY created_at LIMIT 1
                """,
                (runtime_scope, source_snapshot_id),
            ).fetchone()
        return None if row is None else self.get_delta_batch(str(row["batch_id"]))

    def runtime_target_suggestion(
        self, runtime_scope: str, runtime_atomic_id: str
    ) -> tuple[str | None, str | None]:
        with self._read() as connection:
            row = connection.execute(
                """
                SELECT canonical_event_no,canonical_fact_no
                FROM runtime_atomic_mappings
                WHERE runtime_scope=? AND runtime_atomic_id=?
                """,
                (runtime_scope, runtime_atomic_id),
            ).fetchone()
        if row is None:
            return None, None
        event_no = row["canonical_event_no"]
        fact_no = row["canonical_fact_no"]
        return (
            None if event_no is None else f"E{int(event_no)}",
            None if fact_no is None else f"F{int(fact_no)}",
        )

    def runtime_atomic_signature(
        self, runtime_scope: str, runtime_atomic_id: str
    ) -> tuple[int, str] | None:
        with self._read() as connection:
            row = connection.execute(
                """
                SELECT runtime_atomic_version,runtime_signature
                FROM delta_items
                WHERE runtime_scope=? AND runtime_atomic_id=?
                ORDER BY rowid DESC LIMIT 1
                """,
                (runtime_scope, runtime_atomic_id),
            ).fetchone()
        if row is None:
            return None
        return int(row["runtime_atomic_version"]), str(row["runtime_signature"])

    def published_events(self, ticker: str, version: int | None = None) -> list[CanonicalEvent]:
        normalized = self._ticker(ticker)
        selected_version = self.published_version(normalized) if version is None else version
        if selected_version < 0 or selected_version > self.published_version(normalized):
            raise UnknownLibraryObjectError(
                f"library version {selected_version} is not Published for {normalized}"
            )
        if selected_version == 0:
            return []
        with self._read() as connection:
            rows = connection.execute(
                """
                SELECT e.event_no
                FROM canonical_events e
                JOIN canonical_event_states s
                  ON s.ticker=e.ticker AND s.event_no=e.event_no
                 AND s.library_version=(
                    SELECT MAX(s2.library_version) FROM canonical_event_states s2
                    WHERE s2.ticker=e.ticker AND s2.event_no=e.event_no
                      AND s2.library_version<=?
                 )
                WHERE e.ticker=? AND e.created_version<=? AND s.status='ACTIVE'
                ORDER BY e.event_no
                """,
                (selected_version, normalized, selected_version),
            ).fetchall()
            return [
                self._event_from_connection(
                    connection,
                    normalized,
                    int(row["event_no"]),
                    selected_version,
                )
                for row in rows
            ]

    def get_event(
        self, ticker: str, event_id: str, version: int | None = None
    ) -> CanonicalEvent | None:
        normalized = self._ticker(ticker)
        if not event_id.startswith("E"):
            return None
        selected_version = self.published_version(normalized) if version is None else version
        with self._read() as connection:
            exists = connection.execute(
                "SELECT 1 FROM canonical_events "
                "WHERE ticker=? AND event_no=? AND created_version<=?",
                (normalized, _numeric_id(event_id), selected_version),
            ).fetchone()
            if exists is None:
                return None
            return self._event_from_connection(
                connection, normalized, _numeric_id(event_id), selected_version
            )

    def event_status(
        self, ticker: str, event_id: str, version: int | None = None
    ) -> tuple[CanonicalObjectStatus, str | None] | None:
        normalized = self._ticker(ticker)
        selected_version = self.published_version(normalized) if version is None else version
        with self._read() as connection:
            row = connection.execute(
                """
                SELECT status,redirect_to_event_no FROM canonical_event_states
                WHERE ticker=? AND event_no=? AND library_version<=?
                ORDER BY library_version DESC LIMIT 1
                """,
                (normalized, _numeric_id(event_id), selected_version),
            ).fetchone()
        if row is None:
            return None
        redirect = row["redirect_to_event_no"]
        return CanonicalObjectStatus(str(row["status"])), (
            None if redirect is None else f"E{int(redirect)}"
        )

    def active_fact_ids(self, ticker: str, version: int | None = None) -> set[str]:
        return {
            fact.fact_id for event in self.published_events(ticker, version) for fact in event.facts
        }

    def fact_status(
        self, ticker: str, fact_id: str, version: int | None = None
    ) -> tuple[CanonicalObjectStatus, str | None] | None:
        normalized = self._ticker(ticker)
        selected_version = self.published_version(normalized) if version is None else version
        with self._read() as connection:
            row = connection.execute(
                """
                SELECT status,redirect_to_fact_no FROM canonical_fact_states
                WHERE ticker=? AND fact_no=? AND library_version<=?
                ORDER BY library_version DESC LIMIT 1
                """,
                (normalized, _numeric_id(fact_id), selected_version),
            ).fetchone()
        if row is None:
            return None
        redirect = row["redirect_to_fact_no"]
        return CanonicalObjectStatus(str(row["status"])), (
            None if redirect is None else f"F{int(redirect)}"
        )

    def _event_from_connection(
        self,
        connection: sqlite3.Connection,
        ticker: str,
        event_no: int,
        version: int,
    ) -> CanonicalEvent:
        event_row = connection.execute(
            """
            SELECT payload_json FROM canonical_event_revisions
            WHERE ticker=? AND event_no=? AND library_version<=?
            ORDER BY library_version DESC LIMIT 1
            """,
            (ticker, event_no, version),
        ).fetchone()
        state_row = connection.execute(
            """
            SELECT status FROM canonical_event_states
            WHERE ticker=? AND event_no=? AND library_version<=?
            ORDER BY library_version DESC LIMIT 1
            """,
            (ticker, event_no, version),
        ).fetchone()
        if event_row is None or state_row is None:
            raise EventLibraryError(f"Event E{event_no} has incomplete revision state")
        fact_rows = connection.execute(
            """
            SELECT fact_no FROM event_fact_memberships
            WHERE ticker=? AND event_no=? AND valid_from_version<=?
              AND (valid_to_version IS NULL OR valid_to_version>=?)
            ORDER BY fact_no
            """,
            (ticker, event_no, version, version),
        ).fetchall()
        facts: list[dict[str, Any]] = []
        for row in fact_rows:
            fact_no = int(row["fact_no"])
            fact_row = connection.execute(
                """
                SELECT payload_json FROM canonical_fact_revisions
                WHERE ticker=? AND fact_no=? AND library_version<=?
                ORDER BY library_version DESC LIMIT 1
                """,
                (ticker, fact_no, version),
            ).fetchone()
            if fact_row is None:
                raise EventLibraryError(f"Fact F{fact_no} is missing its active revision")
            fact_payload = json.loads(str(fact_row["payload_json"]))
            # V1 persisted Fact revisions may contain the retired, ineffective
            # Canonical `entities` field. Read them through the current schema
            # without mutating immutable historical rows.
            fact_payload.pop("entities", None)
            facts.append(fact_payload)
        payload = json.loads(str(event_row["payload_json"]))
        payload["status"] = str(state_row["status"])
        payload["facts"] = facts
        return CanonicalEvent.model_validate(payload)

    def save_maintenance_run(
        self,
        *,
        run_id: str,
        ticker: str,
        stage: str,
        frozen_view_id: str,
        base_version: int,
        thread_id: str | None = None,
        bundle_path: str | None = None,
        bundle_hash: str | None = None,
        validator_status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._write() as connection:
            connection.execute(
                """
                INSERT INTO maintenance_runs(
                    run_id,ticker,stage,thread_id,frozen_view_id,base_version,
                    bundle_path,bundle_hash,validator_status,metadata_json,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET
                    stage=excluded.stage,thread_id=COALESCE(excluded.thread_id,thread_id),
                    frozen_view_id=excluded.frozen_view_id,
                    base_version=excluded.base_version,
                    bundle_path=COALESCE(excluded.bundle_path,bundle_path),
                    bundle_hash=COALESCE(excluded.bundle_hash,bundle_hash),
                    validator_status=COALESCE(excluded.validator_status,validator_status),
                    metadata_json=excluded.metadata_json,updated_at=excluded.updated_at
                """,
                (
                    run_id,
                    self._ticker(ticker),
                    stage,
                    thread_id,
                    frozen_view_id,
                    base_version,
                    bundle_path,
                    bundle_hash,
                    validator_status,
                    _json(metadata or {}),
                    _now(),
                ),
            )

    def get_maintenance_run(self, run_id: str) -> dict[str, Any] | None:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM maintenance_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            key: (json.loads(str(row[key])) if key == "metadata_json" else row[key])
            for key in row.keys()
        }

    def due_reference_review_candidates(
        self, *, ticker: str, as_of: datetime
    ) -> list[ReferenceReviewCandidate]:
        """Return the durable due set against one frozen as_of clock."""

        normalized = self._ticker(ticker)
        events = {item.event_id: item for item in self.published_events(normalized)}
        superseded_by: dict[str, list[str]] = {}
        for item in events.values():
            if item.supersedes_event_id is not None:
                superseded_by.setdefault(item.supersedes_event_id, []).append(item.event_id)
        with self._read() as connection:
            rows = connection.execute(
                """
                SELECT * FROM reference_review_schedule
                WHERE ticker=? AND next_review_at IS NOT NULL AND next_review_at<=?
                ORDER BY next_review_at,event_no
                """,
                (normalized, as_of.astimezone(UTC).isoformat()),
            ).fetchall()
        candidates: list[ReferenceReviewCandidate] = []
        for row in rows:
            event_id = f"E{int(row['event_no'])}"
            event = events.get(event_id)
            if event is None:
                continue
            with self._read() as connection:
                prior_row = connection.execute(
                    """
                    SELECT decision_json FROM reference_review_history
                    WHERE ticker=? AND event_no=? ORDER BY reviewed_at DESC LIMIT 1
                    """,
                    (normalized, _numeric_id(event_id)),
                ).fetchone()
            prior_basis: ReferenceViewBasis | None = None
            if prior_row is not None:
                prior_payload = json.loads(str(prior_row["decision_json"]))
                if prior_payload.get("reference_view_basis") is not None:
                    prior_basis = ReferenceViewBasis(str(prior_payload["reference_view_basis"]))
            candidate_anchor = (
                None
                if row["occurrence_anchor"] is None
                else datetime.fromisoformat(str(row["occurrence_anchor"])).date()
            )
            last_reviewed = (
                None
                if row["last_reviewed_at"] is None
                else datetime.fromisoformat(str(row["last_reviewed_at"]))
            )
            review_mode, review_reason, _computed_next = classify_review(
                anchor=candidate_anchor,
                as_of=as_of,
                include_in_reference_view=event.include_in_reference_view,
                last_reviewed_at=last_reviewed,
            )
            candidates.append(
                ReferenceReviewCandidate(
                    event_id=event_id,
                    event_type=event.event_type,
                    occurred_at=event.occurred_at,
                    occurrence_time_precision=event.occurrence_time_precision,
                    occurrence_anchor=candidate_anchor,
                    title=event.title,
                    canonical_summary=event.canonical_summary,
                    known_event_summary=event.known_event_summary,
                    facts=list(event.facts),
                    subject_horizons=sorted(
                        {
                            str(fact.subject_time)
                            for fact in event.facts
                            if fact.subject_time not in {None, "SAME"}
                        }
                    ),
                    is_important=event.is_important,
                    include_in_reference_view=event.include_in_reference_view,
                    prior_reference_view_basis=prior_basis,
                    related_event_ids=event.related_event_ids,
                    supersedes_event_id=event.supersedes_event_id,
                    supersedes_event_ids=(
                        []
                        if event.supersedes_event_id is None
                        else [event.supersedes_event_id]
                    ),
                    superseded_by_event_ids=sorted(superseded_by.get(event_id, [])),
                    frozen_as_of=as_of,
                    last_reviewed_at=last_reviewed,
                    next_review_at=datetime.fromisoformat(str(row["next_review_at"])),
                    review_mode=review_mode,
                    candidate_reason=review_reason,
                )
            )
        return candidates

    def reference_review_history_count(self, *, ticker: str, run_id: str) -> int:
        with self._read() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM reference_review_history "
                "WHERE ticker=? AND review_run_id=?",
                (self._ticker(ticker), run_id),
            ).fetchone()
        return int(row["count"] if row is not None else 0)

    def latest_reference_view_basis(self, *, ticker: str, event_id: str) -> str | None:
        """Return the last auditable basis without changing the stable Event wire."""

        with self._read() as connection:
            row = connection.execute(
                """
                SELECT decision_json FROM reference_review_history
                WHERE ticker=? AND event_no=? ORDER BY reviewed_at DESC LIMIT 1
                """,
                (self._ticker(ticker), _numeric_id(event_id)),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row["decision_json"]))
        basis = payload.get("reference_view_basis")
        return None if basis is None else str(basis)

    def publish_bundle(
        self,
        bundle: CanonicalRevisionBundle,
        *,
        source_bundle: CanonicalRevisionBundle | None = None,
        frozen_as_of: datetime | None = None,
    ) -> PublicationResult:
        """Import a validator-approved Bundle and switch the Published head in one transaction."""

        ticker = self._ticker(bundle.ticker)
        with self._write() as connection:
            batch_rows = self._batch_rows(connection, bundle.delta_batch_ids)
            if len(batch_rows) != len(bundle.delta_batch_ids):
                raise UnknownLibraryObjectError("one or more Delta batches do not exist")
            previous = self._reused_publication(batch_rows, source_bundle or bundle)
            if previous is not None:
                return previous
            head = self._head_in_connection(connection, ticker)
            if head != bundle.base_library_version:
                raise StaleLibraryBaseError(
                    f"Bundle base V{bundle.base_library_version} is stale; Published is V{head}"
                )
            if any(str(row["ticker"]) != ticker for row in batch_rows):
                raise EventLibraryError("Delta batch ticker does not match Bundle ticker")
            next_version = head + 1
            event_id_map, fact_id_map = self._allocate_ids(connection, ticker, bundle)
            has_library_changes = bool(bundle.event_revisions or bundle.event_retirements)
            published_version = next_version if has_library_changes else head
            if has_library_changes:
                connection.execute(
                    """
                    INSERT INTO library_versions(
                        ticker,version,base_version,source_delta_batches_json,status,created_at
                    ) VALUES (?,?,?,?,?,?)
                    """,
                    (
                        ticker,
                        next_version,
                        head,
                        _json(bundle.delta_batch_ids),
                        LibraryVersionStatus.WORKING.value,
                        _now(),
                    ),
                )
                self._write_event_revisions(
                    connection,
                    ticker,
                    next_version,
                    bundle.event_revisions,
                    event_id_map,
                    fact_id_map,
                )
                self._write_retirements(connection, ticker, next_version, bundle, event_id_map)
                self._suppress_orphaned_facts(connection, ticker, next_version)
            counts = self._write_delta_dispositions(
                connection,
                ticker,
                bundle,
                event_id_map,
                fact_id_map,
                published_version,
            )
            self._write_reference_reviews(
                connection,
                ticker=ticker,
                run_id=bundle.run_id,
                decisions=bundle.reference_review_decisions,
                event_id_map=event_id_map,
                revised_events=bundle.event_revisions,
                frozen_as_of=frozen_as_of,
            )
            if has_library_changes:
                now = _now()
                connection.execute(
                    """
                    UPDATE library_versions SET status=?,published_at=?
                    WHERE ticker=? AND version=?
                    """,
                    (LibraryVersionStatus.PUBLISHED.value, now, ticker, next_version),
                )
                connection.execute(
                    """
                    INSERT INTO library_heads(ticker,published_version,updated_at) VALUES (?,?,?)
                    ON CONFLICT(ticker) DO UPDATE SET
                        published_version=excluded.published_version,updated_at=excluded.updated_at
                    """,
                    (ticker, next_version, now),
                )
            status = (
                DeltaBatchStatus.PARTIAL_PUBLISHED
                if counts["pending"]
                else DeltaBatchStatus.PUBLISHED
            )
            result = PublicationResult(
                ticker=ticker,
                base_library_version=head,
                published_library_version=published_version,
                delta_batch_ids=bundle.delta_batch_ids,
                batch_status=status,
                event_id_map=event_id_map,
                fact_id_map=fact_id_map,
                applied_event_count=len(bundle.event_revisions),
                pending_delta_count=counts["pending"],
                dropped_delta_count=counts["dropped"],
                duplicate_delta_count=counts["duplicate"],
            )
            for batch_id in bundle.delta_batch_ids:
                connection.execute(
                    """
                    UPDATE delta_batches SET status=?,bundle_run_id=?,bundle_hash=?,
                        source_bundle_hash=?,published_version=?,
                        publication_json=?,updated_at=? WHERE batch_id=?
                    """,
                    (
                        status.value,
                        bundle.run_id,
                        _bundle_hash(bundle),
                        _bundle_hash(source_bundle or bundle),
                        published_version,
                        _json(result.model_dump(mode="json")),
                        _now(),
                        batch_id,
                    ),
                )
            return result

    def _write_reference_reviews(
        self,
        connection: sqlite3.Connection,
        *,
        ticker: str,
        run_id: str,
        decisions: Sequence[ReferenceReviewDecision],
        event_id_map: dict[str, str],
        revised_events: Sequence[CanonicalEventRevision],
        frozen_as_of: datetime | None,
    ) -> None:
        decisions_by_event = {
            event_id_map.get(item.event_id, item.event_id): item for item in decisions
        }
        # Event fields written in this Bundle are the initial/current judgment. They are
        # scheduled once here and are not emitted as same-run review candidates.
        for revision in revised_events:
            stable_id = event_id_map.get(revision.event_id, revision.event_id)
            decision = decisions_by_event.get(stable_id)
            reviewed_at = (
                decision.reviewed_at
                if decision is not None
                else (frozen_as_of or datetime.now(UTC))
            )
            included = (
                decision.include_in_reference_view
                if decision is not None
                else revision.include_in_reference_view
            )
            anchor = event_review_anchor(revision.published())
            mode, reason, next_at = classify_review(
                anchor=anchor,
                as_of=reviewed_at,
                include_in_reference_view=included,
            )
            self._upsert_review_schedule(
                connection,
                ticker=ticker,
                event_no=_numeric_id(stable_id),
                anchor=anchor,
                reviewed_at=reviewed_at,
                next_at=(decision.next_review_at if decision is not None else next_at),
                mode=(decision.review_mode if decision is not None else mode),
                reason=(decision.candidate_reason if decision is not None else reason),
            )
            if decision is not None:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO reference_review_history(
                        ticker,review_run_id,event_no,reviewed_at,review_mode,
                        candidate_reason,changed,include_in_reference_view,decision_json
                    ) VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        ticker,
                        run_id,
                        _numeric_id(stable_id),
                        decision.reviewed_at.isoformat(),
                        decision.review_mode.value,
                        decision.candidate_reason.value,
                        int(decision.changed),
                        int(decision.include_in_reference_view),
                        _json(decision.model_dump(mode="json")),
                    ),
                )
        for stable_id, decision in decisions_by_event.items():
            if any(
                event_id_map.get(item.event_id, item.event_id) == stable_id
                for item in revised_events
            ):
                continue
            event = self._event_from_connection(
                connection,
                ticker,
                _numeric_id(stable_id),
                self._head_in_connection(connection, ticker),
            )
            anchor = event_review_anchor(event)
            mode, reason, computed_next = classify_review(
                anchor=anchor,
                as_of=decision.reviewed_at,
                include_in_reference_view=decision.include_in_reference_view,
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO reference_review_history(
                    ticker,review_run_id,event_no,reviewed_at,review_mode,
                    candidate_reason,changed,include_in_reference_view,decision_json
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    ticker,
                    run_id,
                    _numeric_id(stable_id),
                    decision.reviewed_at.isoformat(),
                    decision.review_mode.value,
                    decision.candidate_reason.value,
                    int(decision.changed),
                    int(decision.include_in_reference_view),
                    _json(decision.model_dump(mode="json")),
                ),
            )
            self._upsert_review_schedule(
                connection,
                ticker=ticker,
                event_no=_numeric_id(stable_id),
                anchor=anchor,
                reviewed_at=decision.reviewed_at,
                next_at=(
                    decision.next_review_at
                    if decision.next_review_at is not None
                    else computed_next
                ),
                mode=decision.review_mode if decision.review_mode else mode,
                reason=decision.candidate_reason if decision.candidate_reason else reason,
            )

    @staticmethod
    def _upsert_review_schedule(
        connection: sqlite3.Connection,
        *,
        ticker: str,
        event_no: int,
        anchor: Any,
        reviewed_at: datetime,
        next_at: datetime | None,
        mode: ReferenceReviewMode,
        reason: ReferenceReviewReason,
    ) -> None:
        connection.execute(
            """
            INSERT INTO reference_review_schedule(
                ticker,event_no,occurrence_anchor,last_reviewed_at,next_review_at,
                review_mode,candidate_reason,updated_at
            ) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(ticker,event_no) DO UPDATE SET
                occurrence_anchor=excluded.occurrence_anchor,
                last_reviewed_at=excluded.last_reviewed_at,
                next_review_at=excluded.next_review_at,
                review_mode=excluded.review_mode,
                candidate_reason=excluded.candidate_reason,
                updated_at=excluded.updated_at
            """,
            (
                ticker,
                event_no,
                None if anchor is None else anchor.isoformat(),
                reviewed_at.astimezone(UTC).isoformat(),
                None if next_at is None else next_at.astimezone(UTC).isoformat(),
                mode.value,
                reason.value,
                _now(),
            ),
        )

    @staticmethod
    def _batch_rows(connection: sqlite3.Connection, batch_ids: Sequence[str]) -> list[sqlite3.Row]:
        return [
            row
            for batch_id in batch_ids
            if (
                row := connection.execute(
                    "SELECT * FROM delta_batches WHERE batch_id=?", (batch_id,)
                ).fetchone()
            )
            is not None
        ]

    @staticmethod
    def _reused_publication(
        rows: Sequence[sqlite3.Row], bundle: CanonicalRevisionBundle
    ) -> PublicationResult | None:
        if not rows or any(row["publication_json"] is None for row in rows):
            return None
        if any(str(row["bundle_run_id"]) != bundle.run_id for row in rows):
            return None
        expected_hash = _bundle_hash(bundle)
        if any(
            expected_hash not in {str(row["bundle_hash"]), str(row["source_bundle_hash"])}
            for row in rows
        ):
            raise EventLibraryError("published Bundle run_id was reused with different content")
        payloads = {str(row["publication_json"]) for row in rows}
        if len(payloads) != 1:
            raise EventLibraryError("Delta batches disagree on prior publication result")
        return PublicationResult.model_validate_json(next(iter(payloads)))

    def prior_publication(self, bundle: CanonicalRevisionBundle) -> PublicationResult | None:
        with self._read() as connection:
            rows = self._batch_rows(connection, bundle.delta_batch_ids)
            if len(rows) != len(bundle.delta_batch_ids):
                return None
            return self._reused_publication(rows, bundle)

    @staticmethod
    def _head_in_connection(connection: sqlite3.Connection, ticker: str) -> int:
        row = connection.execute(
            "SELECT published_version FROM library_heads WHERE ticker=?", (ticker,)
        ).fetchone()
        return 0 if row is None else int(row["published_version"])

    @staticmethod
    def _allocate_ids(
        connection: sqlite3.Connection,
        ticker: str,
        bundle: CanonicalRevisionBundle,
    ) -> tuple[dict[str, str], dict[str, str]]:
        connection.execute(
            "INSERT OR IGNORE INTO id_counters(ticker,next_event_no,next_fact_no) VALUES (?,1,1)",
            (ticker,),
        )
        row = connection.execute(
            "SELECT next_event_no,next_fact_no FROM id_counters WHERE ticker=?", (ticker,)
        ).fetchone()
        assert row is not None
        next_event = int(row["next_event_no"])
        next_fact = int(row["next_fact_no"])
        event_map: dict[str, str] = {}
        fact_map: dict[str, str] = {}
        for event in bundle.event_revisions:
            if event.event_id.startswith("T"):
                event_map[event.event_id] = f"E{next_event}"
                next_event += 1
            for fact in event.facts:
                if fact.fact_id.startswith("TF"):
                    fact_map[fact.fact_id] = f"F{next_fact}"
                    next_fact += 1
        connection.execute(
            "UPDATE id_counters SET next_event_no=?,next_fact_no=? WHERE ticker=?",
            (next_event, next_fact, ticker),
        )
        return event_map, fact_map

    def _write_event_revisions(
        self,
        connection: sqlite3.Connection,
        ticker: str,
        version: int,
        revisions: Sequence[CanonicalEventRevision],
        event_map: dict[str, str],
        fact_map: dict[str, str],
    ) -> None:
        for revision in revisions:
            stable_event_id = event_map.get(revision.event_id, revision.event_id)
            event_no = _numeric_id(stable_event_id)
            exists = connection.execute(
                "SELECT 1 FROM canonical_events WHERE ticker=? AND event_no=?",
                (ticker, event_no),
            ).fetchone()
            if exists is None:
                connection.execute(
                    "INSERT INTO canonical_events(ticker,event_no,created_version) VALUES (?,?,?)",
                    (ticker, event_no, version),
                )
            if revision.status is CanonicalObjectStatus.ACTIVE:
                connection.execute(
                    """
                    INSERT INTO canonical_event_states(
                        ticker,event_no,library_version,status,redirect_to_event_no
                    ) VALUES (?,?,?,?,NULL)
                    """,
                    (ticker, event_no, version, CanonicalObjectStatus.ACTIVE.value),
                )
            event_payload = revision.published().model_dump(mode="json", exclude={"facts"})
            event_payload["event_id"] = stable_event_id
            event_payload["related_event_ids"] = [
                event_map.get(item, item) for item in revision.related_event_ids
            ]
            event_payload["derived_from_event_ids"] = [
                event_map.get(item, item) for item in revision.derived_from_event_ids
            ]
            if revision.supersedes_event_id is not None:
                event_payload["supersedes_event_id"] = event_map.get(
                    revision.supersedes_event_id, revision.supersedes_event_id
                )
            revision_no = self._next_revision_no(
                connection, "canonical_event_revisions", "event_no", ticker, event_no
            )
            connection.execute(
                """
                INSERT INTO canonical_event_revisions(
                    ticker,event_no,revision_no,library_version,payload_json
                ) VALUES (?,?,?,?,?)
                """,
                (ticker, event_no, revision_no, version, _json(event_payload)),
            )
            connection.execute(
                """
                UPDATE event_fact_memberships SET valid_to_version=?
                WHERE ticker=? AND event_no=? AND valid_to_version IS NULL
                """,
                (version - 1, ticker, event_no),
            )
            for fact in revision.facts:
                stable_fact_id = fact_map.get(fact.fact_id, fact.fact_id)
                fact_no = _numeric_id(stable_fact_id)
                fact_exists = connection.execute(
                    "SELECT 1 FROM canonical_facts WHERE ticker=? AND fact_no=?",
                    (ticker, fact_no),
                ).fetchone()
                if fact_exists is None:
                    connection.execute(
                        "INSERT INTO canonical_facts(ticker,fact_no,created_version) "
                        "VALUES (?,?,?)",
                        (ticker, fact_no, version),
                    )
                    if revision.status is CanonicalObjectStatus.ACTIVE:
                        connection.execute(
                            """
                            INSERT INTO canonical_fact_states(
                                ticker,fact_no,library_version,status,redirect_to_fact_no
                            ) VALUES (?,?,?,'ACTIVE',NULL)
                            """,
                            (ticker, fact_no, version),
                        )
                fact_payload = fact.published().model_dump(mode="json")
                fact_payload["fact_id"] = stable_fact_id
                latest = connection.execute(
                    """
                    SELECT payload_json FROM canonical_fact_revisions
                    WHERE ticker=? AND fact_no=? ORDER BY revision_no DESC LIMIT 1
                    """,
                    (ticker, fact_no),
                ).fetchone()
                if latest is None or str(latest["payload_json"]) != _json(fact_payload):
                    fact_revision_no = self._next_revision_no(
                        connection, "canonical_fact_revisions", "fact_no", ticker, fact_no
                    )
                    connection.execute(
                        """
                        INSERT INTO canonical_fact_revisions(
                            ticker,fact_no,revision_no,library_version,payload_json
                        ) VALUES (?,?,?,?,?)
                        """,
                        (ticker, fact_no, fact_revision_no, version, _json(fact_payload)),
                    )
                if revision.status is CanonicalObjectStatus.ACTIVE:
                    connection.execute(
                        """
                        INSERT INTO event_fact_memberships(
                            ticker,event_no,fact_no,valid_from_version,valid_to_version
                        ) VALUES (?,?,?,?,NULL)
                        """,
                        (ticker, event_no, fact_no, version),
                    )
            self._replace_relations(connection, ticker, event_no, version, event_payload)

    @staticmethod
    def _next_revision_no(
        connection: sqlite3.Connection,
        table: str,
        id_column: str,
        ticker: str,
        object_no: int,
    ) -> int:
        row = connection.execute(
            f"SELECT MAX(revision_no) AS value FROM {table} WHERE ticker=? AND {id_column}=?",
            (ticker, object_no),
        ).fetchone()
        return 1 if row is None or row["value"] is None else int(row["value"]) + 1

    @staticmethod
    def _replace_relations(
        connection: sqlite3.Connection,
        ticker: str,
        event_no: int,
        version: int,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            UPDATE event_relations SET valid_to_version=?
            WHERE ticker=? AND source_event_no=? AND valid_to_version IS NULL
            """,
            (version - 1, ticker, event_no),
        )
        relations: list[tuple[str, str]] = []
        relations.extend(("RELATED", item) for item in payload["related_event_ids"])
        relations.extend(("DERIVED_FROM", item) for item in payload["derived_from_event_ids"])
        if payload.get("supersedes_event_id"):
            relations.append(("SUPERSEDES", str(payload["supersedes_event_id"])))
        for relation_type, target in relations:
            connection.execute(
                """
                INSERT INTO event_relations(
                    ticker,source_event_no,relation_type,target_event_no,
                    valid_from_version,valid_to_version
                ) VALUES (?,?,?,?,?,NULL)
                """,
                (ticker, event_no, relation_type, _numeric_id(target), version),
            )

    @staticmethod
    def _write_retirements(
        connection: sqlite3.Connection,
        ticker: str,
        version: int,
        bundle: CanonicalRevisionBundle,
        event_map: dict[str, str],
    ) -> None:
        for retirement in bundle.event_retirements:
            source = event_map.get(retirement.event_id, retirement.event_id)
            target = event_map.get(retirement.redirect_to_event_id, retirement.redirect_to_event_id)
            status = (
                CanonicalObjectStatus.SUPPRESSED
                if retirement.reason == "SUPPRESSED_INVALID_OCCURRENCE"
                else CanonicalObjectStatus.MERGED
            )
            connection.execute(
                """
                INSERT INTO canonical_event_states(
                    ticker,event_no,library_version,status,redirect_to_event_no
                ) VALUES (?,?,?,?,?)
                """,
                (
                    ticker,
                    _numeric_id(source),
                    version,
                    status.value,
                    _numeric_id(target),
                ),
            )
            connection.execute(
                """
                UPDATE event_fact_memberships SET valid_to_version=?
                WHERE ticker=? AND event_no=? AND valid_to_version IS NULL
                """,
                (version - 1, ticker, _numeric_id(source)),
            )

    @staticmethod
    def _suppress_orphaned_facts(connection: sqlite3.Connection, ticker: str, version: int) -> None:
        rows = connection.execute(
            """
            SELECT facts.fact_no
            FROM canonical_facts facts
            WHERE facts.ticker=? AND facts.created_version<=?
              AND NOT EXISTS (
                SELECT 1 FROM event_fact_memberships members
                WHERE members.ticker=facts.ticker AND members.fact_no=facts.fact_no
                  AND members.valid_from_version<=?
                  AND (members.valid_to_version IS NULL OR members.valid_to_version>=?)
              )
              AND COALESCE((
                SELECT states.status FROM canonical_fact_states states
                WHERE states.ticker=facts.ticker AND states.fact_no=facts.fact_no
                  AND states.library_version<=?
                ORDER BY states.library_version DESC LIMIT 1
              ), 'ACTIVE')='ACTIVE'
            ORDER BY facts.fact_no
            """,
            (ticker, version, version, version, version),
        ).fetchall()
        for row in rows:
            connection.execute(
                """
                INSERT INTO canonical_fact_states(
                    ticker,fact_no,library_version,status,redirect_to_fact_no
                ) VALUES (?,?,?,'SUPPRESSED',NULL)
                """,
                (ticker, int(row["fact_no"]), version),
            )

    @staticmethod
    def _write_delta_dispositions(
        connection: sqlite3.Connection,
        ticker: str,
        bundle: CanonicalRevisionBundle,
        event_map: dict[str, str],
        fact_map: dict[str, str],
        published_version: int,
    ) -> dict[str, int]:
        del published_version
        dispositions: dict[str, tuple[str, str | None, str | None]] = {}
        for event in bundle.event_revisions:
            stable_event = event_map.get(event.event_id, event.event_id)
            for fact in event.facts:
                stable_fact = fact_map.get(fact.fact_id, fact.fact_id)
                for delta_id in fact.consumes_delta_ids:
                    dispositions[delta_id] = ("PUBLISHED_FACT", stable_event, stable_fact)
        for item in bundle.residual_delta_resolutions:
            target_event = (
                None
                if item.target_event_id is None
                else event_map.get(item.target_event_id, item.target_event_id)
            )
            target_fact = (
                None
                if item.target_fact_id is None
                else fact_map.get(item.target_fact_id, item.target_fact_id)
            )
            dispositions[item.delta_id] = (item.resolution.value, target_event, target_fact)
        counts = {"pending": 0, "dropped": 0, "duplicate": 0}
        now = _now()
        for batch_id in bundle.delta_batch_ids:
            rows = connection.execute(
                "SELECT * FROM delta_items WHERE batch_id=? ORDER BY delta_id", (batch_id,)
            ).fetchall()
            for row in rows:
                delta_id = str(row["delta_id"])
                resolution, target_event, target_fact = dispositions[delta_id]
                status = (
                    "PENDING" if resolution == DeltaResolution.KEEP_PENDING.value else "RESOLVED"
                )
                if resolution == DeltaResolution.KEEP_PENDING.value:
                    counts["pending"] += 1
                elif resolution == DeltaResolution.DROP_INVALID.value:
                    counts["dropped"] += 1
                elif resolution == DeltaResolution.DUPLICATE_FACT.value:
                    counts["duplicate"] += 1
                event_no = None if target_event is None else _numeric_id(target_event)
                fact_no = None if target_fact is None else _numeric_id(target_fact)
                connection.execute(
                    """
                    UPDATE delta_items SET status=?,resolution=?,target_event_no=?,target_fact_no=?
                    WHERE batch_id=? AND delta_id=?
                    """,
                    (status, resolution, event_no, fact_no, batch_id, delta_id),
                )
                connection.execute(
                    """
                    INSERT INTO runtime_atomic_mappings(
                        runtime_scope,runtime_atomic_id,runtime_atomic_version,runtime_signature,
                        disposition,canonical_event_no,canonical_fact_no,last_delta_batch,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(runtime_scope,runtime_atomic_id) DO UPDATE SET
                        runtime_atomic_version=excluded.runtime_atomic_version,
                        runtime_signature=excluded.runtime_signature,
                        disposition=excluded.disposition,
                        canonical_event_no=excluded.canonical_event_no,
                        canonical_fact_no=excluded.canonical_fact_no,
                        last_delta_batch=excluded.last_delta_batch,
                        updated_at=excluded.updated_at
                    """,
                    (
                        str(row["runtime_scope"]),
                        str(row["runtime_atomic_id"]),
                        int(row["runtime_atomic_version"]),
                        str(row["runtime_signature"]),
                        resolution,
                        event_no,
                        fact_no,
                        batch_id,
                        now,
                    ),
                )
        return counts
