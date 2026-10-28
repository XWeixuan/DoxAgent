"""Transactional native change capture, installed only by explicit migration.

Triggers write immutable source receipts and a small outbox reference in the same
business transaction. Readers never construct a domain repository to read facts.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
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
        "initialization_repair_incidents",
        "initialization_repair_rounds",
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
        "poll_runs",
        "poll_captures",
        "ir_endpoint_states",
        "acquisition_failures",
        "audit_log",
        "article_body_results",
        "message_observations",
        "content_enrichment_jobs",
        "live_articles",
        "live_deliveries",
        "live_origins",
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
            retired=set()
            if self.source=='bus':
                from doxagent.message_bus_v2.collection_plane.observability import enabled,RETIRED_TABLES
                if enabled():
                    retired=RETIRED_TABLES
                    selected=[t for t in selected if t not in retired]
            if dry_run:
                return selected
            db.execute("BEGIN IMMEDIATE")
            for table in retired:
                for operation in ('INSERT','UPDATE','DELETE'):
                    db.execute('DROP TRIGGER IF EXISTS '+quoted('v2_capture_'+table+'_'+operation))
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
            db.execute(
                "CREATE TABLE IF NOT EXISTS v2_source_state (source TEXT PRIMARY KEY, epoch TEXT NOT NULL, head INTEGER NOT NULL, retained_floor INTEGER NOT NULL)"
            )
            db.execute(
                "INSERT OR IGNORE INTO v2_source_state VALUES(?,lower(hex(randomblob(16))),(SELECT coalesce(max(seq),0) FROM v2_source_outbox),0)",
                (self.source,),
            )
            db.execute(
                "CREATE TRIGGER IF NOT EXISTS v2_source_head AFTER INSERT ON v2_source_outbox BEGIN UPDATE v2_source_state SET head=max(head,NEW.seq); END"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS v2_source_consumers (id TEXT PRIMARY KEY, checkpoint INTEGER NOT NULL, updated_at TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS v2_source_pins (id TEXT PRIMARY KEY, seq INTEGER NOT NULL, reason TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS v2_receipt_archive (seq INTEGER PRIMARY KEY, digest TEXT NOT NULL, size INTEGER NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS v2_backfill_checkpoint "
                "(table_name TEXT PRIMARY KEY,position INTEGER NOT NULL)"
            )
            from .artifact_registry import migrate as migrate_artifacts
            from .artifact_registry import native_triggers

            migrate_artifacts(db)
            from .source_lifecycle import migrate as migrate_lifecycle

            migrate_lifecycle(db)
            names.add('v2_source_receipts')
            for native_table in sorted(names):
                if native_table.startswith("sqlite_"):
                    continue
                info = list(db.execute("PRAGMA table_info(" + quoted(native_table) + ")"))
                payloads = [
                    r[1]
                    for r in info
                    if r[1] in {"payload", "payload_json", "inputs", "receipt", "data_json"}
                ]
                keys = sorted((r for r in info if r[5]), key=lambda row: row[5])
                if payloads:
                    identity = (
                        "json_array(" + ",".join("NEW." + quoted(r[1]) for r in keys) + ")"
                        if keys
                        else "CAST(NEW.rowid AS TEXT)"
                    )
                    native_triggers(db, native_table, payloads, identity)
            for table in selected:
                if table == "runtime_values":
                    prior = db.execute(
                        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='v2_capture_runtime_values_INSERT'"
                    ).fetchone()
                    if prior and "policy_direction_usage" not in prior[0]:
                        db.execute(
                            "UPDATE v2_backfill_checkpoint SET position=0 WHERE table_name='runtime_values'"
                        )
                columns = list(db.execute(f"PRAGMA table_info({quoted(table)})"))
                if table == 'poll_captures':
                    columns = [r for r in columns if r[1] not in {
                        'payload_json','receipt_json','claim_token','lease_until','queue_at','not_before'}]
                primary = sorted((r for r in columns if r[5]), key=lambda r: r[5])
                for operation in ("INSERT", "UPDATE", "DELETE"):
                    db.execute(
                        f"DROP TRIGGER IF EXISTS {quoted('v2_capture_' + table + '_' + operation)}"
                    )
                    alias = "OLD" if operation == "DELETE" else "NEW"
                    values = ",".join(f"'{r[1]}',{alias}.{quoted(r[1])}" for r in columns)
                    if table == 'poll_states':
                        # The Owner retains full checkpoints. Read versions need
                        # health/counts and small endpoint summaries, not input lists.
                        data = f'{alias}.data_json'
                        thin = (f"json_set(json_remove({data},'$.units','$.checkpoint'),'$.checkpoint',"
                                f"json_object('ir_endpoints',json_extract({data},'$.checkpoint.ir_endpoints'),"
                                f"'query_cycle',json_extract({data},'$.checkpoint.query_cycle')))")
                        values = ",".join(f"'{r[1]}'," + (f"({thin} || '')" if r[1]=='data_json' else
                                             f'{alias}.{quoted(r[1])}') for r in columns)
                    if table in {'article_body_results', 'content_enrichment_jobs'}:
                        data = f'{alias}.data_json'
                        if table == 'article_body_results':
                            thin = f"json_remove({data},'$.content','$.diagnostics')"
                        else:
                            thin = (f"json_set(json_remove({data},'$.message','$.source','$.binding','$.result'),"
                                f"'$.source',json_object('source_id',json_extract({data},'$.source.source_id')),"
                                f"'$.binding',CASE WHEN json_extract({data},'$.binding.ticker') IS NULL THEN NULL ELSE "
                                f"json_object('ticker',json_extract({data},'$.binding.ticker'),'binding_id',json_extract({data},'$.binding.binding_id')) END,"
                                f"'$.result',json_object('outcome',json_extract({data},'$.result.outcome'),"
                                f"'reason',json_extract({data},'$.result.reason'),'completed_at',json_extract({data},'$.result.completed_at')))")
                        # Receipt columns keep their native SQL TEXT shape. JSON
                        # functions otherwise make json_object embed data_json as
                        # an object, bypassing the existing native-row decoder.
                        values = ",".join(f"'{r[1]}',"+(f"({thin} || '')" if r[1]=='data_json' else
                                             f'{alias}.{quoted(r[1])}') for r in columns)
                    key = "json_array(" + ",".join(f"{alias}.{quoted(r[1])}" for r in primary) + ")"
                    if not primary:
                        key = f"CAST({alias}.rowid AS TEXT)"
                    condition = (
                        f"WHEN {alias}.namespace IN ('trade_intents','trade_receipts',"
                        "'analysis_trade_decisions','candidates','worker_receipts','worker_requests',"
                        "'round_inputs','schedule','selection_snapshot','worker_invocations',"
                        "'reference_deliveries','policy_direction_usage','policy_consumptions',"
                        "'policy_authorization_head','policy_authorization_applied','policy_consumption_migration',"
                        "'policy_watch','policy_watch_applied','policy_watch_validation','message_pins','w3_operations_applied','w3_global_input','w3_operations_input')"
                        if table == "runtime_values"
                        else ""
                    )
                    if operation == "UPDATE":
                        technical = (
                            {"lease_until", "updated_at"} if table == "runtime_tasks" else set()
                        )
                        changed = " OR ".join(
                            f"OLD.{quoted(r[1])} IS NOT NEW.{quoted(r[1])}"
                            for r in columns
                            if r[1] not in technical
                        )
                        if table == "poll_states":
                            # Scheduling-only writes do not change observed health or
                            # freshness. Completed attempts and failures still capture
                            # exact timestamps, counters and current observation data.
                            volatile = "'$.next_dispatch_at','$.target_due_at','$.updated_at'"
                            changed = (
                                'OLD.binding_id IS NOT NEW.binding_id OR '
                                'OLD.source_id IS NOT NEW.source_id OR OLD.ticker IS NOT NEW.ticker OR '
                                'OLD.status IS NOT NEW.status OR '
                                f'json_remove(OLD.data_json,{volatile}) IS NOT '
                                f'json_remove(NEW.data_json,{volatile})'
                            )
                        if table == "message_observations":
                            changed = " OR ".join(
                                f"OLD.{field} IS NOT NEW.{field}" for field in (
                                    "canonical_raw_id", "logical_message_id", "classification"
                                )
                            ) + " OR json_extract(OLD.data_json,'$.metadata.article_body_result_key') IS NOT json_extract(NEW.data_json,'$.metadata.article_body_result_key')"
                        if table == "article_body_results":
                            changed = " OR ".join(
                                f"json_extract(OLD.data_json,'$.{field}') IS NOT json_extract(NEW.data_json,'$.{field}')"
                                for field in ("status", "outcome", "content_hash", "acquired_at", "checked_at", "last_error", "check_pending", "next_check_at")
                            )
                        if table == "content_enrichment_jobs":
                            changed = "OLD.status IS NOT NEW.status"
                        if table=="live_articles":
                            changed=" OR ".join(f"OLD.{field} IS NOT NEW.{field}" for field in (
                                "material_hint","outcome","body_digest","checked_at","last_error"))
                            pending="state IN ('FETCH_DUE','FETCHING','COMMIT_DUE')"
                            changed+=f" OR (OLD.{pending}) IS NOT (NEW.{pending})"
                        if table=="live_deliveries":
                            changed=" OR ".join(f"OLD.{field} IS NOT NEW.{field}" for field in (
                                "state","terms_revision","decision_digest","raw_id","standard_id","consumable_at"))
                        if table == 'poll_runs':
                            changed = f"({changed}) AND (NEW.status!='DISPATCHED' OR OLD.status IS NOT NEW.status)"
                        if table == "poll_captures":
                            changed = " OR ".join(
                                f"OLD.{quoted(r[1])} IS NOT NEW.{quoted(r[1])}"
                                for r in columns
                                if r[1] not in {"status", "intake_position"}
                            ) + (
                                " OR (CASE OLD.status WHEN 'RUNNING' THEN 'PENDING' ELSE OLD.status END) IS NOT "
                                "(CASE NEW.status WHEN 'RUNNING' THEN 'PENDING' ELSE NEW.status END)"
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
                "INSERT INTO v2_capture_meta "
                "VALUES(?,strftime('%Y-%m-%dT%H:%M:%fZ','now'),?) "
                "ON CONFLICT(source) DO UPDATE SET tables_json=excluded.tables_json",
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
            from .source_lifecycle import check_replay
            check_replay(db, after)
            from .transport_archive import has_packs
            if has_packs(db) and db.execute("SELECT 1 FROM v2_receipt_packs WHERE seq>? LIMIT 1", (after,)).fetchone():
                return [self.event(sequence) for sequence in self.coordinates(after, limit)]
            rows = db.execute(
                "SELECT o.*,r.payload FROM v2_source_outbox o "
                "JOIN v2_source_receipts r ON r.id=o.receipt_id "
                "WHERE o.seq>? ORDER BY o.seq LIMIT ?",
                (after, limit),
            ).fetchall()
            archived = db.execute(
                "SELECT seq FROM v2_receipt_archive WHERE seq>? ORDER BY seq LIMIT ?",
                (after, limit),
            ).fetchall()
            if not archived:
                return [
                    {**dict(r), "row": self.content.source_row(json.loads(r["payload"]))}
                    for r in rows
                ]
            hot = {
                r["seq"]: {**dict(r), "row": self.content.source_row(json.loads(r["payload"]))}
                for r in rows
            }
            identities = sorted(set(hot) | {r[0] for r in archived})[:limit]
            return [hot[seq] if seq in hot else self.event(seq) for seq in identities]
        finally:
            db.close()

    def coordinates(self, after: int, limit: int = 100) -> list[int]:
        """Read only bounded transport identities; load payload when it is processed."""
        if not 1 <= limit <= 500:
            raise ValueError("invalid projection batch size")
        db = self.connection()
        try:
            from .source_lifecycle import check_replay
            check_replay(db, after)
            from doxagent.message_bus_v2.collection_plane.observability import enabled,RETIRED_TABLES
            if self.source=='bus' and enabled():
                # Advance through retired technical history using headers only.
                # Bounded scan and real terminal coordinate preserve concurrent
                # business events and archived transport order.
                headers=db.execute('SELECT seq,table_name FROM v2_source_outbox WHERE seq>? ORDER BY seq LIMIT 4096',(after,)).fetchall()
                hot=[r[0] for r in headers if r[1] not in RETIRED_TABLES]
                if headers:hot.append(headers[-1][0])
                cold=[r[0] for r in db.execute('SELECT seq FROM v2_receipt_archive WHERE seq>? ORDER BY seq LIMIT ?',(after,limit))]
                from .transport_archive import has_packs
                if has_packs(db):cold.extend(r[0] for r in db.execute('SELECT seq FROM v2_receipt_packs WHERE seq>? ORDER BY seq LIMIT ?',(after,limit)))
                return sorted(set(hot+cold))[:limit]
            from .transport_archive import has_packs
            packed = (" UNION SELECT seq FROM (SELECT seq FROM v2_receipt_packs WHERE seq>? ORDER BY seq LIMIT ?)"
                      if has_packs(db) else "")
            return [
                row[0]
                for row in db.execute(
                    "SELECT seq FROM (SELECT seq FROM (SELECT seq FROM v2_source_outbox WHERE seq>? ORDER BY seq LIMIT ?) UNION SELECT seq FROM (SELECT seq FROM v2_receipt_archive WHERE seq>? ORDER BY seq LIMIT ?)" + packed + ") ORDER BY seq LIMIT ?",
                    (after, limit, after, limit, *((after, limit) if packed else ()), limit),
                )
            ]
        finally:
            db.close()

    def head(self) -> int:
        db = self.connection()
        try:
            return db.execute(
                "SELECT head FROM v2_source_state WHERE source=?", (self.source,)
            ).fetchone()[0]
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
            state = db.execute(
                "SELECT epoch FROM v2_source_state WHERE source=?", (self.source,)
            ).fetchone()
            return {**dict(row), "source_epoch": state[0], "schema_version": 2}
        finally:
            db.close()

    def event_reader(self):
        """Reuse connection/schema caches for a bounded batch, without BEGIN.

        Each SELECT finishes before the next event is mapped/committed; no
        long-lived source snapshot or unbounded payload prefetch is introduced.
        """
        from contextlib import contextmanager

        @contextmanager
        def reader():
            with closing(self.connection()) as db:
                yield lambda sequence: self.event(sequence, connection=db)

        return reader()

    def event(self, sequence: int, *, decode: bool = True, connection=None, pack_cache=None) -> dict[str, Any] | None:
        def packet(value):
            return {**value,'row':self.content.source_row(json.loads(value['payload']))} if decode else dict(value)
        db = connection if connection is not None else self.connection()
        try:
            from doxagent.message_bus_v2.collection_plane.observability import enabled,RETIRED_TABLES
            if decode and self.source=='bus' and enabled():
                header=db.execute('SELECT * FROM v2_source_outbox WHERE seq=?',(sequence,)).fetchone()
                if header and header['table_name'] in RETIRED_TABLES:
                    return {**dict(header),'row':{}}
            row = db.execute(
                "SELECT o.*,r.payload FROM v2_source_outbox o JOIN "
                "v2_source_receipts r ON r.id=o.receipt_id WHERE o.seq=?",
                (sequence,),
            ).fetchone()
            if row:
                return packet(row)
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_source_exception_receipts'").fetchone():
                saved = db.execute('SELECT packet FROM v2_source_exception_receipts WHERE seq=?',(sequence,)).fetchone()
                if saved:
                    value = json.loads(saved[0])
                    return packet(value)
            from .transport_archive import has_packs, load_pack
            packed = db.execute("SELECT digest,size,ordinal FROM v2_receipt_packs WHERE seq=?",
                                (sequence,)).fetchone() if has_packs(db) else None
            if packed:
                key = (packed[0], packed[1])
                rows = pack_cache.get(key) if pack_cache is not None else None
                if rows is None:
                    rows = load_pack(str(self.path.parent / 'receipt-packs' / self.source), *key)
                    if pack_cache is not None:
                        pack_cache.clear()  # One decompressed pack per maintenance batch.
                        pack_cache[key] = rows
                value = rows[packed[2]]
                if value['seq'] != sequence:
                    raise ValueError('transport pack coordinate mismatch')
                return packet(value)
            archived = db.execute(
                "SELECT digest,size FROM v2_receipt_archive WHERE seq=?", (sequence,)
            ).fetchone()
            if not archived:
                return None
            from .content_files import ContentFiles

            files = ContentFiles(self.path.parent / "receipt-files" / self.source)
            raw = b"".join(
                files.read(archived[0], offset, min(files.CHUNK, archived[1] - offset))
                for offset in range(0, archived[1], files.CHUNK)
            )
            value = json.loads(raw)
            return packet(value)
        finally:
            if connection is None:
                db.close()

    def acknowledge(self, consumer: str, checkpoint: int, repair_pin: int | None = None) -> None:
        """Consumer registrations are durable; offline consumers continue to pin history."""
        with closing(self.connection(write=True)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if repair_pin is None:
                db.execute("DELETE FROM v2_source_pins WHERE id=?", (consumer + ":repair",))
            else:
                db.execute(
                    "INSERT INTO v2_source_pins VALUES(?,?,'repair') ON CONFLICT(id) DO UPDATE SET seq=excluded.seq WHERE seq IS NOT excluded.seq",
                    (consumer + ":repair", repair_pin),
                )
            db.execute(
                "INSERT INTO v2_source_consumers VALUES(?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now')) "
                "ON CONFLICT(id) DO UPDATE SET checkpoint=max(checkpoint,excluded.checkpoint),updated_at=excluded.updated_at WHERE checkpoint<excluded.checkpoint",
                (consumer, checkpoint),
            )

    def acknowledge_exceptions(self, consumer, checkpoint, repairs):
        from .source_lifecycle import acknowledge
        acknowledge(self,consumer,checkpoint,repairs)

    def retire_consumer(self, consumer):
        from .source_lifecycle import retire_consumer
        retire_consumer(self,consumer)

    def claim_serving_facts(self, path):
        if self.source!='runtime':return
        from .source_lifecycle import migrate
        with closing(self.connection(write=True)) as db,db:
            db.execute('BEGIN IMMEDIATE');migrate(db)
            db.execute('INSERT INTO v2_serving_fact_owner VALUES(1,?) ON CONFLICT(id) DO UPDATE SET path=excluded.path',(str(Path(path).resolve()),))

    def archive(self, checkpoint_database: Path, *, before: str, limit: int = 100) -> int:
        """Archive acknowledged transport rows behind a verified consistent source backup.

        Callers keep the checkpoint backup and its immutable files as a recovery set.
        Explicit pins (including repairs/shadows) prevent crossing protected coordinates.
        """
        from datetime import UTC, datetime, timedelta

        from .content_files import ContentFiles

        if datetime.fromisoformat(before) > datetime.now(UTC) - timedelta(days=7):
            raise ValueError("transport retention must be at least seven days")
        if not 1 <= limit <= 500:
            raise ValueError("invalid archive batch")
        backup = sqlite3.connect(
            Path(checkpoint_database).resolve(strict=True).as_uri() + "?mode=ro", uri=True
        )
        try:
            state = backup.execute(
                "SELECT epoch,head FROM v2_source_state WHERE source=?", (self.source,)
            ).fetchone()
            if state is None:
                raise ValueError("checkpoint has no source identity")
        finally:
            backup.close()
        files = ContentFiles(self.path.parent / "receipt-files" / self.source)
        with self.connection(write=True) as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT epoch FROM v2_source_state WHERE source=?", (self.source,)
            ).fetchone()
            if not current or current[0] != state[0]:
                raise ValueError("checkpoint epoch mismatch")
            acknowledged = db.execute("SELECT min(checkpoint) FROM v2_source_consumers").fetchone()[
                0
            ]
            if acknowledged is None:
                raise ValueError("no registered consumers")
            floor = min(acknowledged, state[1])
            pin = db.execute("SELECT min(seq) FROM v2_source_pins").fetchone()[0]
            if pin is not None:
                floor = min(floor, pin - 1)
            rows = db.execute(
                "SELECT o.*,r.payload FROM v2_source_outbox o JOIN v2_source_receipts r ON r.id=o.receipt_id "
                "WHERE o.seq<=? AND o.recorded_at<? ORDER BY o.seq LIMIT ?",
                (floor, before, limit),
            ).fetchall()
            for row in rows:
                raw = json.dumps(
                    dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
                identity = files.put(raw)
                db.execute(
                    "INSERT OR IGNORE INTO v2_receipt_archive VALUES(?,?,?)",
                    (row["seq"], identity, len(raw)),
                )
                db.execute("DELETE FROM v2_source_outbox WHERE seq=?", (row["seq"],))
                db.execute("DELETE FROM v2_source_receipts WHERE id=?", (row["receipt_id"],))
            if rows:
                # A floor is a contiguous boundary, never MAX(archived seq).
                retained = db.execute(
                    "SELECT coalesce(min(seq)-1,?) FROM v2_source_outbox", (floor,)
                ).fetchone()[0]
                db.execute(
                    "UPDATE v2_source_state SET retained_floor=max(retained_floor,?) WHERE source=?",
                    (retained, self.source),
                )
            return len(rows)

    def backfill(
        self, table: str, *, limit: int = 100, namespaces: tuple[str, ...] | None = None
    ) -> int:
        """Bounded source snapshot; new writes remain captured throughout the backfill."""
        if table not in TABLES[self.source] or not 1 <= limit <= 500:
            raise ValueError("invalid backfill scope")
        if namespaces is not None and (table != "runtime_values" or not namespaces):
            raise ValueError("namespace backfill requires runtime_values and a nonempty scope")
        checkpoint = table if namespaces is None else table + ":" + ",".join(sorted(namespaces))
        db = self.connection(write=True)
        try:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT position FROM v2_backfill_checkpoint WHERE table_name=?", (checkpoint,)
            ).fetchone()
            namespace_filter = (
                "AND namespace IN (" + ",".join("?" for _ in namespaces) + ") "
                if namespaces else ""
            )
            rows = db.execute(
                f"SELECT rowid AS _v2_rowid,* FROM {quoted(table)} WHERE rowid>? "
                + namespace_filter + "ORDER BY rowid LIMIT ?",
                (prior[0] if prior else 0, *(namespaces or ()), limit),
            ).fetchall()
            columns = list(db.execute(f"PRAGMA table_info({quoted(table)})"))
            keys = [r[1] for r in sorted((r for r in columns if r[5]), key=lambda r: r[5])]
            for row in rows:
                value = dict(row)
                if table == 'poll_captures':
                    for key in ('payload_json','receipt_json','claim_token','lease_until','queue_at','not_before'):
                        value.pop(key,None)
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
                    "policy_direction_usage",
                    "policy_consumptions",
                    "policy_authorization_head",
                    "policy_authorization_applied",
                    "policy_consumption_migration",
                    "policy_watch",
                    "policy_watch_applied",
                    "policy_watch_validation",
                    "message_pins",
                    "w3_operations_applied",
                    "w3_global_input",
                    "w3_operations_input",
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
                    (checkpoint, rows[-1]["_v2_rowid"]),
                )
            db.commit()
            return len(rows)
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
