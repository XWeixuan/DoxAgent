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
        from doxagent.v2_read.native_content import NativeContent
        self.codec = NativeContent(path)
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
            from doxagent.v2_read.artifact_registry import migrate, native_triggers
            migrate(db)
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_artifact_codex_document3_v21_runs_INSERT'").fetchone():
                native_triggers(db, 'codex_document3_v21_runs', ['payload'], 'json_array(NEW.run_id)')

    def _pack(self, values):
        # Existing immutable Native CAS; unchanged large fields stay as references.
        if (Path(self.path).parent/'.d3-native-inputs-disabled').exists():return values
        large = {'prepared', 'base', 'base_coverage', 'final', 'final_candidates', 'handoff'}
        return {key: json.loads(self.codec.encode({key: value}))[key] if key in large else value
                for key, value in values.items()}

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
        stored = self._pack(payload)
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM codex_document3_v21_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            key = canonical(identity)
            if row:
                if row["identity"] != key:
                    raise ValueError("V21 replay input identity mismatch")
                existing = self.codec.decode(json.loads(row["payload"]))
                if existing.get('resource_retirement'):
                    raise ValueError('D3_RUN_RETIRED_NEW_RUN_REQUIRED')
                return existing
            db.execute(
                "INSERT INTO codex_document3_v21_runs VALUES(?,?,?)",
                (run_id, key, canonical(stored)),
            )
            return payload

    def run(self, run_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM codex_document3_v21_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            stored = json.loads(row[0]) if row else None
        value = self.codec.decode(stored) if stored is not None else None
        if value and value.get('resource_retirement'):
            staged = self.get_staged_by_run(run_id)
            if staged:
                value.setdefault('final', staged[0].get('policies', []))
                value.setdefault('handoff', staged[1])
        return value

    def update(self, run_id, **values):
        packed = self._pack(values)
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM codex_document3_v21_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            payload = json.loads(row[0])
            if payload.get('resource_retirement'):
                raise ValueError('D3_RUN_RETIRED_NEW_RUN_REQUIRED')
            payload.update(packed)
            db.execute(
                "UPDATE codex_document3_v21_runs SET payload=? WHERE run_id=?",
                (canonical(payload), run_id),
            )
        return self.codec.decode(payload)

    def settle(self, run_id, *, at=None, authority):
        """Publication/activation is independent of COMPLETE vs PARTIAL quality."""
        from datetime import UTC, datetime
        with self.connect() as db:
            row = db.execute('SELECT payload FROM codex_document3_v21_runs WHERE run_id=?', (run_id,)).fetchone()
            if not row:
                return
            value = json.loads(row[0])
            if value.get('settlement'):
                return
            value['settlement'] = {'at': at or datetime.now(UTC).isoformat(), 'authority': authority}
            db.execute('UPDATE codex_document3_v21_runs SET payload=? WHERE run_id=?', (canonical(value), run_id))

    def retire(self, run_id, *, now, authority):
        """Caller fences live business/jobs; retain exact staged version and audit identity."""
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM codex_document3_v21_runs WHERE run_id=?', (run_id,)).fetchone()
            staged = db.execute('SELECT payload,receipt FROM codex_document3_v21_staged_sets WHERE run_id=?', (run_id,)).fetchone()
            if not row or not staged:
                return None
            value = json.loads(row[0])
            if value.get('resource_retirement'):
                return value['resource_retirement']
            before = len(row[0].encode())
            exiting=[r[0] for r in db.execute("SELECT artifact_id FROM artifact_ref WHERE owner_type='codex_document3_v21_runs' AND owner_id=json_array(?)",(run_id,))]
            removed = {}
            for key in ('prepared', 'base', 'base_coverage', 'final', 'final_candidates', 'handoff',
                        'shell_discovery_input', 'open_discovery_input'):
                if key in value:
                    removed[key] = digest(value.pop(key))
            receipt = {'at': now.isoformat(), 'authority': authority, 'resume_allowed': False,
                       'fields_sha256': removed, 'before_json_bytes': before}
            value['resource_retirement'] = receipt
            raw = canonical(value)
            receipt['after_json_bytes'] = len(raw.encode())
            db.execute('UPDATE codex_document3_v21_runs SET payload=? WHERE run_id=?', (canonical(value), run_id))
            # Explicit owner exit uses the existing quarantine queue, not the 7d orphan-put grace.
            from doxagent.v2_read.source_native_gc import queue_owner_exit
            queue_owner_exit(db,exiting)
        return receipt

    def task(self, run_id, key):
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM codex_document3_v21_tasks WHERE run_id=? AND task_key=?",
                (run_id, key),
            ).fetchone()
            return json.loads(row[0]) if row else {"status": "PENDING", "attempt_count": 0}

    def io_recovery(self, run_id, key, value=None, *, success=False):
        """Merge a single operation under a short write transaction, across owners."""
        if value is None and not success:
            payload = self.run(run_id)
            return (payload or {}).get("workspace_io_recovery", {}).get("active", {}).get(key)
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM codex_document3_v21_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if not row:
                raise ValueError("workspace recovery requires a persisted D3 run")
            payload = json.loads(row[0])
            recovery = payload.setdefault("workspace_io_recovery", {"active": {}, "recovered": 0})
            active = recovery["active"]
            if success:
                previous = active.pop(key, None)
                if previous is not None:
                    recovery["recovered"] += 1
                    recovery["wait_seconds"] = recovery.get("wait_seconds", 0) + max(
                        0, (value or previous).get("elapsed", 0) - previous.get("elapsed", 0)
                    )
            elif value is not None:
                previous = active.get(key, {})
                recovery["retry_attempts"] = recovery.get("retry_attempts", 0) + max(
                    0, value["attempts"] - previous.get("attempts", 0)
                )
                recovery["wait_seconds"] = recovery.get("wait_seconds", 0) + max(
                    0, value.get("elapsed", 0) - previous.get("elapsed", 0)
                )
                active[key] = value
                recovery["last_failure"] = {"key": key, **value}
            db.execute(
                "UPDATE codex_document3_v21_runs SET payload=? WHERE run_id=?",
                (canonical(payload), run_id),
            )
            return active.get(key)

    def reset_io_recovery(self, run_id):
        """Explicit manual recovery only; does not alter task/job/attempt identity."""
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM codex_document3_v21_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            payload = json.loads(row[0])
            payload.setdefault("workspace_io_recovery", {})["active"] = {}
            db.execute(
                "UPDATE codex_document3_v21_runs SET payload=? WHERE run_id=?",
                (canonical(payload), run_id),
            )

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


def inline_for_rollback(path):
    """Run with D3 writers drained before selecting a pre-codec rollback image."""
    path=Path(path).resolve(strict=True)
    (path.parent/'.d3-native-inputs-disabled').touch(mode=0o600)
    state=StateV21(path);cursor=0;rewritten=0
    while True:
        with state.connect() as db:
            row=db.execute('SELECT rowid,payload FROM codex_document3_v21_runs WHERE rowid>? ORDER BY rowid LIMIT 1',(cursor,)).fetchone()
        if not row:break
        cursor,raw=row
        if '$doxagent_content_v1' not in raw:continue
        value=state.codec.decode(json.loads(raw))
        with state.connect() as db:
            db.execute('UPDATE codex_document3_v21_runs SET payload=? WHERE rowid=? AND payload=?',(canonical(value),cursor,raw))
        rewritten+=1
    return {'rewritten':rewritten,'native_inputs_enabled':False}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Inline D3 inputs before an old-reader rollback; drain writers first.')
    parser.add_argument('--inline-for-rollback',type=Path,required=True)
    print(json.dumps(inline_for_rollback(parser.parse_args().inline_for_rollback)))
