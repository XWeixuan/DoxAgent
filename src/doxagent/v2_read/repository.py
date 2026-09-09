"""MVCC representations and durable change log without long-lived SQLite readers."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
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
    VERSION = 2

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

    @contextmanager
    def connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(
            self.path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=2
        )
        db.row_factory = sqlite3.Row
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
                CREATE TABLE IF NOT EXISTS source_health (source TEXT PRIMARY KEY,
                    head INTEGER, checkpoint INTEGER, checked_at TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS generation (id TEXT PRIMARY KEY);
            """)
            db.execute("UPDATE schema_meta SET version=?", (self.VERSION,))
            if not db.execute("SELECT 1 FROM generation").fetchone():
                db.execute("INSERT INTO generation VALUES(?)", (uuid4().hex,))
            if [r[0] for r in db.execute("SELECT version FROM schema_meta")] != [self.VERSION]:
                raise ValueError("unsupported V2 read schema")

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
            cursor = db.execute(
                "INSERT INTO commits(source,source_event,at) VALUES(?,?,?)",
                (source, event, at or instant(datetime.now(UTC))),
            )
            seq = int(cursor.lastrowid or 0)
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
                    "SELECT payload FROM objects WHERE kind=? AND ticker=? AND id=? "
                    "AND valid_to IS NULL",
                    (kind, ticker, identity),
                ).fetchone()
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
                            encode(payload),
                        ),
                    )
                db.execute(
                    "INSERT INTO changes VALUES(?,?,?,?,?,?,?)",
                    (
                        seq,
                        ordinal,
                        kind,
                        ticker,
                        identity,
                        old[0] if old else None,
                        encode(payload) if payload is not None else None,
                    ),
                )
            deltas: dict[tuple[str, str, str, str], Decimal] = {}
            for item in contributions or []:
                identity = (source, item["metric"], item["ticker"], item["entity"])
                if position is not None:
                    last = db.execute(
                        "SELECT position FROM contribution_watermarks WHERE source=? "
                        "AND metric=? AND ticker=? AND entity=?",
                        identity,
                    ).fetchone()
                    if last and last[0] >= position:
                        continue
                    db.execute(
                        "INSERT INTO contribution_watermarks VALUES(?,?,?,?,?) "
                        "ON CONFLICT(source,metric,ticker,entity) DO UPDATE "
                        "SET position=excluded.position",
                        (*identity, position),
                    )
                dimensions = encode(item.get("dimensions", {}))
                key = (item["metric"], item["ticker"], item["entity"], item["day"], dimensions)
                old_values = db.execute(
                    "SELECT day,dimensions,value FROM contributions WHERE metric=? AND ticker=? "
                    "AND entity=? AND valid_to IS NULL",
                    key[:3],
                ).fetchall()
                for old in old_values:
                    for day in (old[0], "*"):
                        bucket = (item["metric"], item["ticker"], day, old[1])
                        deltas[bucket] = deltas.get(bucket, Decimal(0)) - Decimal(old[2])
                db.execute(
                    "UPDATE contributions SET valid_to=? WHERE metric=? AND ticker=? "
                    "AND entity=? AND valid_to IS NULL",
                    (seq, *key[:3]),
                )
                if item.get("value") is not None:
                    amount = Decimal(str(item["value"]))
                    if not amount.is_finite():
                        raise ValueError("nonfinite contribution")
                    for day in (item["day"], "*"):
                        bucket = (item["metric"], item["ticker"], day, dimensions)
                        deltas[bucket] = deltas.get(bucket, Decimal(0)) + amount
                    db.execute(
                        "INSERT INTO contributions VALUES(?,?,?,?,?,?,?,NULL)",
                        (*key, str(item["value"]), seq),
                    )
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
            return seq

    @staticmethod
    def highwater(db: sqlite3.Connection) -> int:
        return int(db.execute("SELECT COALESCE(MAX(seq),0) FROM commits").fetchone()[0])

    def get(
        self, kind: str, ticker: str, identity: str, seq: int | None = None
    ) -> dict[str, Any] | None:
        with self.connect() as db:
            seq = self.highwater(db) if seq is None else seq
            row = db.execute(
                "SELECT payload FROM objects WHERE kind=? AND ticker=? AND id=? "
                "AND valid_from<=? AND (valid_to IS NULL OR valid_to>?)",
                (kind, ticker, identity, seq, seq),
            ).fetchone()
            return json.loads(row[0]) if row else None

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
            rows = db.execute(
                "SELECT id,sort_key,payload,valid_from FROM objects WHERE "
                + " AND ".join(where)
                + " ORDER BY sort_key DESC,id DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
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
        with self.connect(write=True) as db:
            db.execute(
                "INSERT OR IGNORE INTO contents VALUES(?,?,?,?,?,?)",
                (identity, ticker, kind, sha, len(raw), raw),
            )
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
            raw = bytes(row["chunk"])
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
    ) -> str:
        identity, now = uuid4().hex, now or datetime.now(UTC)
        with self.connect(write=True) as db:
            generation = db.execute("SELECT id FROM generation").fetchone()[0]
            identity = f"{generation}.{identity}.{int((now + timedelta(days=1)).timestamp())}"
            if view:
                db.execute(
                    "INSERT INTO views VALUES(?,?,?,?,?,?)",
                    (
                        identity,
                        owner,
                        payload["seq"],
                        scope,
                        encode(payload),
                        instant(now + timedelta(days=1)),
                    ),
                )
            else:
                db.execute(
                    "INSERT INTO cursors VALUES(?,?,?,?,?)",
                    (identity, owner, scope, encode(payload), instant(now + timedelta(days=1))),
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
                "SELECT * FROM changes WHERE ticker=? AND seq IN "
                "(SELECT DISTINCT seq FROM changes WHERE ticker=? AND seq>? AND kind IN ("
                + ",".join("?" for _ in kinds)
                + ") ORDER BY seq LIMIT ?) AND kind IN ("
                + ",".join("?" for _ in kinds)
                + ") ORDER BY seq,ordinal",
                (ticker, ticker, after, *kinds, limit, *kinds),
            )
            return [dict(r) for r in rows]
