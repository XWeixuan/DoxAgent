"""Retire undispatched old O3 requests and fence only completed-O2 tasks.

Run under the release operator, restore immediately after the Scheduler cutover.
No model result is manufactured: receipts explicitly mean cancelled pre-dispatch.
"""
from datetime import UTC,datetime,timedelta
import hashlib
import json
from pathlib import Path
import sqlite3


def settle(runtime_path, jobs_path):
    from doxagent.v2_read.native_content import NativeContent
    from doxagent.codex_worker.schema import WorkerJob,WorkerRunRequest
    now=datetime.now(UTC);until=(now+timedelta(minutes=30)).isoformat()
    codec=NativeContent(runtime_path)
    with sqlite3.connect(runtime_path,timeout=.1) as db:
        active=db.execute("SELECT id,receipt,token,owner FROM runtime_tasks WHERE kind='MAINTENANCE' AND status='RUNNING'").fetchall()
        pending=db.execute("SELECT id,due_at FROM runtime_tasks WHERE kind='MAINTENANCE' AND status='PENDING' AND due_at<?",(until,)).fetchall()
    cancellations=[];held=[]
    for identity,raw,token,owner in active:
        value=json.loads(raw)
        if not value.get('o2'):
            continue
        run=value.get('_gc_owner',{}).get('run_id') or value.get('frame',{}).get('run_id')
        if not run:
            continue
        prefix='runtime-worker:'+run+':'
        with sqlite3.connect(runtime_path,timeout=.1) as db:
            requests=db.execute("SELECT payload FROM runtime_values WHERE namespace='worker_requests' AND key>=? AND key<?",(prefix,prefix+'\uffff')).fetchall()
        prepared=[]
        for (request_raw,) in requests:
            request=WorkerRunRequest.model_validate(codec.decode(json.loads(request_raw)))
            if not request.run_id.startswith(run+'-o3') or not request.idempotency_key:
                continue
            data=request.model_dump(mode='json')
            for key in ('task_phase','web_search_mode'):
                if data.get(key) is None:data.pop(key,None)
            job_id=hashlib.sha256(f'{request.run_id}:{request.idempotency_key}'.encode()).hexdigest()
            prepared.append(WorkerJob(job_id=job_id,run_id=request.run_id,attempt_id=request.attempt_id,
                request_sha256=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest(),
                status='cancelled',execution_phase='SETTLED',finished_at=now,
                error_code='O3_ADMINISTRATIVELY_PAUSED',error_message='Cancelled before dispatch for O3-off release cutover'))
        with sqlite3.connect(jobs_path,timeout=.1) as db:
            db.execute('BEGIN IMMEDIATE')
            for job in prepared:
                live=db.execute("SELECT status FROM jobs WHERE id=? AND status IN ('queued','running')",(job.job_id,)).fetchone()
                if live:
                    raise ValueError('Cancel the exact live O3 Job through Worker before cutover: '+job.job_id)
                # A real existing Job (including unknown/running) is untouched.
                n=db.execute("INSERT OR IGNORE INTO jobs(id,status,payload,request,priority) VALUES(?,'cancelled',?,NULL,0)",
                    (job.job_id,job.model_dump_json())).rowcount
                if n:cancellations.append(job.job_id)
        with sqlite3.connect(runtime_path,timeout=.1) as db:
            n=db.execute("UPDATE runtime_tasks SET status='HELD',token=token+1,owner=NULL,lease_until=NULL "
                         "WHERE id=? AND status='RUNNING' AND token=? AND owner=?",(identity,token,owner)).rowcount
            if n:held.append(identity)
    with sqlite3.connect(runtime_path,timeout=.1) as db:
        for identity,due in pending:
            db.execute("UPDATE runtime_tasks SET due_at=? WHERE id=? AND status='PENDING' AND due_at=?",(until,identity,due))
    return {'at':now.isoformat(),'until':until,'held_completed_o2':held,'pending_due':pending,'cancelled_before_dispatch':cancellations}


def restore(runtime_path, receipt):
    now=datetime.now(UTC).isoformat()
    with sqlite3.connect(runtime_path,timeout=.1) as db:
        for identity in receipt['held_completed_o2']:
            db.execute("UPDATE runtime_tasks SET status='PENDING',owner=NULL,lease_until=NULL,due_at=? WHERE id=? AND status='HELD'",(now,identity))
        for identity,due in receipt['pending_due']:
            db.execute("UPDATE runtime_tasks SET due_at=? WHERE id=? AND status='PENDING' AND due_at=?",(due,identity,receipt['until']))


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('runtime');p.add_argument('jobs');p.add_argument('receipt',type=Path)
    p.add_argument('--restore',action='store_true');args=p.parse_args()
    if args.restore:
        restore(args.runtime,json.loads(args.receipt.read_text()));print('restored')
    else:
        result=settle(args.runtime,args.jobs)
        args.receipt.write_text(json.dumps(result,indent=2))
        print(json.dumps(result))
