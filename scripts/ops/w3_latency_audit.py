"""Read-only, bounded W3 end-to-end timing. No model, trading or DB writes.

Measures whole Cases across BOTH phases. Only proven SDK intervals and marked
provider waits are subtracted, as unions; internal queues/retries remain charged.
Missing SDK/trace evidence is reported, never treated as zero or passing.
"""
import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import sqlite3


def instant(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()


def merged(intervals):
    result=[]
    for start,end in sorted(intervals):
        if end<start:
            raise ValueError('reversed timing interval')
        if result and start<=result[-1][1]:
            result[-1][1]=max(result[-1][1],end)
        else:
            result.append([start,end])
    return result


def duration(intervals):
    return sum(end-start for start,end in merged(intervals))


def breakdown(start,end,sdk,external):
    clip=lambda xs:[(max(a,start),min(b,end)) for a,b in xs if b>start and a<end]
    sdk,external=clip(sdk),clip(external)
    sdk_s=duration(sdk)
    excluded=duration(sdk+external)
    return dict(total_ms=(end-start)*1000,sdk_ms=sdk_s*1000,
                provider_wait_ms=(excluded-sdk_s)*1000,
                non_sdk_including_provider_ms=((end-start)-sdk_s)*1000,
                non_sdk_ms=max(0,(end-start)-excluded)*1000)


def summary(values):
    values=sorted(values)
    if not values:return dict(count=0,p50=None,p95=None,maximum=None)
    pct=lambda x:values[max(0,math.ceil(len(values)*x)-1)]
    return dict(count=len(values),p50=pct(.5),p95=pct(.95),maximum=values[-1])


def audit(runtime,jobs,*,since,until=None,limit=300):
    connect=lambda p:sqlite3.connect(Path(p).resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
    db,worker=connect(runtime),connect(jobs)
    db.row_factory=worker.row_factory=sqlite3.Row
    # Bound by rowid FIRST. A time predicate without an index is not a bound.
    candidates=db.execute("SELECT case_id,created_at,json_extract(payload_json,'$.runtime_mode') mode "
                          "FROM runtime_v2_cases ORDER BY rowid DESC LIMIT ?",(limit,)).fetchall()
    cases=[]
    for case in candidates:
        if (instant(case['created_at'])<instant(since) or case['mode']=='CLOSED'
            or until and instant(case['created_at'])>instant(until)):continue
        cid=case['case_id']
        effects=[dict(r) for r in db.execute("SELECT effect_id,effect_type,status,created_at "
                 "FROM runtime_v2_effects WHERE case_id=? AND effect_type IN "
                 "('route_to_w3','w3_post_decision')",(cid,))]
        if not effects:continue
        phases=[dict(r) for r in db.execute('SELECT * FROM runtime_v2_w3_stages WHERE case_id=?',(cid,))]
        traces=[]
        external=[]
        for effect in effects:
            prefix=effect['effect_id']+':'
            records=[json.loads(r[0]) for r in db.execute(
                "SELECT payload FROM runtime_values WHERE namespace='w3_boundary_timing' AND key>=? AND key<?",
                (prefix,prefix+'\uffff'))]
            records.sort(key=lambda r:r['started_at'])
            traces+=records
            for index,trace in enumerate(records):
                wait=trace.get('provider_wait')
                if wait and index+1<len(records):
                    following=records[index+1]
                    # A rebased available_at is the recovery eligibility time.
                    # Delay AFTER it is internal scheduler time, never excluded.
                    wait_end=min(instant(wait['retry_at']),instant(following['effect_available_at']),
                              instant(following['started_at']))
                    if wait_end>=instant(wait['observed_at']):
                        external.append((instant(wait['observed_at']),wait_end))
        result=dict(case_id=cid,complete=False,missing=[])
        if len(phases)!=2 or any(p['status']!='COMMITTED' for p in phases):
            result['missing'].append('phase_not_committed')
        if len(effects)!=2 or any(e['status']!='COMPLETED' for e in effects):
            result['missing'].append('effect_not_completed')
        ends=[instant(t['finished_at']) for t in traces if t.get('finished_at')]
        if not ends:result['missing'].append('end_to_end_trace')
        sdk=[]
        runs=set()
        for phase in phases:
            if phase['worker_job_id']:
                r=worker.execute("SELECT json_extract(payload,'$.run_id') FROM jobs WHERE id=?",
                                 (phase['worker_job_id'],)).fetchone()
                if r:runs.add(r[0])
        found=set()
        for run in runs:
            for r in worker.execute("SELECT id,status,json_extract(payload,'$.resource_receipt.execution_timing') timing "
                "FROM jobs INDEXED BY jobs_run_owner WHERE json_extract(payload,'$.run_id')=? "
                "AND json_extract(payload,'$.attempt_id') LIKE ?",(run,cid[-24:]+'-%')):
                if r['id'] in found:continue
                found.add(r['id'])
                timing=json.loads(r['timing']) if r['timing'] else {}
                a,b=timing.get('sdk_dispatched_at'),timing.get('sdk_completed_at')
                if a and b:sdk.append((instant(a),instant(b)))
                else:result['missing'].append('sdk_timing:'+r['id'])
        if not found:result['missing'].append('worker_job')
        if not result['missing']:
            start=min(instant(e['created_at']) for e in effects)
            result.update(breakdown(start,max(ends),sdk,external),complete=True)
        cases.append(result)
    db.close();worker.close()
    measured=[r for r in cases if r['complete']]
    stats=summary([r['non_sdk_ms'] for r in measured])
    truncated=bool(len(candidates)==limit and instant(candidates[-1]['created_at'])>instant(since))
    return dict(since=since,until=until,window_truncated=truncated,case_count=len(cases),measured_count=len(measured),
                incomplete_count=len(cases)-len(measured),non_sdk_ms=stats,
                p95_within_5s=stats['p95'] is not None and stats['p95']<=5000,
                acceptance_ready=(not truncated and len(measured)>=60 and len(measured)==len(cases)
                                  and stats['p95'] is not None and stats['p95']<=5000),
                cases=cases)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--runtime',required=True)
    parser.add_argument('--jobs',required=True)
    parser.add_argument('--since',required=True)
    parser.add_argument('--until')
    parser.add_argument('--limit',type=int,default=300)
    args=parser.parse_args()
    print(json.dumps(audit(args.runtime,args.jobs,since=args.since,until=args.until,limit=args.limit),indent=2))
