"""One-time operator-approved recovery. Existing request/Job identities are immutable.

Prepare only with Scheduler/Worker stopped. Existing Effect.available_at and
Job.next_retry_at hold the exact snapshot. A finite helper releases the
operator-configured number of Cases; new realtime Cases are never included.
No runtime queue or schema.
"""
import argparse
from contextlib import contextmanager
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
import subprocess
import time

HOLD='9999-12-31T00:00:00+00:00'
HOLD_EPOCH=datetime.fromisoformat(HOLD).timestamp()
W3=('route_to_w3','w3_post_decision')

@contextmanager
def connect(path):
    db=sqlite3.connect(path,timeout=.1)
    db.row_factory=sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()

def save(path,value):
    path=Path(path);temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,indent=2),encoding='utf-8')
    temporary.chmod(0o600);temporary.replace(path)

def prepare(runtime,jobs,manifest,*,cutoff,capacity=2):
    if Path(manifest).exists():raise ValueError('Existing recovery manifest must be resumed, not replaced')
    with connect(runtime) as db:
        # The recent map resolves the existing 24-character attempt prefix.
        recent=db.execute('SELECT case_id,created_at FROM runtime_v2_cases ORDER BY rowid DESC LIMIT 5000').fetchall()
        by_prefix={r['case_id'][-24:]:dict(r) for r in recent}
        cases={}
        for status in ('PENDING','PENDING_RETRY','RUNNING'):
            for row in db.execute('SELECT effect_id,case_id,status,available_at,payload_json FROM runtime_v2_effects '
                                  'WHERE status=? AND effect_type IN (?,?)',(status,*W3)):
                case=db.execute('SELECT case_id,created_at FROM runtime_v2_cases WHERE case_id=?',(row['case_id'],)).fetchone()
                if case and case['created_at']<=cutoff:
                    item=cases.setdefault(row['case_id'],dict(created_at=case['created_at'],effects=[],jobs=[]))
                    item['effects'].append(dict(row))
    with connect(jobs) as db:
        for row in db.execute("SELECT id,status,json_extract(request,'$.node') node,"
                "json_extract(request,'$.attempt_id') attempt_id,json_extract(payload,'$.next_retry_at') retry_at,"
                "json_extract(payload,'$.request_sha256') request_sha256 FROM jobs WHERE status IN ('queued','running')"):
            if row['node']!='persistent_runtime_w3':continue
            case=by_prefix.get((row['attempt_id'] or '')[:24])
            if not case:raise ValueError('Active W3 Job cannot be resolved in bounded Case snapshot: '+row['id'])
            if case['created_at']>cutoff:continue
            item=cases.setdefault(case['case_id'],dict(created_at=case['created_at'],effects=[],jobs=[]))
            item['jobs'].append(dict(row))
    state=dict(cutoff=cutoff,capacity=capacity,runtime=str(runtime),jobs_db=str(jobs),cases=cases,active=[],done=[],released=[])
    # Save the exact control-field snapshot before either database is changed.
    save(manifest,state)
    with connect(runtime) as db:
        for item in cases.values():
            for effect in item['effects']:
                db.execute("UPDATE runtime_v2_effects SET status='PENDING_RETRY',available_at=?,payload_json="
                    "json_set(json_remove(payload_json,'$.lease_until','$.payload.provider_wait'),"
                    "'$.status','PENDING_RETRY','$.available_at',?,'$.last_error','ONE_TIME_BACKLOG_RECOVERY') "
                    "WHERE effect_id=? AND status IN ('PENDING','PENDING_RETRY','RUNNING')",(HOLD,HOLD,effect['effect_id']))
    with connect(jobs) as db:
        for item in cases.values():
            for job in item['jobs']:
                db.execute("UPDATE jobs SET payload=json_set(payload,'$.next_retry_at',?) "
                           "WHERE id=? AND status IN ('queued','running')",(HOLD_EPOCH,job['id']))
    return state

def finished(state,cid):
    with connect(state['runtime']) as db:
        row=db.execute("SELECT 1 FROM runtime_v2_effects WHERE case_id=? AND effect_type IN (?,?) "
                       "AND status IN ('PENDING','PENDING_RETRY','RUNNING') LIMIT 1",(cid,*W3)).fetchone()
        if row:return False
    with connect(state['jobs_db']) as db:
        return all(not db.execute("SELECT 1 FROM jobs WHERE id=? AND status IN ('queued','running')",(j['id'],)).fetchone()
                   for j in state['cases'][cid]['jobs'])

def release(state,cid):
    now=datetime.now(UTC).isoformat()
    with connect(state['runtime']) as db:
        for effect in state['cases'][cid]['effects']:
            db.execute("UPDATE runtime_v2_effects SET available_at=?,payload_json="
                       "json_set(payload_json,'$.available_at',?,'$.last_error',?) "
                       "WHERE effect_id=? AND status='PENDING_RETRY' AND available_at=?",
                       (now,now,json.loads(effect['payload_json']).get('last_error'),effect['effect_id'],HOLD))
    with connect(state['jobs_db']) as db:
        for job in state['cases'][cid]['jobs']:
            db.execute("UPDATE jobs SET payload=json_set(payload,'$.next_retry_at',?) "
                       "WHERE id=? AND status IN ('queued','running') AND json_extract(payload,'$.next_retry_at')=?",
                       (max(job['retry_at'] or 0,time.time()),job['id'],HOLD_EPOCH))

def step(state,manifest):
    def progress():
        save(Path(manifest).with_suffix('.progress.json'),
             {key:state[key] for key in ('active','done','released')})
    completed=[cid for cid in state['active'] if finished(state,cid)]
    state['done']+=completed
    state['active']=[cid for cid in state['active'] if cid not in completed]
    if completed:progress()
    waiting=[cid for cid in sorted(state['cases'],key=lambda c:state['cases'][c]['created_at'])
             if cid not in state['active'] and cid not in state['done']]
    for cid in waiting[:max(0,state['capacity']-len(state['active']))]:
        # Publish ownership before releasing deadlines. Resume replays release
        # idempotently if this temporary helper exits between the two DBs.
        state['active'].append(cid);progress()
    for cid in state['active']:
        if cid not in state['released']:
            release(state,cid);state['released'].append(cid);progress()
    return dict(at=datetime.now(UTC).isoformat(),active=state['active'],done=len(state['done']),total=len(state['cases']))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','run'])
    p.add_argument('--manifest',required=True);p.add_argument('--runtime');p.add_argument('--jobs');p.add_argument('--cutoff')
    args=p.parse_args()
    if args.action=='prepare':
        for role in ['v2-scheduler','codex-worker']:
            info=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-'+role+'-1']))[0]
            assert not info['State']['Running'],role+' must be stopped for the one-time snapshot'
        state=prepare(args.runtime,args.jobs,args.manifest,cutoff=args.cutoff)
        print(json.dumps({'cases':len(state['cases']),'effects':sum(len(c['effects']) for c in state['cases'].values()),
                          'jobs':sum(len(c['jobs']) for c in state['cases'].values()),'capacity':state['capacity']}))
    else:
        state=json.loads(Path(args.manifest).read_text())
        progress=Path(args.manifest).with_suffix('.progress.json')
        if progress.exists():state.update(json.loads(progress.read_text()))
        while len(state['done'])<len(state['cases']):
            try:print(json.dumps(step(state,args.manifest)),flush=True)
            except sqlite3.OperationalError as exc:
                if 'locked' not in str(exc).lower():raise
            time.sleep(2)
