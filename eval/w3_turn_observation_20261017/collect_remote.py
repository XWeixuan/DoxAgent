"""Read-only natural-flow acceptance. No replay, dispatch or business writes."""
import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

root=Path('/data/workspaces/w3-observation')
receipts=[]
for path in (root/'receipts').glob('*.json'):
    try: receipts.append(json.loads(path.read_text()))
    except (OSError,ValueError):pass
db=sqlite3.connect('file:/data/workspaces/worker-jobs.sqlite3?mode=ro',uri=True,timeout=1)
db.execute('PRAGMA query_only=ON')
jobs=[]
for r in receipts:
    row=db.execute("SELECT payload,request IS NULL FROM jobs WHERE json_extract(payload,'$.turn_id')=?",
                   (r['turn_id'],)).fetchone()
    if row:
        j=json.loads(row[0])
        jobs.append({k:j.get(k) for k in ('job_id','run_id','attempt_id','status','worker_generation',
                                         'created_at','started_at','finished_at','error_code','resource_receipt')}
                    |{'acknowledged':bool(row[1]),
                      'sdk_duration_ms':(j.get('telemetry') or {}).get('sdk_duration_ms')})
active=[json.loads(r[0]) for r in db.execute("SELECT payload FROM jobs WHERE status IN ('queued','running')")]
active=[{k:j.get(k) for k in ('job_id','run_id','attempt_id','status','started_at','execution_phase','error_code')}
        for j in active]
runtime=sqlite3.connect('file:/data/runtime/runtime.sqlite3?mode=ro',uri=True,timeout=1)
runtime.execute('PRAGMA query_only=ON')
from doxagent.v2_read.native_content import NativeContent
codec=NativeContent('/data/runtime/runtime.sqlite3')
case_results=[]
for receipt in receipts:
    row=runtime.execute('SELECT payload_json FROM runtime_v2_cases WHERE case_id=?',
                        (receipt.get('case_id'),)).fetchone()
    if row:
        c=json.loads(row[0])
        case_results.append({k:codec.decode(c.get(k)) for k in (
            'case_id','ticker','created_at','status','route','decision_completed_at',
            'w3_trade_decision','w3_failure_phase','w3_error')})
sources={name:hashlib.sha256(Path('/app/src/doxagent',name).read_bytes()).hexdigest()
         for name in ('codex_worker/w3_turn_probe.py','codex_worker/sdk_runtime.py',
                      'codex_worker/jobs.py','persistent_runtime_v2/w3.py')}
print(json.dumps({'at':datetime.now(UTC).isoformat(),'release_revision':os.environ.get('DOXAGENT_BUILD_COMMIT'),
                  'control':json.loads((root/'control.json').read_text()) if (root/'control.json').exists() else {},
                  'receipt_count':len(receipts),'bytes':sum(p.stat().st_size for p in (root/'receipts').glob('*.json')),
                  'receipts':receipts,'jobs':jobs,'case_results':case_results,
                  'active_jobs':active,'source_hashes':sources},ensure_ascii=False))
