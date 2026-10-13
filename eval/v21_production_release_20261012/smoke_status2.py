import json,sqlite3
from pathlib import Path
root=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/v21-production-validation')
d=sqlite3.connect(root/'research.sqlite3');r=d.execute("select payload from codex_document3_v21_runs where run_id='v21-deployment-maintain-smoke-3'").fetchone()
if r:
 x=json.loads(r[0]); print('run_status',x.get('status')); print('keys',list(x));print('input_manifest',str(x.get('prepared',{}).get('manifest',[]))[:1500]); print('prepared_keys',list(x.get('prepared',{})))
p=root/'model-smoke.json';x=json.loads(p.read_text());print('completed_maintain',x.get('maintain',{}).get('result',{}).get('run_id'))
