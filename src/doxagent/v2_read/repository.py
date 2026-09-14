"""MVCC representations and durable change log without long-lived SQLite readers."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def instant(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


class ReadStore:
    VERSION = 3

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
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

    @contextmanager
    def connect(self, *, write: bool = False, timeout: float = 2) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(
            self.path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=timeout
        )
        db.row_factory = self.content_codec.row
        if not write:
            from .query_budget import interrupted
            db.set_progress_handler(interrupted, 1000)
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
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

    def metric_table(self, seq, table="metric_buckets"):
        if table not in {"metric_buckets", "contributions"}:
            raise ValueError("invalid metric relation")
        seq = int(seq)
        with self.connect() as db:
            if seq == self.highwater(db):
                return table + "_current"
        return f"(SELECT * FROM {table}_current WHERE valid_from<={seq} UNION ALL SELECT * FROM {table} WHERE valid_to IS NOT NULL AND valid_from<={seq} AND valid_to>{seq})"

    def snapshot_table(self, seq):
        """Disjoint hot/history relation; SQLite pushes caller filters into each branch."""
        seq = int(seq)
        with self.connect() as db:
            if seq == self.highwater(db):
                return "object_current"
        return f"(SELECT * FROM object_current WHERE valid_from<={seq} UNION ALL SELECT * FROM objects INDEXED BY history_recent WHERE valid_to IS NOT NULL AND valid_from<={seq} AND valid_to>{seq})"

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
    ) -> int:
        proofs = [record for record in records if record["kind"] == "capture_coverage"]
        records = [record for record in records if record["kind"] != "capture_coverage"]
        unique: dict[tuple[str, str, str], dict[str, Any]] = {}
        for record in records:
            key = (record["kind"], record["ticker"], record["id"])
            if key in unique and unique[key] != record:
                raise ValueError("conflicting representations in one source receipt")
            unique[key] = record
        records = list(unique.values())
        with self.connect(write=True) as db:
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
                    "SELECT payload,sort_key,parent,day,source_id,route,search_text FROM object_current WHERE kind=? AND ticker=? AND id=? "
                    "AND valid_to IS NULL",
                    (kind, ticker, identity),
                ).fetchone()
                payload = record.get("data")
                previous = json.loads(old[0]) if old else None
                comparable = dict(payload) if isinstance(payload, dict) else payload
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
                db.execute(
                    "UPDATE objects SET valid_to=? WHERE kind=? AND ticker=? AND id=? "
                    "AND valid_to IS NULL",
                    (seq, kind, ticker, identity),
                )
                payload = record.get("data")
                if payload is not None and kind == "message":
                    payload = {**payload, "row_revision": seq}
                elif payload is not None and kind == "case":
                    payload = {**payload, "revision": seq}
                if payload is not None:
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
                    expected[member] = amount
                old_values = db.execute("SELECT day,dimensions,value FROM contributions WHERE metric=? AND ticker=? AND entity=? AND valid_to IS NULL", base).fetchall()
                previous = {(row[0], row[1]): Decimal(row[2]) for row in old_values}
                if previous == expected:
                    continue
                mutated = True
                allocate()
                for (day, dimensions), amount in previous.items():
                    for bucket_day in (day, "*"):
                        bucket = (base[0], base[1], bucket_day, dimensions)
                        deltas[bucket] = deltas.get(bucket, Decimal(0)) - amount
                db.execute("UPDATE contributions SET valid_to=? WHERE metric=? AND ticker=? AND entity=? AND valid_to IS NULL", (seq, *base))
                for (day, dimensions), amount in expected.items():
                    for bucket_day in (day, "*"):
                        bucket = (base[0], base[1], bucket_day, dimensions)
                        deltas[bucket] = deltas.get(bucket, Decimal(0)) + amount
                    db.execute("INSERT INTO contributions VALUES(?,?,?,?,?,?,?,NULL)", (*base, day, dimensions, format(amount, "f"), seq))
            for key, delta in deltas.items():
                if not delta:
                    continue
                old = db.execute(
                    "SELECT value FROM metric_buckets WHERE metric=? AND ticker=? AND day=? "
                    "AND dimensions=? AND valid_to IS NULL",
                    key,
                ).fetchone()
                value = (Decimal(old[0]) if old else Decimal(0)) + delta
                db.execute(
                    "UPDATE metric_buckets SET valid_to=? WHERE metric=? AND ticker=? AND day=? "
                    "AND dimensions=? AND valid_to IS NULL",
                    (seq, *key),
                )
                db.execute(
                    "INSERT INTO metric_buckets VALUES(?,?,?,?,?,?,NULL)",
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
            where.append("instr(search_text,?)>0")
            params.append(q.casefold())
        if source_kind:
            where.append("json_extract(payload,'$.source.kind')=?")
            params.append(source_kind)
        if result:
            where.append(
                "EXISTS (SELECT 1 FROM json_each(objects.payload,'$.results') WHERE value=?)"
            )
            params.append(result)
        if after:
            where.append("(sort_key,id)<(?,?)")
            params.extend(after)
        with self.connect() as db:
            # Disjoint branches apply predicates and keyset before their own LIMIT.
            branches = ["object_current"] if seq == self.highwater(db) else ["object_current", "objects"]
            rows = []
            for table in branches:
                history = " AND valid_to IS NOT NULL" if table == "objects" else ""
                rows.extend(db.execute(
                    "SELECT id,sort_key,payload,valid_from FROM " + table + " AS objects" + (" INDEXED BY history_recent" if table == "objects" else "") + " WHERE "
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
            expires = now + timedelta(days=1)
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
