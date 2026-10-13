from pathlib import Path
import sqlite3,json
root=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/v21-production-validation')
for p in root.rglob('worker-jobs.sqlite3'):
 d=sqlite3.connect(p);d.row_factory=sqlite3.Row
 for r in d.execute('select id,status,request from jobs order by rowid desc limit 2'):
  x=json.loads(r['request']);print(p,r['status'],x.get('run_id'),x.get('workspace'))
d=sqlite3.connect(root/'research.sqlite3')
r=d.execute("select payload from codex_document3_v21_runs where run_id='v21-deployment-maintain-smoke-3'").fetchone()
if r:
 x=json.loads(r[0]);print('run',x.get('status'),'manifest',[{k:v for k,v in a.items() if k in ('path','role','availability')} for a in x.get('manifest',[])][:12]);print('keys',list(x))
