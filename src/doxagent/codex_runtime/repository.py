"""Bounded persistence for the additive Codex Document 1 v2 runtime.

Hybrid mode deliberately keeps evidence text in SQLite while putting only
structured runtime state and explicitly published documents in PostgreSQL.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar, cast

from pydantic import BaseModel

from doxagent.codex_runtime.schema import (
    ArtifactRef,
    CitationManifest,
    CodexRunSummary,
    CodexWorkflowVersion,
    Document1HandoffV1,
    Document1V2Bundle,
    GlobalResearchBundle,
    GlobalResearchHandoffV1,
    MarketSituationBundle,
    MarketSituationHandoffV1,
    NodeAttempt,
    PublishedDocument,
    ResearchBundle,
    ResearchLane,
    SourceRecord,
    ThreadRecord,
    WorkflowCheckpoint,
    WorkflowEvent,
)
from doxagent.postgres import connect_postgres, record_postgres_failure, record_postgres_payload

ModelT = TypeVar("ModelT", bound=BaseModel)
CODEX_RUNTIME_SQLITE_SCHEMA_VERSION = 3

_WARN_BYTES = {
    "thread": 2 * 1024,
    "artifact": 2 * 1024,
    "attempt": 4 * 1024,
    "checkpoint": 8 * 1024,
    "event": 8 * 1024,
    "bundle": 256 * 1024,
    "document": 512 * 1024,
}
_HARD_BYTES = {
    "thread": 4 * 1024,
    "artifact": 4 * 1024,
    "attempt": 8 * 1024,
    "checkpoint": 16 * 1024,
    "event": 16 * 1024,
    "bundle": 512 * 1024,
    "document": 2 * 1024 * 1024,
}


def _json_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def _guard_payload(kind: str, value: Any, *, table: str, run_id: str | None) -> int:
    size = _json_bytes(value)
    if size > _HARD_BYTES[kind]:
        raise ValueError(f"{table} payload for run {run_id or '-'} exceeds hard limit: {size} B")
    if size > _WARN_BYTES[kind]:
        record_postgres_payload(
            operation="payload.warning",
            table=table,
            run_id=run_id,
            payload_bytes=size,
            item_count=1,
        )
    return size


class CodexRuntimeRepository(Protocol):
    def save_thread(self, record: ThreadRecord) -> None: ...
    def get_thread(self, run_id: str, agent_role: str) -> ThreadRecord | None: ...
    def save_attempt(self, attempt: NodeAttempt) -> None: ...
    def list_attempts(self, run_id: str, limit: int = 100) -> list[NodeAttempt]: ...
    def save_checkpoint(self, checkpoint: WorkflowCheckpoint) -> None: ...
    def get_checkpoint(self, run_id: str) -> WorkflowCheckpoint | None: ...
    def save_artifact(self, artifact: ArtifactRef) -> None: ...
    def get_artifact(self, run_id: str, artifact_id: str) -> ArtifactRef | None: ...
    def list_artifacts(self, run_id: str, limit: int = 500) -> list[ArtifactRef]: ...
    def save_source(self, source: SourceRecord) -> None: ...
    def list_sources(self, run_id: str, limit: int = 500) -> list[SourceRecord]: ...
    def save_citation_manifest(self, manifest: CitationManifest) -> None: ...
    def get_citation_manifest(self, run_id: str, artifact_id: str) -> CitationManifest | None: ...
    def save_bundle(self, bundle: ResearchBundle) -> None: ...
    def get_bundle(self, run_id: str) -> ResearchBundle | None: ...
    def mark_run_published(self, run_id: str, published_at: datetime) -> None: ...
    def list_run_summaries(
        self,
        ticker: str | None,
        cursor: str | None = None,
        limit: int = 20,
        research_lane: ResearchLane | None = None,
    ) -> list[CodexRunSummary]: ...
    def append_event(self, event: WorkflowEvent) -> None: ...
    def list_events(
        self, run_id: str, after_sequence: int = -1, limit: int = 100
    ) -> list[WorkflowEvent]: ...
    def save_published_document(self, document: PublishedDocument) -> None: ...
    def get_published_document(self, run_id: str, artifact_id: str) -> PublishedDocument | None: ...


class InMemoryCodexRuntimeRepository:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._threads: dict[tuple[str, str], ThreadRecord] = {}
        self._attempts: dict[str, dict[str, NodeAttempt]] = defaultdict(dict)
        self._checkpoints: dict[str, WorkflowCheckpoint] = {}
        self._artifacts: dict[str, dict[str, ArtifactRef]] = defaultdict(dict)
        self._sources: dict[str, dict[str, SourceRecord]] = defaultdict(dict)
        self._citations: dict[tuple[str, str], CitationManifest] = {}
        self._bundles: dict[str, ResearchBundle] = {}
        self._events: dict[str, dict[str, WorkflowEvent]] = defaultdict(dict)
        self._documents: dict[tuple[str, str], PublishedDocument] = {}

    def save_thread(self, record: ThreadRecord) -> None:
        with self._lock:
            self._threads[(record.run_id, record.agent_role.value)] = record.model_copy(deep=True)

    def get_thread(self, run_id: str, agent_role: str) -> ThreadRecord | None:
        with self._lock:
            item = self._threads.get((run_id, agent_role))
            return item.model_copy(deep=True) if item else None

    def save_attempt(self, attempt: NodeAttempt) -> None:
        with self._lock:
            self._attempts[attempt.run_id][attempt.attempt_id] = attempt.model_copy(deep=True)

    def list_attempts(self, run_id: str, limit: int = 100) -> list[NodeAttempt]:
        limit = _bounded_limit(limit, default=100, maximum=500)
        with self._lock:
            values = sorted(
                (item.model_copy(deep=True) for item in self._attempts[run_id].values()),
                key=lambda item: (item.created_at, item.attempt_number),
            )
            return values[:limit]

    def save_checkpoint(self, checkpoint: WorkflowCheckpoint) -> None:
        with self._lock:
            self._checkpoints[checkpoint.run_id] = checkpoint.model_copy(deep=True)

    def get_checkpoint(self, run_id: str) -> WorkflowCheckpoint | None:
        with self._lock:
            item = self._checkpoints.get(run_id)
            return item.model_copy(deep=True) if item else None

    def save_artifact(self, artifact: ArtifactRef) -> None:
        with self._lock:
            self._artifacts[artifact.run_id][artifact.artifact_id] = artifact.model_copy(deep=True)

    def get_artifact(self, run_id: str, artifact_id: str) -> ArtifactRef | None:
        with self._lock:
            item = self._artifacts[run_id].get(artifact_id)
            return item.model_copy(deep=True) if item else None

    def list_artifacts(self, run_id: str, limit: int = 500) -> list[ArtifactRef]:
        limit = _bounded_limit(limit, default=500, maximum=500)
        with self._lock:
            values = sorted(
                (item.model_copy(deep=True) for item in self._artifacts[run_id].values()),
                key=lambda item: item.created_at,
            )
            return values[:limit]

    def save_source(self, source: SourceRecord) -> None:
        with self._lock:
            self._sources[source.run_id][source.source_id] = source.model_copy(deep=True)

    def list_sources(self, run_id: str, limit: int = 500) -> list[SourceRecord]:
        limit = _bounded_limit(limit, default=500, maximum=2000)
        with self._lock:
            values = sorted(
                (item.model_copy(deep=True) for item in self._sources[run_id].values()),
                key=lambda item: item.captured_at,
            )
            return values[:limit]

    def save_citation_manifest(self, manifest: CitationManifest) -> None:
        with self._lock:
            self._citations[(manifest.run_id, manifest.artifact_id)] = manifest.model_copy(
                deep=True
            )

    def get_citation_manifest(self, run_id: str, artifact_id: str) -> CitationManifest | None:
        with self._lock:
            item = self._citations.get((run_id, artifact_id))
            return item.model_copy(deep=True) if item else None

    def save_bundle(self, bundle: ResearchBundle) -> None:
        with self._lock:
            self._bundles[bundle.run_id] = bundle.model_copy(deep=True)

    def get_bundle(self, run_id: str) -> ResearchBundle | None:
        with self._lock:
            item = self._bundles.get(run_id)
            return item.model_copy(deep=True) if item else None

    def mark_run_published(self, run_id: str, published_at: datetime) -> None:
        return None

    def list_run_summaries(
        self,
        ticker: str | None,
        cursor: str | None = None,
        limit: int = 20,
        research_lane: ResearchLane | None = None,
    ) -> list[CodexRunSummary]:
        limit = _bounded_limit(limit, default=20, maximum=100)
        cursor_pair = _decode_cursor(cursor)
        with self._lock:
            bundles = sorted(
                (
                    item
                    for item in self._bundles.values()
                    if ticker is None or item.ticker.upper() == ticker.upper()
                    if research_lane is None
                    or getattr(item, "research_lane", ResearchLane.LEGACY_DOCUMENT1)
                    is research_lane
                ),
                key=lambda item: (item.created_at, item.run_id),
                reverse=True,
            )
            if cursor_pair:
                bundles = [item for item in bundles if (item.created_at, item.run_id) < cursor_pair]
            return [_summary_from_bundle(item) for item in bundles[:limit]]

    def append_event(self, event: WorkflowEvent) -> None:
        with self._lock:
            if event.event_id in self._events[event.run_id]:
                return
            sequence = (
                max((item.sequence for item in self._events[event.run_id].values()), default=-1) + 1
            )
            self._events[event.run_id][event.event_id] = event.model_copy(
                update={"sequence": sequence}
            )

    def list_events(
        self, run_id: str, after_sequence: int = -1, limit: int = 100
    ) -> list[WorkflowEvent]:
        limit = _bounded_limit(limit, default=100, maximum=500)
        with self._lock:
            values = sorted(
                (
                    item.model_copy(deep=True)
                    for item in self._events[run_id].values()
                    if item.sequence > after_sequence
                ),
                key=lambda item: item.sequence,
            )
            return values[:limit]

    def save_published_document(self, document: PublishedDocument) -> None:
        with self._lock:
            _validate_published_document_artifact(
                document, self._artifacts[document.run_id].get(document.artifact_id)
            )
            self._documents[(document.run_id, document.artifact_id)] = document.model_copy(
                deep=True
            )

    def get_published_document(self, run_id: str, artifact_id: str) -> PublishedDocument | None:
        with self._lock:
            item = self._documents.get((run_id, artifact_id))
            return item.model_copy(deep=True) if item else None


class SQLiteCodexRuntimeRepository:
    """Durable local evidence store plus a temporary cutover mirror."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > CODEX_RUNTIME_SQLITE_SCHEMA_VERSION:
                raise RuntimeError(f"unsupported Codex SQLite schema version {version}")
            connection.execute("BEGIN IMMEDIATE")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS codex_runtime_records (
                    record_type TEXT NOT NULL,
                    record_key TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    workflow_version TEXT NOT NULL DEFAULT 'codex_d1_v2',
                    research_lane TEXT NOT NULL DEFAULT 'legacy_document1',
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (record_type, record_key)
                );
                CREATE INDEX IF NOT EXISTS idx_codex_runtime_run
                    ON codex_runtime_records (run_id, record_type, sort_order);
                CREATE TABLE IF NOT EXISTS codex_local_sources (
                    source_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    alias TEXT NOT NULL,
                    url TEXT NOT NULL,
                    source_metadata_json TEXT NOT NULL,
                    captured_text TEXT,
                    content_hash TEXT,
                    captured_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_codex_local_sources_run
                    ON codex_local_sources (run_id, captured_at, source_id);
                CREATE TABLE IF NOT EXISTS codex_local_citation_manifests (
                    run_id TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    entry_count INTEGER NOT NULL,
                    warning_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, artifact_id)
                );
                CREATE INDEX IF NOT EXISTS idx_codex_local_citations_run
                    ON codex_local_citation_manifests (run_id, created_at, artifact_id);
                """
            )
            if version < 2:
                self._copy_legacy_evidence(connection)
                connection.execute("PRAGMA user_version = 2")
            if version < 3:
                columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(codex_runtime_records)"
                    ).fetchall()
                }
                if "workflow_version" not in columns:
                    connection.execute(
                        "ALTER TABLE codex_runtime_records ADD COLUMN workflow_version "
                        "TEXT NOT NULL DEFAULT 'codex_d1_v2'"
                    )
                if "research_lane" not in columns:
                    connection.execute(
                        "ALTER TABLE codex_runtime_records ADD COLUMN research_lane "
                        "TEXT NOT NULL DEFAULT 'legacy_document1'"
                    )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_codex_runtime_lane_run "
                    "ON codex_runtime_records "
                    "(research_lane, workflow_version, run_id, record_type, sort_order)"
                )
                connection.execute("PRAGMA user_version = 3")
            connection.commit()

    @staticmethod
    def _copy_legacy_evidence(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT record_type, record_key, payload_json FROM codex_runtime_records "
            "WHERE record_type IN ('sources','citations') ORDER BY record_type, record_key"
        ).fetchall()
        expected: dict[tuple[str, str], str] = {}
        for row in rows:
            payload_text = str(row["payload_json"])
            payload = json.loads(payload_text)
            digest = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
            expected[(str(row["record_type"]), str(row["record_key"]))] = digest
            if row["record_type"] == "sources":
                source = SourceRecord.model_validate(payload)
                metadata = source.model_dump(mode="json", exclude={"captured_text"})
                metadata["_migration_record_key"] = str(row["record_key"])
                metadata["_migration_payload_sha256"] = digest
                connection.execute(
                    """INSERT INTO codex_local_sources
                       (source_id, run_id, attempt_id, alias, url, source_metadata_json,
                        captured_text, content_hash, captured_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(source_id) DO NOTHING""",
                    (
                        source.source_id,
                        source.run_id,
                        source.attempt_id,
                        source.alias,
                        source.url,
                        json.dumps(metadata, ensure_ascii=False, default=str),
                        source.captured_text,
                        source.content_hash,
                        source.captured_at.isoformat(),
                    ),
                )
            else:
                manifest = CitationManifest.model_validate(payload)
                connection.execute(
                    """INSERT INTO codex_local_citation_manifests
                       (run_id, artifact_id, manifest_json, entry_count, warning_count, created_at)
                       VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(run_id, artifact_id) DO NOTHING""",
                    (
                        manifest.run_id,
                        manifest.artifact_id,
                        payload_text,
                        len(manifest.entries),
                        len(manifest.warnings),
                        manifest.created_at.isoformat(),
                    ),
                )
        for (record_type, record_key), digest in expected.items():
            if record_type == "sources":
                row = connection.execute(
                    "SELECT source_metadata_json FROM codex_local_sources WHERE source_id=?",
                    (record_key,),
                ).fetchone()
                if row is None:
                    raise RuntimeError(f"SQLite v2 source migration lost {record_key}")
                metadata = json.loads(row["source_metadata_json"])
                if metadata.get("_migration_payload_sha256") != digest:
                    raise RuntimeError(f"SQLite v2 source checksum mismatch for {record_key}")
            else:
                payload = json.loads(
                    connection.execute(
                        "SELECT payload_json FROM codex_runtime_records "
                        "WHERE record_type='citations' AND record_key=?",
                        (record_key,),
                    ).fetchone()[0]
                )
                row = connection.execute(
                    "SELECT manifest_json FROM codex_local_citation_manifests "
                    "WHERE run_id=? AND artifact_id=?",
                    (payload["run_id"], payload["artifact_id"]),
                ).fetchone()
                if row is None or hashlib.sha256(str(row[0]).encode("utf-8")).hexdigest() != digest:
                    raise RuntimeError(f"SQLite v2 citation checksum mismatch for {record_key}")

    def _upsert(
        self, record_type: str, key: str, run_id: str, value: BaseModel, order: int = 0
    ) -> None:
        payload = value.model_dump_json(by_alias=True)
        workflow_version = str(getattr(value, "workflow_version", "codex_d1_v2"))
        research_lane_value = getattr(value, "research_lane", ResearchLane.LEGACY_DOCUMENT1)
        research_lane = str(getattr(research_lane_value, "value", research_lane_value))
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO codex_runtime_records
                   (record_type, record_key, run_id, workflow_version, research_lane,
                    sort_order, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(record_type, record_key) DO UPDATE SET
                     run_id=excluded.run_id,
                     workflow_version=excluded.workflow_version,
                     research_lane=excluded.research_lane,
                     sort_order=excluded.sort_order,
                     payload_json=excluded.payload_json, updated_at=CURRENT_TIMESTAMP""",
                (
                    record_type,
                    key,
                    run_id,
                    workflow_version,
                    research_lane,
                    order,
                    payload,
                ),
            )

    def _one(self, record_type: str, key: str, model: type[ModelT]) -> ModelT | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM codex_runtime_records "
                "WHERE record_type=? AND record_key=?",
                (record_type, key),
            ).fetchone()
        return model.model_validate_json(row["payload_json"]) if row else None

    def _many(
        self, record_type: str, run_id: str, model: type[ModelT], *, limit: int
    ) -> list[ModelT]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT payload_json FROM codex_runtime_records
                   WHERE record_type=? AND run_id=?
                   ORDER BY sort_order, updated_at LIMIT ?""",
                (record_type, run_id, limit),
            ).fetchall()
        return [model.model_validate_json(row["payload_json"]) for row in rows]

    def save_thread(self, record: ThreadRecord) -> None:
        self._upsert("threads", f"{record.run_id}:{record.agent_role.value}", record.run_id, record)

    def get_thread(self, run_id: str, agent_role: str) -> ThreadRecord | None:
        return self._one("threads", f"{run_id}:{agent_role}", ThreadRecord)

    def save_attempt(self, attempt: NodeAttempt) -> None:
        self._upsert(
            "attempts", attempt.attempt_id, attempt.run_id, attempt, attempt.attempt_number
        )

    def list_attempts(self, run_id: str, limit: int = 100) -> list[NodeAttempt]:
        return self._many("attempts", run_id, NodeAttempt, limit=_bounded_limit(limit, 100, 500))

    def save_checkpoint(self, checkpoint: WorkflowCheckpoint) -> None:
        self._upsert("checkpoints", checkpoint.run_id, checkpoint.run_id, checkpoint)

    def get_checkpoint(self, run_id: str) -> WorkflowCheckpoint | None:
        return self._one("checkpoints", run_id, WorkflowCheckpoint)

    def save_artifact(self, artifact: ArtifactRef) -> None:
        self._upsert("artifacts", artifact.artifact_id, artifact.run_id, artifact)

    def get_artifact(self, run_id: str, artifact_id: str) -> ArtifactRef | None:
        item = self._one("artifacts", artifact_id, ArtifactRef)
        return item if item is not None and item.run_id == run_id else None

    def list_artifacts(self, run_id: str, limit: int = 500) -> list[ArtifactRef]:
        return self._many("artifacts", run_id, ArtifactRef, limit=_bounded_limit(limit, 500, 500))

    def save_source(self, source: SourceRecord) -> None:
        metadata = source.model_dump(mode="json", exclude={"captured_text"})
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO codex_local_sources
                   (source_id, run_id, attempt_id, alias, url, source_metadata_json,
                    captured_text, content_hash, captured_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source_id) DO UPDATE SET
                     run_id=excluded.run_id, attempt_id=excluded.attempt_id,
                     alias=excluded.alias, url=excluded.url,
                     source_metadata_json=excluded.source_metadata_json,
                     captured_text=excluded.captured_text, content_hash=excluded.content_hash,
                     captured_at=excluded.captured_at""",
                (
                    source.source_id,
                    source.run_id,
                    source.attempt_id,
                    source.alias,
                    source.url,
                    json.dumps(metadata, ensure_ascii=False, default=str),
                    source.captured_text,
                    source.content_hash,
                    source.captured_at.isoformat(),
                ),
            )

    def list_sources(self, run_id: str, limit: int = 500) -> list[SourceRecord]:
        limit = _bounded_limit(limit, 500, 2000)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT source_metadata_json, captured_text FROM codex_local_sources
                   WHERE run_id=? ORDER BY captured_at, source_id LIMIT ?""",
                (run_id, limit),
            ).fetchall()
        result: list[SourceRecord] = []
        for row in rows:
            payload = json.loads(row["source_metadata_json"])
            payload.pop("_migration_record_key", None)
            payload.pop("_migration_payload_sha256", None)
            payload["captured_text"] = row["captured_text"]
            result.append(SourceRecord.model_validate(payload))
        return result

    def save_citation_manifest(self, manifest: CitationManifest) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO codex_local_citation_manifests
                   (run_id, artifact_id, manifest_json, entry_count, warning_count, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(run_id, artifact_id) DO UPDATE SET
                     manifest_json=excluded.manifest_json, entry_count=excluded.entry_count,
                     warning_count=excluded.warning_count, created_at=excluded.created_at""",
                (
                    manifest.run_id,
                    manifest.artifact_id,
                    manifest.model_dump_json(by_alias=True),
                    len(manifest.entries),
                    len(manifest.warnings),
                    manifest.created_at.isoformat(),
                ),
            )

    def get_citation_manifest(self, run_id: str, artifact_id: str) -> CitationManifest | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """SELECT manifest_json FROM codex_local_citation_manifests
                   WHERE run_id=? AND artifact_id=?""",
                (run_id, artifact_id),
            ).fetchone()
        return CitationManifest.model_validate_json(row["manifest_json"]) if row else None

    def save_bundle(self, bundle: ResearchBundle) -> None:
        self._upsert("bundles", bundle.run_id, bundle.run_id, bundle)

    def get_bundle(self, run_id: str) -> ResearchBundle | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM codex_runtime_records "
                "WHERE record_type='bundles' AND record_key=?",
                (run_id,),
            ).fetchone()
        return _parse_bundle_json(row["payload_json"]) if row else None

    def mark_run_published(self, run_id: str, published_at: datetime) -> None:
        return None

    def list_run_summaries(
        self,
        ticker: str | None,
        cursor: str | None = None,
        limit: int = 20,
        research_lane: ResearchLane | None = None,
    ) -> list[CodexRunSummary]:
        limit = _bounded_limit(limit, 20, 100)
        cursor_pair = _decode_cursor(cursor)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT payload_json FROM codex_runtime_records
                   WHERE record_type='bundles' ORDER BY updated_at DESC LIMIT ?""",
                (limit * 4,),
            ).fetchall()
        bundles = [_parse_bundle_json(row["payload_json"]) for row in rows]
        values = [
            item
            for item in bundles
            if ticker is None or item.ticker.upper() == ticker.upper()
            if research_lane is None
            or getattr(item, "research_lane", ResearchLane.LEGACY_DOCUMENT1) is research_lane
        ]
        if cursor_pair:
            values = [item for item in values if (item.created_at, item.run_id) < cursor_pair]
        return [_summary_from_bundle(item) for item in values[:limit]]

    def append_event(self, event: WorkflowEvent) -> None:
        with self._lock, self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM codex_runtime_records WHERE record_type='events' AND record_key=?",
                (event.event_id,),
            ).fetchone():
                return
            row = connection.execute(
                "SELECT coalesce(max(sort_order), -1) FROM codex_runtime_records "
                "WHERE record_type='events' AND run_id=?",
                (event.run_id,),
            ).fetchone()
            persisted = event.model_copy(update={"sequence": int(row[0]) + 1})
            connection.execute(
                """INSERT INTO codex_runtime_records
                   (record_type, record_key, run_id, workflow_version, research_lane,
                    sort_order, payload_json)
                   VALUES ('events', ?, ?, ?, ?, ?, ?)""",
                (
                    persisted.event_id,
                    persisted.run_id,
                    persisted.workflow_version,
                    persisted.research_lane.value,
                    persisted.sequence,
                    persisted.model_dump_json(),
                ),
            )

    def list_events(
        self, run_id: str, after_sequence: int = -1, limit: int = 100
    ) -> list[WorkflowEvent]:
        limit = _bounded_limit(limit, 100, 500)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT payload_json FROM codex_runtime_records
                   WHERE record_type='events' AND run_id=? AND sort_order>?
                   ORDER BY sort_order LIMIT ?""",
                (run_id, after_sequence, limit),
            ).fetchall()
        return [WorkflowEvent.model_validate_json(row["payload_json"]) for row in rows]

    def save_published_document(self, document: PublishedDocument) -> None:
        _validate_published_document_artifact(
            document, self.get_artifact(document.run_id, document.artifact_id)
        )
        self._upsert("published_documents", document.artifact_id, document.run_id, document)

    def get_published_document(self, run_id: str, artifact_id: str) -> PublishedDocument | None:
        item = self._one("published_documents", artifact_id, PublishedDocument)
        return item if item is not None and item.run_id == run_id else None


class PostgresCodexRuntimeRepository:
    """Structured PostgreSQL repository; no evidence text is stored remotely."""

    def __init__(
        self, database_url: str, *, evidence_repository: CodexRuntimeRepository | None = None
    ) -> None:
        self._database_url = database_url
        self._evidence = evidence_repository or InMemoryCodexRuntimeRepository()
        self._transaction_connection: Any | None = None

    def _connect(self) -> Any:
        import psycopg

        return connect_postgres(psycopg, self._database_url)

    def _execute(self, operation: str, table: str, callback: Any) -> Any:
        try:
            if self._transaction_connection is not None:
                with self._transaction_connection.cursor() as cursor:
                    return callback(self._transaction_connection, cursor)
            with self._connect() as connection, connection.cursor() as cursor:
                return callback(connection, cursor)
        except Exception as exc:
            record_postgres_failure(
                exc, database_url=self._database_url, operation=operation, table=table
            )
            raise

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Reuse one connection for an explicit, single-threaded batch."""

        if self._transaction_connection is not None:
            raise RuntimeError("nested Codex repository transactions are not supported")
        with self._connect() as connection:
            self._transaction_connection = connection
            try:
                yield
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                self._transaction_connection = None

    @staticmethod
    def _audit_read(
        operation: str, table: str, run_id: str | None, payload: Any, count: int
    ) -> None:
        size = _json_bytes(payload)
        if size > 1024 * 1024:
            raise ValueError(f"{table} response for run {run_id or '-'} exceeds 1 MiB: {size} B")
        record_postgres_payload(
            operation=operation, table=table, run_id=run_id, payload_bytes=size, item_count=count
        )

    def save_thread(self, record: ThreadRecord) -> None:
        _guard_payload(
            "thread",
            record.model_dump(mode="json"),
            table="codex_thread_registry",
            run_id=record.run_id,
        )

        def op(_connection: Any, cursor: Any) -> None:
            cursor.execute(
                """INSERT INTO doxagent.codex_thread_registry
                   (run_id, agent_role, thread_id, model, model_provider, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (run_id, agent_role) DO UPDATE SET
                     thread_id=excluded.thread_id, model=excluded.model,
                     model_provider=excluded.model_provider, updated_at=excluded.updated_at""",
                (
                    record.run_id,
                    record.agent_role.value,
                    record.thread_id,
                    record.model,
                    record.model_provider,
                    record.created_at,
                    record.updated_at,
                ),
            )

        self._execute("codex.thread.save", "codex_thread_registry", op)

    def get_thread(self, run_id: str, agent_role: str) -> ThreadRecord | None:
        def op(_connection: Any, cursor: Any) -> Any:
            cursor.execute(
                """SELECT run_id, agent_role, thread_id, model, model_provider,
                          created_at, updated_at
                   FROM doxagent.codex_thread_registry WHERE run_id=%s AND agent_role=%s""",
                (run_id, agent_role),
            )
            return cursor.fetchone()

        row = self._execute("codex.thread.get", "codex_thread_registry", op)
        self._audit_read(
            "codex.thread.get", "codex_thread_registry", run_id, row, int(row is not None)
        )
        ticker, workflow_version, research_lane = self._run_identity(run_id)
        return (
            ThreadRecord(
                workflow_version=workflow_version,
                research_lane=research_lane,
                run_id=row[0],
                ticker=ticker,
                agent_role=row[1],
                thread_id=row[2],
                model=row[3],
                model_provider=row[4],
                created_at=row[5],
                updated_at=row[6],
            )
            if row
            else None
        )

    def save_attempt(self, attempt: NodeAttempt) -> None:
        _guard_payload(
            "attempt",
            attempt.model_dump(mode="json"),
            table="codex_node_attempts",
            run_id=attempt.run_id,
        )

        def op(_connection: Any, cursor: Any) -> None:
            cursor.execute(
                """INSERT INTO doxagent.codex_node_attempts
                   (attempt_id, run_id, node, status, attempt_number, thread_id, input_sha256,
                    error_code, error_message, started_at, completed_at, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (attempt_id) DO UPDATE SET
                     status=excluded.status, thread_id=excluded.thread_id,
                     input_sha256=excluded.input_sha256, error_code=excluded.error_code,
                     error_message=excluded.error_message, started_at=excluded.started_at,
                     completed_at=excluded.completed_at""",
                (
                    attempt.attempt_id,
                    attempt.run_id,
                    attempt.node.value,
                    attempt.status.value,
                    attempt.attempt_number,
                    attempt.thread_id,
                    attempt.input_sha256,
                    attempt.error_code,
                    attempt.error_message,
                    attempt.started_at,
                    attempt.completed_at,
                    attempt.created_at,
                ),
            )

        self._execute("codex.attempt.save", "codex_node_attempts", op)

    def list_attempts(self, run_id: str, limit: int = 100) -> list[NodeAttempt]:
        limit = _bounded_limit(limit, 100, 500)

        def op(_connection: Any, cursor: Any) -> Any:
            cursor.execute(
                """SELECT attempt_id, run_id, node, status, attempt_number, thread_id,
                          input_sha256, error_code, error_message, started_at,
                          completed_at, created_at
                   FROM doxagent.codex_node_attempts WHERE run_id=%s
                   ORDER BY created_at, attempt_number LIMIT %s""",
                (run_id, limit),
            )
            return cursor.fetchall()

        rows = self._execute("codex.attempt.list", "codex_node_attempts", op)
        self._audit_read("codex.attempt.list", "codex_node_attempts", run_id, rows, len(rows))
        ticker, workflow_version, research_lane = self._run_identity(run_id)
        return [
            NodeAttempt(
                workflow_version=workflow_version,
                research_lane=research_lane,
                attempt_id=row[0],
                run_id=row[1],
                ticker=ticker,
                node=row[2],
                status=row[3],
                attempt_number=row[4],
                thread_id=row[5],
                input_sha256=row[6],
                error_code=row[7],
                error_message=row[8],
                started_at=row[9],
                completed_at=row[10],
                created_at=row[11],
            )
            for row in rows
        ]

    def save_checkpoint(self, checkpoint: WorkflowCheckpoint) -> None:
        _guard_payload(
            "checkpoint",
            checkpoint.model_dump(mode="json"),
            table="codex_workflow_checkpoints",
            run_id=checkpoint.run_id,
        )
        completed = [item.value for item in checkpoint.completed_nodes]
        current = [item.value for item in checkpoint.current_nodes]
        failed = [item.value for item in checkpoint.failed_nodes]
        status = (
            "cancelled"
            if checkpoint.cancelled
            else "failed"
            if checkpoint.failed_nodes
            else "running"
        )

        def op(_connection: Any, cursor: Any) -> None:
            cursor.execute(
                """INSERT INTO doxagent.codex_run_registry
                   (run_id,ticker,workflow_version,research_lane,status,current_node,completed_node_count,
                    failed_node_count,latest_event_sequence,created_at,updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,-1,%s,%s)
                   ON CONFLICT (run_id) DO UPDATE SET
                     ticker=excluded.ticker,
                     status=CASE WHEN doxagent.codex_run_registry.status='published'
                                 THEN 'published' ELSE excluded.status END,
                     current_node=excluded.current_node,
                     completed_node_count=excluded.completed_node_count,
                     failed_node_count=excluded.failed_node_count,
                     updated_at=excluded.updated_at""",
                (
                    checkpoint.run_id,
                    checkpoint.ticker,
                    checkpoint.workflow_version,
                    checkpoint.research_lane.value,
                    status,
                    current[0] if current else None,
                    len(completed),
                    len(failed),
                    checkpoint.updated_at,
                    checkpoint.updated_at,
                ),
            )
            cursor.execute(
                """INSERT INTO doxagent.codex_workflow_checkpoints
                   (run_id, ticker, workflow_version, research_lane, completed_nodes, current_nodes,
                    failed_nodes, cancelled, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (run_id) DO UPDATE SET
                     ticker=excluded.ticker, completed_nodes=excluded.completed_nodes,
                     current_nodes=excluded.current_nodes, failed_nodes=excluded.failed_nodes,
                     cancelled=excluded.cancelled, updated_at=excluded.updated_at""",
                (
                    checkpoint.run_id,
                    checkpoint.ticker,
                    checkpoint.workflow_version,
                    checkpoint.research_lane.value,
                    completed,
                    current,
                    failed,
                    checkpoint.cancelled,
                    checkpoint.updated_at,
                ),
            )

        self._execute("codex.checkpoint.save", "codex_workflow_checkpoints", op)

    def get_checkpoint(self, run_id: str) -> WorkflowCheckpoint | None:
        def op(_connection: Any, cursor: Any) -> Any:
            cursor.execute(
                """SELECT run_id,ticker,workflow_version,research_lane,
                          completed_nodes,current_nodes,
                          failed_nodes,cancelled,updated_at
                   FROM doxagent.codex_workflow_checkpoints WHERE run_id=%s""",
                (run_id,),
            )
            return cursor.fetchone()

        row = self._execute("codex.checkpoint.get", "codex_workflow_checkpoints", op)
        self._audit_read(
            "codex.checkpoint.get", "codex_workflow_checkpoints", run_id, row, int(row is not None)
        )
        return (
            WorkflowCheckpoint(
                run_id=row[0],
                ticker=row[1],
                workflow_version=row[2],
                research_lane=row[3],
                completed_nodes=row[4],
                current_nodes=row[5],
                failed_nodes=row[6],
                cancelled=row[7],
                updated_at=row[8],
            )
            if row
            else None
        )

    def save_artifact(self, artifact: ArtifactRef) -> None:
        _guard_payload(
            "artifact",
            artifact.model_dump(mode="json"),
            table="codex_artifacts",
            run_id=artifact.run_id,
        )

        def op(_connection: Any, cursor: Any) -> None:
            cursor.execute(
                """INSERT INTO doxagent.codex_artifacts
                   (artifact_id,run_id,node,attempt_id,kind,relative_path,sha256,size_bytes,
                    content_type,published,created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (artifact_id) DO UPDATE SET
                     relative_path=excluded.relative_path,sha256=excluded.sha256,
                     size_bytes=excluded.size_bytes,content_type=excluded.content_type,
                     published=excluded.published""",
                (
                    artifact.artifact_id,
                    artifact.run_id,
                    artifact.node.value,
                    artifact.attempt_id,
                    artifact.kind.value,
                    artifact.relative_path,
                    artifact.sha256,
                    artifact.size_bytes,
                    artifact.content_type,
                    artifact.published,
                    artifact.created_at,
                ),
            )

        self._execute("codex.artifact.save", "codex_artifacts", op)

    def _artifact_from_row(self, row: Any) -> ArtifactRef:
        _, workflow_version, research_lane = self._run_identity(str(row[1]))
        return ArtifactRef(
            workflow_version=workflow_version,
            research_lane=research_lane,
            artifact_id=row[0],
            run_id=row[1],
            node=row[2],
            attempt_id=row[3],
            kind=row[4],
            relative_path=row[5],
            sha256=row[6],
            size_bytes=row[7],
            content_type=row[8],
            published=row[9],
            created_at=row[10],
        )

    def get_artifact(self, run_id: str, artifact_id: str) -> ArtifactRef | None:
        def op(_connection: Any, cursor: Any) -> Any:
            cursor.execute(
                """SELECT artifact_id,run_id,node,attempt_id,kind,relative_path,sha256,
                          size_bytes,content_type,published,created_at
                   FROM doxagent.codex_artifacts WHERE run_id=%s AND artifact_id=%s""",
                (run_id, artifact_id),
            )
            return cursor.fetchone()

        row = self._execute("codex.artifact.get", "codex_artifacts", op)
        self._audit_read("codex.artifact.get", "codex_artifacts", run_id, row, int(row is not None))
        return self._artifact_from_row(row) if row else None

    def list_artifacts(self, run_id: str, limit: int = 500) -> list[ArtifactRef]:
        limit = _bounded_limit(limit, 500, 500)

        def op(_connection: Any, cursor: Any) -> Any:
            cursor.execute(
                """SELECT artifact_id,run_id,node,attempt_id,kind,relative_path,sha256,
                          size_bytes,content_type,published,created_at
                   FROM doxagent.codex_artifacts WHERE run_id=%s
                   ORDER BY created_at,artifact_id LIMIT %s""",
                (run_id, limit),
            )
            return cursor.fetchall()

        rows = self._execute("codex.artifact.list", "codex_artifacts", op)
        self._audit_read("codex.artifact.list", "codex_artifacts", run_id, rows, len(rows))
        return [self._artifact_from_row(row) for row in rows]

    def save_source(self, source: SourceRecord) -> None:
        self._evidence.save_source(source)

    def list_sources(self, run_id: str, limit: int = 500) -> list[SourceRecord]:
        return self._evidence.list_sources(run_id, limit)

    def save_citation_manifest(self, manifest: CitationManifest) -> None:
        self._evidence.save_citation_manifest(manifest)

    def get_citation_manifest(self, run_id: str, artifact_id: str) -> CitationManifest | None:
        return self._evidence.get_citation_manifest(run_id, artifact_id)

    def save_bundle(self, bundle: ResearchBundle) -> None:
        if isinstance(bundle, (GlobalResearchBundle, MarketSituationBundle)):
            self._save_lane_bundle(bundle)
            return
        report_index = {key: value.model_dump(mode="json") for key, value in bundle.reports.items()}
        relations = [
            item.model_dump(mode="json", by_alias=True) for item in bundle.entity_relations
        ]
        future = [item.model_dump(mode="json", by_alias=True) for item in bundle.future_nodes]
        detail = {
            "report_index": report_index,
            "entity_relations": relations,
            "future_nodes": future,
        }
        _guard_payload("bundle", detail, table="codex_document1_bundles", run_id=bundle.run_id)
        manifest_id = bundle.handoff.citation_manifest_artifact_id if bundle.handoff else None
        document_id = bundle.handoff.document1_artifact_id if bundle.handoff else None

        def op(_connection: Any, cursor: Any) -> None:
            cursor.execute(
                """INSERT INTO doxagent.codex_document1_bundles
                   (run_id,ticker,workflow_version,status,report_index,entity_relations,
                    future_nodes,citation_manifest_artifact_id,document1_artifact_id,
                    created_at,published_at)
                   VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s)
                   ON CONFLICT (run_id) DO UPDATE SET
                     ticker=excluded.ticker,status=excluded.status,
                     report_index=excluded.report_index,entity_relations=excluded.entity_relations,
                     future_nodes=excluded.future_nodes,
                     citation_manifest_artifact_id=excluded.citation_manifest_artifact_id,
                     document1_artifact_id=excluded.document1_artifact_id,
                     published_at=excluded.published_at""",
                (
                    bundle.run_id,
                    bundle.ticker,
                    bundle.workflow_version,
                    bundle.status,
                    json.dumps(report_index, ensure_ascii=False, default=str),
                    json.dumps(relations, ensure_ascii=False, default=str),
                    json.dumps(future, ensure_ascii=False, default=str),
                    manifest_id,
                    document_id,
                    bundle.created_at,
                    bundle.published_at,
                ),
            )

        self._execute("codex.bundle.save", "codex_document1_bundles", op)

    def _save_lane_bundle(self, bundle: GlobalResearchBundle | MarketSituationBundle) -> None:
        report_index = {key: value.model_dump(mode="json") for key, value in bundle.reports.items()}
        manifest_id = bundle.handoff.citation_manifest_artifact_id if bundle.handoff else None
        document_id = bundle.handoff.document_artifact_id if bundle.handoff else None
        if isinstance(bundle, GlobalResearchBundle):
            table = "codex_global_research_bundles"
            relations = [
                item.model_dump(mode="json", by_alias=True) for item in bundle.entity_relations
            ]
            future = [item.model_dump(mode="json", by_alias=True) for item in bundle.future_nodes]
            detail = {
                "report_index": report_index,
                "entity_relations": relations,
                "future_nodes": future,
            }
        else:
            table = "codex_market_situation_bundles"
            relations = []
            future = []
            detail = {"report_index": report_index}
        _guard_payload("bundle", detail, table=table, run_id=bundle.run_id)

        def op(_connection: Any, cursor: Any) -> None:
            if isinstance(bundle, GlobalResearchBundle):
                cursor.execute(
                    """INSERT INTO doxagent.codex_global_research_bundles
                       (run_id,ticker,workflow_version,research_lane,status,report_index,
                        entity_relations,future_nodes,citation_manifest_artifact_id,
                        document_artifact_id,created_at,published_at)
                       VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s)
                       ON CONFLICT (run_id) DO UPDATE SET
                         ticker=excluded.ticker,status=excluded.status,
                         report_index=excluded.report_index,
                         entity_relations=excluded.entity_relations,
                         future_nodes=excluded.future_nodes,
                         citation_manifest_artifact_id=excluded.citation_manifest_artifact_id,
                         document_artifact_id=excluded.document_artifact_id,
                         published_at=excluded.published_at""",
                    (
                        bundle.run_id,
                        bundle.ticker,
                        bundle.workflow_version,
                        bundle.research_lane.value,
                        bundle.status,
                        json.dumps(report_index, ensure_ascii=False, default=str),
                        json.dumps(relations, ensure_ascii=False, default=str),
                        json.dumps(future, ensure_ascii=False, default=str),
                        manifest_id,
                        document_id,
                        bundle.created_at,
                        bundle.published_at,
                    ),
                )
            else:
                cursor.execute(
                    """INSERT INTO doxagent.codex_market_situation_bundles
                       (run_id,ticker,workflow_version,research_lane,status,report_index,
                        citation_manifest_artifact_id,document_artifact_id,created_at,published_at)
                       VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)
                       ON CONFLICT (run_id) DO UPDATE SET
                         ticker=excluded.ticker,status=excluded.status,
                         report_index=excluded.report_index,
                         citation_manifest_artifact_id=excluded.citation_manifest_artifact_id,
                         document_artifact_id=excluded.document_artifact_id,
                         published_at=excluded.published_at""",
                    (
                        bundle.run_id,
                        bundle.ticker,
                        bundle.workflow_version,
                        bundle.research_lane.value,
                        bundle.status,
                        json.dumps(report_index, ensure_ascii=False, default=str),
                        manifest_id,
                        document_id,
                        bundle.created_at,
                        bundle.published_at,
                    ),
                )

        self._execute("codex.bundle.save", table, op)

    def mark_run_published(self, run_id: str, published_at: datetime) -> None:
        def op(_connection: Any, cursor: Any) -> None:
            cursor.execute(
                """UPDATE doxagent.codex_run_registry
                   SET status='published',current_node=NULL,updated_at=%s,published_at=%s
                   WHERE run_id=%s""",
                (published_at, published_at, run_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"run registry missing for publish {run_id}")

        self._execute("codex.run.publish", "codex_run_registry", op)

    def get_bundle(self, run_id: str) -> ResearchBundle | None:
        try:
            _, workflow_version, _ = self._run_identity(run_id)
        except RuntimeError:
            return None
        if workflow_version in {
            "codex_global_research_v1",
            "codex_market_situation_v1",
        }:
            return self._get_lane_bundle(run_id, workflow_version)

        def op(_connection: Any, cursor: Any) -> Any:
            cursor.execute(
                """SELECT run_id,ticker,workflow_version,status,report_index,entity_relations,
                          future_nodes,citation_manifest_artifact_id,document1_artifact_id,
                          created_at,published_at
                   FROM doxagent.codex_document1_bundles WHERE run_id=%s""",
                (run_id,),
            )
            return cursor.fetchone()

        row = self._execute("codex.bundle.get", "codex_document1_bundles", op)
        self._audit_read(
            "codex.bundle.get", "codex_document1_bundles", run_id, row, int(row is not None)
        )
        if not row:
            return None
        handoff = None
        if row[8] and row[10]:
            handoff = Document1HandoffV1(
                run_id=row[0],
                ticker=row[1],
                document1_artifact_id=row[8],
                citation_manifest_artifact_id=row[7],
                published_at=row[10],
            )
        return Document1V2Bundle(
            run_id=row[0],
            ticker=row[1],
            workflow_version=row[2],
            status=row[3],
            reports={key: ArtifactRef.model_validate(value) for key, value in row[4].items()},
            entity_relations=row[5],
            future_nodes=row[6],
            handoff=handoff,
            created_at=row[9],
            published_at=row[10],
        )

    def _get_lane_bundle(self, run_id: str, workflow_version: str) -> ResearchBundle | None:
        global_lane = workflow_version == "codex_global_research_v1"
        table = "codex_global_research_bundles" if global_lane else "codex_market_situation_bundles"

        def op(_connection: Any, cursor: Any) -> Any:
            if global_lane:
                cursor.execute(
                    """SELECT run_id,ticker,workflow_version,research_lane,status,report_index,
                              entity_relations,future_nodes,citation_manifest_artifact_id,
                              document_artifact_id,created_at,published_at
                       FROM doxagent.codex_global_research_bundles WHERE run_id=%s""",
                    (run_id,),
                )
            else:
                cursor.execute(
                    """SELECT run_id,ticker,workflow_version,research_lane,status,report_index,
                              citation_manifest_artifact_id,document_artifact_id,
                              created_at,published_at
                       FROM doxagent.codex_market_situation_bundles WHERE run_id=%s""",
                    (run_id,),
                )
            return cursor.fetchone()

        row = self._execute("codex.bundle.get", table, op)
        self._audit_read("codex.bundle.get", table, run_id, row, int(row is not None))
        if not row:
            return None
        if global_lane:
            global_handoff = (
                GlobalResearchHandoffV1(
                    run_id=row[0],
                    ticker=row[1],
                    document_artifact_id=row[9],
                    citation_manifest_artifact_id=row[8],
                    published_at=row[11],
                )
                if row[9] and row[11]
                else None
            )
            return GlobalResearchBundle(
                run_id=row[0],
                ticker=row[1],
                status=row[4],
                reports={key: ArtifactRef.model_validate(value) for key, value in row[5].items()},
                entity_relations=row[6],
                future_nodes=row[7],
                handoff=global_handoff,
                created_at=row[10],
                published_at=row[11],
            )
        market_handoff = (
            MarketSituationHandoffV1(
                run_id=row[0],
                ticker=row[1],
                document_artifact_id=row[7],
                citation_manifest_artifact_id=row[6],
                published_at=row[9],
            )
            if row[7] and row[9]
            else None
        )
        return MarketSituationBundle(
            run_id=row[0],
            ticker=row[1],
            status=row[4],
            reports={key: ArtifactRef.model_validate(value) for key, value in row[5].items()},
            handoff=market_handoff,
            created_at=row[8],
            published_at=row[9],
        )

    def list_run_summaries(
        self,
        ticker: str | None,
        cursor: str | None = None,
        limit: int = 20,
        research_lane: ResearchLane | None = None,
    ) -> list[CodexRunSummary]:
        limit = _bounded_limit(limit, 20, 100)
        cursor_pair = _decode_cursor(cursor)

        def op(_connection: Any, db_cursor: Any) -> Any:
            conditions: list[str] = []
            params: list[Any] = []
            if ticker:
                conditions.append("ticker=upper(%s)")
                params.append(ticker)
            if research_lane is not None:
                conditions.append("research_lane=%s")
                params.append(research_lane.value)
            if cursor_pair:
                conditions.append("(created_at,run_id)<(%s,%s)")
                params.extend(cursor_pair)
            where = " WHERE " + " AND ".join(conditions) if conditions else ""
            params.append(limit)
            db_cursor.execute(
                """SELECT run_id,ticker,workflow_version,research_lane,status,current_node,
                          completed_node_count,failed_node_count,latest_event_sequence,
                          created_at,updated_at,published_at
                   FROM doxagent.codex_run_registry"""
                + where
                + " ORDER BY created_at DESC,run_id DESC LIMIT %s",
                tuple(params),
            )
            return db_cursor.fetchall()

        rows = self._execute("codex.run.list", "codex_run_registry", op)
        self._audit_read("codex.run.list", "codex_run_registry", None, rows, len(rows))
        if _json_bytes(rows) > 64 * 1024:
            raise ValueError(f"codex_run_registry page exceeds 64 KiB: {_json_bytes(rows)} B")
        return [
            CodexRunSummary(
                run_id=row[0],
                ticker=row[1],
                workflow_version=row[2],
                research_lane=row[3],
                status=row[4],
                current_node=row[5],
                completed_node_count=row[6],
                failed_node_count=row[7],
                latest_event_sequence=row[8],
                created_at=row[9],
                updated_at=row[10],
                published_at=row[11],
            )
            for row in rows
        ]

    def append_event(self, event: WorkflowEvent) -> None:
        _guard_payload("event", event.payload, table="codex_workflow_events", run_id=event.run_id)

        def op(_connection: Any, cursor: Any) -> None:
            cursor.execute(
                "SELECT sequence FROM doxagent.codex_workflow_events WHERE event_id=%s",
                (event.event_id,),
            )
            if cursor.fetchone() is not None:
                return
            cursor.execute(
                """UPDATE doxagent.codex_run_registry
                   SET latest_event_sequence=latest_event_sequence+1,
                       updated_at=greatest(updated_at,%s)
                   WHERE run_id=%s RETURNING latest_event_sequence""",
                (event.created_at, event.run_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError(f"run registry missing before event for {event.run_id}")
            cursor.execute(
                """INSERT INTO doxagent.codex_workflow_events
                   (event_id,run_id,sequence,event_type,payload,created_at)
                   VALUES (%s,%s,%s,%s,%s::jsonb,%s)""",
                (
                    event.event_id,
                    event.run_id,
                    row[0],
                    event.event_type,
                    json.dumps(event.payload, ensure_ascii=False, default=str),
                    event.created_at,
                ),
            )

        self._execute("codex.event.append", "codex_workflow_events", op)

    def list_events(
        self, run_id: str, after_sequence: int = -1, limit: int = 100
    ) -> list[WorkflowEvent]:
        limit = _bounded_limit(limit, 100, 500)

        def op(_connection: Any, cursor: Any) -> Any:
            cursor.execute(
                """SELECT event_id,run_id,sequence,event_type,payload,created_at
                   FROM doxagent.codex_workflow_events
                   WHERE run_id=%s AND sequence>%s ORDER BY sequence LIMIT %s""",
                (run_id, after_sequence, limit),
            )
            return cursor.fetchall()

        rows = self._execute("codex.event.list", "codex_workflow_events", op)
        size = _json_bytes(rows)
        self._audit_read("codex.event.list", "codex_workflow_events", run_id, rows, len(rows))
        if size > 256 * 1024:
            raise ValueError(
                f"codex_workflow_events page for run {run_id} exceeds 256 KiB: {size} B"
            )
        return [
            WorkflowEvent(
                event_id=row[0],
                run_id=row[1],
                sequence=row[2],
                event_type=row[3],
                payload=row[4],
                created_at=row[5],
            )
            for row in rows
        ]

    def save_published_document(self, document: PublishedDocument) -> None:
        _validate_published_document_artifact(
            document, self.get_artifact(document.run_id, document.artifact_id)
        )
        size = (
            len(document.content_text.encode("utf-8")) if document.content_text is not None else 0
        )
        if size > _WARN_BYTES["document"]:
            record_postgres_payload(
                operation="codex.document.warning",
                table="codex_published_documents",
                run_id=document.run_id,
                payload_bytes=size,
                item_count=1,
            )

        def op(_connection: Any, cursor: Any) -> None:
            cursor.execute(
                """INSERT INTO doxagent.codex_published_documents
                   (artifact_id,run_id,artifact_kind,sha256,size_bytes,content_type,
                    content_text,storage_path,published_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (artifact_id) DO UPDATE SET
                     sha256=excluded.sha256,size_bytes=excluded.size_bytes,
                     content_type=excluded.content_type,content_text=excluded.content_text,
                     storage_path=excluded.storage_path,published_at=excluded.published_at""",
                (
                    document.artifact_id,
                    document.run_id,
                    document.artifact_kind,
                    document.sha256,
                    document.size_bytes,
                    document.content_type,
                    document.content_text,
                    document.storage_path,
                    document.published_at,
                ),
            )

        self._execute("codex.document.save", "codex_published_documents", op)

    def get_published_document(self, run_id: str, artifact_id: str) -> PublishedDocument | None:
        def op(_connection: Any, cursor: Any) -> Any:
            cursor.execute(
                """SELECT artifact_id,run_id,artifact_kind,sha256,size_bytes,content_type,
                          content_text,storage_path,published_at
                   FROM doxagent.codex_published_documents
                   WHERE run_id=%s AND artifact_id=%s""",
                (run_id, artifact_id),
            )
            return cursor.fetchone()

        row = self._execute("codex.document.get", "codex_published_documents", op)
        self._audit_read(
            "codex.document.get", "codex_published_documents", run_id, row, int(row is not None)
        )
        return (
            PublishedDocument(
                artifact_id=row[0],
                run_id=row[1],
                artifact_kind=row[2],
                sha256=row[3],
                size_bytes=row[4],
                content_type=row[5],
                content_text=row[6],
                storage_path=row[7],
                published_at=row[8],
            )
            if row
            else None
        )

    def _ticker(self, run_id: str) -> str:
        return self._run_identity(run_id)[0]

    def _run_identity(self, run_id: str) -> tuple[str, CodexWorkflowVersion, ResearchLane]:
        def op(_connection: Any, cursor: Any) -> Any:
            cursor.execute(
                "SELECT ticker,workflow_version,research_lane "
                "FROM doxagent.codex_run_registry WHERE run_id=%s",
                (run_id,),
            )
            return cursor.fetchone()

        row = self._execute("codex.run.identity", "codex_run_registry", op)
        if row is None:
            raise RuntimeError(f"run registry missing for {run_id}")
        return (
            str(row[0]),
            cast(CodexWorkflowVersion, str(row[1])),
            ResearchLane(str(row[2])),
        )


class HybridCodexRuntimeRepository:
    """Routes evidence locally and runtime state remotely without silent failover."""

    def __init__(
        self,
        *,
        local: SQLiteCodexRuntimeRepository,
        remote: PostgresCodexRuntimeRepository,
        mirror_remote_runtime_locally: bool = True,
    ) -> None:
        self.local = local
        self.remote = remote
        self._mirror = mirror_remote_runtime_locally

    def _remote_then_mirror(self, method: str, value: Any) -> None:
        getattr(self.remote, method)(value)
        if self._mirror:
            getattr(self.local, method)(value)

    def save_thread(self, record: ThreadRecord) -> None:
        self._remote_then_mirror("save_thread", record)

    def get_thread(self, run_id: str, agent_role: str) -> ThreadRecord | None:
        return self.remote.get_thread(run_id, agent_role)

    def save_attempt(self, attempt: NodeAttempt) -> None:
        self._remote_then_mirror("save_attempt", attempt)

    def list_attempts(self, run_id: str, limit: int = 100) -> list[NodeAttempt]:
        return self.remote.list_attempts(run_id, limit)

    def save_checkpoint(self, checkpoint: WorkflowCheckpoint) -> None:
        self._remote_then_mirror("save_checkpoint", checkpoint)

    def get_checkpoint(self, run_id: str) -> WorkflowCheckpoint | None:
        return self.remote.get_checkpoint(run_id)

    def save_artifact(self, artifact: ArtifactRef) -> None:
        self._remote_then_mirror("save_artifact", artifact)

    def get_artifact(self, run_id: str, artifact_id: str) -> ArtifactRef | None:
        return self.remote.get_artifact(run_id, artifact_id)

    def list_artifacts(self, run_id: str, limit: int = 500) -> list[ArtifactRef]:
        return self.remote.list_artifacts(run_id, limit)

    def save_source(self, source: SourceRecord) -> None:
        self.local.save_source(source)

    def list_sources(self, run_id: str, limit: int = 500) -> list[SourceRecord]:
        return self.local.list_sources(run_id, limit)

    def save_citation_manifest(self, manifest: CitationManifest) -> None:
        self.local.save_citation_manifest(manifest)

    def get_citation_manifest(self, run_id: str, artifact_id: str) -> CitationManifest | None:
        return self.local.get_citation_manifest(run_id, artifact_id)

    def save_bundle(self, bundle: ResearchBundle) -> None:
        self._remote_then_mirror("save_bundle", bundle)

    def get_bundle(self, run_id: str) -> ResearchBundle | None:
        bundle = self.remote.get_bundle(run_id)
        if bundle and bundle.handoff and bundle.handoff.citation_manifest_artifact_id:
            document_artifact_id = getattr(
                bundle.handoff,
                "document1_artifact_id",
                getattr(bundle.handoff, "document_artifact_id", None),
            )
            manifest = self.local.get_citation_manifest(run_id, document_artifact_id or "")
            if manifest is not None:
                bundle = bundle.model_copy(update={"citation_manifest": manifest})
        return bundle

    def mark_run_published(self, run_id: str, published_at: datetime) -> None:
        self.remote.mark_run_published(run_id, published_at)

    def list_run_summaries(
        self,
        ticker: str | None,
        cursor: str | None = None,
        limit: int = 20,
        research_lane: ResearchLane | None = None,
    ) -> list[CodexRunSummary]:
        return self.remote.list_run_summaries(ticker, cursor, limit, research_lane)

    def append_event(self, event: WorkflowEvent) -> None:
        self._remote_then_mirror("append_event", event)

    def list_events(
        self, run_id: str, after_sequence: int = -1, limit: int = 100
    ) -> list[WorkflowEvent]:
        return self.remote.list_events(run_id, after_sequence, limit)

    def save_published_document(self, document: PublishedDocument) -> None:
        self.remote.save_published_document(document)

    def get_published_document(self, run_id: str, artifact_id: str) -> PublishedDocument | None:
        return self.remote.get_published_document(run_id, artifact_id)


def _bounded_limit(value: int | None, default: int, maximum: int) -> int:
    if value is None:
        return default
    if value < 1 or value > maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return value


def encode_run_cursor(summary: CodexRunSummary) -> str:
    return f"{summary.created_at.isoformat()}|{summary.run_id}"


def _decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if not cursor:
        return None
    try:
        created_at, run_id = cursor.rsplit("|", 1)
        return datetime.fromisoformat(created_at), run_id
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid run cursor") from exc


def _bundle_model_for_version(workflow_version: str) -> type[ResearchBundle]:
    if workflow_version == "codex_global_research_v1":
        return GlobalResearchBundle
    if workflow_version == "codex_market_situation_v1":
        return MarketSituationBundle
    return Document1V2Bundle


def _parse_bundle_json(payload: str) -> ResearchBundle:
    parsed = json.loads(payload)
    model = _bundle_model_for_version(str(parsed.get("workflow_version", "codex_d1_v2")))
    return model.model_validate(parsed)


def _summary_from_bundle(bundle: ResearchBundle) -> CodexRunSummary:
    status: Literal["queued", "running", "failed", "cancelled", "published"] = (
        "published"
        if bundle.status == "published"
        else "failed"
        if bundle.status == "failed"
        else "running"
    )
    return CodexRunSummary(
        run_id=bundle.run_id,
        ticker=bundle.ticker,
        workflow_version=bundle.workflow_version,
        research_lane=getattr(bundle, "research_lane", ResearchLane.LEGACY_DOCUMENT1),
        status=status,
        created_at=bundle.created_at,
        updated_at=bundle.published_at or bundle.created_at,
        published_at=bundle.published_at,
    )


def _validate_published_document_artifact(
    document: PublishedDocument, artifact: ArtifactRef | None
) -> None:
    if artifact is None:
        raise ValueError(f"published document artifact metadata is missing: {document.artifact_id}")
    if artifact.run_id != document.run_id or artifact.kind.value != document.artifact_kind:
        raise ValueError(
            f"published document does not match artifact metadata: {document.artifact_id}"
        )
