"""Bounded metadata/log/trace reader; no model or DB writes. Nice=10."""
from contextlib import closing
from datetime import UTC,datetime
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

ROOT=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
targets=json.loads(Path(sys.argv[1]).read_text());out=Path(sys.argv[2]);os.nice(10)
started=time.monotonic();result={'at':datetime.now(UTC).isoformat(),'db':[],'files':[],'logs':[],
                                'transactions':[],'operations':[],'content_shapes':[],'budgets':{}}
cases=set(targets['case_ids']);effects=set(targets['effect_ids']);jobs=set(targets['job_ids']);slots=set(targets['slot_ids'])

def selected(v):return v.get('case_id') in cases or v.get('effect_id') in effects or v.get('job_id') in jobs or v.get('operation_key') in {'w3-slot-fork:'+s for s in slots}
def connect(path):
    db=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=.1);db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON');return db

with closing(connect(ROOT/'runtime/runtime.sqlite3')) as db,closing(connect(ROOT/'workspaces/worker-jobs.sqlite3')) as worker:
    for eid in effects:
        for row in db.execute("SELECT key,payload FROM runtime_values WHERE namespace='w3_boundary_timing' AND key>=? AND key<?",(eid+':',eid+':\uffff')):
            value=json.loads(row['payload']);result['db'].append(value|{'storage_key':row['key']})
    for slot in slots:
        row=worker.execute("SELECT operation_key,status,step,created_at,updated_at,fork_dispatched_at,startup_attempts,"
            "json_extract(startup_receipt,'$.timing') timing,json_extract(request,'$.execution_lane') execution_lane "
            "FROM w3_thread_operations WHERE operation_key=?",('w3-slot-fork:'+slot,)).fetchone()
        if row:result['operations'].append(dict(row))
    for jid in jobs:
        row=db.execute("SELECT length(payload) payload_bytes,json_extract(payload,'$.job.bytes') job_bytes,"
            "json_extract(payload,'$.job.\"$doxagent_content_v1\"') job_content_id "
            "FROM runtime_values WHERE namespace='worker_invocations' AND key=?",(jid,)).fetchone()
        if row:result['content_shapes'].append(dict(row)|{'job_id':jid})
    for cid in cases:
        row=db.execute("SELECT length(payload_json) payload_bytes,json_extract(payload_json,'$.frozen_inputs.bytes') frozen_bytes,"
                       "json_extract(payload_json,'$.source.bytes') source_bytes FROM runtime_v2_cases WHERE case_id=?",(cid,)).fetchone()
        if row:result['content_shapes'].append(dict(row)|{'case_id':cid})

for role in ['v2-scheduler','codex-worker']:
    info=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-'+role+'-1']))[0]
    path=Path(info['LogPath']);size=path.stat().st_size
    with path.open('rb') as stream:
        stream.seek(max(0,size-8*1024**2));raw=stream.read(8*1024**2)
    lines=raw.splitlines()[int(size>len(raw)):]
    matches=0
    for line in lines:
        try:
            envelope=json.loads(line);message=envelope['log']
            if 'w3_boundary_timing ' in message:
                value=json.loads(message.split('w3_boundary_timing ',1)[1])
                if selected(value):result['logs'].append(value|{'role':role});matches+=1
            elif 'runtime_transaction ' in message:result['transactions'].append({'at':envelope['time'],'role':role,'message':message.strip()})
        except (ValueError,KeyError):continue
    result['budgets'][role+'_logs']={'bytes':len(raw),'truncated':size>len(raw),'matching_traces':matches}

for directory in [ROOT/'runtime/boundary-timing',ROOT/'workspaces/boundary-timing']:
    checked=read=matched=bytes_read=0;truncated=False
    if directory.exists():
        with os.scandir(directory) as iterator:
            for entry in iterator:
                checked+=1
                if checked>5000 or bytes_read>8*1024**2 or time.monotonic()-started>10:
                    truncated=True;break
                if not entry.name.endswith('.json'):continue
                stat=entry.stat()
                if stat.st_mtime<1794330000 or stat.st_size>256*1024:continue  # 16:00 UTC; bounded receipts only
                try:
                    raw=Path(entry.path).read_bytes();bytes_read+=len(raw);read+=1;value=json.loads(raw)
                    if selected(value):result['files'].append(value);matched+=1
                except (OSError,ValueError):continue
    result['budgets'][str(directory)]={'checked':checked,'read':read,'matched':matched,'bytes':bytes_read,'truncated':truncated}
result['elapsed_s']=time.monotonic()-started
out.write_text(json.dumps(result,separators=(',',':')))
print(json.dumps({'db_traces':len(result['db']),'file_traces':len(result['files']),'log_traces':len(result['logs']),
                  'elapsed_s':result['elapsed_s'],'budgets':result['budgets']}))
