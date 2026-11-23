"""Exact new Maintenance thread owners; no SDK listing or global Job scans."""
import asyncio
from contextlib import closing
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3


def migrate(db):
    db.execute('CREATE TABLE IF NOT EXISTS worker_process_threads('
               'job_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,thread_id TEXT,status TEXT NOT NULL,'
               'creates_root INTEGER NOT NULL,consumed INTEGER NOT NULL DEFAULT 0)')
    db.execute('CREATE INDEX IF NOT EXISTS worker_process_threads_thread ON worker_process_threads(thread_id,status,consumed)')
    db.execute('CREATE TABLE IF NOT EXISTS worker_session_exit(thread_id TEXT PRIMARY KEY,due TEXT NOT NULL,retry_at TEXT NOT NULL,error TEXT)')
    db.execute('CREATE INDEX IF NOT EXISTS worker_session_exit_due ON worker_session_exit(retry_at,due)')
    db.execute('CREATE TABLE IF NOT EXISTS worker_session_retirements(thread_id TEXT PRIMARY KEY,state TEXT NOT NULL,receipt TEXT NOT NULL,updated_at TEXT NOT NULL)')


def observe_external_thread(db, job, request):
    """An explicitly reused managed thread loses local-only retirement authority."""
    if request is None or not request.thread_id or job.run_id.startswith('runtime-maintain-'):
        return
    if db.execute('SELECT 1 FROM worker_process_threads WHERE thread_id=? AND creates_root=1 LIMIT 1', (request.thread_id,)).fetchone():
        if db.execute("SELECT 1 FROM worker_session_retirements WHERE thread_id=? AND state IN ('DELETING','DELETED')", (request.thread_id,)).fetchone():
            raise ValueError('SESSION_RETIRED_NEW_SESSION_REQUIRED')
        db.execute('UPDATE worker_process_threads SET creates_root=0 WHERE thread_id=?', (request.thread_id,))
        db.execute('DELETE FROM worker_session_exit WHERE thread_id=?', (request.thread_id,))


def process_job(job):
    from doxagent.codex_runtime.schema import CodexD3Node
    node=getattr(job,'node',None)
    return job.run_id.startswith('runtime-maintain-') or getattr(node,'value',node) in {n.value for n in CodexD3Node}


def prepare(db, job, request):
    if not process_job(job):
        return
    if request is not None:
        if request.thread_id and db.execute("SELECT 1 FROM worker_session_retirements WHERE thread_id=? AND state IN ('DELETING','DELETED')",(request.thread_id,)).fetchone():
            raise ValueError('SESSION_RETIRED_NEW_SESSION_REQUIRED')
        # Historical jobs are deliberately not inferred to have complete local
        # ownership. Only a new, no-subagent root can become a local candidate.
        if not db.execute('SELECT 1 FROM jobs WHERE id=?',(job.job_id,)).fetchone():
            db.execute('INSERT OR IGNORE INTO worker_process_threads VALUES(?,?,?,?,?,0)',
                (job.job_id,job.run_id,job.thread_id,job.status,int(request.thread_id is None and (not getattr(request,'allow_subagents',False) or request.max_subagents==0))))
    db.execute('UPDATE worker_process_threads SET thread_id=coalesce(?,thread_id),status=? WHERE job_id=? '
               'AND (status<>? OR (? IS NOT NULL AND thread_id IS NOT ?))',
               (job.thread_id,job.status,job.job_id,job.status,job.thread_id,job.thread_id))


def consumed(db, job_id, now):
    # Called by the confirmed downstream ACK, in its existing compaction txn.
    row=db.execute('SELECT thread_id,creates_root FROM worker_process_threads WHERE job_id=?',(job_id,)).fetchone()
    db.execute('UPDATE worker_process_threads SET consumed=1 WHERE job_id=? AND consumed=0',(job_id,))
    if row and row[0] and row[1]:
        db.execute('INSERT OR IGNORE INTO worker_session_exit VALUES(?,?,?,NULL)',(row[0],now,now))


async def collect(jobs, *, now=None, limit=1):
    now=now or datetime.now(UTC);stamp=now.isoformat()
    def candidates():
        with closing(jobs.store.connect()) as db:
            return [r[0] for r in db.execute('SELECT thread_id FROM worker_session_exit WHERE retry_at<=? AND due<=? ORDER BY retry_at,due LIMIT ?', (stamp,stamp,limit))]
    report={'deleted':[],'deferred':[]}
    for thread in await asyncio.to_thread(candidates):
        try:
            def claim():
                with closing(jobs.store.connect()) as db,db:
                    db.execute('PRAGMA busy_timeout=10');db.execute('BEGIN IMMEDIATE')
                    if db.execute("SELECT 1 FROM worker_process_threads WHERE thread_id=? AND (consumed=0 OR status NOT IN ('succeeded','failed','cancelled')) LIMIT 1",(thread,)).fetchone():
                        return False
                    db.execute("INSERT INTO worker_session_retirements VALUES(?,'DELETING',?,?) ON CONFLICT(thread_id) DO UPDATE SET state='DELETING',updated_at=excluded.updated_at",(thread,json.dumps({'owner':'consumed-maintenance-root'}),stamp))
                    return True
            if not await asyncio.to_thread(claim):
                raise ValueError('thread has an unconsumed or live owner')
            transport=jobs.thread_lifecycle.transport
            try:
                meta=(await transport.request('thread/read',{'threadId':thread,'includeTurns':False}))['thread']
            except Exception as exc:
                if not any(s in str(exc).lower() for s in ('not found','no rollout','does not exist','unable to find')):
                    raise
                meta={}
            if meta.get('status',{}).get('type')=='active' or meta.get('parentThreadId') or meta.get('forkedFromId'):
                raise ValueError('thread active or has an undeclared parent')
            release=getattr(jobs._runtime,'release_thread',None)
            if release:
                await release(thread)
            try:
                await transport.request('thread/delete',{'threadId':thread})
            except Exception as exc:
                if not any(s in str(exc).lower() for s in ('not found','no rollout','does not exist','unable to find')):
                    raise
            if meta.get('path') and Path(meta['path']).exists():
                raise ValueError('SDK retained the deleted thread rollout')
            def finish():
                with closing(jobs.store.connect()) as db,db:
                    db.execute("UPDATE worker_session_retirements SET state='DELETED',updated_at=? WHERE thread_id=?",(stamp,thread))
                    db.execute('DELETE FROM worker_session_exit WHERE thread_id=?',(thread,))
            await asyncio.to_thread(finish)
            report['deleted'].append(thread)
        except Exception as exc:
            def retry():
                with closing(jobs.store.connect()) as db,db:
                    db.execute('UPDATE worker_session_exit SET retry_at=?,error=? WHERE thread_id=?',((now+timedelta(minutes=15)).isoformat(),str(exc)[:200],thread))
            await asyncio.to_thread(retry)
            report['deferred'].append(thread)
    return report
