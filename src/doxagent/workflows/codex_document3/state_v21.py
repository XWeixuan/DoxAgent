"""Short SQLite transactions for V21 authority and replayable task budgets."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections import Counter
from contextlib import contextmanager
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(
        (value if isinstance(value, str) else canonical(value)).encode()
    ).hexdigest()


class StateV21:
    def __init__(self, path: str | Path, *, read_only: bool = False):
        self.path = str(path)
        self.read_only = read_only
        self.lock = threading.RLock()
        from doxagent.v2_read.native_content import NativeContent
        self.codec = NativeContent(path)
        if read_only:
            return
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            required=('codex_document3_v21_runs','codex_document3_condition_highwater',
                'codex_document3_process_exit','codex_document3_history_retired',
                'v2_artifact_codex_document3_v21_runs_INSERT',
                'v2_artifact_codex_document3_v21_tasks_INSERT',
                'v2_artifact_codex_document3_v21_draft_ids_INSERT',
                'codex_document3_v21_staged_sets_history_owner')
            ready=db.execute('SELECT count(*) FROM sqlite_master WHERE name IN ('+','.join('?' for _ in required)+')',required).fetchone()[0]==len(required)
        if ready:return
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
            CREATE TABLE IF NOT EXISTS codex_document3_condition_highwater
              (policy_id TEXT PRIMARY KEY,value INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS codex_document3_storage_meta
              (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            """)
            from .process_exit import migrate as migrate_process_exit
            migrate_process_exit(db)
            from .history_exit import migrate as migrate_history_exit
            migrate_history_exit(db)
            from doxagent.v2_read.artifact_registry import migrate, native_triggers
            migrate(db)
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_artifact_codex_document3_v21_runs_INSERT'").fetchone():
                native_triggers(db, 'codex_document3_v21_runs', ['payload'], 'json_array(NEW.run_id)')
            from .policy_storage import install_refs
            install_refs(db, 'codex_document3_v21_staged_sets', ['payload'], 'json_array(NEW.ticker,NEW.version)')
            install_refs(db, 'codex_document3_v21_tasks', ['payload'], 'json_array(NEW.run_id,NEW.task_key)')
            install_refs(db, 'codex_document3_v21_draft_ids', ['payload'], 'json_array(NEW.run_id,NEW.path)')

    def _pack(self, values):
        # Existing immutable Native CAS; unchanged large fields stay as references.
        if (Path(self.path).parent/'.d3-native-inputs-disabled').exists():return values
        large = {'prepared', 'base', 'base_coverage', 'final', 'final_candidates', 'handoff'}
        return {key: json.loads(self.codec.encode({key: value}))[key] if key in large else value
                for key, value in values.items()}

    @contextmanager
    def connect(self):
        target = Path(self.path).resolve().as_uri() + "?mode=ro" if self.read_only else self.path
        db = sqlite3.connect(target, uri=self.read_only, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def start(self, run_id, identity, payload):
        stored = self._pack(payload)
        key, raw = canonical(identity), canonical(stored)
        with self.connect() as db:
            row=db.execute('SELECT identity,payload FROM codex_document3_v21_runs WHERE run_id=?',(run_id,)).fetchone()
        if row is None:
            with self.lock, self.connect() as db:
                inserted=db.execute('INSERT OR IGNORE INTO codex_document3_v21_runs VALUES(?,?,?)',(run_id,key,raw)).rowcount
                if inserted:
                    from datetime import UTC, datetime
                    from .process_exit import enqueue_sql
                    enqueue_sql(db,run_id,datetime.now(UTC).isoformat(),days=30)
            if inserted:
                return payload
            with self.connect() as db:
                row=db.execute('SELECT identity,payload FROM codex_document3_v21_runs WHERE run_id=?',(run_id,)).fetchone()
        if row['identity']!=key:raise ValueError('V21 replay input identity mismatch')
        existing=self.codec.decode(json.loads(row['payload']))
        if existing.get('resource_retirement'):raise ValueError('D3_RUN_RETIRED_NEW_RUN_REQUIRED')
        return existing

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

    def pilot_view(self, run_id):
        if self.read_only:
            with self.connect() as db:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE name='codex_document3_v21_runs'").fetchone():
                    return None
        run = self.run(run_id)
        if run is None:
            return None
        tasks = self.tasks(run_id)
        drafts = self.drafts(run_id)
        view = {
            **{
                k: v
                for k, v in run.items()
                if k not in {"prepared", "assets", "basis", "final_candidates", "scan_inputs"}
            },
            "input_manifest": run.get("prepared", {}).get("manifest", []),
            "material_refs": list(run.get("prepared", {}).get("files", {})),
            "basis_paths": list(run.get("basis", {})),
            "candidate_paths": list(run.get("final_candidates", {})),
            "task_metrics": {
                "total": len(tasks),
                "by_status": dict(Counter(t["status"] for t in tasks.values())),
                "attempts": sum(t["attempt_count"] for t in tasks.values()),
                "readable_records": sum(len(t.get("files", {})) for t in tasks.values()),
                "readable_policy_count": len(drafts),
                "readable_condition_count": sum(
                    len(d["policy"]["activation_conditions"]) for d in drafts.values()
                ),
                "quarantined_policy_count": len(
                    {
                        i["path"]
                        for i in run.get("diagnostics", [])
                        if isinstance(i, dict)
                        and i.get("error")
                        and i.get("path")
                        and "/policies/" in i["path"]
                        and i["path"] not in drafts
                    }
                ),
                "quarantined_condition_count": sum(
                    len(d.get("errors", [])) for d in drafts.values()
                ),
                "main_tasks": {
                    k: t["status"] for k, t in tasks.items() if k.startswith("research:")
                },
                "supplement_tasks": {
                    k: t["status"] for k, t in tasks.items() if k.startswith("supplement:")
                },
            },
        }
        return view

    def update(self, run_id, **values):
        packed = self._pack(values)
        for _ in range(8):
            with self.connect() as db:
                row=db.execute('SELECT payload FROM codex_document3_v21_runs WHERE run_id=?',(run_id,)).fetchone()
            if row is None:raise ValueError('D3 run not found')
            payload=json.loads(row[0])
            if payload.get('resource_retirement'):raise ValueError('D3_RUN_RETIRED_NEW_RUN_REQUIRED')
            payload.update(packed)
            raw=canonical(payload)
            with self.lock, self.connect() as db:
                changed=db.execute('UPDATE codex_document3_v21_runs SET payload=? WHERE run_id=? AND payload=?',(raw,run_id,row[0])).rowcount
            if changed:return self.codec.decode(payload)
        raise RuntimeError('D3 owner changed repeatedly during update')

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
            from .process_exit import enqueue_sql
            enqueue_sql(db,run_id,value['settlement']['at'],days=1)

    def retire(self, run_id, *, now, authority):
        """Owner-proven exit. Prepare outside writer; retire bounded process rows."""
        with self.connect() as db:
            row=db.execute('SELECT payload FROM codex_document3_v21_runs WHERE run_id=?',(run_id,)).fetchone()
            staged=db.execute('SELECT 1 FROM codex_document3_v21_staged_sets WHERE run_id=?',(run_id,)).fetchone()
        if not row:return None
        if not staged and authority!='terminal-unpublished-inputs':return None
        value=json.loads(row[0]);receipt=value.get('resource_retirement')
        if receipt is None:
            removed={}
            for key in ('prepared','base','base_coverage','final','final_candidates','handoff',
                        'shell_discovery_input','open_discovery_input','assets','basis','scan_inputs'):
                if not staged and key in {'final','final_candidates','handoff'}:continue
                if key in value:removed[key]=digest(value.pop(key))
            receipt={'at':now.isoformat(),'authority':authority,'resume_allowed':False,
                     'fields_sha256':removed,'before_json_bytes':len(row[0].encode())}
            value['resource_retirement']=receipt
        raw=canonical(value)
        if receipt and raw==row[0]:
            with self.connect() as db:
                pending=bool(staged and any(db.execute('SELECT 1 FROM '+table+' WHERE run_id=? LIMIT 1',(run_id,)).fetchone() for table in ('codex_document3_v21_tasks','codex_document3_v21_draft_ids')))
            if not pending:return {**receipt,'batch_rows':{},'remaining':False}
        with self.lock,self.connect() as db:
            db.execute('PRAGMA busy_timeout=10');db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT payload FROM codex_document3_v21_runs WHERE run_id=?',(run_id,)).fetchone()
            if not current or current[0]!=row[0]:raise ValueError('D3 owner changed during retirement')
            exiting=[r[0] for r in db.execute("SELECT artifact_id FROM artifact_ref WHERE owner_type='codex_document3_v21_runs' AND owner_id=json_array(?)",(run_id,))]
            if raw!=row[0]:db.execute('UPDATE codex_document3_v21_runs SET payload=? WHERE run_id=?',(raw,run_id))
            counts={}
            for table,column in (('codex_document3_v21_tasks','task_key'),('codex_document3_v21_draft_ids','path')):
                if not staged:continue
                rows=db.execute('SELECT rowid,'+column+' FROM '+table+' WHERE run_id=? LIMIT 4',(run_id,)).fetchall()
                for identity,key in rows:
                    # Draft condition highwaters must outlive their process body.
                    if table.endswith('draft_ids') and not db.execute("SELECT 1 FROM codex_document3_storage_meta WHERE key='condition_highwater_complete'").fetchone():
                        continue
                    exiting.extend(r[0] for r in db.execute('SELECT artifact_id FROM artifact_ref WHERE owner_type=? AND owner_id=json_array(?,?)',(table,run_id,key)))
                    db.execute('DELETE FROM '+table+' WHERE rowid=?',(identity,))
                counts[table]=len(rows) if not table.endswith('draft_ids') or db.execute("SELECT 1 FROM codex_document3_storage_meta WHERE key='condition_highwater_complete'").fetchone() else 0
            from doxagent.v2_read.source_native_gc import queue_owner_exit
            queue_owner_exit(db,exiting)
        with self.connect() as db:
            remaining=bool(staged and any(db.execute('SELECT 1 FROM '+table+' WHERE run_id=? LIMIT 1',(run_id,)).fetchone() for table in ('codex_document3_v21_tasks','codex_document3_v21_draft_ids')))
        return {**receipt,'batch_rows':counts,'remaining':remaining}

    def task(self, run_id, key):
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM codex_document3_v21_tasks WHERE run_id=? AND task_key=?",
                (run_id, key),
            ).fetchone()
        return self.codec.decode(json.loads(row[0])) if row else {"status": "PENDING", "attempt_count": 0}

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
        from .policy_storage import shared_ready
        packed=dict(payload)
        if shared_ready(self.codec):
            for name in ('files','io_inputs','baseline_outputs'):
                if isinstance(packed.get(name),dict):
                    packed[name]={k:json.loads(self.codec.encode({'v':v}))['v'] for k,v in packed[name].items()}
            if isinstance(packed.get('pending_acceptance'),dict):
                pending=dict(packed['pending_acceptance'])
                pending['files']={k:json.loads(self.codec.encode({'v':v}))['v'] for k,v in pending.get('files',{}).items()}
                packed['pending_acceptance']=pending
            for name in ('request','job'):
                if name in packed:
                    packed[name]=json.loads(self.codec.encode({'v':packed[name]}))['v']
        raw=canonical(packed)
        with self.lock, self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO codex_document3_v21_tasks VALUES(?,?,?)",
                (run_id, key, raw),
            )

    def tasks(self, run_id):
        with self.connect() as db:
            rows=db.execute("SELECT task_key,payload FROM codex_document3_v21_tasks WHERE run_id=?",(run_id,)).fetchall()
        return {row[0]:self.codec.decode(json.loads(row[1])) for row in rows}

    def claim(self, run_id, key, *, max_attempts=2):
        with self.connect() as db:
            row=db.execute('SELECT payload FROM codex_document3_v21_tasks WHERE run_id=? AND task_key=?',(run_id,key)).fetchone()
        raw=row[0] if row else None
        item=self.codec.decode(json.loads(raw)) if raw else {"attempt_count":0,"status":"PENDING"}
        if item['status'] in {'COMPLETED','PARTIAL','FAILED','CANCELLED'} or item['attempt_count']>=max_attempts:
            return None
        item.update(status='RUNNING',attempt_count=item['attempt_count']+1)
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if raw is None:
                if not db.execute('INSERT OR IGNORE INTO codex_document3_v21_tasks VALUES(?,?,?)',(run_id,key,canonical(item))).rowcount:return None
            elif not db.execute("UPDATE codex_document3_v21_tasks SET payload=json_set(payload,'$.status','RUNNING','$.attempt_count',?) WHERE run_id=? AND task_key=? AND payload=?",
                                (item['attempt_count'],run_id,key,raw)).rowcount:return None
            return item

    def drafts(self, run_id):
        with self.connect() as db:
            rows=db.execute('SELECT path,payload FROM codex_document3_v21_draft_ids WHERE run_id=?',(run_id,)).fetchall()
        values={r[0]:self.codec.decode(json.loads(r[1])) for r in rows}
        for value in values.values():value.pop('_policy_id',None)
        return values

    def save_draft(self, run_id, path, value):
        from .policy_storage import shared_ready,pack_object
        stored=dict(value)
        if shared_ready(self.codec) and isinstance(stored.get('policy'),dict):
            stored['_policy_id']=stored['policy']['policy_id']
            stored['policy']=pack_object(self.codec,stored['policy'])
        raw=canonical(stored)
        with self.lock, self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO codex_document3_v21_draft_ids VALUES(?,?,?)",
                (run_id, path, raw),
            )
            if isinstance(value.get('policy'),dict):
                db.execute('INSERT INTO codex_document3_condition_highwater VALUES(?,?) ON CONFLICT(policy_id) DO UPDATE SET value=max(value,excluded.value)',
                           (value['policy']['policy_id'],int(value.get('max_condition',0))))

    def get_draft(self, run_id, path):
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM codex_document3_v21_draft_ids WHERE run_id=? AND path=?",
                (run_id, path),
            ).fetchone()
        value=self.codec.decode(json.loads(row[0])) if row else None
        if value:value.pop('_policy_id',None)
        return value

    def backfill_condition_highwater(self, *, limit=64):
        """Explicit resumable migration; regular validation never scans drafts after it."""
        if not 1 <= limit <= 256:
            raise ValueError("invalid highwater batch")
        with self.connect() as db:
            meta = dict(db.execute('SELECT key,value FROM codex_document3_storage_meta'))
            if 'condition_highwater_complete' in meta:
                return True
            after = int(meta.get('condition_highwater_cursor', 0))
            stop = int(meta.get('condition_highwater_stop', db.execute(
                'SELECT coalesce(max(rowid),0) FROM codex_document3_v21_draft_ids').fetchone()[0]))
            rows = db.execute(
                "SELECT rowid,coalesce(json_extract(payload,'$._policy_id'),"
                "json_extract(payload,'$.policy.policy_id')) AS policy_id,"
                "coalesce(json_extract(payload,'$.max_condition'),0) AS value "
                "FROM codex_document3_v21_draft_ids WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT ?",
                (after, stop, limit),
            ).fetchall()
        counters = {}
        for row in rows:
            if row['policy_id']:
                counters[row['policy_id']] = max(counters.get(row['policy_id'], 0), int(row['value']))
        cursor = rows[-1]['rowid'] if rows else stop
        complete = len(rows) < limit or cursor >= stop
        with self.lock, self.connect() as db:
            db.execute('PRAGMA busy_timeout=10'); db.execute('BEGIN IMMEDIATE')
            current = db.execute("SELECT value FROM codex_document3_storage_meta WHERE key='condition_highwater_cursor'").fetchone()
            if int(current[0] if current else 0) != after:
                return False
            db.executemany('INSERT INTO codex_document3_condition_highwater VALUES(?,?) '
                           'ON CONFLICT(policy_id) DO UPDATE SET value=max(value,excluded.value)', counters.items())
            for key, value in [('condition_highwater_cursor', cursor), ('condition_highwater_stop', stop)]:
                db.execute('INSERT INTO codex_document3_storage_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key,str(value)))
            if complete:
                db.execute("INSERT OR IGNORE INTO codex_document3_storage_meta VALUES('condition_highwater_complete','1')")
        return complete

    def maximum_condition(self, policy_id):
        with self.connect() as db:
            counter=db.execute('SELECT value FROM codex_document3_condition_highwater WHERE policy_id=?',(policy_id,)).fetchone()
            if db.execute("SELECT 1 FROM codex_document3_storage_meta WHERE key='condition_highwater_complete'").fetchone():
                return counter[0] if counter else 0
            row = db.execute(
                "SELECT MAX(CAST(json_extract(payload, '$.max_condition') AS INTEGER)) "
                "FROM codex_document3_v21_draft_ids "
                "WHERE coalesce(json_extract(payload,'$._policy_id'),json_extract(payload, '$.policy.policy_id'))=?",
                (policy_id,),
            ).fetchone()
            return max(row[0] or 0,counter[0] if counter else 0)

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
        from .policy_storage import pack_policy_set
        packed = pack_policy_set(self.codec, data)
        # Projection is derived from typed policy semantics, not recovery truth.
        receipt = {key: value for key, value in receipt.items() if key != 'projection'}
        with self.connect() as db:
            old = db.execute("SELECT payload,receipt FROM codex_document3_v21_staged_sets WHERE run_id=?", (run_id,)).fetchone()
        if old:
            previous = json.loads(old[1])
            previous.pop('projection', None)
            if self.codec.decode(json.loads(old[0])) != data or previous != receipt:
                raise ValueError("immutable staged commit mismatch")
            return
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT payload,receipt FROM codex_document3_v21_staged_sets WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if old:
                previous = json.loads(old[1])
                if "projection" not in receipt:
                    previous.pop("projection", None)
                if old[0] != packed or previous != receipt:
                    raise ValueError("immutable staged commit mismatch")
                return
            db.execute(
                "INSERT INTO codex_document3_v21_staged_sets VALUES(?,?,?,?,?)",
                (
                    data["ticker"],
                    data["policy_set_version"],
                    run_id,
                    packed,
                    canonical(receipt),
                ),
            )

    def get_staged_v3(self, ticker, version):
        from .schema_v21 import PolicySetV3

        with self.connect() as db:
            if db.execute('SELECT 1 FROM codex_document3_history_retired WHERE ticker=? AND version=?',(ticker.upper(),version)).fetchone():raise ValueError('HISTORY_RETIRED')
            row = db.execute(
                "SELECT payload FROM codex_document3_v21_staged_sets WHERE ticker=? AND version=?",
                (ticker.upper(), version),
            ).fetchone()
            return PolicySetV3.model_validate(self.codec.decode(json.loads(row[0]))) if row else None

    def get_staged_by_run(self, run_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT payload,receipt FROM codex_document3_v21_staged_sets WHERE run_id=?",
                (run_id,),
            ).fetchone()
            return (self.codec.decode(json.loads(row[0])), json.loads(row[1])) if row else None

    def coverage(self, run_id):
        """Legacy semantic coverage without expanding prepared inputs or drafts."""
        with self.connect() as db:
            row=db.execute("SELECT json_extract(payload,'$.coverage') FROM codex_document3_v21_runs WHERE run_id=?",(run_id,)).fetchone()
        return self.codec.decode(json.loads(row[0])) if row and row[0] else None


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
