import sqlite3,json
from pathlib import Path
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/workspaces/worker-jobs.sqlite3');db=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.2);db.row_factory=sqlite3.Row
out=[]
for r in db.execute("select id,status,payload from jobs where status in ('running','queued') limit 15"):
 v=json.loads(r['payload']);items=[]
 for e in db.execute('select payload from events where job_id=? order by sequence desc limit 2',(r['id'],)):
  x=json.loads(e[0]);items.append(dict(type=x.get('event_type'),at=x.get('created_at'),keys=list(x.get('payload',{}))[:8]))
 out.append(dict(id=r['id'],run=v['run_id'],started=v.get('started_at'),phase=v.get('execution_phase'),events=items))
print(json.dumps(out))
