"""SQLite implementation of the standalone CDECR registry port."""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import uuid
from array import array
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from cdecr.contracts import (
    AtomicEvent,
    EventMention,
    EventPackage,
    ExternalEventRelation,
    PackageExternalRelation,
    PackageMembership,
    SourceMessage,
    StrictModel,
)
from cdecr.cross_document_contracts import (
    AtomicAssignmentRecord,
    CrossDocumentResult,
    CrossDocumentStatus,
    HoldRecord,
    PackageAssignmentRecord,
    PackagePairMergeDecision,
)
from cdecr.ports import DecisionAuditRecord
from cdecr.single_document_contracts import (
    DreamCandidate,
    GrounderOutput,
    JudgeDecisionRecord,
    ModelCallSummary,
    NormalizationDecision,
    PreprocessedDocument,
    PreprocessingResult,
    ProcessingStatus,
    SingleDocumentResult,
)

SCHEMA_VERSION = 5


class RegistryError(RuntimeError):
    pass


class ImmutableRecordConflict(RegistryError):
    pass


class VersionConflict(RegistryError):
    pass


class StoredEmbedding(StrictModel):
    embedding_id: str
    owner_kind: str
    owner_id: str
    model: str
    dimension: int = Field(gt=0)
    input_hash: str
    vector: list[float]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json_payload(value: StrictModel | dict[str, Any] | list[Any]) -> str:
    data: Any
    if isinstance(value, StrictModel):
        data = value.model_dump(mode="json")
    else:
        data = value
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _v5_predicate(value: object) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value).casefold()).strip("_")
    if not text or not text[0].isalpha():
        return "other_event"
    return text


def _migrate_v5_payload(value: Any, *, draft: bool = False) -> Any:
    """Remove retired confidence fields and add the v5 source-claim contract."""

    if isinstance(value, list):
        return [_migrate_v5_payload(item, draft=draft) for item in value]
    if not isinstance(value, dict):
        return value
    migrated = {
        key: _migrate_v5_payload(item, draft=draft)
        for key, item in value.items()
        if key not in {"confidence", "extraction_confidence", "cluster_confidence"}
    }
    hint = migrated.get("local_package_hint")
    if isinstance(hint, dict):
        hint.pop("package_family", None)
    if draft and "segment_id" in migrated and "text" in migrated:
        migrated.pop("start_char", None)
        migrated.pop("end_char", None)
    mention_shape = {
        "canonical_proposition",
        "event_family",
        "predicate",
        "participants",
        "assertion_state",
        "quantities",
        "open_attributes",
    }
    if mention_shape.issubset(migrated):
        migrated.setdefault("source_claim", None)
        predicate = migrated.get("predicate")
        if isinstance(predicate, dict) and "normalized" in predicate:
            predicate["normalized"] = _v5_predicate(predicate["normalized"])
        participants = migrated.get("participants")
        if draft and isinstance(participants, list):
            for participant in participants:
                if isinstance(participant, dict):
                    participant.pop("entity_id", None)
        quantities = migrated.get("quantities")
        if isinstance(quantities, list):
            for quantity in quantities:
                if isinstance(quantity, dict) and not quantity.get("metric_id"):
                    quantity["metric_id"] = (
                        "unknown_metric" if draft else "UNKNOWN_METRIC"
                    )
    claims = migrated.get("source_claims")
    if isinstance(claims, list):
        for claim in claims:
            if isinstance(claim, dict):
                claim.setdefault("source_claim", None)
    return migrated


def _migrate_json_rows(
    connection: sqlite3.Connection,
    *,
    table: str,
    id_columns: tuple[str, ...],
    json_column: str = "payload_json",
    draft: bool = False,
) -> None:
    columns = ", ".join((*id_columns, json_column))
    for row in connection.execute(f"SELECT {columns} FROM {table}").fetchall():
        raw = row[json_column]
        if raw is None:
            continue
        migrated = _migrate_v5_payload(json.loads(str(raw)), draft=draft)
        payload = json.dumps(
            migrated, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        where = " AND ".join(f"{column} = ?" for column in id_columns)
        connection.execute(
            f"UPDATE {table} SET {json_column} = ? WHERE {where}",
            (payload, *(row[column] for column in id_columns)),
        )


class SQLiteCDECRRegistry:
    """Versioned local registry with immutable source, mention, and audit records."""

    def __init__(self, path: Path | str, *, busy_timeout_ms: int = 5000) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current not in (0, 1, 2, 3, 4, SCHEMA_VERSION):
                raise RegistryError(
                    f"unsupported registry schema version {current}; expected {SCHEMA_VERSION}"
                )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    run_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS source_messages (
                    message_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    primary_ticker TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_source_messages_published
                    ON source_messages(published_at);
                CREATE INDEX IF NOT EXISTS idx_source_messages_fingerprint
                    ON source_messages(fingerprint);

                CREATE TABLE IF NOT EXISTS event_mentions (
                    mention_id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL REFERENCES source_messages(message_id),
                    event_family TEXT NOT NULL,
                    normalized_predicate TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_mentions_source ON event_mentions(message_id);
                CREATE INDEX IF NOT EXISTS idx_mentions_recall
                    ON event_mentions(event_family, normalized_predicate);

                CREATE TABLE IF NOT EXISTS atomic_event_heads (
                    event_id TEXT PRIMARY KEY,
                    current_version INTEGER NOT NULL CHECK(current_version >= 1)
                );
                CREATE TABLE IF NOT EXISTS atomic_event_versions (
                    event_id TEXT NOT NULL REFERENCES atomic_event_heads(event_id),
                    version INTEGER NOT NULL CHECK(version >= 1),
                    event_family TEXT NOT NULL,
                    assertion_state TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(event_id, version)
                );
                CREATE INDEX IF NOT EXISTS idx_atomic_recall
                    ON atomic_event_versions(event_family, assertion_state);
                CREATE TABLE IF NOT EXISTS atomic_event_mentions (
                    event_id TEXT NOT NULL,
                    event_version INTEGER NOT NULL,
                    mention_id TEXT NOT NULL REFERENCES event_mentions(mention_id),
                    PRIMARY KEY(event_id, event_version, mention_id),
                    FOREIGN KEY(event_id, event_version)
                        REFERENCES atomic_event_versions(event_id, version)
                );

                CREATE TABLE IF NOT EXISTS event_package_heads (
                    package_id TEXT PRIMARY KEY,
                    current_version INTEGER NOT NULL CHECK(current_version >= 1)
                );
                CREATE TABLE IF NOT EXISTS event_package_versions (
                    package_id TEXT NOT NULL REFERENCES event_package_heads(package_id),
                    version INTEGER NOT NULL CHECK(version >= 1),
                    package_kind TEXT NOT NULL,
                    package_family TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(package_id, version)
                );
                CREATE INDEX IF NOT EXISTS idx_package_recall
                    ON event_package_versions(package_kind, package_family, status);

                CREATE TABLE IF NOT EXISTS package_memberships (
                    membership_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL REFERENCES atomic_event_heads(event_id),
                    package_id TEXT NOT NULL REFERENCES event_package_heads(package_id),
                    relation TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(event_id, package_id, relation)
                );
                CREATE INDEX IF NOT EXISTS idx_membership_package
                    ON package_memberships(package_id, relation);

                CREATE TABLE IF NOT EXISTS external_relations (
                    relation_id TEXT PRIMARY KEY,
                    source_event_id TEXT NOT NULL REFERENCES atomic_event_heads(event_id),
                    target_event_id TEXT NOT NULL REFERENCES atomic_event_heads(event_id),
                    relation TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(source_event_id, target_event_id, relation)
                );

                CREATE TABLE IF NOT EXISTS embeddings (
                    embedding_id TEXT PRIMARY KEY,
                    owner_kind TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    dimension INTEGER NOT NULL CHECK(dimension > 0),
                    input_hash TEXT NOT NULL,
                    vector_f32 BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(owner_kind, owner_id, model, input_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_embeddings_lookup
                    ON embeddings(owner_kind, owner_id, model);

                CREATE TABLE IF NOT EXISTS model_calls (
                    model_call_id TEXT PRIMARY KEY,
                    run_id TEXT REFERENCES runs(run_id),
                    tier TEXT NOT NULL,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    latency_ms INTEGER NOT NULL CHECK(latency_ms >= 0),
                    error_code TEXT,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_model_calls_run ON model_calls(run_id, tier);

                CREATE TABLE IF NOT EXISTS decision_audits (
                    audit_id TEXT PRIMARY KEY,
                    run_id TEXT REFERENCES runs(run_id),
                    decision_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_decision_audit_subject
                    ON decision_audits(decision_type, subject_id, created_at);

                CREATE TABLE IF NOT EXISTS document_processing_runs (
                    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
                    processing_key TEXT NOT NULL,
                    message_id TEXT NOT NULL REFERENCES source_messages(message_id),
                    pipeline_version TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    catalog_version TEXT NOT NULL,
                    model_config_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    error_code TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_document_completed_key
                    ON document_processing_runs(processing_key) WHERE status = 'SUCCEEDED';
                CREATE INDEX IF NOT EXISTS idx_document_runs_message
                    ON document_processing_runs(message_id, started_at);

                CREATE TABLE IF NOT EXISTS preprocessed_documents (
                    preprocessing_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES document_processing_runs(run_id),
                    message_id TEXT NOT NULL REFERENCES source_messages(message_id),
                    source_fingerprint TEXT NOT NULL,
                    normalized_fingerprint TEXT NOT NULL,
                    normalized_url TEXT NOT NULL,
                    minhash_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, message_id)
                );
                CREATE INDEX IF NOT EXISTS idx_preprocessed_exact
                    ON preprocessed_documents(source_fingerprint);
                CREATE INDEX IF NOT EXISTS idx_preprocessed_normalized
                    ON preprocessed_documents(normalized_fingerprint);
                CREATE INDEX IF NOT EXISTS idx_preprocessed_url
                    ON preprocessed_documents(normalized_url);

                CREATE TABLE IF NOT EXISTS duplicate_relations (
                    relation_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES document_processing_runs(run_id),
                    source_message_id TEXT NOT NULL REFERENCES source_messages(message_id),
                    target_message_id TEXT NOT NULL REFERENCES source_messages(message_id),
                    relation_type TEXT NOT NULL,
                    score REAL NOT NULL CHECK(score >= 0 AND score <= 1),
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_duplicates_source
                    ON duplicate_relations(source_message_id, relation_type);

                CREATE TABLE IF NOT EXISTS dream_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES document_processing_runs(run_id),
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_dream_candidates_run
                    ON dream_candidates(run_id, candidate_id);

                CREATE TABLE IF NOT EXISTS grounder_batch_results (
                    batch_key TEXT PRIMARY KEY,
                    processing_key TEXT NOT NULL,
                    run_id TEXT NOT NULL REFERENCES document_processing_runs(run_id),
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_grounder_batch_processing
                    ON grounder_batch_results(processing_key, created_at);

                CREATE TABLE IF NOT EXISTS judge_decisions (
                    decision_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES document_processing_runs(run_id),
                    target_draft_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_judge_decisions_run
                    ON judge_decisions(run_id, action);

                CREATE TABLE IF NOT EXISTS normalization_decisions (
                    decision_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES document_processing_runs(run_id),
                    mention_id TEXT NOT NULL,
                    field_path TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    method TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_normalization_run
                    ON normalization_decisions(run_id, kind, method);

                CREATE TABLE IF NOT EXISTS document_run_mentions (
                    run_id TEXT NOT NULL REFERENCES document_processing_runs(run_id),
                    mention_id TEXT NOT NULL REFERENCES event_mentions(mention_id),
                    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
                    PRIMARY KEY(run_id, mention_id),
                    UNIQUE(run_id, ordinal)
                );

                CREATE TABLE IF NOT EXISTS cross_document_runs (
                    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
                    processing_key TEXT NOT NULL,
                    message_id TEXT NOT NULL REFERENCES source_messages(message_id),
                    engine_version TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    model_config_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    error_code TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_cross_document_completed_key
                    ON cross_document_runs(processing_key) WHERE status = 'SUCCEEDED';
                CREATE INDEX IF NOT EXISTS idx_cross_document_message
                    ON cross_document_runs(message_id, started_at);

                CREATE TABLE IF NOT EXISTS atomic_assignment_decisions (
                    assignment_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES cross_document_runs(run_id),
                    mention_id TEXT NOT NULL REFERENCES event_mentions(mention_id),
                    candidate_event_id TEXT REFERENCES atomic_event_heads(event_id),
                    resulting_event_id TEXT REFERENCES atomic_event_heads(event_id),
                    action TEXT NOT NULL,
                    relation TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, mention_id)
                );
                CREATE INDEX IF NOT EXISTS idx_atomic_assignments_mention
                    ON atomic_assignment_decisions(mention_id, created_at);

                CREATE TABLE IF NOT EXISTS package_assignment_decisions (
                    assignment_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES cross_document_runs(run_id),
                    event_id TEXT NOT NULL REFERENCES atomic_event_heads(event_id),
                    candidate_package_id TEXT REFERENCES event_package_heads(package_id),
                    resulting_package_id TEXT REFERENCES event_package_heads(package_id),
                    action TEXT NOT NULL,
                    relation TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_package_assignments_event
                    ON package_assignment_decisions(event_id, created_at);

                CREATE TABLE IF NOT EXISTS package_merge_decisions (
                    decision_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES cross_document_runs(run_id),
                    source_package_id TEXT NOT NULL REFERENCES event_package_heads(package_id),
                    target_package_id TEXT NOT NULL REFERENCES event_package_heads(package_id),
                    relation TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS hold_queue (
                    hold_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES cross_document_runs(run_id),
                    kind TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_hold_queue_open
                    ON hold_queue(status, kind, created_at);

                CREATE TABLE IF NOT EXISTS package_external_relations (
                    relation_id TEXT PRIMARY KEY,
                    source_event_id TEXT NOT NULL REFERENCES atomic_event_heads(event_id),
                    target_package_id TEXT NOT NULL REFERENCES event_package_heads(package_id),
                    relation TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(source_event_id, target_package_id, relation)
                );

                CREATE TABLE IF NOT EXISTS atomic_event_redirects (
                    source_event_id TEXT PRIMARY KEY REFERENCES atomic_event_heads(event_id),
                    target_event_id TEXT NOT NULL REFERENCES atomic_event_heads(event_id),
                    run_id TEXT NOT NULL REFERENCES cross_document_runs(run_id),
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    CHECK(source_event_id <> target_event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_atomic_redirect_target
                    ON atomic_event_redirects(target_event_id);

                CREATE TABLE IF NOT EXISTS package_redirects (
                    source_package_id TEXT PRIMARY KEY REFERENCES event_package_heads(package_id),
                    target_package_id TEXT NOT NULL REFERENCES event_package_heads(package_id),
                    run_id TEXT NOT NULL REFERENCES cross_document_runs(run_id),
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    CHECK(source_package_id <> target_package_id)
                );
                CREATE INDEX IF NOT EXISTS idx_package_redirect_target
                    ON package_redirects(target_package_id);

                CREATE TABLE IF NOT EXISTS atomic_event_recall (
                    event_id TEXT PRIMARY KEY REFERENCES atomic_event_heads(event_id),
                    current_version INTEGER NOT NULL,
                    event_family TEXT NOT NULL,
                    normalized_predicate TEXT NOT NULL,
                    schema_type TEXT NOT NULL,
                    assertion_state TEXT NOT NULL,
                    reference_period_id TEXT,
                    event_start TEXT,
                    event_end TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_atomic_recall_identity
                    ON atomic_event_recall(event_family, normalized_predicate, schema_type);
                CREATE INDEX IF NOT EXISTS idx_atomic_recall_period
                    ON atomic_event_recall(reference_period_id, event_family);
                CREATE INDEX IF NOT EXISTS idx_atomic_recall_time
                    ON atomic_event_recall(event_start, event_end);
                CREATE TABLE IF NOT EXISTS atomic_event_recall_entities (
                    event_id TEXT NOT NULL REFERENCES atomic_event_heads(event_id),
                    entity_id TEXT NOT NULL,
                    PRIMARY KEY(event_id, entity_id)
                );
                CREATE INDEX IF NOT EXISTS idx_atomic_recall_entity
                    ON atomic_event_recall_entities(entity_id, event_id);
                CREATE TABLE IF NOT EXISTS atomic_event_recall_sources (
                    event_id TEXT NOT NULL REFERENCES atomic_event_heads(event_id),
                    source_fingerprint TEXT NOT NULL,
                    PRIMARY KEY(event_id, source_fingerprint)
                );
                CREATE INDEX IF NOT EXISTS idx_atomic_recall_source
                    ON atomic_event_recall_sources(source_fingerprint, event_id);

                CREATE TABLE IF NOT EXISTS package_recall (
                    package_id TEXT PRIMARY KEY REFERENCES event_package_heads(package_id),
                    current_version INTEGER NOT NULL,
                    package_kind TEXT NOT NULL,
                    package_family TEXT NOT NULL,
                    local_anchor_hint TEXT,
                    anchor_artifact_id TEXT,
                    anchor_period_id TEXT,
                    time_start TEXT,
                    time_end TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_package_recall_anchor
                    ON package_recall(
                        package_kind, package_family, anchor_period_id, anchor_artifact_id
                    );
                CREATE INDEX IF NOT EXISTS idx_package_recall_time
                    ON package_recall(time_start, time_end);
                CREATE TABLE IF NOT EXISTS package_recall_entities (
                    package_id TEXT NOT NULL REFERENCES event_package_heads(package_id),
                    entity_id TEXT NOT NULL,
                    PRIMARY KEY(package_id, entity_id)
                );
                CREATE INDEX IF NOT EXISTS idx_package_recall_entity
                    ON package_recall_entities(entity_id, package_id);
                """
            )
            if current < 5:
                for table in (
                    "dream_candidates",
                    "atomic_assignment_decisions",
                    "package_assignment_decisions",
                    "package_merge_decisions",
                ):
                    columns = {
                        str(row[1])
                        for row in connection.execute(f"PRAGMA table_info({table})")
                    }
                    if "confidence" in columns:
                        if table == "dream_candidates":
                            connection.execute("DROP INDEX IF EXISTS idx_dream_candidates_run")
                        connection.execute(f"ALTER TABLE {table} DROP COLUMN confidence")
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_dream_candidates_run "
                    "ON dream_candidates(run_id, candidate_id)"
                )
                for table, ids, json_column, draft in (
                    ("event_mentions", ("mention_id",), "payload_json", False),
                    (
                        "atomic_event_versions",
                        ("event_id", "version"),
                        "payload_json",
                        False,
                    ),
                    (
                        "event_package_versions",
                        ("package_id", "version"),
                        "payload_json",
                        False,
                    ),
                    ("package_memberships", ("membership_id",), "payload_json", False),
                    ("external_relations", ("relation_id",), "payload_json", False),
                    (
                        "package_external_relations",
                        ("relation_id",),
                        "payload_json",
                        False,
                    ),
                    ("dream_candidates", ("candidate_id",), "payload_json", False),
                    ("grounder_batch_results", ("batch_key",), "payload_json", True),
                    ("judge_decisions", ("decision_id",), "payload_json", True),
                    (
                        "normalization_decisions",
                        ("decision_id",),
                        "payload_json",
                        False,
                    ),
                    (
                        "atomic_assignment_decisions",
                        ("assignment_id",),
                        "payload_json",
                        False,
                    ),
                    (
                        "package_assignment_decisions",
                        ("assignment_id",),
                        "payload_json",
                        False,
                    ),
                    (
                        "package_merge_decisions",
                        ("decision_id",),
                        "payload_json",
                        False,
                    ),
                    ("hold_queue", ("hold_id",), "payload_json", False),
                    (
                        "document_processing_runs",
                        ("run_id",),
                        "result_json",
                        False,
                    ),
                    ("cross_document_runs", ("run_id",), "result_json", False),
                ):
                    _migrate_json_rows(
                        connection,
                        table=table,
                        id_columns=ids,
                        json_column=json_column,
                        draft=draft,
                    )
            source_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(source_messages)")
            }
            if "primary_ticker" not in source_columns:
                connection.execute("ALTER TABLE source_messages ADD COLUMN primary_ticker TEXT")
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_source_messages_ticker_published
                ON source_messages(primary_ticker, published_at)
                """
            )
            model_call_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(model_calls)")
            }
            for name in ("stage", "prompt_version", "schema_hash", "input_hash"):
                if name not in model_call_columns:
                    connection.execute(f"ALTER TABLE model_calls ADD COLUMN {name} TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_model_calls_stage ON model_calls(run_id, stage)"
            )
            package_recall_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(package_recall)")
            }
            if "local_anchor_hint" not in package_recall_columns:
                connection.execute("ALTER TABLE package_recall ADD COLUMN local_anchor_hint TEXT")
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_package_recall_local_anchor
                ON package_recall(local_anchor_hint, package_family)
                """
            )
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            connection.commit()
        self._backfill_recall_indexes()

    def _backfill_recall_indexes(self) -> None:
        """Populate v3 recall indexes for objects created by v1/v2 registries."""

        with self._connection() as connection:
            missing_atomic = connection.execute(
                """
                SELECT versions.payload_json
                FROM atomic_event_heads heads
                JOIN atomic_event_versions versions
                  ON versions.event_id = heads.event_id
                 AND versions.version = heads.current_version
                LEFT JOIN atomic_event_recall recall ON recall.event_id = heads.event_id
                WHERE recall.event_id IS NULL
                """
            ).fetchall()
            missing_packages = connection.execute(
                """
                SELECT versions.payload_json
                FROM event_package_heads heads
                JOIN event_package_versions versions
                  ON versions.package_id = heads.package_id
                 AND versions.version = heads.current_version
                LEFT JOIN package_recall recall ON recall.package_id = heads.package_id
                WHERE recall.package_id IS NULL
                """
            ).fetchall()
        for row in missing_atomic:
            self._refresh_atomic_recall(AtomicEvent.model_validate_json(str(row["payload_json"])))
        for row in missing_packages:
            self._refresh_package_recall(EventPackage.model_validate_json(str(row["payload_json"])))

    def pragma_state(self) -> dict[str, int | str]:
        with self._connection() as connection:
            return {
                "user_version": int(connection.execute("PRAGMA user_version").fetchone()[0]),
                "foreign_keys": int(connection.execute("PRAGMA foreign_keys").fetchone()[0]),
                "journal_mode": str(connection.execute("PRAGMA journal_mode").fetchone()[0]),
                "busy_timeout": int(connection.execute("PRAGMA busy_timeout").fetchone()[0]),
            }

    def get_source(self, message_id: str) -> SourceMessage | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM source_messages WHERE message_id = ?", (message_id,)
            ).fetchone()
        if row is None:
            return None
        return SourceMessage.model_validate_json(str(row["payload_json"]))

    def list_all_sources(self, *, limit: int = 10000) -> list[SourceMessage]:
        if limit < 1 or limit > 100000:
            raise ValueError("source list limit must be between 1 and 100000")
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM source_messages "
                "ORDER BY published_at, message_id LIMIT ?",
                (limit,),
            ).fetchall()
        return [SourceMessage.model_validate_json(str(row["payload_json"])) for row in rows]

    def get_source_fingerprint(self, message_id: str) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT fingerprint FROM source_messages WHERE message_id = ?", (message_id,)
            ).fetchone()
        return None if row is None else str(row["fingerprint"])

    def get_mention(self, mention_id: str) -> EventMention | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM event_mentions WHERE mention_id = ?", (mention_id,)
            ).fetchone()
        if row is None:
            return None
        return EventMention.model_validate_json(str(row["payload_json"]))

    def list_all_mentions(self, *, limit: int = 100000) -> list[EventMention]:
        if limit < 1 or limit > 1000000:
            raise ValueError("mention list limit must be between 1 and 1000000")
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM event_mentions "
                "ORDER BY message_id, mention_id LIMIT ?",
                (limit,),
            ).fetchall()
        return [EventMention.model_validate_json(str(row["payload_json"])) for row in rows]

    def list_mentions_for_message(self, message_id: str) -> list[EventMention]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM event_mentions
                WHERE message_id = ? ORDER BY created_at, mention_id
                """,
                (message_id,),
            ).fetchall()
        return [EventMention.model_validate_json(str(row["payload_json"])) for row in rows]

    def get_current_atomic_event(self, event_id: str) -> AtomicEvent | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT versions.payload_json
                FROM atomic_event_heads heads
                JOIN atomic_event_versions versions
                  ON versions.event_id = heads.event_id
                 AND versions.version = heads.current_version
                WHERE heads.event_id = ?
                """,
                (event_id,),
            ).fetchone()
        if row is None:
            return None
        return AtomicEvent.model_validate_json(str(row["payload_json"]))

    def list_current_atomic_events(self, *, limit: int = 1000) -> list[AtomicEvent]:
        if limit < 1 or limit > 10000:
            raise ValueError("atomic event list limit must be between 1 and 10000")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT versions.payload_json
                FROM atomic_event_heads heads
                JOIN atomic_event_versions versions
                  ON versions.event_id = heads.event_id
                 AND versions.version = heads.current_version
                LEFT JOIN atomic_event_redirects redirects
                  ON redirects.source_event_id = heads.event_id
                WHERE redirects.source_event_id IS NULL
                ORDER BY heads.event_id LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [AtomicEvent.model_validate_json(str(row["payload_json"])) for row in rows]

    def get_atomic_event_for_mention(self, mention_id: str) -> AtomicEvent | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT versions.payload_json
                FROM atomic_event_heads heads
                JOIN atomic_event_versions versions
                  ON versions.event_id = heads.event_id
                 AND versions.version = heads.current_version
                JOIN atomic_event_mentions members
                  ON members.event_id = versions.event_id
                 AND members.event_version = versions.version
                LEFT JOIN atomic_event_redirects redirects
                  ON redirects.source_event_id = heads.event_id
                WHERE members.mention_id = ? AND redirects.source_event_id IS NULL
                ORDER BY heads.event_id LIMIT 1
                """,
                (mention_id,),
            ).fetchone()
        if row is None:
            return None
        return AtomicEvent.model_validate_json(str(row["payload_json"]))

    def get_current_package(self, package_id: str) -> EventPackage | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT versions.payload_json
                FROM event_package_heads heads
                JOIN event_package_versions versions
                  ON versions.package_id = heads.package_id
                 AND versions.version = heads.current_version
                WHERE heads.package_id = ?
                """,
                (package_id,),
            ).fetchone()
        if row is None:
            return None
        return EventPackage.model_validate_json(str(row["payload_json"]))

    def list_current_packages(self, *, limit: int = 1000) -> list[EventPackage]:
        if limit < 1 or limit > 10000:
            raise ValueError("package list limit must be between 1 and 10000")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT versions.payload_json
                FROM event_package_heads heads
                JOIN event_package_versions versions
                  ON versions.package_id = heads.package_id
                 AND versions.version = heads.current_version
                LEFT JOIN package_redirects redirects
                  ON redirects.source_package_id = heads.package_id
                WHERE redirects.source_package_id IS NULL
                ORDER BY heads.package_id LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [EventPackage.model_validate_json(str(row["payload_json"])) for row in rows]

    def list_packages_for_event(self, event_id: str) -> list[EventPackage]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT versions.payload_json
                FROM package_memberships memberships
                JOIN event_package_heads heads ON heads.package_id = memberships.package_id
                JOIN event_package_versions versions
                  ON versions.package_id = heads.package_id
                 AND versions.version = heads.current_version
                LEFT JOIN package_redirects redirects
                  ON redirects.source_package_id = heads.package_id
                WHERE memberships.event_id = ? AND redirects.source_package_id IS NULL
                ORDER BY heads.package_id
                """,
                (event_id,),
            ).fetchall()
        return [EventPackage.model_validate_json(str(row["payload_json"])) for row in rows]

    def recall_atomic_event_ids(
        self,
        *,
        entity_ids: Sequence[str],
        event_family: str,
        normalized_predicate: str,
        schema_type: str,
        reference_period_id: str | None,
        event_start: str | None,
        event_end: str | None,
        source_fingerprint: str | None,
        per_route_limit: int = 20,
    ) -> dict[str, set[str]]:
        """Return a bounded union of indexed M0 recall routes."""

        if per_route_limit < 1 or per_route_limit > 100:
            raise ValueError("per_route_limit must be between 1 and 100")
        found: dict[str, set[str]] = {}

        def add(rows: Sequence[sqlite3.Row], route: str) -> None:
            for row in rows:
                found.setdefault(str(row["event_id"]), set()).add(route)

        with self._connection() as connection:
            redirected = (
                "NOT EXISTS (SELECT 1 FROM atomic_event_redirects r "
                "WHERE r.source_event_id = a.event_id)"
            )
            add(
                connection.execute(
                    f"""
                    SELECT a.event_id FROM atomic_event_recall a
                    WHERE a.event_family = ? AND {redirected}
                    ORDER BY a.updated_at DESC LIMIT ?
                    """,
                    (event_family, per_route_limit),
                ).fetchall(),
                "EVENT_FAMILY",
            )
            add(
                connection.execute(
                    f"""
                    SELECT a.event_id FROM atomic_event_recall a
                    WHERE a.normalized_predicate = ? AND a.schema_type = ? AND {redirected}
                    ORDER BY a.updated_at DESC LIMIT ?
                    """,
                    (normalized_predicate, schema_type, per_route_limit),
                ).fetchall(),
                "SCHEMA_IDENTITY",
            )
            for entity_id in sorted(set(entity_ids))[:8]:
                add(
                    connection.execute(
                        f"""
                        SELECT entities.event_id
                        FROM atomic_event_recall_entities entities
                        JOIN atomic_event_recall a ON a.event_id = entities.event_id
                        WHERE entities.entity_id = ? AND {redirected}
                        ORDER BY a.updated_at DESC LIMIT ?
                        """,
                        (entity_id, per_route_limit),
                    ).fetchall(),
                    "CORE_ENTITY",
                )
            if reference_period_id is not None:
                add(
                    connection.execute(
                        f"""
                        SELECT a.event_id FROM atomic_event_recall a
                        WHERE a.reference_period_id = ? AND {redirected}
                        ORDER BY a.updated_at DESC LIMIT ?
                        """,
                        (reference_period_id, per_route_limit),
                    ).fetchall(),
                    "TIME_WINDOW",
                )
            if event_start is not None:
                upper = event_end or event_start
                add(
                    connection.execute(
                        f"""
                        SELECT a.event_id FROM atomic_event_recall a
                        WHERE a.event_start IS NOT NULL
                          AND a.event_start <= ?
                          AND COALESCE(a.event_end, a.event_start) >= ?
                          AND {redirected}
                        ORDER BY a.updated_at DESC LIMIT ?
                        """,
                        (upper, event_start, per_route_limit),
                    ).fetchall(),
                    "TIME_WINDOW",
                )
            if source_fingerprint is not None:
                add(
                    connection.execute(
                        f"""
                        SELECT sources.event_id
                        FROM atomic_event_recall_sources sources
                        JOIN atomic_event_recall a ON a.event_id = sources.event_id
                        WHERE sources.source_fingerprint = ? AND {redirected}
                        ORDER BY a.updated_at DESC LIMIT ?
                        """,
                        (source_fingerprint, per_route_limit),
                    ).fetchall(),
                    "SOURCE_FINGERPRINT",
                )
        return found

    def recall_package_ids(
        self,
        *,
        package_kind: str,
        package_family: str,
        anchor_entities: Sequence[str],
        local_anchor_hint: str | None = None,
        anchor_artifact_id: str | None,
        anchor_period_id: str | None,
        time_start: str | None,
        time_end: str | None,
        per_route_limit: int = 20,
    ) -> dict[str, set[str]]:
        if per_route_limit < 1 or per_route_limit > 100:
            raise ValueError("per_route_limit must be between 1 and 100")
        found: dict[str, set[str]] = {}

        def add(rows: Sequence[sqlite3.Row], route: str) -> None:
            for row in rows:
                found.setdefault(str(row["package_id"]), set()).add(route)

        with self._connection() as connection:
            active = (
                "NOT EXISTS (SELECT 1 FROM package_redirects r "
                "WHERE r.source_package_id = p.package_id)"
            )
            add(
                connection.execute(
                    f"""
                    SELECT p.package_id FROM package_recall p
                    WHERE p.package_kind = ? AND p.package_family = ? AND {active}
                    ORDER BY p.updated_at DESC LIMIT ?
                    """,
                    (package_kind, package_family, per_route_limit),
                ).fetchall(),
                "PACKAGE_ANCHOR",
            )
            for entity_id in sorted(set(anchor_entities))[:8]:
                add(
                    connection.execute(
                        f"""
                        SELECT entities.package_id
                        FROM package_recall_entities entities
                        JOIN package_recall p ON p.package_id = entities.package_id
                        WHERE entities.entity_id = ? AND {active}
                        ORDER BY p.updated_at DESC LIMIT ?
                        """,
                        (entity_id, per_route_limit),
                    ).fetchall(),
                    "CORE_ENTITY",
                )
            if anchor_period_id is not None:
                add(
                    connection.execute(
                        f"""
                        SELECT p.package_id FROM package_recall p
                        WHERE p.anchor_period_id = ? AND {active}
                        ORDER BY p.updated_at DESC LIMIT ?
                        """,
                        (anchor_period_id, per_route_limit),
                    ).fetchall(),
                    "TIME_WINDOW",
                )
            if anchor_artifact_id is not None:
                add(
                    connection.execute(
                        f"""
                        SELECT p.package_id FROM package_recall p
                        WHERE p.anchor_artifact_id = ? AND {active}
                        ORDER BY p.updated_at DESC LIMIT ?
                        """,
                        (anchor_artifact_id, per_route_limit),
                    ).fetchall(),
                    "LOCAL_PACKAGE_HINT",
                )
            if local_anchor_hint is not None:
                add(
                    connection.execute(
                        f"""
                        SELECT p.package_id FROM package_recall p
                        WHERE p.local_anchor_hint = ? AND {active}
                        ORDER BY p.updated_at DESC LIMIT ?
                        """,
                        (local_anchor_hint, per_route_limit),
                    ).fetchall(),
                    "LOCAL_PACKAGE_HINT",
                )
            if time_start is not None:
                upper = time_end or time_start
                add(
                    connection.execute(
                        f"""
                        SELECT p.package_id FROM package_recall p
                        WHERE p.time_start IS NOT NULL
                          AND p.time_start <= ?
                          AND COALESCE(p.time_end, p.time_start) >= ?
                          AND {active}
                        ORDER BY p.updated_at DESC LIMIT ?
                        """,
                        (upper, time_start, per_route_limit),
                    ).fetchall(),
                    "TIME_WINDOW",
                )
        return found

    def list_sources(
        self,
        *,
        market: str | None = None,
        ticker: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 100,
    ) -> list[SourceMessage]:
        if limit < 1 or limit > 1000:
            raise ValueError("source list limit must be between 1 and 1000")
        clauses: list[str] = []
        parameters: list[object] = []
        if ticker is not None:
            clauses.append("primary_ticker = ?")
            parameters.append(ticker.upper())
        if start_at is not None:
            clauses.append("published_at >= ?")
            parameters.append(start_at.isoformat())
        if end_at is not None:
            clauses.append("published_at < ?")
            parameters.append(end_at.isoformat())
        # Market is currently encoded by the DoxAtlas message namespace, not the v1 table.
        if market is not None and market.upper() != "US":
            return []
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT payload_json FROM source_messages {where}
                ORDER BY published_at, message_id LIMIT ?
                """,
                (*parameters, limit),
            ).fetchall()
        return [SourceMessage.model_validate_json(str(row["payload_json"])) for row in rows]

    def list_preprocessed_documents(
        self, *, exclude_message_id: str | None = None
    ) -> list[PreprocessedDocument]:
        sql = "SELECT payload_json FROM preprocessed_documents"
        parameters: tuple[object, ...] = ()
        if exclude_message_id is not None:
            sql += " WHERE message_id <> ?"
            parameters = (exclude_message_id,)
        sql += " ORDER BY created_at"
        with self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [PreprocessedDocument.model_validate_json(str(row["payload_json"])) for row in rows]

    def _save_immutable(
        self,
        *,
        table: str,
        id_column: str,
        record_id: str,
        payload: str,
        insert_sql: str,
        insert_values: tuple[object, ...],
    ) -> bool:
        with self._connection() as connection:
            existing = connection.execute(
                f"SELECT payload_json FROM {table} WHERE {id_column} = ?", (record_id,)
            ).fetchone()
            if existing is not None:
                if str(existing["payload_json"]) == payload:
                    return False
                raise ImmutableRecordConflict(f"{table} record {record_id!r} is immutable")
            connection.execute(insert_sql, insert_values)
            connection.commit()
            return True

    def save_source(self, source: SourceMessage, *, fingerprint: str) -> bool:
        payload = _json_payload(source)
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT fingerprint, payload_json FROM source_messages WHERE message_id = ?",
                (source.message_id,),
            ).fetchone()
            if existing is not None:
                if existing["fingerprint"] == fingerprint and existing["payload_json"] == payload:
                    return False
                raise ImmutableRecordConflict(
                    f"source_messages record {source.message_id!r} is immutable"
                )
            connection.execute(
                """
                INSERT INTO source_messages(
                    message_id, fingerprint, published_at, source_type, primary_ticker,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source.message_id,
                    fingerprint,
                    source.published_at.isoformat(),
                    source.source_type.value,
                    source.ticker_hints[0],
                    payload,
                    _now(),
                ),
            )
            connection.commit()
            return True

    def save_mention(self, mention: EventMention) -> bool:
        payload = _json_payload(mention)
        return self._save_immutable(
            table="event_mentions",
            id_column="mention_id",
            record_id=mention.mention_id,
            payload=payload,
            insert_sql="""
                INSERT INTO event_mentions(
                    mention_id, message_id, event_family, normalized_predicate,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            insert_values=(
                mention.mention_id,
                mention.message_id,
                mention.event_family.value,
                mention.predicate.normalized,
                payload,
                _now(),
            ),
        )

    def _save_versioned(
        self,
        *,
        object_name: str,
        object_id: str,
        version: int,
        payload: str,
        head_table: str,
        version_table: str,
        version_insert_sql: str,
        version_insert_values: tuple[object, ...],
        mention_ids: Sequence[str] = (),
    ) -> bool:
        id_column = "event_id" if object_name == "atomic event" else "package_id"
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_version = connection.execute(
                f"SELECT payload_json FROM {version_table} WHERE {id_column} = ? AND version = ?",
                (object_id, version),
            ).fetchone()
            if existing_version is not None:
                if existing_version["payload_json"] == payload:
                    connection.rollback()
                    return False
                connection.rollback()
                raise ImmutableRecordConflict(
                    f"{object_name} {object_id!r} version {version} is immutable"
                )
            head = connection.execute(
                f"SELECT current_version FROM {head_table} WHERE {id_column} = ?", (object_id,)
            ).fetchone()
            if head is None:
                if version != 1:
                    connection.rollback()
                    raise VersionConflict(f"new {object_name} must start at version 1")
                connection.execute(
                    f"INSERT INTO {head_table}({id_column}, current_version) VALUES (?, 1)",
                    (object_id,),
                )
            else:
                current = int(head["current_version"])
                if version != current + 1:
                    connection.rollback()
                    raise VersionConflict(
                        f"{object_name} {object_id!r} must advance from {current} to {current + 1}"
                    )
            connection.execute(version_insert_sql, version_insert_values)
            if mention_ids:
                connection.executemany(
                    """
                    INSERT INTO atomic_event_mentions(event_id, event_version, mention_id)
                    VALUES (?, ?, ?)
                    """,
                    [(object_id, version, mention_id) for mention_id in mention_ids],
                )
            if head is not None:
                connection.execute(
                    f"UPDATE {head_table} SET current_version = ? WHERE {id_column} = ?",
                    (version, object_id),
                )
            connection.commit()
            return True

    def save_atomic_event(self, event: AtomicEvent) -> bool:
        payload = _json_payload(event)
        saved = self._save_versioned(
            object_name="atomic event",
            object_id=event.event_id,
            version=event.version,
            payload=payload,
            head_table="atomic_event_heads",
            version_table="atomic_event_versions",
            version_insert_sql="""
                INSERT INTO atomic_event_versions(
                    event_id, version, event_family, assertion_state, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            version_insert_values=(
                event.event_id,
                event.version,
                event.event_family.value,
                event.assertion_state.value,
                payload,
                _now(),
            ),
            mention_ids=event.mention_ids,
        )
        self._refresh_atomic_recall(event)
        return saved

    def save_package(self, package: EventPackage) -> bool:
        payload = _json_payload(package)
        saved = self._save_versioned(
            object_name="event package",
            object_id=package.package_id,
            version=package.version,
            payload=payload,
            head_table="event_package_heads",
            version_table="event_package_versions",
            version_insert_sql="""
                INSERT INTO event_package_versions(
                    package_id, version, package_kind, package_family, status,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            version_insert_values=(
                package.package_id,
                package.version,
                package.package_kind.value,
                package.package_family.value,
                package.status.value,
                payload,
                _now(),
            ),
        )
        self._refresh_package_recall(package)
        return saved

    def _refresh_atomic_recall(self, event: AtomicEvent) -> None:
        fields = event.identity_profile.fields.model_dump(mode="json")
        schema_type = event.identity_profile.schema_type
        reference_period = fields.get("period_id") or fields.get("reference_period_id")
        entity_keys = (
            "issuer_id",
            "institution_id",
            "company_id",
        )
        entity_ids = {str(fields[key]) for key in entity_keys if isinstance(fields.get(key), str)}
        principal_ids = fields.get("principal_participant_ids", [])
        if isinstance(principal_ids, list):
            entity_ids.update(str(value) for value in principal_ids if isinstance(value, str))
        normalized_predicate = ""
        source_fingerprints: set[str] = set()
        with self._connection() as connection:
            placeholders = ",".join("?" for _ in event.mention_ids)
            rows = connection.execute(
                f"""
                SELECT mentions.payload_json, sources.fingerprint
                FROM event_mentions mentions
                JOIN source_messages sources ON sources.message_id = mentions.message_id
                WHERE mentions.mention_id IN ({placeholders})
                ORDER BY mentions.mention_id
                """,
                tuple(event.mention_ids),
            ).fetchall()
            for row in rows:
                mention = EventMention.model_validate_json(str(row["payload_json"]))
                if not normalized_predicate:
                    normalized_predicate = mention.predicate.normalized
                source_fingerprints.add(str(row["fingerprint"]))
                entity_ids.update(
                    participant.entity_id
                    for participant in mention.participants
                    if participant.entity_id is not None
                )
            start = event.time.event_start.isoformat() if event.time.event_start else None
            end = event.time.event_end.isoformat() if event.time.event_end else start
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO atomic_event_recall(
                    event_id, current_version, event_family, normalized_predicate,
                    schema_type, assertion_state, reference_period_id, event_start,
                    event_end, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    current_version=excluded.current_version,
                    event_family=excluded.event_family,
                    normalized_predicate=excluded.normalized_predicate,
                    schema_type=excluded.schema_type,
                    assertion_state=excluded.assertion_state,
                    reference_period_id=excluded.reference_period_id,
                    event_start=excluded.event_start,
                    event_end=excluded.event_end,
                    updated_at=excluded.updated_at
                """,
                (
                    event.event_id,
                    event.version,
                    event.event_family.value,
                    normalized_predicate,
                    schema_type,
                    event.assertion_state.value,
                    reference_period,
                    start,
                    end,
                    _now(),
                ),
            )
            connection.execute(
                "DELETE FROM atomic_event_recall_entities WHERE event_id = ?", (event.event_id,)
            )
            connection.executemany(
                "INSERT INTO atomic_event_recall_entities(event_id, entity_id) VALUES (?, ?)",
                [(event.event_id, entity_id) for entity_id in sorted(entity_ids)],
            )
            connection.execute(
                "DELETE FROM atomic_event_recall_sources WHERE event_id = ?", (event.event_id,)
            )
            connection.executemany(
                """
                INSERT INTO atomic_event_recall_sources(event_id, source_fingerprint)
                VALUES (?, ?)
                """,
                [(event.event_id, value) for value in sorted(source_fingerprints)],
            )
            connection.commit()

    def _refresh_package_recall(self, package: EventPackage) -> None:
        start = package.time_range.start.isoformat() if package.time_range.start else None
        end = package.time_range.end.isoformat() if package.time_range.end else start
        with self._connection() as connection:
            local_anchor_hint: str | None = None
            for event_id in package.member_event_ids:
                rows = connection.execute(
                    """
                    SELECT mentions.payload_json
                    FROM atomic_event_heads heads
                    JOIN atomic_event_versions versions
                      ON versions.event_id = heads.event_id
                     AND versions.version = heads.current_version
                    JOIN atomic_event_mentions members
                      ON members.event_id = versions.event_id
                     AND members.event_version = versions.version
                    JOIN event_mentions mentions ON mentions.mention_id = members.mention_id
                    WHERE heads.event_id = ? ORDER BY mentions.mention_id
                    """,
                    (event_id,),
                ).fetchall()
                for row in rows:
                    mention = EventMention.model_validate_json(str(row["payload_json"]))
                    if mention.local_package_hint is not None:
                        local_anchor_hint = mention.local_package_hint.anchor
                        break
                if local_anchor_hint is not None:
                    break
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO package_recall(
                    package_id, current_version, package_kind, package_family,
                    local_anchor_hint, anchor_artifact_id, anchor_period_id,
                    time_start, time_end, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(package_id) DO UPDATE SET
                    current_version=excluded.current_version,
                    package_kind=excluded.package_kind,
                    package_family=excluded.package_family,
                    local_anchor_hint=excluded.local_anchor_hint,
                    anchor_artifact_id=excluded.anchor_artifact_id,
                    anchor_period_id=excluded.anchor_period_id,
                    time_start=excluded.time_start,
                    time_end=excluded.time_end,
                    updated_at=excluded.updated_at
                """,
                (
                    package.package_id,
                    package.version,
                    package.package_kind.value,
                    package.package_family.value,
                    local_anchor_hint,
                    package.anchor_artifact_id,
                    package.anchor_period_id,
                    start,
                    end,
                    _now(),
                ),
            )
            connection.execute(
                "DELETE FROM package_recall_entities WHERE package_id = ?", (package.package_id,)
            )
            connection.executemany(
                "INSERT INTO package_recall_entities(package_id, entity_id) VALUES (?, ?)",
                [(package.package_id, value) for value in sorted(set(package.anchor_entities))],
            )
            connection.commit()

    def save_membership(self, membership: PackageMembership) -> bool:
        payload = _json_payload(membership)
        return self._save_immutable(
            table="package_memberships",
            id_column="membership_id",
            record_id=membership.membership_id,
            payload=payload,
            insert_sql="""
                INSERT INTO package_memberships(
                    membership_id, event_id, package_id, relation, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            insert_values=(
                membership.membership_id,
                membership.event_id,
                membership.package_id,
                membership.relation.value,
                payload,
                _now(),
            ),
        )

    def save_external_relation(self, relation: ExternalEventRelation) -> bool:
        payload = _json_payload(relation)
        return self._save_immutable(
            table="external_relations",
            id_column="relation_id",
            record_id=relation.relation_id,
            payload=payload,
            insert_sql="""
                INSERT INTO external_relations(
                    relation_id, source_event_id, target_event_id, relation,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            insert_values=(
                relation.relation_id,
                relation.source_event_id,
                relation.target_event_id,
                relation.relation.value,
                payload,
                _now(),
            ),
        )

    def list_external_relations(self) -> list[ExternalEventRelation]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM external_relations ORDER BY created_at, relation_id"
            ).fetchall()
        return [
            ExternalEventRelation.model_validate_json(str(row["payload_json"])) for row in rows
        ]

    def save_embedding(
        self,
        *,
        owner_kind: str,
        owner_id: str,
        model: str,
        input_hash: str,
        vector: Sequence[float],
        embedding_id: str | None = None,
    ) -> bool:
        values = array("f", vector)
        if sys.byteorder != "little":
            values.byteswap()
        record_id = embedding_id or str(uuid.uuid4())
        blob = values.tobytes()
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT dimension, vector_f32 FROM embeddings
                WHERE owner_kind = ? AND owner_id = ? AND model = ? AND input_hash = ?
                """,
                (owner_kind, owner_id, model, input_hash),
            ).fetchone()
            if existing is not None:
                same_dimension = int(existing["dimension"]) == len(values)
                if same_dimension:
                    # model + input_hash define logical identity; providers may return tiny
                    # float-level differences for the same request, so retain the first value.
                    return False
                raise ImmutableRecordConflict(
                    "embedding identity already exists with a different dimension"
                )
            connection.execute(
                """
                INSERT INTO embeddings(
                    embedding_id, owner_kind, owner_id, model, dimension,
                    input_hash, vector_f32, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    owner_kind,
                    owner_id,
                    model,
                    len(values),
                    input_hash,
                    blob,
                    _now(),
                ),
            )
            connection.commit()
            return True

    def get_embedding(
        self, *, owner_kind: str, owner_id: str, model: str, input_hash: str
    ) -> StoredEmbedding | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM embeddings
                WHERE owner_kind = ? AND owner_id = ? AND model = ? AND input_hash = ?
                """,
                (owner_kind, owner_id, model, input_hash),
            ).fetchone()
        if row is None:
            return None
        vector = array("f")
        vector.frombytes(bytes(row["vector_f32"]))
        if sys.byteorder != "little":
            vector.byteswap()
        return StoredEmbedding(
            embedding_id=row["embedding_id"],
            owner_kind=row["owner_kind"],
            owner_id=row["owner_id"],
            model=row["model"],
            dimension=row["dimension"],
            input_hash=row["input_hash"],
            vector=list(vector),
        )

    def get_latest_embedding(
        self, *, owner_kind: str, owner_id: str, model: str
    ) -> StoredEmbedding | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM embeddings
                WHERE owner_kind = ? AND owner_id = ? AND model = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (owner_kind, owner_id, model),
            ).fetchone()
        if row is None:
            return None
        vector = array("f")
        vector.frombytes(bytes(row["vector_f32"]))
        if sys.byteorder != "little":
            vector.byteswap()
        return StoredEmbedding(
            embedding_id=row["embedding_id"],
            owner_kind=row["owner_kind"],
            owner_id=row["owner_id"],
            model=row["model"],
            dimension=row["dimension"],
            input_hash=row["input_hash"],
            vector=list(vector),
        )

    def list_latest_embeddings(
        self, *, owner_kind: str, model: str, limit: int = 10000
    ) -> list[StoredEmbedding]:
        if limit < 1 or limit > 100000:
            raise ValueError("embedding list limit must be between 1 and 100000")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT embeddings.* FROM embeddings
                JOIN (
                    SELECT owner_id, MAX(created_at) AS latest
                    FROM embeddings WHERE owner_kind = ? AND model = ?
                    GROUP BY owner_id
                ) selected
                  ON selected.owner_id = embeddings.owner_id
                 AND selected.latest = embeddings.created_at
                WHERE embeddings.owner_kind = ? AND embeddings.model = ?
                ORDER BY embeddings.owner_id LIMIT ?
                """,
                (owner_kind, model, owner_kind, model, limit),
            ).fetchall()
        records: list[StoredEmbedding] = []
        for row in rows:
            vector = array("f")
            vector.frombytes(bytes(row["vector_f32"]))
            if sys.byteorder != "little":
                vector.byteswap()
            records.append(
                StoredEmbedding(
                    embedding_id=row["embedding_id"],
                    owner_kind=row["owner_kind"],
                    owner_id=row["owner_id"],
                    model=row["model"],
                    dimension=row["dimension"],
                    input_hash=row["input_hash"],
                    vector=list(vector),
                )
            )
        return records

    def list_memberships_for_package(self, package_id: str) -> list[PackageMembership]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM package_memberships
                WHERE package_id = ? ORDER BY created_at, membership_id
                """,
                (package_id,),
            ).fetchall()
        return [PackageMembership.model_validate_json(str(row["payload_json"])) for row in rows]

    def create_run(
        self, *, run_id: str, run_type: str, config: dict[str, Any], status: str = "RUNNING"
    ) -> bool:
        payload = _json_payload(config)
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if existing is not None:
                if (
                    existing["run_type"] == run_type
                    and existing["status"] == status
                    and existing["config_json"] == payload
                ):
                    return False
                raise ImmutableRecordConflict(f"run {run_id!r} already exists")
            connection.execute(
                """
                INSERT INTO runs(run_id, run_type, status, config_json, started_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, run_type, status, payload, _now()),
            )
            connection.commit()
            return True

    def start_cross_document_run(
        self,
        *,
        run_id: str,
        processing_key: str,
        message_id: str,
        engine_version: str,
        prompt_version: str,
        model_config: dict[str, Any],
    ) -> bool:
        config = {
            "processing_key": processing_key,
            "message_id": message_id,
            "engine_version": engine_version,
            "prompt_version": prompt_version,
            "model_config": model_config,
        }
        config_json = _json_payload(config)
        model_config_json = _json_payload(model_config)
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            completed = connection.execute(
                """
                SELECT run_id FROM cross_document_runs
                WHERE processing_key = ? AND status = 'SUCCEEDED'
                """,
                (processing_key,),
            ).fetchone()
            if completed is not None:
                connection.rollback()
                return False
            existing = connection.execute(
                "SELECT processing_key, message_id FROM cross_document_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["processing_key"] == processing_key
                    and existing["message_id"] == message_id
                ):
                    connection.rollback()
                    return False
                connection.rollback()
                raise ImmutableRecordConflict(f"cross-document run {run_id!r} already exists")
            connection.execute(
                """
                INSERT INTO runs(run_id, run_type, status, config_json, started_at)
                VALUES (?, 'CROSS_DOCUMENT', 'RUNNING', ?, ?)
                """,
                (run_id, config_json, now),
            )
            connection.execute(
                """
                INSERT INTO cross_document_runs(
                    run_id, processing_key, message_id, engine_version, prompt_version,
                    model_config_json, status, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'RUNNING', ?)
                """,
                (
                    run_id,
                    processing_key,
                    message_id,
                    engine_version,
                    prompt_version,
                    model_config_json,
                    now,
                ),
            )
            connection.commit()
            return True

    def get_completed_cross_document_result(
        self, processing_key: str
    ) -> CrossDocumentResult | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT result_json FROM cross_document_runs
                WHERE processing_key = ? AND status = 'SUCCEEDED'
                """,
                (processing_key,),
            ).fetchone()
        if row is None or row["result_json"] is None:
            return None
        return CrossDocumentResult.model_validate_json(str(row["result_json"]))

    def complete_cross_document_run(self, result: CrossDocumentResult) -> bool:
        if result.status is not CrossDocumentStatus.SUCCEEDED:
            raise ValueError("only successful cross-document results can complete a run")
        payload = _json_payload(result)
        finished = (result.finished_at or datetime.now(UTC)).isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, result_json FROM cross_document_runs WHERE run_id = ?",
                (result.run_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise RegistryError(f"unknown cross-document run {result.run_id!r}")
            if row["status"] == "SUCCEEDED":
                if row["result_json"] == payload:
                    connection.rollback()
                    return False
                connection.rollback()
                raise ImmutableRecordConflict("completed cross-document result is immutable")
            connection.execute(
                """
                UPDATE cross_document_runs
                SET status = 'SUCCEEDED', result_json = ?, finished_at = ?
                WHERE run_id = ? AND status = 'RUNNING'
                """,
                (payload, finished, result.run_id),
            )
            connection.execute(
                "UPDATE runs SET status = 'SUCCEEDED', finished_at = ? WHERE run_id = ?",
                (finished, result.run_id),
            )
            connection.commit()
            return True

    def fail_cross_document_run(self, run_id: str, *, error_code: str) -> None:
        finished = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM cross_document_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise RegistryError(f"unknown cross-document run {run_id!r}")
            if row["status"] != "RUNNING":
                connection.rollback()
                return
            connection.execute(
                """
                UPDATE cross_document_runs SET status = 'FAILED', error_code = ?, finished_at = ?
                WHERE run_id = ?
                """,
                (error_code, finished, run_id),
            )
            connection.execute(
                "UPDATE runs SET status = 'FAILED', finished_at = ? WHERE run_id = ?",
                (finished, run_id),
            )
            connection.commit()

    def save_atomic_assignment(self, record: AtomicAssignmentRecord) -> bool:
        return self._save_immutable(
            table="atomic_assignment_decisions",
            id_column="assignment_id",
            record_id=record.assignment_id,
            payload=_json_payload(record),
            insert_sql="""
                INSERT INTO atomic_assignment_decisions(
                    assignment_id, run_id, mention_id, candidate_event_id,
                    resulting_event_id, action, relation, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            insert_values=(
                record.assignment_id,
                record.run_id,
                record.mention_id,
                record.candidate_event_id,
                record.resulting_event_id,
                record.action.value,
                record.relation.value if record.relation else None,
                _json_payload(record),
                _now(),
            ),
        )

    def save_package_assignment(self, record: PackageAssignmentRecord) -> bool:
        return self._save_immutable(
            table="package_assignment_decisions",
            id_column="assignment_id",
            record_id=record.assignment_id,
            payload=_json_payload(record),
            insert_sql="""
                INSERT INTO package_assignment_decisions(
                    assignment_id, run_id, event_id, candidate_package_id,
                    resulting_package_id, action, relation, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            insert_values=(
                record.assignment_id,
                record.run_id,
                record.event_id,
                record.candidate_package_id,
                record.resulting_package_id,
                record.action.value,
                record.relation.value if record.relation else None,
                _json_payload(record),
                _now(),
            ),
        )

    def save_package_merge_decision(
        self, *, decision_id: str, run_id: str, decision: PackagePairMergeDecision
    ) -> bool:
        payload = _json_payload(decision)
        return self._save_immutable(
            table="package_merge_decisions",
            id_column="decision_id",
            record_id=decision_id,
            payload=payload,
            insert_sql="""
                INSERT INTO package_merge_decisions(
                    decision_id, run_id, source_package_id, target_package_id,
                    relation, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            insert_values=(
                decision_id,
                run_id,
                decision.source_package_id,
                decision.target_package_id,
                decision.relation.value,
                payload,
                _now(),
            ),
        )

    def save_hold(self, hold: HoldRecord) -> bool:
        return self._save_immutable(
            table="hold_queue",
            id_column="hold_id",
            record_id=hold.hold_id,
            payload=_json_payload(hold),
            insert_sql="""
                INSERT INTO hold_queue(
                    hold_id, run_id, kind, subject_id, status, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            insert_values=(
                hold.hold_id,
                hold.run_id,
                hold.kind.value,
                hold.subject_id,
                hold.status.value,
                _json_payload(hold),
                hold.created_at.isoformat(),
            ),
        )

    def list_open_holds(self, *, limit: int = 100) -> list[HoldRecord]:
        if limit < 1 or limit > 1000:
            raise ValueError("hold limit must be between 1 and 1000")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM hold_queue
                WHERE status = 'OPEN' ORDER BY created_at LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [HoldRecord.model_validate_json(str(row["payload_json"])) for row in rows]

    def save_package_external_relation(self, relation: PackageExternalRelation) -> bool:
        payload = _json_payload(relation)
        return self._save_immutable(
            table="package_external_relations",
            id_column="relation_id",
            record_id=relation.relation_id,
            payload=payload,
            insert_sql="""
                INSERT INTO package_external_relations(
                    relation_id, source_event_id, target_package_id, relation,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            insert_values=(
                relation.relation_id,
                relation.source_event_id,
                relation.target_package_id,
                relation.relation.value,
                payload,
                _now(),
            ),
        )

    def list_package_external_relations(
        self, *, source_event_id: str | None = None
    ) -> list[PackageExternalRelation]:
        sql = "SELECT payload_json FROM package_external_relations"
        parameters: tuple[object, ...] = ()
        if source_event_id is not None:
            sql += " WHERE source_event_id = ?"
            parameters = (source_event_id,)
        sql += " ORDER BY created_at, relation_id"
        with self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [
            PackageExternalRelation.model_validate_json(str(row["payload_json"])) for row in rows
        ]

    def save_atomic_redirect(
        self, *, source_event_id: str, target_event_id: str, run_id: str, reason: str
    ) -> bool:
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT target_event_id, run_id, reason FROM atomic_event_redirects
                WHERE source_event_id = ?
                """,
                (source_event_id,),
            ).fetchone()
            if existing is not None:
                if tuple(existing) == (target_event_id, run_id, reason):
                    return False
                raise ImmutableRecordConflict("atomic event redirect is immutable")
            connection.execute(
                """
                INSERT INTO atomic_event_redirects(
                    source_event_id, target_event_id, run_id, reason, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (source_event_id, target_event_id, run_id, reason, _now()),
            )
            connection.commit()
            return True

    def save_package_redirect(
        self, *, source_package_id: str, target_package_id: str, run_id: str, reason: str
    ) -> bool:
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT target_package_id, run_id, reason FROM package_redirects
                WHERE source_package_id = ?
                """,
                (source_package_id,),
            ).fetchone()
            if existing is not None:
                if tuple(existing) == (target_package_id, run_id, reason):
                    return False
                raise ImmutableRecordConflict("package redirect is immutable")
            connection.execute(
                """
                INSERT INTO package_redirects(
                    source_package_id, target_package_id, run_id, reason, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (source_package_id, target_package_id, run_id, reason, _now()),
            )
            connection.commit()
            return True

    def start_document_run(
        self,
        *,
        run_id: str,
        processing_key: str,
        message_id: str,
        pipeline_version: str,
        prompt_version: str,
        catalog_version: str,
        model_config: dict[str, Any],
    ) -> bool:
        model_config_json = _json_payload(model_config)
        run_config = _json_payload(
            {
                "processing_key": processing_key,
                "message_id": message_id,
                "pipeline_version": pipeline_version,
                "prompt_version": prompt_version,
                "catalog_version": catalog_version,
                "model_config": model_config,
            }
        )
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM document_processing_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if existing is not None:
                if (
                    existing["processing_key"] == processing_key
                    and existing["message_id"] == message_id
                ):
                    connection.rollback()
                    return False
                connection.rollback()
                raise ImmutableRecordConflict(f"document run {run_id!r} already exists")
            completed = connection.execute(
                """
                SELECT run_id FROM document_processing_runs
                WHERE processing_key = ? AND status = 'SUCCEEDED'
                """,
                (processing_key,),
            ).fetchone()
            if completed is not None:
                connection.rollback()
                return False
            connection.execute(
                """
                INSERT INTO runs(run_id, run_type, status, config_json, started_at)
                VALUES (?, 'SINGLE_DOCUMENT', 'RUNNING', ?, ?)
                """,
                (run_id, run_config, now),
            )
            connection.execute(
                """
                INSERT INTO document_processing_runs(
                    run_id, processing_key, message_id, pipeline_version, prompt_version,
                    catalog_version, model_config_json, status, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'RUNNING', ?)
                """,
                (
                    run_id,
                    processing_key,
                    message_id,
                    pipeline_version,
                    prompt_version,
                    catalog_version,
                    model_config_json,
                    now,
                ),
            )
            connection.commit()
            return True

    def get_completed_document_result(self, processing_key: str) -> SingleDocumentResult | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT result_json FROM document_processing_runs
                WHERE processing_key = ? AND status = 'SUCCEEDED'
                """,
                (processing_key,),
            ).fetchone()
        if row is None or row["result_json"] is None:
            return None
        return SingleDocumentResult.model_validate_json(str(row["result_json"]))

    def get_latest_completed_document_result_for_message(
        self, message_id: str
    ) -> SingleDocumentResult | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT result_json FROM document_processing_runs
                WHERE message_id = ? AND status = 'SUCCEEDED'
                ORDER BY finished_at DESC LIMIT 1
                """,
                (message_id,),
            ).fetchone()
        if row is None or row["result_json"] is None:
            return None
        return SingleDocumentResult.model_validate_json(str(row["result_json"]))

    def save_preprocessing_result(self, run_id: str, result: PreprocessingResult) -> bool:
        document = result.document
        payload = _json_payload(document)
        preprocessing_id = f"preprocessed:{run_id}"
        inserted = False
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload_json FROM preprocessed_documents WHERE preprocessing_id = ?",
                (preprocessing_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_json"]) != payload:
                    connection.rollback()
                    raise ImmutableRecordConflict("preprocessed document is immutable")
            else:
                connection.execute(
                    """
                    INSERT INTO preprocessed_documents(
                        preprocessing_id, run_id, message_id, source_fingerprint,
                        normalized_fingerprint, normalized_url, minhash_json,
                        payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        preprocessing_id,
                        run_id,
                        document.message_id,
                        document.source_fingerprint,
                        document.normalized_fingerprint,
                        document.normalized_url,
                        _json_payload(document.minhash64),
                        payload,
                        _now(),
                    ),
                )
                inserted = True
            for relation in result.duplicate_relations:
                relation_payload = _json_payload(relation)
                existing_relation = connection.execute(
                    "SELECT payload_json FROM duplicate_relations WHERE relation_id = ?",
                    (relation.relation_id,),
                ).fetchone()
                if existing_relation is not None:
                    if str(existing_relation["payload_json"]) != relation_payload:
                        connection.rollback()
                        raise ImmutableRecordConflict("duplicate relation is immutable")
                    continue
                connection.execute(
                    """
                    INSERT INTO duplicate_relations(
                        relation_id, run_id, source_message_id, target_message_id,
                        relation_type, score, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        relation.relation_id,
                        run_id,
                        relation.source_message_id,
                        relation.target_message_id,
                        relation.relation_type.value,
                        relation.score,
                        relation_payload,
                        _now(),
                    ),
                )
            connection.commit()
        return inserted

    def save_dream_candidates(self, run_id: str, candidates: Sequence[DreamCandidate]) -> int:
        inserted = 0
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for candidate in candidates:
                payload = _json_payload(candidate)
                existing = connection.execute(
                    "SELECT payload_json FROM dream_candidates WHERE candidate_id = ?",
                    (candidate.candidate_id,),
                ).fetchone()
                if existing is not None:
                    if str(existing["payload_json"]) != payload:
                        connection.rollback()
                        raise ImmutableRecordConflict("dream candidate is immutable")
                    continue
                connection.execute(
                    """
                    INSERT INTO dream_candidates(
                        candidate_id, run_id, payload_json, created_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (candidate.candidate_id, run_id, payload, _now()),
                )
                inserted += 1
            connection.commit()
        return inserted

    def get_latest_dream_candidates_for_processing_key(
        self, processing_key: str
    ) -> list[DreamCandidate]:
        with self._connection() as connection:
            run = connection.execute(
                """
                SELECT runs.run_id
                FROM document_processing_runs AS runs
                WHERE runs.processing_key = ?
                  AND EXISTS(
                      SELECT 1 FROM dream_candidates AS candidates
                      WHERE candidates.run_id = runs.run_id
                  )
                ORDER BY runs.started_at DESC
                LIMIT 1
                """,
                (processing_key,),
            ).fetchone()
            if run is None:
                return []
            rows = connection.execute(
                """
                SELECT payload_json FROM dream_candidates
                WHERE run_id = ? ORDER BY candidate_id
                """,
                (run["run_id"],),
            ).fetchall()
        return [DreamCandidate.model_validate_json(str(row["payload_json"])) for row in rows]

    def save_grounder_batch(
        self,
        *,
        run_id: str,
        processing_key: str,
        batch_key: str,
        output: GrounderOutput,
    ) -> bool:
        payload = _json_payload(output)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT processing_key, payload_json FROM grounder_batch_results "
                "WHERE batch_key = ?",
                (batch_key,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["processing_key"]) != processing_key
                    or str(existing["payload_json"]) != payload
                ):
                    connection.rollback()
                    raise ImmutableRecordConflict("grounder batch result is immutable")
                connection.commit()
                return False
            connection.execute(
                """
                INSERT INTO grounder_batch_results(
                    batch_key, processing_key, run_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (batch_key, processing_key, run_id, payload, _now()),
            )
            connection.commit()
        return True

    def get_grounder_batch(
        self, *, processing_key: str, batch_key: str
    ) -> GrounderOutput | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM grounder_batch_results "
                "WHERE processing_key = ? AND batch_key = ?",
                (processing_key, batch_key),
            ).fetchone()
        if row is None:
            return None
        return GrounderOutput.model_validate_json(str(row["payload_json"]))

    def save_judge_decisions(self, run_id: str, decisions: Sequence[JudgeDecisionRecord]) -> int:
        inserted = 0
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for decision in decisions:
                payload = _json_payload(decision)
                existing = connection.execute(
                    "SELECT payload_json FROM judge_decisions WHERE decision_id = ?",
                    (decision.decision_id,),
                ).fetchone()
                if existing is not None:
                    if str(existing["payload_json"]) != payload:
                        connection.rollback()
                        raise ImmutableRecordConflict("judge decision is immutable")
                    continue
                connection.execute(
                    """
                    INSERT INTO judge_decisions(
                        decision_id, run_id, target_draft_id, action, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        decision.decision_id,
                        run_id,
                        decision.target_draft_id,
                        decision.action.value,
                        payload,
                        _now(),
                    ),
                )
                inserted += 1
            connection.commit()
        return inserted

    def save_normalization_decisions(
        self, run_id: str, decisions: Sequence[NormalizationDecision]
    ) -> int:
        inserted = 0
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for decision in decisions:
                payload = _json_payload(decision)
                existing = connection.execute(
                    "SELECT payload_json FROM normalization_decisions WHERE decision_id = ?",
                    (decision.decision_id,),
                ).fetchone()
                if existing is not None:
                    if str(existing["payload_json"]) != payload:
                        connection.rollback()
                        raise ImmutableRecordConflict("normalization decision is immutable")
                    continue
                connection.execute(
                    """
                    INSERT INTO normalization_decisions(
                        decision_id, run_id, mention_id, field_path, kind, method,
                        payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        decision.decision_id,
                        run_id,
                        decision.mention_id,
                        decision.field_path,
                        decision.kind.value,
                        decision.method.value,
                        payload,
                        _now(),
                    ),
                )
                inserted += 1
            connection.commit()
        return inserted

    def complete_document_run(self, result: SingleDocumentResult) -> int:
        if result.status is not ProcessingStatus.SUCCEEDED:
            raise ValueError("only successful document results can complete a run")
        payload = _json_payload(result)
        inserted_mentions = 0
        finished_at = (result.finished_at or datetime.now(UTC)).isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, result_json FROM document_processing_runs WHERE run_id = ?",
                (result.run_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise RegistryError(f"unknown document run {result.run_id!r}")
            if row["status"] == "SUCCEEDED":
                if row["result_json"] == payload:
                    connection.rollback()
                    return 0
                connection.rollback()
                raise ImmutableRecordConflict("completed document result is immutable")
            for ordinal, mention in enumerate(result.mentions):
                mention_payload = _json_payload(mention)
                existing = connection.execute(
                    "SELECT payload_json FROM event_mentions WHERE mention_id = ?",
                    (mention.mention_id,),
                ).fetchone()
                if existing is not None:
                    if str(existing["payload_json"]) != mention_payload:
                        connection.rollback()
                        raise ImmutableRecordConflict("event mention is immutable")
                else:
                    connection.execute(
                        """
                        INSERT INTO event_mentions(
                            mention_id, message_id, event_family, normalized_predicate,
                            payload_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            mention.mention_id,
                            mention.message_id,
                            mention.event_family.value,
                            mention.predicate.normalized,
                            mention_payload,
                            _now(),
                        ),
                    )
                    inserted_mentions += 1
                connection.execute(
                    """
                    INSERT OR IGNORE INTO document_run_mentions(run_id, mention_id, ordinal)
                    VALUES (?, ?, ?)
                    """,
                    (result.run_id, mention.mention_id, ordinal),
                )
            connection.execute(
                """
                UPDATE document_processing_runs
                SET status = 'SUCCEEDED', result_json = ?, finished_at = ?
                WHERE run_id = ? AND status = 'RUNNING'
                """,
                (payload, finished_at, result.run_id),
            )
            connection.execute(
                "UPDATE runs SET status = 'SUCCEEDED', finished_at = ? WHERE run_id = ?",
                (finished_at, result.run_id),
            )
            connection.commit()
        return inserted_mentions

    def fail_document_run(self, run_id: str, *, error_code: str) -> None:
        finished_at = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM document_processing_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise RegistryError(f"unknown document run {run_id!r}")
            if row["status"] != "RUNNING":
                connection.rollback()
                return
            connection.execute(
                """
                UPDATE document_processing_runs
                SET status = 'FAILED', error_code = ?, finished_at = ? WHERE run_id = ?
                """,
                (error_code, finished_at, run_id),
            )
            connection.execute(
                "UPDATE runs SET status = 'FAILED', finished_at = ? WHERE run_id = ?",
                (finished_at, run_id),
            )
            connection.commit()

    def count_model_calls(self, *, run_id: str | None = None) -> int:
        with self._connection() as connection:
            if run_id is None:
                row = connection.execute("SELECT COUNT(*) FROM model_calls").fetchone()
            else:
                row = connection.execute(
                    "SELECT COUNT(*) FROM model_calls WHERE run_id = ?", (run_id,)
                ).fetchone()
        return int(row[0])

    def list_model_call_summaries(self, *, limit: int = 10_000) -> list[ModelCallSummary]:
        if limit < 1 or limit > 100_000:
            raise ValueError("model call limit must be between 1 and 100000")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT stage, tier, model, input_tokens, output_tokens, latency_ms,
                       status, error_code
                FROM model_calls ORDER BY created_at, model_call_id LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            ModelCallSummary(
                stage=str(row["stage"] or "unattributed"),
                tier=row["tier"],
                model=row["model"],
                input_tokens=row["input_tokens"],
                output_tokens=row["output_tokens"],
                latency_ms=row["latency_ms"],
                status=row["status"],
                error_code=row["error_code"],
                repaired=str(row["stage"] or "").endswith("_repair"),
            )
            for row in rows
        ]

    def record_model_call(
        self,
        *,
        model_call_id: str,
        run_id: str | None,
        tier: str,
        model: str,
        status: Literal["SUCCEEDED", "FAILED"],
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int,
        error_code: str | None,
        metadata: dict[str, Any],
        stage: str | None = None,
        prompt_version: str | None = None,
        schema_hash: str | None = None,
        input_hash: str | None = None,
    ) -> bool:
        payload = _json_payload(metadata)
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM model_calls WHERE model_call_id = ?", (model_call_id,)
            ).fetchone()
            comparable = (
                run_id,
                tier,
                model,
                status,
                input_tokens,
                output_tokens,
                latency_ms,
                error_code,
                payload,
                stage,
                prompt_version,
                schema_hash,
                input_hash,
            )
            if existing is not None:
                stored = tuple(
                    existing[name]
                    for name in (
                        "run_id",
                        "tier",
                        "model",
                        "status",
                        "input_tokens",
                        "output_tokens",
                        "latency_ms",
                        "error_code",
                        "metadata_json",
                        "stage",
                        "prompt_version",
                        "schema_hash",
                        "input_hash",
                    )
                )
                if stored == comparable:
                    return False
                raise ImmutableRecordConflict(f"model call {model_call_id!r} is immutable")
            connection.execute(
                """
                INSERT INTO model_calls(
                    model_call_id, run_id, tier, model, status, input_tokens,
                    output_tokens, latency_ms, error_code, metadata_json, stage,
                    prompt_version, schema_hash, input_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (model_call_id, *comparable, _now()),
            )
            connection.commit()
            return True

    def append_decision_audit(self, record: DecisionAuditRecord) -> bool:
        payload = _json_payload(record.payload)
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM decision_audits WHERE audit_id = ?", (record.audit_id,)
            ).fetchone()
            comparable = (
                record.run_id,
                record.decision_type,
                record.subject_id,
                payload,
            )
            if existing is not None:
                stored = tuple(
                    existing[name]
                    for name in ("run_id", "decision_type", "subject_id", "payload_json")
                )
                if stored == comparable:
                    return False
                raise ImmutableRecordConflict(f"decision audit {record.audit_id!r} is immutable")
            connection.execute(
                """
                INSERT INTO decision_audits(
                    audit_id, run_id, decision_type, subject_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (record.audit_id, *comparable, _now()),
            )
            connection.commit()
            return True
