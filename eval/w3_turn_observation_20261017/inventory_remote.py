import sqlite3,json
from pathlib import Path
p=Path('/data/workspaces/worker-jobs.sqlite3')
d=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True)
d.execute('PRAGMA query_only=ON')
rows=[]
for status,payload,request in d.execute("SELECT status,payload,request FROM jobs WHERE status IN ('running','queued')"):
 j=json.loads(payload); r=json.loads(request or '{}');rows.append({k:r.get(k) for k in ['run_id','node','task_phase','model']}|{'status':status,'started_at':j.get('started_at'),'phase':j.get('execution_phase')})
print(json.dumps({'active_jobs':rows}))
