"""Transactional native change capture, installed only by explicit migration.

Triggers write immutable source receipts and a small outbox reference in the same
business transaction. Readers never construct a domain repository to read facts.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

TABLES = {
    "runtime": (
        "v2_business_imports",
        "runtime_v2_cases",
        "runtime_v2_turns",
        "runtime_v2_effects",
        "runtime_v2_candidates",
        "runtime_v2_archives",
        "runtime_v2_trade_records",
        "runtime_v2_policy_activations",
        "runtime_v2_badcases",
        "runtime_v2_w3_cases",
        "runtime_v2_daily_close_runs",
        "runtime_tasks",
        "runtime_values",
        "v2_ticker_control",
        "v2_control_ack",
        "v2_analysis_admission",
        "te_executions",
        "te_jobs",
        "te_attempts",
        "te_fills",
        "te_events",
        "te_allocations",
        "te_lots",
        "te_lot_adjustments",
    ),
    "initialization": (
        "v2_model_invocations",
        "initialization_runs",
        "initialization_nodes",
        "initialization_events",
        "activation_revisions",
        "ticker_active_revision",
    ),
    "bus": (
        "source_definitions",
        "ticker_monitoring_states",
        "ticker_source_bindings",
        "standard_messages",
        "stream_items",
        "stream_members",
        "poll_states",
        "acquisition_failures",
        "audit_log",
    ),
    "research": ("codex_runtime_records",),
    "events": (
        "library_versions",
        "library_heads",
        "canonical_events",
        "canonical_facts",
        "canonical_event_revisions",
        "canonical_event_states",
        "canonical_fact_revisions",
        "canonical_fact_states",
        "event_fact_memberships",
        "reference_view_deltas",
    ),
    "usage": ("model_usage_events",),
}


def quoted(identifier: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
        raise ValueError("invalid source identifier")
    return '"' + identifier + '"'


class SourceOutbox:
    def __init__(self, path: str | Path, source: str) -> None:
        self.path, self.source = Path(path).resolve(), source
        from .native_content import NativeContent
        self.content = NativeContent(self.path)

    def connection(self, *, write: bool = False) -> sqlite3.Connection:
        db = sqlite3.connect(
            self.path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=2
        )
        db.row_factory = sqlite3.Row
        return db

    def migrate(self, tables: tuple[str, ...] | None = None, *, dry_run: bool = False) -> list[str]:
        db = self.connection(write=not dry_run)
        try:
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            selected = [t for t in (tables or TABLES[self.source]) if t in names]
            if dry_run:
                return selected
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS v2_capture_meta "
                "(source TEXT PRIMARY KEY,started_at TEXT NOT NULL,tables_json TEXT NOT NULL)"
            )
            if self.source == "initialization":
                from doxagent.ticker_initialization.usage_capture import migrate

                migrate(db)
                if "v2_model_invocations" not in selected:
                    selected.append("v2_model_invocations")
            if self.source == "bus":
                from doxagent.v2_control.bindings import migrate

                migrate(db)
            if self.source == "events":
                from .identities import migrate

                migrate(db)
            db.execute(
                "CREATE TABLE IF NOT EXISTS v2_source_receipts "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT,payload TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS v2_source_outbox "
                "(seq INTEGER PRIMARY KEY AUTOINCREMENT,table_name TEXT,entity_id TEXT,"
                "operation TEXT,receipt_id INTEGER,recorded_at TEXT)"
            )
            db.execute("CREATE TABLE IF NOT EXISTS v2_source_state (source TEXT PRIMARY KEY, epoch TEXT NOT NULL, head INTEGER NOT NULL, retained_floor INTEGER NOT NULL)")
            db.execute("INSERT OR IGNORE INTO v2_source_state VALUES(?,lower(hex(randomblob(16))),(SELECT coalesce(max(seq),0) FROM v2_source_outbox),0)", (self.source,))
            db.execute("CREATE TRIGGER IF NOT EXISTS v2_source_head AFTER INSERT ON v2_source_outbox BEGIN UPDATE v2_source_state SET head=max(head,NEW.seq); END")
            db.execute("CREATE TABLE IF NOT EXISTS v2_source_consumers (id TEXT PRIMARY KEY, checkpoint INTEGER NOT NULL, updated_at TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS v2_source_pins (id TEXT PRIMARY KEY, seq INTEGER NOT NULL, reason TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS v2_receipt_archive (seq INTEGER PRIMARY KEY, digest TEXT NOT NULL, size INTEGER NOT NULL)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS v2_backfill_checkpoint "
                "(table_name TEXT PRIMARY KEY,position INTEGER NOT NULL)"
            )
            from .artifact_registry import migrate as migrate_artifacts, native_triggers
            migrate_artifacts(db)
            for native_table in sorted(names):
                if native_table.startswith("sqlite_"):
                    continue
                info = list(db.execute("PRAGMA table_info(" + quoted(native_table) + ")"))
                payloads = [r[1] for r in info if r[1] in {"payload", "payload_json", "inputs", "receipt", "data_json"}]
                keys = sorted((r for r in info if r[5]), key=lambda row: row[5])
                if payloads:
                    identity = "json_array(" + ",".join("NEW." + quoted(r[1]) for r in keys) + ")" if keys else "CAST(NEW.rowid AS TEXT)"
                    native_triggers(db, native_table, payloads, identity)
            for table in selected:
                columns = list(db.execute(f"PRAGMA table_info({quoted(table)})"))
                primary = sorted((r for r in columns if r[5]), key=lambda r: r[5])
                for operation in ("INSERT", "UPDATE", "DELETE"):
                    db.execute(
                        f"DROP TRIGGER IF EXISTS {quoted('v2_capture_' + table + '_' + operation)}"
                    )
                    alias = "OLD" if operation == "DELETE" else "NEW"
                    values = ",".join(f"'{r[1]}',{alias}.{quoted(r[1])}" for r in columns)
                    key = "json_array(" + ",".join(f"{alias}.{quoted(r[1])}" for r in primary) + ")"
                    if not primary:
                        key = f"CAST({alias}.rowid AS TEXT)"
                    condition = (
                        f"WHEN {alias}.namespace IN ('trade_intents','trade_receipts',"
                        "'analysis_trade_decisions','candidates','worker_receipts','worker_requests',"
                        "'round_inputs','schedule','selection_snapshot','worker_invocations',"
                        "'reference_deliveries')"
                        if table == "runtime_values"
                        else ""
                    )
                    if operation == "UPDATE":
                        technical = {"lease_until", "updated_at"} if table == "runtime_tasks" else set()
                        changed = " OR ".join(
                            f"OLD.{quoted(r[1])} IS NOT NEW.{quoted(r[1])}" for r in columns if r[1] not in technical
                        )
                        condition += (" AND " if condition else "WHEN ") + f"({changed})"
                    db.execute(
                        "CREATE TRIGGER IF NOT EXISTS "
                        f"{quoted('v2_capture_' + table + '_' + operation)} "
                        f"AFTER {operation} ON {quoted(table)} {condition} BEGIN "
                        f"INSERT INTO v2_source_receipts(payload) VALUES(json_object({values})); "
                        "INSERT INTO v2_source_outbox("
                        "table_name,entity_id,operation,receipt_id,recorded_at) "
                        f"VALUES('{table}',{key},'{operation}',last_insert_rowid(),"
                        "strftime('%Y-%m-%dT%H:%M:%fZ','now')); END"
                    )
            db.execute(
                "INSERT OR IGNORE INTO v2_capture_meta "
                "VALUES(?,strftime('%Y-%m-%dT%H:%M:%fZ','now'),?)",
                (self.source, json.dumps(selected)),
            )
            db.commit()
            return selected
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def read(self, after: int, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("source batch limit must be 1..500")
        db = self.connection()
        try:
            rows = db.execute(
                "SELECT o.*,r.payload FROM v2_source_outbox o "
                "JOIN v2_source_receipts r ON r.id=o.receipt_id "
                "WHERE o.seq>? ORDER BY o.seq LIMIT ?",
                (after, limit),
            ).fetchall()
            archived = db.execute("SELECT seq FROM v2_receipt_archive WHERE seq>? ORDER BY seq LIMIT ?", (after, limit)).fetchall()
            if not archived:
                return [{**dict(r), "row": self.content.source_row(json.loads(r["payload"]))} for r in rows]
            hot = {r["seq"]: {**dict(r), "row": self.content.source_row(json.loads(r["payload"]))} for r in rows}
            identities = sorted(set(hot) | {r[0] for r in archived})[:limit]
            return [hot[seq] if seq in hot else self.event(seq) for seq in identities]
        finally:
            db.close()

    def head(self) -> int:
        db = self.connection()
        try:
            return db.execute("SELECT head FROM v2_source_state WHERE source=?", (self.source,)).fetchone()[0]
        finally:
            db.close()

    def capture(self):
        db = self.connection()
        try:
            row = db.execute(
                "SELECT * FROM v2_capture_meta WHERE source=?", (self.source,)
            ).fetchone()
            if not row:
                return None
            state = db.execute("SELECT epoch FROM v2_source_state WHERE source=?", (self.source,)).fetchone()
            return {**dict(row), "source_epoch": state[0], "schema_version": 2}
        finally:
            db.close()

    def event(self, sequence: int) -> dict[str, Any] | None:
        db = self.connection()
        try:
            row = db.execute(
                "SELECT o.*,r.payload FROM v2_source_outbox o JOIN "
                "v2_source_receipts r ON r.id=o.receipt_id WHERE o.seq=?",
                (sequence,),
            ).fetchone()
            if row:
                return {**dict(row), "row": self.content.source_row(json.loads(row["payload"]))}
            archived = db.execute("SELECT digest,size FROM v2_receipt_archive WHERE seq=?", (sequence,)).fetchone()
            if not archived:
                return None
            from .content_files import ContentFiles
            files = ContentFiles(self.path.parent / "receipt-files" / self.source)
            raw = b"".join(files.read(archived[0], offset, min(files.CHUNK, archived[1] - offset))
                           for offset in range(0, archived[1], files.CHUNK))
            value = json.loads(raw)
            return {**value, "row": self.content.source_row(json.loads(value["payload"]))}
        finally:
            db.close()

    def acknowledge(self, consumer: str, checkpoint: int, repair_pin: int | None = None) -> None:
        """Consumer registrations are durable; offline consumers continue to pin history."""
        with self.connection(write=True) as db:
            db.execute("BEGIN IMMEDIATE")
            if repair_pin is None:
                db.execute("DELETE FROM v2_source_pins WHERE id=?", (consumer + ":repair",))
            else:
                db.execute("INSERT INTO v2_source_pins VALUES(?,?,'repair') ON CONFLICT(id) DO UPDATE SET seq=excluded.seq WHERE seq IS NOT excluded.seq", (consumer + ":repair", repair_pin))
            db.execute("INSERT INTO v2_source_consumers VALUES(?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now')) "
                       "ON CONFLICT(id) DO UPDATE SET checkpoint=max(checkpoint,excluded.checkpoint),updated_at=excluded.updated_at WHERE checkpoint<excluded.checkpoint",
                       (consumer, checkpoint))

    def archive(self, checkpoint_database: Path, *, before: str, limit: int = 100) -> int:
        """Archive acknowledged transport rows behind a verified consistent source backup.

        Callers keep the checkpoint backup and its immutable files as a recovery set.
        Explicit pins (including repairs/shadows) prevent crossing protected coordinates.
        """
        from .content_files import ContentFiles
        from datetime import datetime, UTC, timedelta
        if datetime.fromisoformat(before) > datetime.now(UTC) - timedelta(days=7):
            raise ValueError("transport retention must be at least seven days")
        if not 1 <= limit <= 500:
            raise ValueError("invalid archive batch")
        backup = sqlite3.connect(Path(checkpoint_database).resolve(strict=True).as_uri() + "?mode=ro", uri=True)
        try:
            state = backup.execute("SELECT epoch,head FROM v2_source_state WHERE source=?", (self.source,)).fetchone()
            if state is None:
                raise ValueError("checkpoint has no source identity")
        finally:
            backup.close()
        files = ContentFiles(self.path.parent / "receipt-files" / self.source)
        with self.connection(write=True) as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT epoch FROM v2_source_state WHERE source=?", (self.source,)).fetchone()
            if not current or current[0] != state[0]:
                raise ValueError("checkpoint epoch mismatch")
            acknowledged = db.execute("SELECT min(checkpoint) FROM v2_source_consumers").fetchone()[0]
            if acknowledged is None:
                raise ValueError("no registered consumers")
            floor = min(acknowledged, state[1])
            pin = db.execute("SELECT min(seq) FROM v2_source_pins").fetchone()[0]
            if pin is not None:
                floor = min(floor, pin - 1)
            rows = db.execute("SELECT o.*,r.payload FROM v2_source_outbox o JOIN v2_source_receipts r ON r.id=o.receipt_id "
                              "WHERE o.seq<=? AND o.recorded_at<? ORDER BY o.seq LIMIT ?", (floor, before, limit)).fetchall()
            for row in rows:
                raw = json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
                identity = files.put(raw)
                db.execute("INSERT OR IGNORE INTO v2_receipt_archive VALUES(?,?,?)", (row["seq"], identity, len(raw)))
                db.execute("DELETE FROM v2_source_outbox WHERE seq=?", (row["seq"],))
                db.execute("DELETE FROM v2_source_receipts WHERE id=?", (row["receipt_id"],))
            if rows:
                # A floor is a contiguous boundary, never MAX(archived seq).
                retained = db.execute("SELECT coalesce(min(seq)-1,?) FROM v2_source_outbox", (floor,)).fetchone()[0]
                db.execute("UPDATE v2_source_state SET retained_floor=max(retained_floor,?) WHERE source=?", (retained, self.source))
            return len(rows)

    def backfill(self, table: str, *, limit: int = 100) -> int:
        """Bounded source snapshot; new writes remain captured throughout the backfill."""
        if table not in TABLES[self.source] or not 1 <= limit <= 500:
            raise ValueError("invalid backfill scope")
        db = self.connection(write=True)
        try:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT position FROM v2_backfill_checkpoint WHERE table_name=?", (table,)
            ).fetchone()
            rows = db.execute(
                f"SELECT rowid AS _v2_rowid,* FROM {quoted(table)} WHERE rowid>? "
                "ORDER BY rowid LIMIT ?",
                (prior[0] if prior else 0, limit),
            ).fetchall()
            columns = list(db.execute(f"PRAGMA table_info({quoted(table)})"))
            keys = [r[1] for r in sorted((r for r in columns if r[5]), key=lambda r: r[5])]
            for row in rows:
                value = dict(row)
                position = value.pop("_v2_rowid")
                if table == "runtime_values" and value["namespace"] not in {
                    "trade_intents",
                    "trade_receipts",
                    "analysis_trade_decisions",
                    "candidates",
                    "worker_receipts",
                    "worker_requests",
                    "round_inputs",
                    "schedule",
                    "selection_snapshot",
                    "worker_invocations",
                    "reference_deliveries",
                }:
                    continue
                identity = (
                    json.dumps([value[k] for k in keys], separators=(",", ":"))
                    if keys
                    else str(position)
                )
                receipt = db.execute(
                    "INSERT INTO v2_source_receipts(payload) VALUES(?)",
                    (json.dumps(value, ensure_ascii=False),),
                ).lastrowid
                db.execute(
                    "INSERT INTO v2_source_outbox("
                    "table_name,entity_id,operation,receipt_id,recorded_at) "
                    "VALUES(?,?,'BACKFILL',?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                    (table, identity, receipt),
                )
            if rows:
                db.execute(
                    "INSERT INTO v2_backfill_checkpoint VALUES(?,?) ON CONFLICT(table_name) "
                    "DO UPDATE SET position=excluded.position",
                    (table, rows[-1]["_v2_rowid"]),
                )
            db.commit()
            return len(rows)
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
