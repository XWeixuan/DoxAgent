"""Read-only post-release natural-flow acceptance and failed-case retry inventory."""
import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SINCE = '2026-10-26T10:40:36+00:00'

def connect(path):
    db = sqlite3.connect('file:'+path+'?mode=ro', uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db

def rows(db, sql, args=()):
    return [dict(r) for r in db.execute(sql, args)]

worker = connect('/data/workspaces/worker-jobs.sqlite3')
runtime = connect('/data/runtime/runtime.sqlite3')
out = dict(at=datetime.now(UTC).isoformat(), since=SINCE,
           release=os.getenv('DOXAGENT_BUILD_COMMIT'))
out['sources'] = {name: hashlib.sha256((Path('/app/src/doxagent/codex_worker')/name).read_bytes()).hexdigest()
                  for name in ('startup_admission.py', 'jobs.py')}
out['slots'] = rows(worker, "SELECT id,state,case_id,thread_id,created_at,ready_at,error,claim_kind FROM w3_ready_slots WHERE created_at>=? ORDER BY created_at", (SINCE,))
out['jobs'] = rows(worker, "SELECT id,status,request,payload FROM jobs WHERE json_extract(payload,'$.created_at')>=? ORDER BY sequence DESC", (SINCE,))
for job in out['jobs']:
    request, payload = json.loads(job.pop('request') or '{}'), json.loads(job.pop('payload') or '{}')
    job['request'] = {k: request.get(k) for k in ('run_id','record_id','source_case_id','thread_id','model','task_phase','phase')}
    job['receipt'] = {k: payload.get(k) for k in ('created_at','started_at','completed_at','thread_id','turn_id','error_code','error_message','execution_phase','timings')}
out['cases'] = rows(runtime, "SELECT case_id,ticker,status,technical_status,created_at,updated_at,json_extract(payload_json,'$.error_code') error_code,json_extract(payload_json,'$.error_message') error_message FROM runtime_v2_cases WHERE created_at>=? ORDER BY created_at", (SINCE,))
for case in out['cases']:
    payload = json.loads(runtime.execute('SELECT payload_json FROM runtime_v2_cases WHERE case_id=?', (case['case_id'],)).fetchone()[0])
    case['decision_completed_at'] = payload.get('decision_completed_at')
    case['w3_trade_decision'] = payload.get('w3_trade_decision')
    case['w3_operations_status'] = payload.get('w3_operations_status')
out['w3'] = rows(runtime, "SELECT case_id,ticker,status,created_at,updated_at,payload_json FROM runtime_v2_w3_cases WHERE created_at>=? ORDER BY created_at", (SINCE,))
for case in out['w3']:
    payload = json.loads(case.pop('payload_json'))
    case['business'] = {k: payload.get(k) for k in ('thread_id','trade_decision','trade_intent','trade_intents','error_code','error_message','case_type')}
out['turns'] = rows(runtime, "SELECT turn_id,case_id,lane,round_name,status,response_id,created_at FROM runtime_v2_turns WHERE created_at>=? ORDER BY created_at", (SINCE,))
out['stages'] = rows(runtime, "SELECT * FROM runtime_v2_w3_stages WHERE case_id IN (SELECT case_id FROM runtime_v2_cases WHERE created_at>=?)", (SINCE,))
for stage in out['stages']:
    for key in ('result_json', 'request_json'):
        if key in stage:
            value = stage.pop(key)
            stage[key+'_present'] = bool(value)
out['failed_slot_summary'] = rows(worker, "SELECT state,count(*) n,min(bound_at) oldest,max(bound_at) newest FROM w3_ready_slots WHERE case_id IS NOT NULL AND error='TimeoutError' AND thread_id IS NULL GROUP BY state")
out['recent_failed_slots'] = rows(worker, "SELECT id,state,case_id,record_id,thread_id,prepare_deadline_at,bound_at,error FROM w3_ready_slots WHERE case_id IS NOT NULL AND error='TimeoutError' ORDER BY bound_at DESC LIMIT 5")
for slot in out['recent_failed_slots']:
    slot['operation'] = rows(worker, "SELECT status,thread_id,startup_attempts,step,prepare_deadline_at FROM w3_thread_operations WHERE operation_key=?", ('w3-slot-fork:'+slot['id'],))
out['new_prepare_failures'] = rows(runtime, "SELECT case_id,created_at,updated_at FROM runtime_v2_cases WHERE created_at>=? AND json_extract(payload_json,'$.error_code')='w3_ready_prepare_failed'", (SINCE,))
out['latest_cases'] = rows(runtime, "SELECT case_id,ticker,status,created_at,updated_at FROM runtime_v2_cases ORDER BY created_at DESC LIMIT 5")
out['active_effects'] = rows(runtime, "SELECT effect_id,case_id,effect_type,status,attempt_count,updated_at FROM runtime_v2_effects WHERE status IN ('PENDING','PENDING_RETRY','RUNNING') ORDER BY updated_at DESC LIMIT 12")
print(json.dumps(out, ensure_ascii=False))
