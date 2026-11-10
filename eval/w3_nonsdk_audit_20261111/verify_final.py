"""Read-only final source/runtime/recovery scalar verification."""
from contextlib import closing
from datetime import UTC,datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess

os.nice(19)
repo=Path('/home/ubuntu/doxagent');volume=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
out={'at':datetime.now(UTC).isoformat(),'git':subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip(),'roles':{}}
for role in ['v2-scheduler','codex-worker','v2-initialization','v2-cdecr-executor']:
    data=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-'+role+'-1']))[0]
    out['roles'][role]={k:data['State'][k] for k in ['Status','OOMKilled','StartedAt']}
    out['roles'][role].update(image=data['Image'],restart=data['RestartCount'])
    if role=='codex-worker':
        paths=['codex_worker/jobs.py','codex_worker/w3_ready.py']
    elif role=='v2-scheduler':
        paths=['runtime_scheduler/service.py','runtime_scheduler/v2.py','ticker_initialization/consumers.py',
               'workflows/codex_document3/orchestrator_v21.py','workflows/codex_document3/runner_v21.py',
               'persistent_runtime_v2/w3.py','persistent_runtime_v2/service.py','persistent_runtime_v2/w3_execution.py',
               'persistent_runtime_v2/worker_receipts.py','persistent_runtime_v2/journal.py','codex_runtime/client.py','control_io.py']
    else:paths=['workflows/codex_document3/orchestrator_v21.py','workflows/codex_document3/runner_v21.py']
    out['roles'][role]['source_matches']={}
    for path in paths:
        actual=subprocess.check_output(['docker','exec',data['Id'],'sha256sum','/app/src/doxagent/'+path],text=True).split()[0]
        expected=hashlib.sha256((repo/'src/doxagent'/path).read_bytes()).hexdigest()
        out['roles'][role]['source_matches'][path]=actual==expected
manifest=Path('/var/lib/doxagent/releases/w3-backlog-recovery-20261110.json')
m=json.loads(manifest.read_text());p=json.loads(manifest.with_suffix('.progress.json').read_text());ids=list(m['cases'])
out['recovery']={'capacity':m['capacity'],'total':len(ids),'done':len(p['done']),'active':len(p['active'])}
with closing(sqlite3.connect('file:'+str(volume/'runtime/runtime.sqlite3')+'?mode=ro',uri=True,timeout=.1)) as db:
    db.execute('PRAGMA query_only=ON');marks=','.join('?' for _ in ids)
    out['recovery']['case_statuses']=dict(db.execute('SELECT status,count(*) FROM runtime_v2_cases WHERE case_id IN ('+marks+') GROUP BY status',ids))
    out['recovery']['stage_statuses']=[list(r) for r in db.execute('SELECT phase,status,count(*) FROM runtime_v2_w3_stages WHERE case_id IN ('+marks+') GROUP BY phase,status',ids)]
mem=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
out['mem_available_kib']=int(mem['MemAvailable'].split()[0])
out['pressure']={name:(Path('/proc/pressure')/name).read_text() for name in ['cpu','memory','io']}
out['recovery']['unit_active']=subprocess.run(['systemctl','is-active','doxagent-w3-backlog-20261110.service'],capture_output=True,text=True).stdout.strip()
print(json.dumps(out))
