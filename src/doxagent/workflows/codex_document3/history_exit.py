"""Finite semantic history in the existing formal/staged stores, not a new archive."""
from contextlib import closing
from datetime import datetime,timedelta
import json
from pathlib import Path
import sqlite3
import time


def migrate(db):
    db.execute('CREATE TABLE IF NOT EXISTS codex_document3_history_exit(ticker TEXT,version INTEGER,due TEXT NOT NULL,PRIMARY KEY(ticker,version))')
    db.execute('CREATE INDEX IF NOT EXISTS codex_document3_history_exit_due ON codex_document3_history_exit(due,ticker,version)')
    db.execute('CREATE TABLE IF NOT EXISTS codex_document3_history_retired(ticker TEXT,version INTEGER,at TEXT NOT NULL,PRIMARY KEY(ticker,version))')
    upgrade=not db.execute("SELECT 1 FROM sqlite_master WHERE name='codex_document3_history_exit_v2'").fetchone()
    for table,version,time in [('codex_document3_policy_sets','policy_set_version','NEW.published_at'),('codex_document3_v21_staged_sets','version',"json_extract(NEW.payload,'$.published_at')")]:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone():continue
        if upgrade:db.execute('DROP TRIGGER IF EXISTS '+table+'_history_owner')
        db.execute('CREATE TRIGGER IF NOT EXISTS '+table+'_history_owner AFTER INSERT ON '+table+' BEGIN '
                   'INSERT OR IGNORE INTO codex_document3_history_exit VALUES(NEW.ticker,NEW.'+version+',datetime(coalesce('+time+",strftime('%Y-%m-%dT%H:%M:%fZ','now')))); END")
    db.execute('CREATE INDEX IF NOT EXISTS codex_document3_history_exit_v2 ON codex_document3_history_exit(ticker,due,version)')


def audit_case_evidence(source, *, limit=1):
    """One-time Case PK pages in a separate process; no recurring W3 scans.

    Only legacy Cases without self-contained policy evidence pin a whole old
    version. Modern Cases already freeze complete V5 policies. Pins name the
    concrete missing evidence, never every parent in its lineage.
    """
    from doxagent.v2_read.native_content import NativeContent,MARKER
    with closing(source.connection()) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='runtime_resource_ref_meta'").fetchone():return False
        meta=dict(db.execute("SELECT key,value FROM runtime_resource_ref_meta WHERE key LIKE 'history_evidence_%'"))
        if meta.get('history_evidence_complete')=='1':return True
        rows=db.execute("SELECT c.case_id,c.ticker,t.case_id,json_extract(t.payload_json,'$.policy_set_version') FROM runtime_v2_cases c LEFT JOIN runtime_v2_trade_records t USING(case_id) WHERE c.case_id>? ORDER BY c.case_id LIMIT ?",(meta.get('history_evidence_after',''),limit)).fetchall()
    codec=NativeContent(source.path);pins=[];processed=[];deadline=time.monotonic()+.04
    for identity,ticker,has_trade,version in rows:
        if processed and time.monotonic()>=deadline:break
        processed.append(identity)
        # Closed non-trade history does not pin an entire PolicySet merely
        # because a Case once read it. Active owners are protected separately.
        if not has_trade:continue
        with closing(source.connection()) as db:
            raw=db.execute('SELECT CASE WHEN length(payload_json)<=262144 THEN payload_json END FROM runtime_v2_cases WHERE case_id=?',(identity,)).fetchone()[0]
        if version is None:raise ValueError('TRADE_VERSION_EVIDENCE_MISSING:'+identity)
        if raw is None:
            pins.append((identity,ticker,version));continue
        value=json.loads(raw)
        frozen=value.get('frozen_inputs') or {}
        # A unique oversized legacy object is an explicit evidence pin; do not
        # load it merely to decide whether historical storage may be retired.
        if isinstance(frozen,dict) and MARKER in frozen and frozen.get('bytes',0)>8*1024**2:
            pins.append((identity,ticker,version));continue
        frozen=codec.decode(frozen) if MARKER in frozen else frozen
        projection=codec.decode(frozen.get('projection') or {})
        policies=projection.get('policies')
        complete=isinstance(policies,list) and all('transmission' in p and all('criterion' in c for c in p.get('activation_conditions',[])) for p in policies)
        # No model-visible projection and no decision needs no historical D3.
        if not complete:pins.append((identity,ticker,version))
    with closing(source.connection(write=True)) as db,db:
        db.execute('PRAGMA busy_timeout=10');db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS runtime_history_evidence_pins(case_id TEXT PRIMARY KEY,ticker TEXT,policy_version INTEGER)')
        db.execute('CREATE INDEX IF NOT EXISTS runtime_history_evidence_version ON runtime_history_evidence_pins(ticker,policy_version)')
        db.executemany('INSERT OR IGNORE INTO runtime_history_evidence_pins VALUES(?,?,?)',pins)
        db.execute("INSERT INTO runtime_resource_ref_meta VALUES('history_evidence_after',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(processed[-1] if processed else meta.get('history_evidence_after',''),))
        complete=len(rows)<limit and len(processed)==len(rows)
        if complete:db.execute("INSERT OR REPLACE INTO runtime_resource_ref_meta VALUES('history_evidence_complete','1')")
    return complete


def register_legacy(state, *, limit=16):
    with state.connect() as db:
        saved=db.execute("SELECT value FROM codex_document3_storage_meta WHERE key='history_exit_backfill'").fetchone()
        phase,after=json.loads(saved[0]) if saved else [0,['',0]]
        if phase==2:return True
        table,version,time=(('codex_document3_policy_sets','policy_set_version','published_at'),('codex_document3_v21_staged_sets','version',"json_extract(payload,'$.published_at')"))[phase]
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone():rows=[]
        else:rows=db.execute('SELECT ticker,'+version+','+time+' FROM '+table+' WHERE (ticker,'+version+')>(?,?) ORDER BY ticker,'+version+' LIMIT ?',(*after,limit)).fetchall()
    with state.connect() as db:
        db.execute('PRAGMA busy_timeout=10');db.execute('BEGIN IMMEDIATE')
        db.executemany("INSERT INTO codex_document3_history_exit VALUES(?,?,datetime(?)) ON CONFLICT(ticker,version) DO UPDATE SET due=min(due,excluded.due)",[(r[0],r[1],r[2] or '1970-01-01T00:00:00Z') for r in rows])
        if len(rows)==limit:after=list(rows[-1][:2])
        else:phase+=1;after=['',0]
        db.execute("INSERT INTO codex_document3_storage_meta VALUES('history_exit_backfill',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps([phase,after]),))
    return phase==2


def collect(state,ticker,*,keep_versions,now,is_live=lambda run:True):
    """Retire one exact version; keep current, three recent versions and real pins."""
    with state.connect() as db:
        keep=set(keep_versions)
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='codex_document3_policy_sets'").fetchone():
            keep.update(r[0] for r in db.execute('SELECT policy_set_version FROM codex_document3_policy_sets WHERE ticker=? AND is_current=1',(ticker,)))
        cutoff=(now-timedelta(days=7)).isoformat()
        formal_exists=bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='codex_document3_policy_sets'").fetchone())
        # Only six index headers enter the merge. Never parse every staged
        # PolicySet JSON to find the three newest recovery points.
        latest={r[0] for r in db.execute('SELECT version FROM codex_document3_v21_staged_sets WHERE ticker=? ORDER BY version DESC LIMIT 3',(ticker,))}
        if formal_exists:latest.update(r[0] for r in db.execute('SELECT policy_set_version FROM codex_document3_policy_sets WHERE ticker=? ORDER BY policy_set_version DESC LIMIT 3',(ticker,)))
        for version in sorted(latest,reverse=True)[:3]:
            stamp=db.execute('SELECT published_at FROM codex_document3_policy_sets WHERE ticker=? AND policy_set_version=?',(ticker,version)).fetchone() if formal_exists else None
            if stamp is None:stamp=db.execute("SELECT json_extract(payload,'$.published_at') FROM codex_document3_v21_staged_sets WHERE ticker=? AND version=?",(ticker,version)).fetchone()
            if stamp and stamp[0] and datetime.fromisoformat(stamp[0].replace('Z','+00:00'))>=now-timedelta(days=7):keep.add(version)
        # datetime normalizes mixed legacy ISO and SQL timestamps consistently.
        row=db.execute('SELECT version FROM codex_document3_history_exit WHERE ticker=? AND due<=datetime(?) AND version NOT IN (SELECT value FROM json_each(?)) ORDER BY due LIMIT 1',(ticker,now.isoformat(),json.dumps(sorted(keep)))).fetchone()
        if not row:return {'idle':True}
        version=row[0]
        staged=db.execute('SELECT run_id FROM codex_document3_v21_staged_sets WHERE ticker=? AND version=?',(ticker,version)).fetchone()
    if staged and is_live(staged[0]):
        with state.connect() as db:
            db.execute('PRAGMA busy_timeout=10');db.execute('UPDATE codex_document3_history_exit SET due=datetime(?) WHERE ticker=? AND version=?',((now+timedelta(minutes=30)).isoformat(),ticker,version))
        return {'deferred_owner':staged[0]}
    with state.connect() as db:
        db.execute('PRAGMA busy_timeout=10');db.execute('BEGIN IMMEDIATE')
        identities=[]
        for table,key,column in [('codex_document3_policy_sets','policy_set_version','policy_set_json'),('codex_document3_v21_staged_sets','version','payload')]:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone():continue
            if table.endswith('policy_sets') and db.execute('SELECT 1 FROM '+table+' WHERE ticker=? AND policy_set_version=? AND is_current=1',(ticker,version)).fetchone():return {'became_current':True}
            identities.extend(r[0] for r in db.execute('SELECT artifact_id FROM artifact_ref WHERE owner_type=? AND owner_id=json_array(?,?)',(table,ticker,version)))
        db.execute('DELETE FROM codex_document3_v21_staged_sets WHERE ticker=? AND version=?',(ticker,version))
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='codex_document3_policy_sets'").fetchone():db.execute('DELETE FROM codex_document3_policy_sets WHERE ticker=? AND policy_set_version=? AND is_current=0',(ticker,version))
        from doxagent.v2_read.source_native_gc import queue_owner_exit
        queue_owner_exit(db,identities)
        db.execute('INSERT OR REPLACE INTO codex_document3_history_retired VALUES(?,?,?)',(ticker,version,now.isoformat()))
        db.execute('DELETE FROM codex_document3_history_exit WHERE ticker=? AND version=?',(ticker,version))
    return {'retired_version':version,'released_objects':len(set(identities))}


def maintain(state,settings,*,now):
    from doxagent.v2_read.outbox import SourceOutbox
    from doxagent.codex_worker.session_retention import run_held
    runtime=Path(settings.persistent_runtime_v2_sqlite_path)
    control=Path(settings.ticker_initialization_control_path)
    if not runtime.is_file() or not control.is_file():return {'waiting_for_reference_authority':True}
    source=SourceOutbox(runtime,'runtime')
    # Migration runs here, outside the Scheduler and W3 interpreter/transactions.
    if not audit_case_evidence(source,limit=4):return {'phase':'legacy_case_evidence'}
    with closing(source.connection()) as db:
        if not db.execute("SELECT 1 FROM runtime_resource_ref_meta WHERE key='complete' AND value='1'").fetchone():return {'phase':'active_case_refs'}
    register_legacy(state)
    with closing(sqlite3.connect(control.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        heads=list(db.execute("SELECT r.ticker,json_extract(r.payload,'$.artifacts.document3.version') FROM activation_revisions r JOIN ticker_active_revision a USING(revision_id) ORDER BY r.ticker"))
    if not heads:return {'idle':True}
    offset=getattr(maintain,'offset',0)%len(heads);maintain.offset=offset+1
    ticker,current=heads[offset]
    with closing(source.connection()) as db:
        keep={r[0] for r in db.execute('SELECT policy_version FROM runtime_case_resource_refs WHERE ticker=? UNION SELECT policy_version FROM runtime_history_evidence_pins WHERE ticker=?',(ticker,ticker)) if r[0] is not None}
    if current is not None:keep.add(int(current))
    def live(run):
        if run_held(settings,run):return True
        path=Path(settings.codex_workspace_root)/'worker-jobs.sqlite3'
        if not path.is_file():return True
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.01)) as db:
            return bool(db.execute("SELECT 1 FROM jobs INDEXED BY jobs_run_owner WHERE json_extract(payload,'$.run_id')>=? AND json_extract(payload,'$.run_id')<? AND status IN ('queued','running') LIMIT 1",(run,run+'\uffff')).fetchone())
    return {'ticker':ticker,**collect(state,ticker,keep_versions=keep,now=now,is_live=live)}
