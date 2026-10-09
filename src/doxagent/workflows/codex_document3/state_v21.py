"""Short SQLite transactions for V21 authority and replayable task budgets."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(
        (value if isinstance(value, str) else canonical(value)).encode()
    ).hexdigest()


class StateV21:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS codex_document3_v21_runs
              (run_id TEXT PRIMARY KEY, identity TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS codex_document3_v21_tasks
              (run_id TEXT NOT NULL, task_key TEXT NOT NULL, payload TEXT NOT NULL,
               PRIMARY KEY(run_id,task_key));
            CREATE TABLE IF NOT EXISTS codex_document3_v21_draft_ids
              (run_id TEXT NOT NULL,path TEXT NOT NULL,payload TEXT NOT NULL,
               PRIMARY KEY(run_id,path));
            CREATE TABLE IF NOT EXISTS codex_document3_v21_staged_sets
              (ticker TEXT NOT NULL,version INTEGER NOT NULL,run_id TEXT UNIQUE NOT NULL,
               payload TEXT NOT NULL,receipt TEXT NOT NULL,PRIMARY KEY(ticker,version));
            CREATE TABLE IF NOT EXISTS codex_document3_version_reservations
              (run_id TEXT PRIMARY KEY,ticker TEXT NOT NULL,version INTEGER NOT NULL,
               UNIQUE(ticker,version));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def start(self, run_id, identity, payload):
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM codex_document3_v21_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            key = canonical(identity)
            if row:
                if row["identity"] != key:
                    raise ValueError("V21 replay input identity mismatch")
                return json.loads(row["payload"])
            db.execute(
                "INSERT INTO codex_document3_v21_runs VALUES(?,?,?)",
                (run_id, key, canonical(payload)),
            )
            return payload

    def run(self, run_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM codex_document3_v21_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            return json.loads(row[0]) if row else None

    def update(self, run_id, **values):
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM codex_document3_v21_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            payload = json.loads(row[0])
            payload.update(values)
            db.execute(
                "UPDATE codex_document3_v21_runs SET payload=? WHERE run_id=?",
                (canonical(payload), run_id),
            )
            return payload

    def task(self, run_id, key):
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM codex_document3_v21_tasks WHERE run_id=? AND task_key=?",
                (run_id, key),
            ).fetchone()
            return json.loads(row[0]) if row else {"status": "PENDING", "attempt_count": 0}

    def save_task(self, run_id, key, payload):
        with self.lock, self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO codex_document3_v21_tasks VALUES(?,?,?)",
                (run_id, key, canonical(payload)),
            )

    def tasks(self, run_id):
        with self.connect() as db:
            return {
                row[0]: json.loads(row[1])
                for row in db.execute(
                    "SELECT task_key,payload FROM codex_document3_v21_tasks WHERE run_id=?",
                    (run_id,),
                )
            }

    def claim(self, run_id, key, *, max_attempts=2):
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM codex_document3_v21_tasks WHERE run_id=? AND task_key=?",
                (run_id, key),
            ).fetchone()
            item = json.loads(row[0]) if row else {"attempt_count": 0, "status": "PENDING"}
            if (
                item["status"] in {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}
                or item["attempt_count"] >= max_attempts
            ):
                return None
            item.update(status="RUNNING", attempt_count=item["attempt_count"] + 1)
            db.execute(
                "INSERT OR REPLACE INTO codex_document3_v21_tasks VALUES(?,?,?)",
                (run_id, key, canonical(item)),
            )
            return item

    def drafts(self, run_id):
        with self.connect() as db:
            return {
                r[0]: json.loads(r[1])
                for r in db.execute(
                    "SELECT path,payload FROM codex_document3_v21_draft_ids WHERE run_id=?",
                    (run_id,),
                )
            }

    def save_draft(self, run_id, path, value):
        with self.lock, self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO codex_document3_v21_draft_ids VALUES(?,?,?)",
                (run_id, path, canonical(value)),
            )

    def get_draft(self, run_id, path):
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM codex_document3_v21_draft_ids WHERE run_id=? AND path=?",
                (run_id, path),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def maximum_condition(self, policy_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT MAX(CAST(json_extract(payload, '$.max_condition') AS INTEGER)) "
                "FROM codex_document3_v21_draft_ids "
                "WHERE json_extract(payload, '$.policy.policy_id')=?",
                (policy_id,),
            ).fetchone()
            return row[0] or 0

    def reserve(self, run_id, ticker, floor=0):
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT ticker,version FROM codex_document3_version_reservations WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if row:
                if row[0] != ticker:
                    raise ValueError("reservation ticker mismatch")
                return row[1]
            versions = [floor]
            for table in [
                "codex_document3_version_reservations",
                "codex_document3_v21_staged_sets",
                "codex_document3_policy_sets",
            ]:
                if db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone():
                    column = (
                        "policy_set_version"
                        if table == "codex_document3_policy_sets"
                        else "version"
                    )
                    row = db.execute(
                        f"SELECT MAX({column}) FROM {table} WHERE ticker=?", (ticker,)
                    ).fetchone()
                    versions.append(row[0] or 0)
            version = max(versions) + 1
            db.execute(
                "INSERT INTO codex_document3_version_reservations VALUES(?,?,?)",
                (run_id, ticker, version),
            )
            return version

    def save_staged_v3(self, run_id, policy_set, receipt):
        data = policy_set.model_dump(mode="json")
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT payload,receipt FROM codex_document3_v21_staged_sets WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if old:
                if old[0] != canonical(data) or old[1] != canonical(receipt):
                    raise ValueError("immutable staged commit mismatch")
                return
            db.execute(
                "INSERT INTO codex_document3_v21_staged_sets VALUES(?,?,?,?,?)",
                (
                    data["ticker"],
                    data["policy_set_version"],
                    run_id,
                    canonical(data),
                    canonical(receipt),
                ),
            )

    def get_staged_v3(self, ticker, version):
        from .schema_v21 import PolicySetV3

        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM codex_document3_v21_staged_sets WHERE ticker=? AND version=?",
                (ticker.upper(), version),
            ).fetchone()
            return PolicySetV3.model_validate_json(row[0]) if row else None

    def get_staged_by_run(self, run_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT payload,receipt FROM codex_document3_v21_staged_sets WHERE run_id=?",
                (run_id,),
            ).fetchone()
            return (json.loads(row[0]), json.loads(row[1])) if row else None
