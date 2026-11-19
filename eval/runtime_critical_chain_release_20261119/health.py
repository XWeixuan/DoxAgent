import json,subprocess,sqlite3,time
from pathlib import Path
from datetime import datetime,UTC
from collections import Counter
root=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
roles=['codex-worker','v2-scheduler','v2-projector','v2-control','v2-maintenance','v2-api']
out={'at':datetime.now(UTC).isoformat(),'containers':[]}
for role in roles:
 c=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-'+role+'-1']))[0]
 out['containers'].append({'role':role,'image':c['Image'],'revision':c['Config'].get('Labels',{}).get('org.opencontainers.image.revision'),'started':c['State']['StartedAt'],'running':c['State']['Running'],'health':c['State'].get('Health',{}).get('Status'),'restarts':c['RestartCount'],'oom':c['State']['OOMKilled']})
code="""import os,json,urllib.request
r=urllib.request.Request('http://127.0.0.1:8791/v1/resources',headers={'Authorization':'Bearer '+os.environ.get('DOXAGENT_CODEX_WORKER_BEARER_TOKEN','')})
with urllib.request.urlopen(r,timeout=5) as f:d=json.load(f)
print(json.dumps({k:d.get(k) for k in ['startup_admission','active_executions','active_by_lane','queued_by_lane','event_loop_lag','dispatcher_status','cleanup']}))
"""
try:out['resources']=json.loads(subprocess.check_output(['docker','exec','doxagent-v2-codex-worker-1','python','-c',code],timeout=8))
except Exception as e:out['resource_error']=type(e).__name__
def dbread(path,sql,args=()):
 with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.15) as db:
  db.row_factory=sqlite3.Row;db.execute('pragma query_only=ON');db.execute('pragma cache_size=-2048');deadline=time.monotonic()+1
  db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
  return [dict(r) for r in db.execute(sql,args)]
try:
 out['stock']=dbread(root/'workspaces/worker-jobs.sqlite3',"SELECT scope,state,capsule_id FROM w3_ready_slots WHERE state IN ('READY','BUILDING') LIMIT 32")
 out['claims']=dbread(root/'workspaces/worker-jobs.sqlite3',"SELECT id,state,claim_kind,case_id,record_id,bound_at,ready_at FROM w3_ready_slots ORDER BY rowid DESC LIMIT 256")
except Exception as e:out['sql_error']=str(e)
out['meminfo']={line.split(':')[0]:line.split(':')[1].strip() for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith(('MemAvailable:','SwapFree:','Cached:'))}
out['psi']={p:Path('/proc/pressure',p).read_text() for p in ['cpu','io','memory']}
out['disk_free_bytes']=__import__('shutil').disk_usage('/').free
out['safety']=json.loads(Path('/run/doxagent-resources/safety.json').read_text())
print(json.dumps(out))
