import sqlite3,json,subprocess,importlib.util
from pathlib import Path
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/workspaces/worker-jobs.sqlite3');d=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.2);d.row_factory=sqlite3.Row
runs=['w3-ready-be630b98658c45159c16349ad63df240','w3-ready-cfe397c6ae2f4bd19b39a6eec1e5ffce','w3-ready-8748f2b93ec84ae6a640a1eef5087079','w3-ready-7c426df2434c42c5a6164c92c4fb5e8d']
out=[]
for run in runs:
 s=d.execute('select id,state,created_at,ready_at,bound_at,claim_kind,error from w3_ready_slots where run_id=?',(run,)).fetchone()
 if not s:continue
 v=dict(s);o=d.execute('select created_at,updated_at,status,step,error_code,startup_receipt from w3_thread_operations where operation_key=?',('w3-slot-fork:'+s['id'],)).fetchone();v['operation']=dict(o) if o else None
 if v['operation'] and v['operation']['startup_receipt']:v['operation']['startup_receipt']=json.loads(v['operation']['startup_receipt'])
 out.append(v)
print(json.dumps(out))
