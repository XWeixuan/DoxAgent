"""O2-only process owners: terminal event -> indexed due work -> bounded exit."""
from contextlib import closing
from datetime import timedelta
import json
import os
from pathlib import Path
import re
import sqlite3
import time


def migrate(db):
    db.execute('CREATE TABLE IF NOT EXISTS runtime_resource_exit('
               'run_id TEXT PRIMARY KEY,task_id TEXT NOT NULL,state TEXT NOT NULL,'
               'not_before TEXT NOT NULL,retry_at TEXT NOT NULL,error TEXT)')
    db.execute('CREATE INDEX IF NOT EXISTS runtime_resource_exit_due ON runtime_resource_exit(state,retry_at,not_before)')
    db.execute('CREATE TABLE IF NOT EXISTS runtime_resource_exit_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')


def enqueue(db, task_id, receipt, now):
    run=(receipt.get('_gc_owner') or {}).get('run_id')
    if not run:
        return
    due=(now+timedelta(hours=24)).isoformat()
    db.execute("INSERT OR IGNORE INTO runtime_resource_exit VALUES(?,?,'PENDING',?,? ,NULL)", (run,task_id,due,due))


def register_legacy(journal,*,limit=16):
    """One-time indexed successful Maintenance page, no Case/Context expansion."""
    with journal.transaction(write=False,expand=False) as db:
        saved=db.execute("SELECT value FROM runtime_resource_exit_meta WHERE key='legacy_cursor'").fetchone()
        cursor=json.loads(saved[0]) if saved else ['','']
        if cursor==['DONE']:return {'registered':0,'complete':True}
        rows=db.execute("SELECT id,due_at,updated_at,coalesce(json_extract(receipt,'$._gc_owner.run_id'),json_extract(receipt,'$.frame.run_id')) FROM runtime_tasks WHERE kind='MAINTENANCE' AND status='SUCCEEDED' AND (due_at,id)>(?,?) ORDER BY due_at,id LIMIT ?",(*cursor,limit)).fetchall()
    from datetime import datetime
    with journal.transaction(expand=False,timeout=.01,operation='owner.exit.legacy') as db:
        for identity,_,at,run in rows:
            if run and re.fullmatch(r'runtime-maintain-[0-9a-f]{24}',run):enqueue(db,identity,{'_gc_owner':{'run_id':run}},datetime.fromisoformat(at))
        cursor=[rows[-1][1],rows[-1][0]] if len(rows)==limit else ['DONE']
        db.execute("INSERT INTO runtime_resource_exit_meta VALUES('legacy_cursor',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(cursor),))
    return {'registered':len(rows),'complete':cursor==['DONE']}


def _unlink_page(root, *, deadline, limit=256):
    """Only this fenced owner tree; no global directory or descriptor scan."""
    from doxagent.v2_read.detached_files import unlink_page
    return unlink_page(root,max_entries=limit,deadline=deadline)[0]


def drain(journal, settings, *, limit=2, seconds=.1):
    now=journal.clock();stamp=now.isoformat();deadline=time.monotonic()+seconds
    with journal.transaction(write=False,expand=False) as db:
        rows=db.execute("SELECT run_id,task_id,state FROM runtime_resource_exit WHERE state IN ('PENDING','DELETING') "
                        'AND retry_at<=? AND not_before<=? ORDER BY state,retry_at,not_before LIMIT ?', (stamp,stamp,limit)).fetchall()
    report={'processed':[],'errors':[]}
    for run,task_id,state in rows:
        if time.monotonic()>=deadline:
            break
        try:
            if not re.fullmatch(r'runtime-maintain-[0-9a-f]{24}',run):
                raise ValueError('invalid process owner')
            artifact=journal.path.resolve().parent/'runtime_artifacts'/run
            # A live/historical Event repository does not own the run's copied
            # inputs. Keep its entire artifact tree here; retire only process
            # workspaces and receipts. History migration owns Event files.
            keep_artifact=(artifact/'events').exists()
            workspace=Path(settings.codex_workspace_root).resolve()
            jobs_path=workspace/'worker-jobs.sqlite3'
            if not jobs_path.is_file():
                raise ValueError('Worker authority unavailable')
            with journal.transaction(write=False,expand=False) as db:
                task=db.execute("SELECT status,updated_at,coalesce(json_extract(receipt,'$._gc_owner.run_id'),json_extract(receipt,'$.frame.run_id')) FROM runtime_tasks WHERE id=?",(task_id,)).fetchone()
            if not task or task[0]!='SUCCEEDED':
                raise ValueError('owner no longer terminal')
            if task[2]!=run:raise ValueError('queued owner differs from current Task; review the legacy owner')
            from .resource_gc import compact_process
            compact_process(journal,{run:{'task':task_id,'status':task[0],'at':task[1]}},set(),now,limit=2,settings=settings)
            prefix='runtime-worker:'+run+':'
            with journal.transaction(write=False,expand=False) as db:
                remaining=db.execute("SELECT 1 FROM runtime_values WHERE namespace='worker_requests' AND key>=? AND key<? "
                    "AND coalesce(json_extract(payload,'$.retired_process'),0)=0 LIMIT 1",(prefix,prefix+'\uffff')).fetchone()
            if remaining:
                report['processed'].append({'run':run,'done':False,'phase':'process_receipts'})
                continue
            if state=='PENDING':
                with closing(sqlite3.connect(jobs_path,timeout=.01)) as jobs, jobs:
                    jobs.execute('BEGIN IMMEDIATE')
                    live=jobs.execute("SELECT 1 FROM jobs INDEXED BY jobs_run_owner WHERE "
                        "(json_extract(payload,'$.run_id')=? OR (json_extract(payload,'$.run_id')>=? AND json_extract(payload,'$.run_id')<?)) "
                        "AND status IN ('queued','running') LIMIT 1", (run,run+'-',run+'.')).fetchone()
                    if live:
                        raise ValueError('owner still has a live Worker job')
                    jobs.execute('INSERT OR IGNORE INTO metadata VALUES(?,?)',('workspace_retirement:'+run,stamp))
                with journal.transaction(expand=False,timeout=.01,operation='owner.exit.claim') as db:
                    task=db.execute("SELECT receipt FROM runtime_tasks WHERE id=? AND status='SUCCEEDED'",(task_id,)).fetchone()
                    if not task:
                        raise ValueError('owner no longer terminal')
                    value=json.loads(task[0])
                    current_owner=(value.get('_gc_owner') or {}).get('run_id') or (value.get('frame') or {}).get('run_id')
                    if current_owner!=run:raise ValueError('Task owner changed during exit')
                    exiting=[r[0] for r in db.execute("SELECT artifact_id FROM artifact_ref WHERE owner_type='runtime_tasks' AND owner_id=json_array(?)",(task_id,))] if db.execute("SELECT 1 FROM sqlite_master WHERE name='artifact_ref'").fetchone() else []
                    value.pop('frame',None)
                    value.setdefault('_gc_owner',{'run_id':run})
                    value.update(process_retired=True,retired_frame={'at':stamp,'authority':'TERMINAL_MAINTENANCE_ACK','resume_allowed':False})
                    db.execute('UPDATE runtime_tasks SET receipt=? WHERE id=?',(json.dumps(value,separators=(',',':')),task_id))
                    if exiting:
                        from doxagent.v2_read.source_native_gc import queue_owner_exit
                        queue_owner_exit(db,exiting)
                    db.execute("UPDATE runtime_resource_exit SET state='DELETING',error=NULL WHERE run_id=?",(run,))
            paths=[*([artifact] if not keep_artifact else []),workspace/run,workspace/(run+'-o2')]
            done=True
            for path in paths:
                if path.is_symlink() or path.resolve()!=path:
                    raise ValueError('linked process owner')
                tombstone=path.with_name('.retiring-'+path.name)
                if path.is_dir():
                    path.rename(tombstone)
                if tombstone.exists() and not _unlink_page(tombstone,deadline=deadline):
                    done=False;break
            if done:
                with journal.transaction(timeout=.01,operation='owner.exit.done') as db:
                    db.execute("UPDATE runtime_resource_exit SET state='DONE',error=NULL WHERE run_id=?",(run,))
            report['processed'].append({'run':run,'done':done})
        except (OSError,ValueError,sqlite3.Error) as exc:
            report['errors'].append({'run':run,'error':str(exc)[:200]})
            with journal.transaction(timeout=.01,operation='owner.exit.retry') as db:
                db.execute('UPDATE runtime_resource_exit SET retry_at=?,error=? WHERE run_id=?',
                    ((now+timedelta(minutes=15)).isoformat(),str(exc)[:200],run))
    return report
