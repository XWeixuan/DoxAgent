"""Runtime-owned durable tasks, inbox and snapshots; no initialization-wide lock.

Each mutation is a short SQLite transaction. Payloads are local-only and immutable
inputs are separate from mutable receipts. A reclaimed lease fences late writers.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4


def encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


class LeaseLost(RuntimeError):
    pass


class RuntimeJournal:
    VERSION = 1

    def __init__(self, path: str | Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: datetime.now(UTC))
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS orchestration_meta (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS runtime_tasks (
                    id TEXT PRIMARY KEY, ticker TEXT NOT NULL, kind TEXT NOT NULL,
                    status TEXT NOT NULL, inputs TEXT NOT NULL, receipt TEXT NOT NULL,
                    generation INTEGER NOT NULL DEFAULT 1, failures INTEGER NOT NULL DEFAULT 0,
                    max_failures INTEGER NOT NULL DEFAULT 2, owner TEXT, token INTEGER DEFAULT 0,
                    lease_until TEXT, due_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS runtime_tasks_due ON runtime_tasks(kind,status,due_at);
                CREATE INDEX IF NOT EXISTS runtime_tasks_ticker_status
                    ON runtime_tasks(ticker,status,kind);
                CREATE INDEX IF NOT EXISTS runtime_tasks_sweep
                    ON runtime_tasks(json_extract(inputs,'$.sweep_id'),kind);
                CREATE TABLE IF NOT EXISTS runtime_gaps (
                    id TEXT PRIMARY KEY, task_id TEXT, ticker TEXT, code TEXT, detail TEXT,
                    closed INTEGER DEFAULT 0, created_at TEXT);
                CREATE TABLE IF NOT EXISTS runtime_values (
                    namespace TEXT, key TEXT, payload TEXT NOT NULL,
                    PRIMARY KEY(namespace,key));
                CREATE TABLE IF NOT EXISTS runtime_snapshots (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            """)
            row = db.execute("SELECT value FROM orchestration_meta WHERE key='version'").fetchone()
            if row and int(row[0]) != self.VERSION:
                raise ValueError("unsupported orchestration schema; migration required")
            db.execute(
                "INSERT OR IGNORE INTO orchestration_meta VALUES('version',?)", (str(self.VERSION),)
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _task(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["inputs"] = json.loads(result["inputs"])
        result["receipt"] = json.loads(result["receipt"])
        return result

    def put_task(
        self,
        identity: str,
        ticker: str,
        kind: str,
        inputs: dict[str, Any],
        *,
        due_at: datetime | None = None,
        max_failures: int = 2,
    ) -> dict[str, Any]:
        now = self.clock().isoformat()
        with self.transaction() as db:
            old = db.execute("SELECT * FROM runtime_tasks WHERE id=?", (identity,)).fetchone()
            if old:
                if (
                    old["ticker"] != ticker
                    or old["kind"] != kind
                    or old["inputs"] != encode(inputs)
                ):
                    raise ValueError("immutable task identity conflict")
                return self._task(old)
            db.execute(
                "INSERT INTO runtime_tasks(id,ticker,kind,status,inputs,receipt,max_failures,"
                "due_at,updated_at) VALUES(?,?,?,'PENDING',?,'{}',?,?,?)",
                (
                    identity,
                    ticker,
                    kind,
                    encode(inputs),
                    max_failures,
                    (due_at or self.clock()).isoformat(),
                    now,
                ),
            )
            return self._task(
                db.execute("SELECT * FROM runtime_tasks WHERE id=?", (identity,)).fetchone()
            )

    def get_task(self, identity: str) -> dict[str, Any] | None:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM runtime_tasks WHERE id=?", (identity,)).fetchone()
            return self._task(row) if row else None

    def tasks(
        self,
        *,
        ticker: str | None = None,
        kind: str | None = None,
        active_only: bool = False,
        sweep_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self.transaction() as db:
            rows = db.execute(
                "SELECT * FROM runtime_tasks WHERE (? IS NULL OR ticker=?) "
                "AND (? IS NULL OR kind=?) "
                "AND (?=0 OR status IN ('PENDING','RUNNING')) "
                "AND (? IS NULL OR json_extract(inputs,'$.sweep_id')=?) ORDER BY due_at,id",
                (ticker, ticker, kind, kind, int(active_only), sweep_id, sweep_id),
            ).fetchall()
            return [self._task(row) for row in rows]

    def claim(self, identity: str, *, seconds: float = 1800) -> dict[str, Any] | None:
        now = self.clock()
        with self.transaction() as db:
            row = db.execute("SELECT * FROM runtime_tasks WHERE id=?", (identity,)).fetchone()
            if not row or row["status"] in {"SUCCEEDED", "FAILED", "HELD"}:
                return None
            if row["due_at"] > now.isoformat():
                return None
            if row["status"] == "RUNNING" and row["lease_until"] > now.isoformat():
                return None
            cap = {"SWEEP": 1, "MAINTENANCE": 1, "SELECTION": 1, "CASE": 4}.get(row["kind"])
            if (
                cap
                and db.execute(
                    "SELECT COUNT(*) FROM runtime_tasks WHERE ticker=? AND kind=? "
                    "AND status='RUNNING' AND lease_until>? AND id<>?",
                    (row["ticker"], row["kind"], now.isoformat(), identity),
                ).fetchone()[0]
                >= cap
            ):
                return None
            db.execute(
                "UPDATE runtime_tasks SET status='RUNNING',owner=?,token=token+1,"
                "lease_until=?,updated_at=? WHERE id=?",
                (
                    uuid4().hex,
                    (now + timedelta(seconds=seconds)).isoformat(),
                    now.isoformat(),
                    identity,
                ),
            )
            return self._task(
                db.execute("SELECT * FROM runtime_tasks WHERE id=?", (identity,)).fetchone()
            )

    def fence(self, db: sqlite3.Connection, task: dict[str, Any]) -> sqlite3.Row:
        row = db.execute("SELECT * FROM runtime_tasks WHERE id=?", (task["id"],)).fetchone()
        if (
            not row
            or row["status"] != "RUNNING"
            or row["token"] != task["token"]
            or row["owner"] != task["owner"]
            or row["lease_until"] <= self.clock().isoformat()
        ):
            raise LeaseLost(task["id"])
        return cast(sqlite3.Row, row)

    def renew(self, task: dict[str, Any], seconds: float = 120) -> None:
        with self.transaction() as db:
            self.fence(db, task)
            db.execute(
                "UPDATE runtime_tasks SET lease_until=?,updated_at=? WHERE id=?",
                (
                    (self.clock() + timedelta(seconds=seconds)).isoformat(),
                    self.clock().isoformat(),
                    task["id"],
                ),
            )

    def checkpoint(self, task: dict[str, Any], **receipt: Any) -> None:
        with self.transaction() as db:
            row = self.fence(db, task)
            value = {**json.loads(row["receipt"]), **receipt}
            db.execute(
                "UPDATE runtime_tasks SET receipt=?,updated_at=? WHERE id=?",
                (encode(value), self.clock().isoformat(), task["id"]),
            )
            task["receipt"] = value

    def finish(self, task: dict[str, Any], **receipt: Any) -> None:
        with self.transaction() as db:
            row = self.fence(db, task)
            value = {**json.loads(row["receipt"]), **receipt}
            db.execute(
                "UPDATE runtime_tasks SET status='SUCCEEDED',receipt=?,updated_at=? WHERE id=?",
                (encode(value), self.clock().isoformat(), task["id"]),
            )
            db.execute("UPDATE runtime_gaps SET closed=1 WHERE task_id=?", (task["id"],))

    def fail(self, task: dict[str, Any], error: Exception, *, retryable: bool = True) -> None:
        with self.transaction() as db:
            row = self.fence(db, task)
            count = row["failures"] + 1
            status = "PENDING" if retryable and count < row["max_failures"] else "FAILED"
            db.execute(
                "UPDATE runtime_tasks SET status=?,failures=?,due_at=?,updated_at=? WHERE id=?",
                (
                    status,
                    count,
                    (self.clock() + timedelta(seconds=5)).isoformat(),
                    self.clock().isoformat(),
                    task["id"],
                ),
            )
            self._gap(db, task["id"], task["ticker"], type(error).__name__, str(error)[:1000])

    def _gap(
        self, db: sqlite3.Connection, identity: str, ticker: str, code: str, detail: str
    ) -> None:
        db.execute(
            "INSERT INTO runtime_gaps VALUES(?,?,?,?,?,0,?) ON CONFLICT(id) DO UPDATE "
            "SET code=excluded.code,detail=excluded.detail,closed=0",
            (identity, identity, ticker, code, detail, self.clock().isoformat()),
        )

    def gap(self, identity: str, ticker: str, code: str, detail: str = "") -> None:
        with self.transaction() as db:
            self._gap(db, identity, ticker, code, detail)

    def gaps(self, ticker: str | None = None) -> list[dict[str, Any]]:
        with self.transaction() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM runtime_gaps WHERE (? IS NULL OR ticker=?)", (ticker, ticker)
                )
            ]

    def resume(self, identity: str, reason: str) -> None:
        if not reason.strip():
            raise ValueError("resume reason required")
        with self.transaction() as db:
            row = db.execute("SELECT * FROM runtime_tasks WHERE id=?", (identity,)).fetchone()
            if not row or row["status"] != "FAILED":
                raise ValueError("only failed tasks may be resumed")
            receipt = {**json.loads(row["receipt"]), "resume_reason": reason}
            db.execute(
                "UPDATE runtime_tasks SET status='PENDING',generation=generation+1,"
                "failures=0,receipt=?,due_at=? WHERE id=?",
                (encode(receipt), self.clock().isoformat(), identity),
            )

    def get(self, namespace: str, key: str, default: Any = None) -> Any:
        with self.transaction() as db:
            row = db.execute(
                "SELECT payload FROM runtime_values WHERE namespace=? AND key=?", (namespace, key)
            ).fetchone()
            return json.loads(row[0]) if row else default

    def set(self, namespace: str, key: str, value: Any) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO runtime_values VALUES(?,?,?) ON CONFLICT(namespace,key) "
                "DO UPDATE SET payload=excluded.payload",
                (namespace, key, encode(value)),
            )

    def delete(self, namespace: str, key: str) -> None:
        with self.transaction() as db:
            db.execute("DELETE FROM runtime_values WHERE namespace=? AND key=?", (namespace, key))

    def values(self, namespace: str) -> list[Any]:
        with self.transaction() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM runtime_values WHERE namespace=? ORDER BY key",
                    (namespace,),
                )
            ]

    def freeze(self, value: Any) -> str:
        identity = digest(value)
        with self.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO runtime_snapshots VALUES(?,?)", (identity, encode(value))
            )
        return identity

    def snapshot(self, identity: str) -> Any:
        with self.transaction() as db:
            row = db.execute(
                "SELECT payload FROM runtime_snapshots WHERE id=?", (identity,)
            ).fetchone()
            if row is None or digest(json.loads(row[0])) != identity:
                raise ValueError("immutable runtime snapshot unavailable or corrupted")
            return json.loads(row[0])
