"""Bounded read-only production timing export. No model calls or body export."""
import argparse
from contextlib import closing
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time

ROOT=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
KNOWN=['case_fbc43d3c06a1459c868b78e0a094370b','case_1a1c0d6c6cbc45dca1852edb6c84c71e',
       'case_110829fc37084f4cb2fdb47ddf10b06e','case_adc9817fb471478aa11bcf0518f7be00']

def connect(path):
    db=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=.1)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db

def collect(limit=400, case_ids=None):
    started=time.monotonic();result={'at':datetime.now(UTC).isoformat(),'limit':limit,'cases':[]}
    with closing(connect(ROOT/'runtime/runtime.sqlite3')) as db,closing(connect(ROOT/'workspaces/worker-jobs.sqlite3')) as worker:
        query="SELECT case_id,ticker,created_at,status,technical_status,"
        query+="json_extract(payload_json,'$.runtime_mode') runtime_mode,json_extract(payload_json,'$.source.title') title FROM runtime_v2_cases "
        if case_ids:
            cases=[dict(row) for cid in case_ids if (row:=db.execute(query+'WHERE case_id=?',(cid,)).fetchone())]
        else:
            cases=[dict(r) for r in db.execute("SELECT case_id,ticker,created_at,status,technical_status,"
            "json_extract(payload_json,'$.runtime_mode') runtime_mode,"
            "json_extract(payload_json,'$.source.title') title FROM runtime_v2_cases ORDER BY rowid DESC LIMIT ?",(limit,))]
        ids={c['case_id'] for c in cases}
        for cid in ([] if case_ids else KNOWN):
            if cid not in ids:
                row=db.execute("SELECT case_id,ticker,created_at,status,technical_status,"
                    "json_extract(payload_json,'$.runtime_mode') runtime_mode,"
                    "json_extract(payload_json,'$.source.title') title FROM runtime_v2_cases WHERE case_id=?",(cid,)).fetchone()
                if row:cases.append(dict(row))
        for case in cases:
            if time.monotonic()-started>8:
                result['budget_exhausted']=True;break
            cid=case['case_id']
            effects=[dict(r) for r in db.execute("SELECT effect_id,effect_type,status,available_at,updated_at,attempt_count,"
                "json_extract(payload_json,'$.created_at') created_at,json_extract(payload_json,'$.lease_token') lease_token,"
                "json_extract(payload_json,'$.last_error') last_error,json_extract(payload_json,'$.payload.provider_wait') provider_wait "
                "FROM runtime_v2_effects WHERE case_id=? AND effect_type IN ('route_to_w3','w3_post_decision')",(cid,))]
            if not effects:continue
            case['effects']=effects
            case['stages']=[dict(r) for r in db.execute('SELECT phase,status,thread_id,accepted_at,committed_at,worker_job_id,disposition,error '
                'FROM runtime_v2_w3_stages WHERE case_id=?',(cid,))]
            record=db.execute("SELECT record_id,workspace,thread_id,expires_at FROM runtime_v2_w3_threads "
                "WHERE owner_case_id=? AND kind='CASE' LIMIT 1",(cid,)).fetchone()
            if record is None:
                thread=next((s['thread_id'] for s in case['stages'] if s['thread_id']),None)
                if thread:
                    record=db.execute('SELECT record_id,workspace,thread_id,expires_at FROM runtime_v2_w3_threads WHERE thread_id=?',
                                      (thread,)).fetchone()
            case['record']=dict(record) if record else None
            case['jobs']=[];case['receipts']=[]
            if record:
                for r in worker.execute("SELECT id,status,json_extract(payload,'$.run_id') run_id,"
                    "json_extract(payload,'$.attempt_id') attempt_id,json_extract(payload,'$.created_at') created_at,"
                    "json_extract(payload,'$.started_at') started_at,json_extract(payload,'$.finished_at') finished_at,"
                    "json_extract(payload,'$.updated_at') updated_at,json_extract(payload,'$.execution_phase') execution_phase,"
                    "json_extract(payload,'$.error_code') error_code,substr(json_extract(payload,'$.error_message'),1,900) error_message,"
                    "json_extract(payload,'$.infra_recovery_count') infra_recovery_count,"
                    "json_extract(payload,'$.resource_receipt.execution_timing') execution_timing,"
                    "json_extract(payload,'$.resource_receipt.startup_timing') startup_timing,"
                    "json_extract(payload,'$.resource_receipt.capsule_startup_timing') capsule_startup_timing,"
                    "json_extract(payload,'$.resource_receipt.cold_start_ms') cold_start_ms,"
                    "json_extract(payload,'$.resource_receipt.cleanup_ms') cleanup_ms "
                    "FROM jobs INDEXED BY jobs_run_owner WHERE json_extract(payload,'$.run_id')=? "
                    "AND json_extract(payload,'$.attempt_id') LIKE ?",(record['workspace'],cid[-24:]+'-%')):
                    job=dict(r)
                    for k in ('execution_timing','startup_timing','capsule_startup_timing'):
                        job[k]=json.loads(job[k]) if job[k] else None
                    case['jobs'].append(job)
                for phase in ('trade_decision','post_decision_operations'):
                    identity=f"runtime-worker:{cid}:{record['workspace']}:persistent_runtime_w3:{phase}:{cid}"
                    receipt=db.execute("SELECT json_extract(payload,'$.job_id') job_id,json_extract(payload,'$.status') status,"
                        "json_extract(payload,'$.finished_at') finished_at FROM runtime_values WHERE namespace='worker_receipts' AND key=?",(identity,)).fetchone()
                    if receipt:case['receipts'].append(dict(receipt)|{'phase':phase})
                slot=worker.execute("SELECT id,scope,state,claim_kind,created_at,ready_at,bound_at,"
                    "prepare_deadline_at,execution_lane FROM w3_ready_slots WHERE run_id=?",(record['workspace'],)).fetchone()
                case['ready']=dict(slot) if slot else None
            result['cases'].append(case)
        result['query_plans']={'owned_thread':db.execute('EXPLAIN QUERY PLAN SELECT workspace FROM runtime_v2_w3_threads WHERE owner_case_id=? AND kind=\'CASE\' LIMIT 1',(KNOWN[0],)).fetchall()}
        result['query_plans']={k:[list(r) for r in v] for k,v in result['query_plans'].items()}
    manifest=Path('/var/lib/doxagent/releases/w3-backlog-recovery-20261110.json')
    if manifest.exists():
        policy=json.loads(manifest.read_text());progress=json.loads(manifest.with_suffix('.progress.json').read_text())
        result['historical_case_ids']=list(policy['cases']);result['recovery']={'capacity':policy['capacity'],'done':len(progress['done']),'active':len(progress['active'])}
    result['elapsed_seconds']=time.monotonic()-started
    return result

if __name__=='__main__':
    os.nice(10)
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True);parser.add_argument('--limit',type=int,default=400)
    parser.add_argument('--case-ids')
    args=parser.parse_args();data=collect(args.limit,json.loads(Path(args.case_ids).read_text()) if args.case_ids else None)
    Path(args.out).write_text(json.dumps(data,separators=(',',':')))
    print(json.dumps({'cases':len(data['cases']),'seconds':data['elapsed_seconds'],'budget_exhausted':data.get('budget_exhausted',False)}))
