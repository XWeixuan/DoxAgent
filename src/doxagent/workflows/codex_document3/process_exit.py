"""D3 process owners, driven by the existing separate transport maintenance process."""
from contextlib import closing
from datetime import UTC,datetime,timedelta
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace


def migrate(db):
    db.execute('CREATE TABLE IF NOT EXISTS codex_document3_process_exit('
               'run_id TEXT PRIMARY KEY,due TEXT NOT NULL,error TEXT)')
    db.execute('CREATE INDEX IF NOT EXISTS codex_document3_process_exit_due ON codex_document3_process_exit(due,run_id)')


def enqueue_sql(db,run_id,at,*,days):
    due=(datetime.fromisoformat(at.replace('Z','+00:00'))+timedelta(days=days)).isoformat()
    db.execute('INSERT INTO codex_document3_process_exit VALUES(?,?,NULL) '
               'ON CONFLICT(run_id) DO UPDATE SET due=min(due,excluded.due)',(run_id,due))


def enqueue(state,run_id,*,days):
    with state.connect() as db:enqueue_sql(db,run_id,datetime.now(UTC).isoformat(),days=days)


def register_legacy(state,*,limit=16):
    with state.connect() as db:
        meta=db.execute("SELECT value FROM codex_document3_storage_meta WHERE key='process_exit_backfill'").fetchone()
        cursor=meta[0] if meta else ''
        if cursor=='DONE':return
        roots=[r[0] for r in db.execute('SELECT run_id FROM codex_document3_v21_runs WHERE run_id>? ORDER BY run_id LIMIT ?',(cursor,limit))]
    prepared=[]
    with state.connect() as db:
        for run in roots:
            row=db.execute("SELECT json_extract(payload_json,'$.published_at') FROM codex_runtime_records WHERE record_type='bundles' AND run_id=? LIMIT 1",(run,)).fetchone()
            # Completed candidate owners can exit after 24h. Unknown/unpublished
            # legacy owners get a finite 30-day grace, never a global hold.
            prepared.append((run,(datetime.fromisoformat(row[0].replace('Z','+00:00'))+timedelta(days=1)).isoformat() if row and row[0] else (datetime.now(UTC)+timedelta(days=30)).isoformat()))
    with state.connect() as db:
        db.execute('PRAGMA busy_timeout=10');db.execute('BEGIN IMMEDIATE')
        db.executemany('INSERT OR IGNORE INTO codex_document3_process_exit VALUES(?,?,NULL)',prepared)
        db.execute("INSERT INTO codex_document3_storage_meta VALUES('process_exit_backfill',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(roots[-1] if len(roots)==limit else 'DONE',))


def collect(settings,*,now=None):
    from .state_v21 import StateV21
    from .policy_storage import shared_ready
    from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
    from doxagent.codex_worker.workspace_retention import collect as retire_inputs,family_jobs
    from doxagent.codex_worker.job_store import JobStore
    source=Path(settings.codex_runtime_sqlite_path);workspace=Path(settings.codex_workspace_root)
    now=now or datetime.now(UTC)
    if not source.is_file() or not (workspace/'worker-jobs.sqlite3').is_file():return {'disabled':'authority_missing'}
    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='codex_document3_v21_runs'").fetchone():return {'disabled':'no_d3_owners'}
    state=StateV21(source)
    if not shared_ready(state.codec):return {'waiting_for_reader_rollout':True}
    from .policy_storage import convert_legacy
    conversion=convert_legacy(state)
    from .history_exit import maintain as maintain_history
    history=maintain_history(state,settings,now=now) if getattr(settings,'persistent_runtime_v2_sqlite_path',None) else {'disabled':'no_runtime_reference_authority'}
    counters=state.backfill_condition_highwater()
    register_legacy(state)
    with state.connect() as db:
        row=db.execute('SELECT run_id FROM codex_document3_process_exit WHERE due<=? ORDER BY due,run_id LIMIT 1',(now.isoformat(),)).fetchone()
    if row is None:return {'idle':True,'condition_counters_ready':counters,'history':history,'policy_conversion':conversion}
    run=row[0];report={'run':run,'history':history,'policy_conversion':conversion}
    class Store:
        path=workspace/'worker-jobs.sqlite3'
        def connect(self):return sqlite3.connect(self.path,timeout=.01)
    store=Store();jobs=SimpleNamespace(store=store)
    try:
        with closing(store.connect()) as db:owners=family_jobs(db,run)
        if any(r[0] not in {'succeeded','failed','cancelled'} for r in owners):raise ValueError('owner has live Worker jobs')
        workspaces=LocalWorkspaceStore(workspace)
        result=retire_inputs(workspaces,jobs,settings,now=now,limit=1,run_ids=[run])
        report['workspace']=result
        if result.get('errors'):raise ValueError(str(result['errors'][0]))
        scopes={run,*[r[1] for r in owners]}
        def completed(child):
            directory=workspace/child
            marker=directory/'audit/retained-assets.json'
            return not directory.is_dir() or (marker.is_file() and json.loads(marker.read_text()).get('complete'))
        # A skipped/held/young owner is not authority to drop its process state.
        # An existing retirement fence/receipt permits subsequent bounded pages.
        parent_marker=workspace/run/'audit/retained-assets.json'
        if not result.get('runs') and not parent_marker.is_file():
            raise ValueError('workspace owner has not reached an eligible retirement boundary')
        authority='published-candidate'
        with state.connect() as db:
            if not db.execute('SELECT 1 FROM codex_document3_v21_staged_sets WHERE run_id=?',(run,)).fetchone():authority='terminal-unpublished-inputs'
        report['state']=state.retire(run,now=now,authority=authority)
        # Reuse existing confirmed consumption compaction, two exact Jobs at a time.
        retired=0;pending=False
        prepared=[]
        with closing(store.connect()) as db:
            for child in dict.fromkeys(r[1] for r in owners):
                for item in db.execute("SELECT payload,request FROM jobs WHERE json_extract(payload,'$.run_id')=?",(child,)):
                    if not item[1]:continue
                    request=json.loads(item[1]);receipt=json.loads(item[0])
                    needs=request.get('prompt') or request.get('output_schema') or (receipt.get('telemetry') or {}).get('events')
                    if needs:
                        prepared.append((request,receipt))
                        if len(prepared)>=3:break
                if len(prepared)>=3:break
        # Do not pin a Worker read snapshot while opening its write connection.
        for request,receipt in prepared[:2]:
            if JobStore.retire_consumed_process(workspace,request,receipt):retired+=1
            else:pending=True
        pending=pending or len(prepared)>2
        report['jobs_compacted']=retired
        # Each completed directory has a durable local receipt; no whole tree/proc scan.
        files_done=all(completed(child) for child in scopes)
        done=files_done and not pending and not (report['state'] or {}).get('remaining')
        with state.connect() as db:
            db.execute('PRAGMA busy_timeout=10')
            if done:db.execute('DELETE FROM codex_document3_process_exit WHERE run_id=?',(run,))
            else:db.execute('UPDATE codex_document3_process_exit SET due=?,error=NULL WHERE run_id=?',((now+timedelta(minutes=5)).isoformat(),run))
        report['done']=done
    except (ValueError,OSError,sqlite3.Error) as exc:
        report['error']=str(exc)[:250]
        with state.connect() as db:
            db.execute('PRAGMA busy_timeout=10')
            db.execute('UPDATE codex_document3_process_exit SET due=?,error=? WHERE run_id=?',((now+timedelta(minutes=30)).isoformat(),report['error'],run))
    return report
