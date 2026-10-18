"""Terminal non-W3 SDK sessions exit at their Worker owner, with tree protection."""
import asyncio
from contextlib import closing
from datetime import UTC,datetime,timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3

SOURCE_KINDS=['cli','vscode','exec','appServer','subAgent','subAgentReview','subAgentCompact',
              'subAgentThreadSpawn','subAgentOther','unknown']


def closure(seeds, neighbours):
    result=set(seeds);pending=list(result)
    while pending:
        for other in neighbours.get(pending.pop(),()):
            if other not in result:result.add(other);pending.append(other)
    return result


def references(value):
    if isinstance(value,dict):
        for key,item in value.items():
            if key in {'thread_id','parent_thread_id'} and isinstance(item,str):yield ('thread',item)
            if key in {'run_id','workspace_run_id','initialization_id'} and isinstance(item,str):yield ('run',item)
            yield from references(item)
    elif isinstance(value,list):
        for item in value:yield from references(item)


class TerminalSessions:
    def __init__(self,jobs,transport,*,owner_holds=None,extra_job_databases=()):
        self.jobs,self.transport=jobs,transport
        self.owner_holds=owner_holds or (lambda:set())
        self.paths=[jobs.store.path,*map(Path,extra_job_databases)]
        with closing(jobs.store.connect()) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS worker_session_retirements(thread_id TEXT PRIMARY KEY,state TEXT NOT NULL,receipt TEXT NOT NULL,updated_at TEXT NOT NULL)')

    def validate_submit(self,request):
        if not request.thread_id:return
        with closing(self.jobs.store.connect()) as db:
            row=db.execute('SELECT state FROM worker_session_retirements WHERE thread_id=?',(request.thread_id,)).fetchone()
        if row and row[0] in {'DELETING','DELETED'}:raise ValueError('SESSION_RETIRED_NEW_SESSION_REQUIRED')

    def owned(self,now):
        owned={};protected=set();runs=set()
        holds=set(self.owner_holds())
        protected.update(v for k,v in holds if k=='thread');runs.update(v for k,v in holds if k=='run')
        for path in self.paths:
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                rows=db.execute("SELECT id,status,json_extract(payload,'$.thread_id'),json_extract(request,'$.thread_id'),json_extract(payload,'$.run_id'),coalesce(json_extract(payload,'$.finished_at'),json_extract(payload,'$.updated_at')),json_extract(payload,'$.final_response') IS NOT NULL,json_extract(payload,'$.error_code') IS NOT NULL,json_extract(request,'$.task_phase') FROM jobs")
                for job,status,thread,requested,run,at,output,error,phase in rows:
                    ids={x for x in (thread,requested) if x}
                    w3=(run or '').startswith(('w3-case-','w3-template-')) or phase in {'w3_template','trade_decision','post_decision_operations'}
                    age=(now-datetime.fromisoformat(at)).total_seconds() if at else -1
                    retained=any(run==r or (run or '').startswith(r+'-') for r in runs)
                    eligible=(status in {'succeeded','failed','cancelled'} and not w3 and not retained
                              and age>=(7 if status=='succeeded' else 14)*86400
                              and (output if status=='succeeded' else error or status=='cancelled'))
                    for identity in ids:
                        owned.setdefault(identity,[]).append(dict(job=job,path=str(path),run=run,eligible=eligible))
                        if not eligible:protected.add(identity)
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='w3_thread_registry'").fetchone():
                    protected.update(r[0] for r in db.execute("SELECT thread_id FROM w3_thread_registry WHERE status<>'DELETED'"))
        return owned,protected

    async def listing(self):
        threads={}
        for archived in (False,True):
            cursor=None
            while True:
                page=await self.transport.request('thread/list',dict(limit=100,cursor=cursor,archived=archived,
                           sourceKinds=SOURCE_KINDS,modelProviders=[],useStateDbOnly=True))
                for row in page.get('data',[]):threads[row['id']]=row
                cursor=page.get('nextCursor')
                if not cursor:break
        return threads

    @staticmethod
    def groups(threads):
        edges={};sessions={}
        for identity,row in threads.items():
            for parent in {row.get('forkedFromId'),row.get('parentThreadId')} - {None}:
                edges.setdefault(identity,set()).add(parent);edges.setdefault(parent,set()).add(identity)
            session=row.get('sessionId')
            if session:
                anchor=sessions.setdefault(session,identity)
                edges.setdefault(anchor,set()).add(identity);edges.setdefault(identity,set()).add(anchor)
        return edges

    async def collect(self,*,now=None,limit=32,dry_run=False):
        from doxagent.trade_execution.worker import WriterLock
        now=now or datetime.now(UTC)
        report=dict(eligible_threads=0,deleted=[],errors=[],protected=0,allocated_before_deleted=0)
        try:
            with WriterLock(self.jobs.store.root/'session-retirement'):
                threads=await self.listing();owned,protected=self.owned(now);edges=self.groups(threads)
                for identity,row in threads.items():
                    if row.get('status',{}).get('type')=='active':protected.add(identity)
                    # Unknown roots are not Worker-owned sessions. Subagents can
                    # exit with an eligible owned parent, including archived ones.
                    if identity not in owned and not row.get('forkedFromId') and not row.get('parentThreadId'):
                        protected.add(identity)
                    if now.timestamp()-row.get('updatedAt',now.timestamp())<7*86400:protected.add(identity)
                protected=closure(protected,edges);report['protected']=len(protected)
                candidates={t for t,jobs in owned.items() if jobs and all(j['eligible'] for j in jobs)}-protected
                seen=set()
                for identity in sorted(candidates):
                    group=closure({identity},edges)
                    if group&seen or group&protected:continue
                    seen.update(group);group&=threads.keys()
                    if not group:continue
                    report['eligible_threads']+=len(group)
                    if dry_run:continue
                    if len(report['deleted'])>=limit:break
                    metadata={}
                    try:
                        for thread in group:
                            metadata[thread]=(await self.transport.request('thread/read',dict(threadId=thread,includeTurns=False)))['thread']
                        if any(r.get('status',{}).get('type')=='active' for r in metadata.values()):continue
                        paths={str(Path(r['path']).resolve()) for r in metadata.values() if r.get('path')}
                        opened=False
                        if os.name=='posix':
                            for proc in Path('/proc').iterdir():
                                if not proc.name.isdigit():continue
                                for fd in (proc/'fd').glob('*'):
                                    try:opened=opened or os.readlink(fd) in paths
                                    except OSError:pass
                        if opened:continue
                        # Refresh current owner references immediately before the
                        # claim; new Worker submissions cannot resume DELETING IDs.
                        _owned,again=self.owned(now)
                        if closure(again,edges)&group:continue
                        with closing(self.jobs.store.connect()) as db,db:
                            db.execute('BEGIN IMMEDIATE')
                            active=[r[0] for r in db.execute("SELECT json_extract(payload,'$.thread_id') FROM jobs WHERE status IN ('queued','running') UNION SELECT json_extract(request,'$.thread_id') FROM jobs WHERE status IN ('queued','running')")]
                            if group&set(active):continue
                            for thread in group:
                                receipt=dict(owner='terminal-worker-session',jobs=[j['job'] for j in owned.get(thread,[])],
                                             parent=metadata[thread].get('parentThreadId'),fork=metadata[thread].get('forkedFromId'))
                                db.execute('INSERT INTO worker_session_retirements VALUES(?,?,?,?) ON CONFLICT(thread_id) DO UPDATE SET state=excluded.state,receipt=excluded.receipt,updated_at=excluded.updated_at',
                                           (thread,'DELETING',json.dumps(receipt),now.isoformat()))
                        # Children leave before their parent; the entire group is
                        # already fenced, so an interrupted pass can retry safely.
                        def depth(thread,seen=frozenset()):
                            if thread in seen:return 0
                            parents={metadata[thread].get('parentThreadId'),metadata[thread].get('forkedFromId')}&group
                            return 1+max((depth(p,seen|{thread}) for p in parents),default=0)
                        order=sorted(group,key=depth,reverse=True)
                        for thread in order:
                            release=getattr(getattr(self.jobs,'_runtime',None),'release_thread',None)
                            if release:await release(thread)
                            p=Path(metadata[thread]['path']) if metadata[thread].get('path') else None
                            before=p.stat() if p and p.is_file() else None
                            try:await self.transport.request('thread/delete',dict(threadId=thread))
                            except Exception as exc:
                                if not any(s in str(exc).lower() for s in ('not found','no rollout','does not exist','unable to find')):raise
                            if p and p.exists():raise ValueError('SDK delete retained the rollout file')
                            with closing(self.jobs.store.connect()) as db,db:
                                db.execute("UPDATE worker_session_retirements SET state='DELETED',updated_at=? WHERE thread_id=?",(now.isoformat(),thread))
                                for job in owned.get(thread,[]):
                                    if Path(job['path'])!=self.jobs.store.path:continue
                                    # Final response, usage, errors, request hash
                                    # and job identity stay durable. The exited
                                    # session's prompt/event stream is process data.
                                    db.execute("UPDATE jobs SET request=NULL,payload=CASE WHEN json_type(payload,'$.telemetry')='object' THEN json_set(payload,'$.telemetry.events',json('[]')) ELSE payload END WHERE id=? AND status IN ('succeeded','failed','cancelled')",(job['job'],))
                                    db.execute('DELETE FROM events WHERE job_id=?',(job['job'],))
                            report['deleted'].append(thread)
                            if before:report['allocated_before_deleted']+=getattr(before,'st_blocks',(before.st_size+511)//512)*512
                    except Exception as exc:
                        report['errors'].append(dict(thread=identity,error=type(exc).__name__+':'+str(exc)[:250]))
                return report
        except RuntimeError as exc:
            if str(exc)!='EXECUTOR_ALREADY_RUNNING':raise
            return {**report,'deferred':'owner cleanup already running'}


def business_holds(settings,*,now=None):
    """Continuable workflows protect their threads; published old runs do not."""
    from doxagent.v2_read.native_content import NativeContent
    now=now or datetime.now(UTC);holds=set()
    scopes=[(settings.codex_runtime_sqlite_path,"SELECT payload_json AS payload FROM codex_runtime_records WHERE record_type='checkpoints' AND updated_at>?",((now-timedelta(days=14)).isoformat(),)),
            (settings.ticker_initialization_control_path,"SELECT payload FROM initialization_runs WHERE status NOT IN ('SUCCEEDED','FAILED','CANCELLED') OR json_extract(payload,'$.manual_resume_required')=1",()),
            (settings.persistent_runtime_v2_sqlite_path,"SELECT inputs,receipt FROM runtime_tasks WHERE status IN ('PENDING','RUNNING','RETRY','WAITING')",())]
    for path,sql,args in scopes:
        if not path or not Path(path).is_file():continue
        with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=NativeContent(path).row
            for row in db.execute(sql,args):
                for raw in row:
                    holds.update(references(json.loads(raw)))
    return holds


def main():
    import argparse
    from types import SimpleNamespace
    from doxagent.settings import DoxAgentSettings
    from .job_store import JobStore
    from .thread_lifecycle import AppServerThreads
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace-root',type=Path,required=True)
    p.add_argument('--extra-job-db',type=Path,action='append',default=[])
    p.add_argument('--limit',type=int,default=32)
    p.add_argument('--dry-run',action='store_true');args=p.parse_args()
    if not 1<=args.limit<=1000:p.error('limit must be 1..1000')
    settings=DoxAgentSettings();transport=AppServerThreads()
    owner=TerminalSessions(SimpleNamespace(store=JobStore(args.workspace_root)),transport,
        owner_holds=lambda:business_holds(settings),extra_job_databases=args.extra_job_db)
    async def run():
        try:print(json.dumps(await owner.collect(limit=args.limit,dry_run=args.dry_run)),flush=True)
        finally:await transport.close()
    asyncio.run(run())


if __name__=='__main__':main()
