"""MVCC representations and durable change log without long-lived SQLite readers."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def instant(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def migrate_cost_indexes(db) -> None:
    """Only cost buckets are indexed; no source receipt or JSON payload index."""
    for table in ("metric_buckets_current", "metric_buckets_history"):
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            fields = "scope,valid_to,valid_from,day,metric" if table.endswith("history") else "scope,day,metric"
            db.execute("CREATE INDEX IF NOT EXISTS " + table + "_cost_global ON " + table +
                       "(" + fields + ") WHERE scope IN ('API','CODEX')")


def migrate_case_evidence_indexes(db) -> None:
    """Add display lookup indexes to an existing release without a full migration."""
    relation=db.execute("SELECT type FROM sqlite_master WHERE name='objects'").fetchone()
    if relation and relation[0]=='view':
        from .serving_schema import upgrade
        upgrade(db)
        return  # V4 owns physical current/history lookup indexes, not view indexes.
    for name, fields, kind in (
        ("case_fact_evidence", "parent,json_extract(payload,'$.fact.fact_id'),valid_from", "fact"),
        ("case_policy_evidence", "parent,json_extract(payload,'$.summary.policy_id'),valid_from", "policy_detail"),
        ("case_provisional_evidence", "json_extract(payload,'$.provisional_event_id'),"
         "json_extract(payload,'$.trading_date'),json_extract(payload,'$.snapshot_version'),valid_from",
         "native:runtime_v2_candidates"),
    ):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone():
            # SQLite sorts on disk with a small page cache; no Python payload loading.
            db.execute("PRAGMA cache_size=-8192")
            db.execute("PRAGMA temp_store=FILE")
            db.execute("CREATE INDEX IF NOT EXISTS " + name + " ON objects(kind,ticker," +
                       fields + ") WHERE kind='" + kind + "'")


class ReadStore:
    VERSION = 4

    def migrate_projection_indexes(self):
        """Explicit worker release migration, never DDL inside an HTTP read."""
        with self.connect(write=True, timeout=30) as db:
            db.execute("CREATE INDEX IF NOT EXISTS current_kind_revision ON object_current(kind,valid_from)")
            db.execute("CREATE INDEX IF NOT EXISTS contribution_metric_revision "
                       "ON contributions_current(metric,valid_from)")
            db.execute("CREATE INDEX IF NOT EXISTS current_graph_case_members "
                       "ON object_current(kind,ticker,json_extract(payload,'$.case_id')) "
                       "WHERE kind='graph_member'")
            db.execute("CREATE INDEX IF NOT EXISTS legacy_policy_coverage "
                       "ON object_current(kind,json_extract(payload,'$.consumed.value')) "
                       "WHERE kind='policy_catalog' AND json_type(payload,'$.directions') IS NULL")

    def migrate_policy_query_indexes(self):
        """Explicit, idempotent Policy read migration; never run on a GET/startup."""
        with self.connect(write=True) as db:
            from .serving_schema import history_relation
            db.execute("CREATE INDEX IF NOT EXISTS policy_positive_current ON contributions_current("
                       "metric,ticker,day,json_extract(dimensions,'$.business_key'),valid_from) "
                       "WHERE metric IN ('policy_ar_hits','policy_executed') AND CAST(value AS NUMERIC)>0")
            db.execute("CREATE INDEX IF NOT EXISTS policy_positive_history ON " + history_relation(db,'contributions') + "("
                       "metric,ticker,valid_to,valid_from,day,json_extract(dimensions,'$.business_key')) "
                       "WHERE valid_to IS NOT NULL AND metric IN ('policy_ar_hits','policy_executed') AND CAST(value AS NUMERIC)>0")

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self._retirement_shapes={}
        self._projection_connections = None
        if self.path.suffix == ".json":
            alias = json.loads(self.path.read_text(encoding="utf-8"))
            if alias.get("format") != "doxagent.v2.read-alias.1":
                raise ValueError("invalid V2 read alias")
            target = Path(alias["path"])
            self.path = (target if target.is_absolute() else self.path.parent / target).resolve()
            if not self.path.is_file():
                raise ValueError("read alias target is missing")

    @property
    def content_codec(self):
        from .native_content import NativeContent
        return NativeContent(self.path)

    def close_current(self,db,base,where,args,seq):
        """Move current once; never search technical history to close a head."""
        if base not in self._retirement_shapes:
            from .serving_schema import history_relation
            history=history_relation(db,base)
            columns=[r[1] for r in db.execute('PRAGMA table_info('+history+')')]
            self._retirement_shapes[base]=(history,columns)
        history,columns=self._retirement_shapes[base]
        if history==base:
            db.execute('UPDATE '+base+' SET valid_to=? WHERE '+where+' AND valid_to IS NULL',(seq,*args));return
        current='object_current' if base=='objects' else base+'_current'
        selected=','.join('?' if column=='valid_to' else column for column in columns)
        db.execute('INSERT INTO '+history+'('+','.join(columns)+') SELECT '+selected+' FROM '+current+' WHERE '+where,(seq,*args))
        db.execute('DELETE FROM '+current+' WHERE '+where,args)

    @contextmanager
    def projection_session(self):
        """Worker-only, tick-local schema cache; transactions still commit per event.

        Idle connections have no open transaction/snapshot. Nested checkouts get
        separate connections. Nothing is shared with the API or across ticks.
        """
        if self._projection_connections is not None:
            raise RuntimeError("nested projection session")
        idle = {False: [], True: []}
        self._projection_connections = idle
        try:
            yield
        finally:
            self._projection_connections = None
            for connections in idle.values():
                for db in connections:
                    db.close()

    @contextmanager
    def connect(self, *, write: bool = False, timeout: float = 2) -> Iterator[sqlite3.Connection]:
        idle = self._projection_connections
        if idle is not None and idle[write]:
            db = idle[write].pop()
        else:
            db = sqlite3.connect(
                self.path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=timeout
            )
            if not write:
                from .query_budget import interrupted
                db.set_progress_handler(interrupted, 1000)
            db.execute('PRAGMA recursive_triggers=ON')
            codec = self.content_codec
            codec.definition_connection = db
            db.row_factory = codec.row
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            if idle is None:
                db.close()
            else:
                idle[write].append(db)

    def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            existing = db.execute("SELECT 1 FROM sqlite_master WHERE name='schema_meta'").fetchone()
            if existing:
                version = db.execute('SELECT version FROM schema_meta').fetchone()[0]
                if version == 4:
                    from .serving_schema import upgrade
                    upgrade(db)
                    return
                if version not in (1,2,3):
                    raise ValueError('unsupported V2 read schema')
                if db.execute('SELECT 1 FROM objects LIMIT 1').fetchone():
                    raise ValueError('populated V3 requires an explicit serving generation build')
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_meta'"
            ).fetchone() and [r[0] for r in db.execute("SELECT version FROM schema_meta")] not in (
                [1],
                [2],
                [self.VERSION],
            ):
                raise ValueError("unsupported V2 read schema")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER PRIMARY KEY);
                INSERT INTO schema_meta SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_meta);
                CREATE TABLE IF NOT EXISTS commits (seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT, source_event TEXT, at TEXT, UNIQUE(source,source_event));
                CREATE TABLE IF NOT EXISTS objects (
                    kind TEXT, ticker TEXT, id TEXT, valid_from INTEGER, valid_to INTEGER,
                    sort_key TEXT, parent TEXT, day TEXT, source_id TEXT, route TEXT,
                    search_text TEXT, payload TEXT NOT NULL,
                    PRIMARY KEY(kind,ticker,id,valid_from));
                CREATE UNIQUE INDEX IF NOT EXISTS objects_head ON objects(kind,ticker,id)
                    WHERE valid_to IS NULL;
                CREATE INDEX IF NOT EXISTS objects_page ON objects(kind,ticker,sort_key,id);
                CREATE INDEX IF NOT EXISTS objects_parent
                    ON objects(kind,ticker,parent,sort_key,id);
                CREATE INDEX IF NOT EXISTS objects_day ON objects(kind,ticker,day,valid_from);
                CREATE INDEX IF NOT EXISTS objects_day_page ON objects(kind,ticker,day,sort_key,id);
                CREATE INDEX IF NOT EXISTS objects_source_page ON objects(kind,ticker,source_id,day,sort_key,id);
                CREATE INDEX IF NOT EXISTS objects_source ON objects(kind,ticker,source_id,day);
                CREATE INDEX IF NOT EXISTS objects_source_kind ON objects(
                    kind,ticker,json_extract(payload,'$.source.kind'),day);
                CREATE TABLE IF NOT EXISTS changes (seq INTEGER, ordinal INTEGER, kind TEXT,
                    ticker TEXT, id TEXT, before_payload TEXT, after_payload TEXT,
                    PRIMARY KEY(seq,ordinal));
                CREATE INDEX IF NOT EXISTS changes_scope ON changes(ticker,kind,seq,ordinal);
                CREATE TABLE IF NOT EXISTS source_epochs(source TEXT PRIMARY KEY,epoch TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS checkpoints (source TEXT PRIMARY KEY, position INTEGER);
                CREATE TABLE IF NOT EXISTS entity_watermarks (source TEXT,kind TEXT,ticker TEXT,
                    id TEXT,position INTEGER,PRIMARY KEY(source,kind,ticker,id));
                CREATE TABLE IF NOT EXISTS gaps (source TEXT, event TEXT, reason TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,next_attempt_at TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(source,event));
                CREATE TABLE IF NOT EXISTS contents (id TEXT PRIMARY KEY,ticker TEXT,kind TEXT,
                    sha256 TEXT,size INTEGER,body BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS views (id TEXT PRIMARY KEY,owner TEXT,seq INTEGER,
                    scope TEXT,payload TEXT,expires_at TEXT);
                CREATE TABLE IF NOT EXISTS cursors (id TEXT PRIMARY KEY,owner TEXT,scope TEXT,
                    payload TEXT,expires_at TEXT);
                CREATE TABLE IF NOT EXISTS contributions (
                    metric TEXT,ticker TEXT,entity TEXT,day TEXT,dimensions TEXT,value TEXT,
                    valid_from INTEGER,valid_to INTEGER,
                    PRIMARY KEY(metric,ticker,entity,day,dimensions,valid_from));
                CREATE INDEX IF NOT EXISTS contributions_window
                    ON contributions(metric,ticker,day,valid_from);
                CREATE TABLE IF NOT EXISTS metric_buckets (
                    metric TEXT,ticker TEXT,day TEXT,dimensions TEXT,value TEXT,
                    valid_from INTEGER,valid_to INTEGER,
                    PRIMARY KEY(metric,ticker,day,dimensions,valid_from));
                CREATE INDEX IF NOT EXISTS metric_buckets_window
                    ON metric_buckets(metric,ticker,day,valid_from);
                CREATE TABLE IF NOT EXISTS contribution_watermarks (
                    source TEXT,metric TEXT,ticker TEXT,entity TEXT,position INTEGER,
                    PRIMARY KEY(source,metric,ticker,entity));
                CREATE TABLE IF NOT EXISTS coverage (source TEXT,ticker TEXT,start_at TEXT,
                    end_at TEXT,state TEXT,reason TEXT,PRIMARY KEY(source,ticker));
                CREATE TABLE IF NOT EXISTS capture_proofs(source TEXT PRIMARY KEY,payload TEXT NOT NULL,seq INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS source_health (source TEXT PRIMARY KEY,
                    head INTEGER, checkpoint INTEGER, checked_at TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS generation (id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS business_revisions (
                    kind TEXT,ticker TEXT,id TEXT,valid_from INTEGER,valid_to INTEGER,
                    sort_key TEXT,parent TEXT,day TEXT,source_id TEXT,route TEXT,search_text TEXT,payload TEXT,
                    PRIMARY KEY(kind,ticker,id,valid_from));
                CREATE TRIGGER IF NOT EXISTS preserve_business_revision AFTER UPDATE OF valid_to ON objects
                WHEN NEW.valid_to IS NOT NULL AND NEW.kind NOT IN ('ticker','navigation','capture_coverage','native:runtime_tasks','native:runtime_values','native:poll_states')
                BEGIN INSERT OR IGNORE INTO business_revisions SELECT * FROM objects WHERE kind=NEW.kind AND ticker=NEW.ticker AND id=NEW.id AND valid_from=NEW.valid_from; END;
                CREATE TABLE IF NOT EXISTS content_locations(id TEXT PRIMARY KEY, sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS read_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS commit_retention(seq INTEGER PRIMARY KEY,projected_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS commit_retention_at ON commit_retention(projected_at,seq);
                CREATE INDEX IF NOT EXISTS commits_at ON commits(at,seq);
                CREATE INDEX IF NOT EXISTS objects_gc ON objects(valid_to) WHERE valid_to IS NOT NULL;
                CREATE INDEX IF NOT EXISTS contributions_head ON contributions(metric,ticker,entity) WHERE valid_to IS NULL;
                CREATE INDEX IF NOT EXISTS metric_buckets_scope ON metric_buckets(ticker,day,metric,valid_from);
                CREATE INDEX IF NOT EXISTS views_expiry ON views(expires_at,seq);
                CREATE INDEX IF NOT EXISTS cursors_expiry ON cursors(expires_at);
                CREATE TABLE IF NOT EXISTS object_current AS SELECT * FROM objects WHERE 0;
                CREATE UNIQUE INDEX IF NOT EXISTS current_identity ON object_current(kind,ticker,id);
                CREATE INDEX IF NOT EXISTS current_page ON object_current(kind,ticker,sort_key,id);
                CREATE INDEX IF NOT EXISTS current_parent ON object_current(kind,ticker,parent,sort_key,id);
                CREATE INDEX IF NOT EXISTS current_day ON object_current(kind,ticker,day,sort_key,id);
                CREATE INDEX IF NOT EXISTS current_policy_consumed ON object_current(kind,json_extract(payload,'$.consumed.value'),ticker,id) WHERE kind='policy_catalog';
                CREATE INDEX IF NOT EXISTS current_source ON object_current(kind,ticker,source_id,day,sort_key,id);
                CREATE INDEX IF NOT EXISTS history_recent ON objects(kind,ticker,valid_to,valid_from,sort_key,id) WHERE valid_to IS NOT NULL;
                CREATE INDEX IF NOT EXISTS history_page ON objects(kind,ticker,sort_key,id,valid_from,valid_to) WHERE valid_to IS NOT NULL;

                CREATE TRIGGER IF NOT EXISTS current_insert AFTER INSERT ON objects
                WHEN NEW.valid_to IS NULL BEGIN
                    INSERT OR REPLACE INTO object_current SELECT * FROM objects
                    WHERE kind=NEW.kind AND ticker=NEW.ticker AND id=NEW.id AND valid_from=NEW.valid_from;
                END;
                CREATE TRIGGER IF NOT EXISTS current_refresh AFTER UPDATE ON objects
                WHEN NEW.valid_to IS NULL BEGIN
                    INSERT OR REPLACE INTO object_current SELECT * FROM objects
                    WHERE kind=NEW.kind AND ticker=NEW.ticker AND id=NEW.id AND valid_from=NEW.valid_from;
                END;
                CREATE TRIGGER IF NOT EXISTS current_close AFTER UPDATE OF valid_to ON objects
                WHEN NEW.valid_to IS NOT NULL BEGIN
                    DELETE FROM object_current WHERE kind=NEW.kind AND ticker=NEW.ticker
                    AND id=NEW.id AND valid_from=NEW.valid_from;
                END;
                CREATE TRIGGER IF NOT EXISTS current_delete AFTER DELETE ON objects BEGIN
                    DELETE FROM object_current WHERE kind=OLD.kind AND ticker=OLD.ticker
                    AND id=OLD.id AND valid_from=OLD.valid_from;
                END;
            """)
            migrate_case_evidence_indexes(db)
            from .artifact_registry import migrate as migrate_artifacts, native_triggers
            migrate_artifacts(db)
            native_triggers(db, "objects", ["payload"], "json_array(NEW.kind,NEW.ticker,NEW.id,NEW.valid_from)")
            for table in ("contributions", "metric_buckets"):
                columns = {r[1] for r in db.execute("PRAGMA table_xinfo(" + table + ")")}
                for field in ("scope", "provider", "model", "node", "source_id", "source_kind", "route"):
                    if field not in columns:
                        db.execute("ALTER TABLE " + table + " ADD COLUMN " + field + " TEXT GENERATED ALWAYS AS (json_extract(dimensions,'$." + field + "')) VIRTUAL")
                db.execute("CREATE INDEX IF NOT EXISTS " + table + "_cost ON " + table + "(ticker,scope,node,day,metric,valid_from)")
                db.execute("CREATE INDEX IF NOT EXISTS " + table + "_bus ON " + table + "(metric,ticker,source_id,source_kind,route,day,valid_from)")
                db.execute("CREATE INDEX IF NOT EXISTS " + table + "_gc ON " + table + "(valid_to) WHERE valid_to IS NOT NULL")
            for table, identity in (("contributions",("metric","ticker","entity","day","dimensions")), ("metric_buckets",("metric","ticker","day","dimensions"))):
                current = table + "_current"
                db.execute("CREATE TABLE IF NOT EXISTS " + current + " AS SELECT * FROM " + table + " WHERE 0")
                db.execute("CREATE UNIQUE INDEX IF NOT EXISTS " + current + "_identity ON " + current + "(" + ",".join(identity) + ")")
                db.execute("CREATE INDEX IF NOT EXISTS " + current + "_window ON " + current + "(metric,ticker,day)")
                db.execute("CREATE INDEX IF NOT EXISTS " + current + "_cost ON " + current + "(ticker,scope,node,day,metric)")
                db.execute("CREATE INDEX IF NOT EXISTS " + current + "_bus ON " + current + "(metric,ticker,source_id,source_kind,route,day)")
                match = " AND ".join(field+"=NEW."+field for field in identity)
                db.execute("CREATE TRIGGER IF NOT EXISTS " + current + "_insert AFTER INSERT ON " + table + " WHEN NEW.valid_to IS NULL BEGIN INSERT OR REPLACE INTO " + current + " SELECT * FROM " + table + " WHERE " + match + " AND valid_from=NEW.valid_from; END")
                db.execute("CREATE TRIGGER IF NOT EXISTS " + current + "_close AFTER UPDATE OF valid_to ON " + table + " WHEN NEW.valid_to IS NOT NULL BEGIN DELETE FROM " + current + " WHERE " + match + " AND valid_from=NEW.valid_from; END")
                db.execute("INSERT OR IGNORE INTO " + current + " SELECT * FROM " + table + " WHERE valid_to IS NULL")
                db.execute("CREATE INDEX IF NOT EXISTS " + table + "_history_window ON " + table + "(metric,ticker,day,valid_to,valid_from) WHERE valid_to IS NOT NULL")
            if not db.execute("SELECT 1 FROM read_meta WHERE key='event_sort_v1'").fetchone():
                from .ordering import occurrence_anchor
                db.create_function("occurrence_anchor", 2, occurrence_anchor, deterministic=True)
                db.execute("UPDATE objects SET sort_key=occurrence_anchor(json_extract(payload,'$.occurred_at'),json_extract(payload,'$.occurrence_time_precision')) WHERE kind='event_catalog'")
                db.execute("UPDATE object_current SET sort_key=occurrence_anchor(json_extract(payload,'$.occurred_at'),json_extract(payload,'$.occurrence_time_precision')) WHERE kind='event_catalog'")
                db.execute("INSERT INTO read_meta VALUES('event_sort_v1','1')")
            db.execute("INSERT OR IGNORE INTO object_current SELECT * FROM objects WHERE valid_to IS NULL")
            db.execute("INSERT OR IGNORE INTO read_meta VALUES('highwater',?)", (str(db.execute("SELECT coalesce(max(seq),0) FROM commits").fetchone()[0]),))
            db.execute("INSERT OR IGNORE INTO read_meta VALUES('retained_floor','0')")
            db.execute("INSERT OR IGNORE INTO read_meta VALUES('first_at',?)", (db.execute("SELECT coalesce(min(at),'') FROM commits").fetchone()[0],))
            db.execute("INSERT OR IGNORE INTO commit_retention SELECT seq,? FROM commits", (instant(datetime.now(UTC)),))
            db.execute("INSERT OR IGNORE INTO capture_proofs SELECT id,payload,valid_from FROM object_current WHERE kind='capture_coverage'")
            from .graph_seed import migrate as seed_graph
            seed_graph(db, self.highwater(db))
            from .bus_aggregates import seed as seed_bus
            seed_bus(db, self.highwater(db))
            db.execute("UPDATE schema_meta SET version=?", (self.VERSION,))
            if not db.execute("SELECT 1 FROM generation").fetchone():
                db.execute("INSERT INTO generation VALUES(?)", (uuid4().hex,))
            if [r[0] for r in db.execute("SELECT version FROM schema_meta")] != [self.VERSION]:
                raise ValueError("unsupported V2 read schema")
            from .serving_schema import install
            install(db)
            migrate_cost_indexes(db)

    @property
    def history_table(self):
        with self.connect() as db:
            from .serving_schema import history_relation
            return history_relation(db)

    def metric_table(self, seq, table="metric_buckets"):
        if table not in {"metric_buckets", "contributions"}:
            raise ValueError("invalid metric relation")
        seq = int(seq)
        with self.connect() as db:
            if seq == self.highwater(db):
                return table + "_current"
        with self.connect() as db:
            from .serving_schema import history_relation
            history = history_relation(db, table)
        return f"(SELECT * FROM {table}_current WHERE valid_from<={seq} UNION ALL SELECT * FROM {history} WHERE valid_to IS NOT NULL AND valid_from<={seq} AND valid_to>{seq})"

    def snapshot_table(self, seq):
        """Disjoint hot/history relation; SQLite pushes caller filters into each branch."""
        seq = int(seq)
        with self.connect() as db:
            if seq == self.highwater(db):
                return "object_current"
        return f"(SELECT * FROM object_current WHERE valid_from<={seq} UNION ALL SELECT * FROM {self.history_table} INDEXED BY history_recent WHERE valid_to IS NOT NULL AND valid_from<={seq} AND valid_to>{seq})"

    def position(self, source: str) -> int:
        with self.connect() as db:
            row = db.execute(
                "SELECT position FROM checkpoints WHERE source=?", (source,)
            ).fetchone()
            return int(row[0]) if row else 0

    def freshness(self, now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        with self.connect() as db:
            rows = db.execute(
                "SELECT head,checkpoint,checked_at,error FROM source_health"
            ).fetchall()
            gaps = db.execute("SELECT 1 FROM gaps LIMIT 1").fetchone()
        return (
            "FRESH"
            if rows
            and not gaps
            and all(
                not row[3]
                and row[1] >= row[0]
                and (now - datetime.fromisoformat(row[2])).total_seconds() < 30
                for row in rows
            )
            else "STALE"
        )

    def ingest(
        self,
        source: str,
        event: str,
        records: list[dict[str, Any]],
        *,
        position: int | None = None,
        at: str | None = None,
        contributions: list[dict[str, Any]] | None = None,
        gap: str | None = None,
        resolved_gap: str | None = None,
        expected_usage_versions: list[tuple[str, str, int]] | None = None,
    ) -> int:
        proofs = [record for record in records if record["kind"] == "capture_coverage"]
        records = [record for record in records if record["kind"] != "capture_coverage"]
        unique: dict[tuple[str, str, str], dict[str, Any]] = {}
        for record in records:
            from .serving_fields import select
            selected = select(record['kind'], record['id'], record.get('data'))
            if selected is None and record.get('data') is not None:
                continue
            record = {**record, 'data': selected}
            key = (record["kind"], record["ticker"], record["id"])
            if key in unique and unique[key] != record:
                raise ValueError("conflicting representations in one source receipt")
            unique[key] = record
        records = list(unique.values())
        with self.connect(write=True) as db:
            # A cost backfill must not overwrite a receipt updated by the live projector.
            for ticker, identity, revision in expected_usage_versions or ():
                current = db.execute("SELECT valid_from FROM object_current WHERE kind='usage' AND ticker=? AND id=?", (ticker, identity)).fetchone()
                if not current or current[0] != revision:
                    raise ValueError("COST_BACKFILL_CONCURRENT_UPDATE")
            row = db.execute(
                "SELECT seq FROM commits WHERE source=? AND source_event=?", (source, event)
            ).fetchone()
            if row:
                return int(row[0])
            seq = 0
            def allocate():
                nonlocal seq
                if not seq:
                    cursor = db.execute("INSERT INTO commits(source,source_event,at) VALUES(?,?,?)",
                                        (source, event, at or instant(datetime.now(UTC))))
                    seq = int(cursor.lastrowid)
                return seq
            mutated = False
            bus_members, raw_links, graph_cases = set(), set(), set()
            for ordinal, record in enumerate(records):
                kind, ticker, identity = record["kind"], record["ticker"], record["id"]
                payload=record.get('data')
                normalized=kind in {'fact','event_detail','policy_detail'}
                if normalized and payload is not None:
                    from .serving_schema import normalize_definition
                    payload=normalize_definition(db,kind,payload)
                if position is not None:
                    last = db.execute(
                        "SELECT position FROM entity_watermarks WHERE source=? "
                        "AND kind=? AND ticker=? AND id=?",
                        (source, kind, ticker, identity),
                    ).fetchone()
                    if last and last[0] >= position:
                        continue
                    db.execute(
                        "INSERT INTO entity_watermarks VALUES(?,?,?,?,?) "
                        "ON CONFLICT(source,kind,ticker,id) DO UPDATE "
                        "SET position=excluded.position",
                        (source, kind, ticker, identity, position),
                    )
                old = db.execute(
                    "SELECT "+('payload AS persisted_json' if normalized else 'payload')+",sort_key,parent,day,source_id,route,search_text FROM object_current WHERE kind=? AND ticker=? AND id=? "
                    "AND valid_to IS NULL",
                    (kind, ticker, identity),
                ).fetchone()
                previous = json.loads(old[0]) if old else None
                if normalized and previous is not None:
                    previous=self.content_codec.decode(previous,expand_definitions=False)
                comparable = dict(payload) if isinstance(payload, dict) else payload
                if kind == "native:runtime_tasks":
                    for technical in ("lease_until", "updated_at"):
                        if isinstance(previous, dict):
                            previous.pop(technical, None)
                        if isinstance(comparable, dict):
                            comparable.pop(technical, None)
                if kind in {"message", "case"}:
                    revision_key = "row_revision" if kind == "message" else "revision"
                    if isinstance(previous, dict):
                        previous.pop(revision_key, None)
                    if isinstance(comparable, dict):
                        comparable.pop(revision_key, None)
                metadata = (record.get("sort", identity), record.get("parent"), record.get("day"),
                            record.get("source_id"), record.get("route"), record.get("search", "").casefold())
                if (old is None and payload is None) or (
                    old is not None and previous == comparable and tuple(old)[1:] == metadata
                ):
                    continue
                mutated = True
                allocate()
                if kind == "graph_case":
                    graph_cases.add((ticker,identity))
                elif kind == "graph_member":
                    for candidate in (previous,payload):
                        if candidate and candidate.get("case_id"):
                            graph_cases.add((ticker,candidate["case_id"]))
                if kind == "message":
                    bus_members.add((ticker,identity))
                elif kind == "message_raw_link":
                    for parent in ((old[2] if old else None), record.get("parent")):
                        if parent:
                            bus_members.add((ticker,parent))
                elif kind == "body_attempt":
                    for parent in ((old[2] if old else None), record.get("parent")):
                        if parent:
                            raw_links.add((ticker,parent))
                if old:self.close_current(db,'objects','kind=? AND ticker=? AND id=?',(kind,ticker,identity),seq)
                if payload is not None and kind == "message":
                    payload = {**payload, "row_revision": seq}
                elif payload is not None and kind == "case":
                    payload = {**payload, "revision": seq}
                if payload is not None:
                    from .serving_schema import normalize_definition
                    payload = normalize_definition(db, kind, payload)
                    db.execute(
                        "INSERT INTO objects VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            kind,
                            ticker,
                            identity,
                            seq,
                            None,
                            record.get("sort", identity),
                            record.get("parent"),
                            record.get("day"),
                            record.get("source_id"),
                            record.get("route"),
                            record.get("search", "").casefold(),
                            self.content_codec.encode(payload, keep={"inputs", "source", "summary", "consumed", "effective", "polling"}),
                        ),
                    )
                if kind in {"message", "case", "graph_member"}:
                    db.execute(
                        "INSERT INTO changes VALUES(?,?,?,?,?,?,?)",
                        (
                            seq,
                            ordinal,
                            kind,
                            ticker,
                            identity,
                            None,
                            None,
                        ),
                    )
            deltas: dict[tuple[str, str, str, str], Decimal] = {}
            for ticker, raw_id in raw_links:
                for (parent,) in db.execute("SELECT parent FROM object_current WHERE kind='message_raw_link' AND ticker=? AND id=?", (ticker,raw_id)):
                    bus_members.add((ticker,parent))
            from .bus_aggregates import contributions as bus_contributions
            from .graph_seed import contributions as graph_contributions
            contributions = [*(item for item in (contributions or []) if item["metric"] != "graph_paths"), *bus_contributions(db,bus_members), *graph_contributions(db,graph_cases)]
            groups = {}
            for item in contributions or []:
                groups.setdefault((item["metric"], item["ticker"], item["entity"]), []).append(item)
            # Cost history migration already checks all receipt revisions under this
            # writer lock. Fetch its old contribution sets in one indexed join, then
            # move/insert rows in prepared batches instead of thousands of round trips.
            bulk_cost = source == "cost_backfill" and expected_usage_versions is not None
            old_sets, closing, inserting = {}, [], []
            if bulk_cost and groups:
                for row in db.execute(
                    "SELECT c.metric,c.ticker,c.entity,c.day,c.dimensions,c.value "
                    "FROM json_each(?) AS requested JOIN contributions_current AS c "
                    "ON c.metric=json_extract(requested.value,'$[0]') "
                    "AND c.ticker=json_extract(requested.value,'$[1]') "
                    "AND c.entity=json_extract(requested.value,'$[2]')",
                    (encode(list(groups)),),
                ):
                    old_sets.setdefault(tuple(row[:3]), []).append(tuple(row[3:]))
            for base, items in groups.items():
                identity = (source, *base)
                if position is not None:
                    last = db.execute("SELECT position FROM contribution_watermarks WHERE source=? AND metric=? AND ticker=? AND entity=?", identity).fetchone()
                    if last and last[0] >= position:
                        continue
                    db.execute("INSERT INTO contribution_watermarks VALUES(?,?,?,?,?) ON CONFLICT(source,metric,ticker,entity) DO UPDATE SET position=excluded.position", (*identity, position))
                expected = {}
                for item in items:
                    if item.get("value") is None:
                        continue
                    amount = Decimal(str(item["value"]))
                    if not amount.is_finite():
                        raise ValueError("nonfinite contribution")
                    member = (item["day"], encode(item.get("dimensions", {})))
                    if member in expected and expected[member] != amount:
                        raise ValueError("conflicting contribution set")
                    if amount:
                        expected[member] = amount
                old_values = old_sets.get(base, []) if bulk_cost else db.execute("SELECT day,dimensions,value FROM contributions_current WHERE metric=? AND ticker=? AND entity=?", base).fetchall()
                previous = {(row[0], row[1]): Decimal(row[2]) for row in old_values}
                if previous == expected:
                    continue
                mutated = True
                allocate()
                for (day, dimensions), amount in previous.items():
                    for bucket_day in (day, "*"):
                        bucket = (base[0], base[1], bucket_day, dimensions)
                        deltas[bucket] = deltas.get(bucket, Decimal(0)) - amount
                if old_values:
                    if bulk_cost:
                        closing.append(base)
                    else:
                        self.close_current(db,'contributions','metric=? AND ticker=? AND entity=?',base,seq)
                for (day, dimensions), amount in expected.items():
                    for bucket_day in (day, "*"):
                        bucket = (base[0], base[1], bucket_day, dimensions)
                        deltas[bucket] = deltas.get(bucket, Decimal(0)) + amount
                    row = (*base, day, dimensions, format(amount, "f"), seq)
                    if bulk_cost:
                        inserting.append(row)
                    else:
                        db.execute("INSERT INTO contributions(metric,ticker,entity,day,dimensions,value,valid_from,valid_to) VALUES(?,?,?,?,?,?,?,NULL)", row)
            if bulk_cost:
                if closing:
                    from .serving_schema import history_relation
                    history = history_relation(db, "contributions")
                    columns = [r[1] for r in db.execute("PRAGMA table_info("+history+")")]
                    selected = ",".join("?" if column == "valid_to" else column for column in columns)
                    predicate = "metric=? AND ticker=? AND entity=?"
                    db.executemany("INSERT INTO "+history+"("+",".join(columns)+") SELECT "+selected+
                                   " FROM contributions_current WHERE "+predicate, [(seq,*base) for base in closing])
                    db.executemany("DELETE FROM contributions_current WHERE "+predicate, closing)
                db.executemany("INSERT INTO contributions(metric,ticker,entity,day,dimensions,value,valid_from,valid_to) VALUES(?,?,?,?,?,?,?,NULL)", inserting)
            # One positive settlement feeds both dashboard reductions. Only
            # small buckets, not per-Case contribution entities, are duplicated.
            for (metric,ticker,day,dimensions),delta in list(deltas.items()):
                if metric == 'policy_ar_hits':
                    dims = json.loads(dimensions)
                    policy = dims.get('policy_id')
                    if policy:
                        alias = ('policy_hits',ticker,day,encode({'business_key':policy}))
                        deltas[alias] = deltas.get(alias,Decimal(0)) + delta
            for key, delta in deltas.items():
                if not delta:
                    continue
                old = db.execute(
                    "SELECT value FROM metric_buckets_current WHERE metric=? AND ticker=? AND day=? "
                    "AND dimensions=?",
                    key,
                ).fetchone()
                value = (Decimal(old[0]) if old else Decimal(0)) + delta
                if old:self.close_current(db,'metric_buckets','metric=? AND ticker=? AND day=? AND dimensions=?',key,seq)
                db.execute(
                    "INSERT INTO metric_buckets(metric,ticker,day,dimensions,value,valid_from,valid_to) VALUES(?,?,?,?,?,?,NULL)",
                    (*key, format(value, "f"), seq),
                )
            if gap or resolved_gap:
                allocate()
            if gap:
                db.execute(
                    "INSERT OR REPLACE INTO gaps(source,event,reason) VALUES(?,?,?)",
                    (source, event, gap),
                )
            if resolved_gap:
                db.execute("DELETE FROM gaps WHERE source=? AND event=?", (source, resolved_gap))
            if position is not None:
                db.execute(
                    "INSERT INTO checkpoints VALUES(?,?) ON CONFLICT(source) DO UPDATE "
                    "SET position=MAX(position,excluded.position)",
                    (source, position),
                )
            for proof in proofs:
                data = dict(proof["data"])
                prior = db.execute("SELECT payload FROM capture_proofs WHERE source=?", (proof["id"],)).fetchone()
                old_proof = json.loads(prior[0]) if prior else {}
                closed = old_proof.get("closed_end_at") or (old_proof.get("end_at") if old_proof.get("complete") else None)
                if data.get("complete"):
                    closed = data.get("end_at")
                data["closed_end_at"] = closed
                db.execute("INSERT INTO capture_proofs VALUES(?,?,?) ON CONFLICT(source) DO UPDATE SET payload=excluded.payload,seq=excluded.seq", (proof["id"],encode(data),seq or self.highwater(db)))
            if not mutated and not gap and not resolved_gap:
                return self.highwater(db)
            db.execute("UPDATE read_meta SET value=? WHERE key='highwater'", (str(seq),))
            db.execute("UPDATE read_meta SET value=? WHERE key='first_at' AND value=''", (at or instant(datetime.now(UTC)),))
            db.execute("INSERT INTO commit_retention VALUES(?,?)", (seq, instant(datetime.now(UTC))))
            return seq

    @staticmethod
    def highwater(db: sqlite3.Connection) -> int:
        return int(db.execute("SELECT value FROM read_meta WHERE key='highwater'").fetchone()[0])

    def batch_get(self, requests, seq):
        """Bounded identity lookups in one connection and query; no payload history scan."""
        if len(requests) > 500:
            raise ValueError("identity batch exceeds 500")
        if not requests:
            return {}
        sql = "SELECT kind,ticker,id,payload FROM objects WHERE (kind,ticker,id) IN (SELECT json_extract(value,'$[0]'),json_extract(value,'$[1]'),json_extract(value,'$[2]') FROM json_each(?)) AND valid_from<=? AND (valid_to IS NULL OR valid_to>?)"
        sql = sql.replace("FROM objects", "FROM " + self.snapshot_table(seq))
        with self.connect() as db:
            return {(row[0],row[1],row[2]):json.loads(row[3]) for row in db.execute(sql,(encode(requests),seq,seq))}

    def source_scalars(self, kind, identities, seq):
        """Fixed safe scalar projections; exact identities in both MVCC branches."""
        fields = {
            "native:source_definitions": ("enabled",),
            "native:poll_states": ("status", "last_latency_ms"),
        }
        if kind not in fields or len(identities) > 500:
            raise ValueError("invalid scalar scope")
        if not identities:
            return []
        columns = "ticker,id," + ",".join("json_extract(payload,'$." + field + "')" for field in fields[kind])
        where = "kind=? AND (ticker,id) IN (SELECT json_extract(value,'$[0]'),json_extract(value,'$[1]') FROM json_each(?)) AND valid_from<=?"
        params = (kind, encode(identities), seq)
        with self.connect() as db:
            return db.execute("SELECT " + columns + " FROM object_current WHERE " + where +
                              " UNION ALL SELECT " + columns + " FROM objects WHERE " + where + " AND valid_to>?",
                              (*params, *params, seq)).fetchall()

    def latest_message_at(self, ticker, seq):
        return self.latest_messages_at([ticker], seq).get(ticker)

    def latest_messages_at(self, tickers, seq):
        """Choose thin sort candidates before reading one timestamp, never the body."""
        if len(tickers) > 100:
            raise ValueError("message timestamp batch exceeds 100")
        result = {}
        if not tickers:
            return result
        with self.connect() as db:
            from .serving_schema import history_relation
            history = history_relation(db)
            for ticker in dict.fromkeys(tickers):
                candidates = []
                for table, extra, parameters in (
                    ("object_current INDEXED BY current_page", "", (ticker, seq)),
                    (history + " INDEXED BY history_recent", " AND valid_to>?", (ticker, seq, seq)),
                ):
                    row = db.execute("SELECT id,sort_key,valid_from FROM " + table +
                                     " WHERE kind='message' AND ticker=? AND valid_from<=?" + extra +
                                     " ORDER BY sort_key DESC,id DESC LIMIT 1", parameters).fetchone()
                    if row:
                        candidates.append(tuple(row))
                if not candidates:
                    result[ticker] = None
                    continue
                identity, _, revision = max(candidates, key=lambda row: (row[1], row[0]))
                result[ticker] = db.execute("SELECT json_extract(payload,'$.stream_published_at') FROM objects "
                                           "WHERE kind='message' AND ticker=? AND id=? AND valid_from=?",
                                           (ticker, identity, revision)).fetchone()[0]
        return result

    def get(
        self, kind: str, ticker: str, identity: str, seq: int | None = None
    ) -> dict[str, Any] | None:
        if kind == "capture_coverage":
            from .query_budget import frozen_proofs
            frozen = frozen_proofs.get()
            if seq is not None and frozen is not None and frozen[0] == seq and frozen[1] is not None:
                return frozen[1].get(identity)
            with self.connect() as db:
                if seq is None or (frozen is None and seq == self.highwater(db)):
                    proof = db.execute("SELECT payload FROM capture_proofs WHERE source=?", (identity,)).fetchone()
                    if proof:
                        return json.loads(proof[0])
        with self.connect() as db:
            if seq is None:
                row = db.execute(
                    "SELECT payload FROM object_current WHERE kind=? AND ticker=? AND id=?",
                    (kind, ticker, identity),
                ).fetchone()
                return json.loads(row[0]) if row else None
            row = db.execute(
                "SELECT payload,valid_to FROM objects WHERE kind=? AND ticker=? AND id=? "
                "AND valid_from<=? ORDER BY valid_from DESC LIMIT 1",
                (kind, ticker, identity, seq),
            ).fetchone()
            return json.loads(row[0]) if row and (row[1] is None or row[1] > seq) else None

    def page(
        self,
        kind: str,
        ticker: str,
        seq: int,
        *,
        limit: int = 20,
        after: tuple[str, str] | None = None,
        parent: str | None = None,
        days: list[str] | None = None,
        source_id: str | None = None,
        source_kind: str | None = None,
        route: str | None = None,
        q: str | None = None,
        result: str | None = None,
        originating_node: str | None = None,
        available_for: str | None = None,
        live_bindings: bool = False,
        identity: str | None = None,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 101:
            raise ValueError("invalid limit")
        where = ["kind=?", "ticker=?", "valid_from<=?", "(valid_to IS NULL OR valid_to>?)"]
        params: list[Any] = [kind, ticker, seq, seq]
        if identity is not None:
            where.append("id=?")
            params.append(identity)
        if live_bindings:
            if kind != "native:ticker_source_bindings":
                raise ValueError("binding filter requires binding objects")
            where.append("json_extract(payload,'$.tombstoned_at') IS NULL")
        if available_for is not None:
            if kind != "native:source_definitions":
                raise ValueError("available source scope requires source definitions")
            where.extend(
                [
                    "json_extract(payload,'$.kind')='api'",
                    "json_extract(payload,'$.enabled')=1",
                    "NOT EXISTS (SELECT 1 FROM objects b "
                    "WHERE b.kind='native:ticker_source_bindings' "
                    "AND b.ticker=? AND b.id=?||':'||objects.id AND b.valid_from<=? "
                    "AND (b.valid_to IS NULL OR b.valid_to>?) "
                    "AND json_extract(b.payload,'$.tombstoned_at') IS NULL)",
                ]
            )
            params.extend([available_for, available_for, seq, seq])
        for column, value in (("parent", parent), ("source_id", source_id), ("route", route)):
            if value is not None:
                where.append(f"{column}=?")
                params.append(value)
        if days is not None:
            where.append("day IN (" + ",".join("?" for _ in days) + ")" if days else "0")
            params.extend(days)
        if q:
            # Historical Case summaries have no search_text. Search their title
            # directly, without requiring a backfill or including message bodies.
            where.append(
                "instr(casefold(json_extract(payload,'$.title.value')),?)>0"
                if kind == "case" else "instr(search_text,?)>0"
            )
            params.append(q.casefold())
        if originating_node is not None:
            where.append("json_extract(payload,'$.originating_node')=?")
            params.append(originating_node)
        if source_kind:
            where.append("json_extract(payload,'$.source.kind')=?")
            params.append(source_kind)
        if result in {"FIRST_ROUND_TRADE", "POLICY_RELATED_EVENT", "SUSPECTED_DUPLICATE_HIT"} and kind == "case":
            where.append("EXISTS (SELECT 1 FROM json_each(objects.payload,'$.first_round_tags') WHERE value=?)")
            params.append(result)
        elif result == "POLICY_HIT" and kind == "case":
            where.append("json_extract(objects.payload,'$.final_policy_hit.value')=1")
        elif result == "POLICY_RECALLED" and kind == "case":
            # Read the admitted Case's R1 evidence at the same view watermark.
            # A large reasoning field may externalize the entire round1 object.
            where.append(
                "EXISTS (SELECT 1 FROM objects recalled "
                "WHERE recalled.kind='native:runtime_v2_cases' "
                "AND recalled.ticker=objects.ticker AND recalled.id=objects.id "
                "AND recalled.valid_from<=? AND (recalled.valid_to IS NULL OR recalled.valid_to>?) "
                "AND policy_recalled(json_extract(recalled.payload,'$.w2_round1'))=1)"
            )
            params.extend([seq, seq])
        elif result:
            where.append(
                "EXISTS (SELECT 1 FROM json_each(objects.payload,'$.results') WHERE value=?)"
            )
            params.append(result)
        if after:
            where.append("(sort_key,id)<(?,?)")
            params.extend(after)
        with self.connect() as db:
            if q and kind == "case":
                db.create_function("casefold", 1, lambda text: (text or "").casefold(), deterministic=True)
            if result == "POLICY_RECALLED" and kind == "case":
                def policy_recalled(raw):
                    if raw is None:
                        return False
                    recall = self.content_codec.decode(json.loads(raw))
                    ids = recall.get("candidate_policy_ids") if isinstance(recall, dict) else None
                    return isinstance(ids, list) and bool(ids)

                db.create_function("policy_recalled", 1, policy_recalled, deterministic=True)
            # Disjoint branches apply predicates and keyset before their own LIMIT.
            from .serving_schema import history_relation
            history_name = history_relation(db)
            branches = ["object_current"] if seq == self.highwater(db) else ["object_current", history_name]
            rows = []
            for table in branches:
                history = " AND valid_to IS NOT NULL" if table == history_name else ""
                index = (
                    "objects_parent" if table == history_name else "current_parent"
                ) if parent is not None else (
                    "history_recent" if table == history_name else (
                        "current_page" if kind == "case" and identity is None else None
                    )
                )
                rows.extend(db.execute(
                    "SELECT id,sort_key,payload,valid_from FROM " + table + " AS objects" + (" INDEXED BY " + index if index else "") + " WHERE "
                    + " AND ".join(where) + history
                    + " ORDER BY sort_key DESC,id DESC LIMIT ?", [*params, limit]
                ).fetchall())
            rows = sorted(rows, key=lambda row: (row["sort_key"], row["id"]), reverse=True)[:limit]
            return [
                {
                    "id": r["id"],
                    "sort": r["sort_key"],
                    "revision": r["valid_from"],
                    "data": json.loads(r["payload"]),
                }
                for r in rows
            ]

    def put_content(self, ticker: str, body: str, kind: str = "text/plain") -> dict[str, Any]:
        raw = body.encode("utf-8")
        sha = hashlib.sha256(raw).hexdigest()
        identity = hashlib.sha256(f"{ticker}:{kind}:{sha}".encode()).hexdigest()
        from .content_files import ContentFiles
        ContentFiles(self.path.parent / "content-files").put(raw)
        with self.connect(write=True) as db:
            db.execute(
                "INSERT OR IGNORE INTO contents VALUES(?,?,?,?,?,?)",
                (identity, ticker, kind, sha, len(raw), b""),
            )
            db.execute("INSERT OR IGNORE INTO content_locations VALUES(?,?)", (identity, sha))
            from .artifact_registry import retain
            retain(db, identity, sha, len(raw), codec="utf8-chunks-v1", location="content-files/"+sha[:2]+"/"+sha,
                   owner_type="content", owner_id=identity)

        return {"content_id": identity, "content_type": kind, "size_bytes": len(raw), "sha256": sha}

    def content(
        self, ticker: str, identity: str, offset: int = 0, size: int = 120000
    ) -> tuple[dict[str, Any], str, int]:
        if offset < 0 or not 1 <= size <= 131072:
            raise ValueError("invalid chunk")
        with self.connect() as db:
            row = db.execute(
                "SELECT id,kind,sha256,size,substr(body,?,?) AS chunk FROM contents "
                "WHERE id=? AND ticker=?",
                (offset + 1, size, identity, ticker),
            ).fetchone()
            if row is None:
                raise KeyError(identity)
            raw = bytes(row["chunk"] or b"")
            location = db.execute("SELECT sha256 FROM content_locations WHERE id=?", (identity,)).fetchone()
            if location:
                from .content_files import ContentFiles
                raw = ContentFiles(self.path.parent / "content-files").read(location[0], offset, size)
            # Only trim a split trailing UTF-8 codepoint; next chunk starts at that byte.
            text = raw.decode("utf-8", errors="ignore")
            end = offset + len(text.encode("utf-8"))
            return (
                {
                    "content_id": identity,
                    "content_type": row["kind"],
                    "size_bytes": row["size"],
                    "sha256": row["sha256"],
                },
                text,
                end,
            )

    def save_token(
        self,
        owner: str,
        scope: str,
        payload: dict[str, Any],
        *,
        view: bool = False,
        now: datetime | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> str:
        identity, now = uuid4().hex, now or datetime.now(UTC)
        with (nullcontext(connection) if connection is not None else self.connect(write=True)) as db:
            retained = int(db.execute("SELECT value FROM read_meta WHERE key='retained_floor'").fetchone()[0])
            if "seq" in payload and payload["seq"] < retained:
                raise TimeoutError("VIEW_EXPIRED" if view else "CURSOR_EXPIRED")
            from .settings import Limits
            expires = now + timedelta(minutes=Limits.load().view_minutes if view else Limits.load().stream_minutes)
            if not view and payload.get("view_id"):
                parent = db.execute("SELECT expires_at FROM views WHERE id=? AND owner=?", (payload["view_id"],owner)).fetchone()
                if not parent or datetime.fromisoformat(parent[0]) <= now:
                    raise TimeoutError("VIEW_EXPIRED")
                expires = min(expires, datetime.fromisoformat(parent[0]))
            generation = db.execute("SELECT id FROM generation").fetchone()[0]
            identity = f"{generation}.{identity}.{int(expires.timestamp())}"
            if view:
                db.execute(
                    "INSERT INTO views VALUES(?,?,?,?,?,?)",
                    (
                        identity,
                        owner,
                        payload["seq"],
                        scope,
                        encode(payload),
                        instant(expires),
                    ),
                )
            else:
                db.execute(
                    "INSERT INTO cursors VALUES(?,?,?,?,?)",
                    (identity, owner, scope, encode(payload), instant(expires)),
                )
        return identity

    def token(
        self,
        identity: str,
        owner: str,
        *,
        view: bool = False,
        scope: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        with self.connect() as db:
            parts = identity.split(".")
            if (
                len(parts) == 3
                and len(parts[0]) == 32
                and len(parts[1]) == 32
                and parts[2].isdigit()
            ):
                generation = db.execute("SELECT id FROM generation").fetchone()[0]
                if (
                    parts[0] != generation
                    or int(parts[2]) <= (now or datetime.now(UTC)).timestamp()
                ):
                    raise TimeoutError("VIEW_EXPIRED" if view else "CURSOR_EXPIRED")
            table = "views" if view else "cursors"
            row = db.execute(
                f"SELECT * FROM {table} WHERE id=? AND owner=?", (identity, owner)
            ).fetchone()
            if not row:
                raise ValueError("INVALID_VIEW" if view else "INVALID_CURSOR")
            if row["expires_at"] <= instant(now or datetime.now(UTC)):
                raise TimeoutError("VIEW_EXPIRED" if view else "CURSOR_EXPIRED")
            if scope is not None and row["scope"] != scope:
                raise ValueError("SCOPE_MISMATCH")
            return json.loads(row["payload"])

    def changes(
        self, ticker: str, kinds: list[str], after: int, limit: int = 100
    ) -> list[dict[str, Any]]:
        with self.connect() as db:
            # Whole commits are atomic; limit commit IDs, never cut a multi-object commit.
            rows = db.execute(
                "SELECT seq,ordinal,kind,ticker,id,NULL AS before_payload,NULL AS after_payload FROM changes WHERE ticker=? AND seq IN "
                "(SELECT DISTINCT seq FROM changes WHERE ticker=? AND seq>? AND kind IN ("
                + ",".join("?" for _ in kinds)
                + ") ORDER BY seq LIMIT ?) AND kind IN ("
                + ",".join("?" for _ in kinds)
                + ") ORDER BY seq,ordinal",
                (ticker, ticker, after, *kinds, limit, *kinds),
            )
            result = []
            size = 0
            for row in rows:
                item = dict(row)
                # Legacy stream payloads are never loaded; business rows are resolved by identity.
                size += len(encode(item).encode())
                if len(result) >= 512 or size > 262144:
                    from doxagent.api_v2.errors import ApiFailure
                    raise ApiFailure("BASELINE_UNAVAILABLE", 410)
                result.append(item)
            return result
