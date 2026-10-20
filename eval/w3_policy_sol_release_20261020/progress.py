import json,subprocess
from pathlib import Path
p=Path('/var/lib/doxagent/releases/w3-policy-sol-160199b7a200')
out={name:json.loads((p/name).read_text()) for name in ('progress.json','receipt.json') if (p/name).exists()}
code="import json,sqlite3;d=sqlite3.connect('file:/data/workspaces/worker-jobs.sqlite3?mode=ro',uri=True);print(json.dumps([{'id':r[0],'phase':json.loads(r[1] or '{}').get('task_phase'),'started_at':json.loads(r[2]).get('started_at'),'model':json.loads(r[1] or '{}').get('model')} for r in d.execute(\"SELECT id,request,payload FROM jobs WHERE status IN ('queued','running')\")]))"
out['active_jobs_detail']=json.loads(subprocess.check_output(['docker','exec','doxagent-v2-codex-worker-1','python','-c',code],text=True))
print(json.dumps(out))
