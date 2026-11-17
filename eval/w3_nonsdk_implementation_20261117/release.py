import importlib.util,json,sqlite3,subprocess,sys
from pathlib import Path
repo=Path('/home/ubuntu/doxagent'); root=Path('/var/lib/doxagent/releases'); audit=root/'w3-nonsdk-20261117';audit.mkdir(mode=0o700,exist_ok=True)
commit='6006500cadc5086f29aab570f11930c7a089010a'
spec=importlib.util.spec_from_file_location('owner',repo/'scripts/ops/storage_release.py'); owner=importlib.util.module_from_spec(spec);spec.loader.exec_module(owner)
data=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
def jobs():
 with sqlite3.connect((data/'workspaces/worker-jobs.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.2) as db:
  return db.execute("SELECT status,json_extract(request,'$.task_phase'),count(*) FROM jobs WHERE status IN ('queued','running') GROUP BY 1,2").fetchall()
if sys.argv[1]=='jobs':print(json.dumps(jobs()))
elif sys.argv[1]=='build':
 with owner.lock(root):
  result=owner.build(repo,commit,root,['backend']);(audit/'build.json').write_text(json.dumps(result));print(json.dumps({k:v['image'] for k,v in result['images'].items()}))
elif sys.argv[1]=='deploy':
 with owner.lock(root):
  active=jobs();assert not active,active
  spec=importlib.util.spec_from_file_location('idle',repo/'scripts/ops/w3_latency_release_20261111.py'); idle=importlib.util.module_from_spec(spec);spec.loader.exec_module(idle);idle.assert_scheduler_idle(data/'runtime/runtime.sqlite3')
  built=json.loads((audit/'build.json').read_text());assert built['commit']==commit
  owner.build=lambda *a,**k:built
  state=json.loads((root/'state.json').read_text());result=owner.release_components(root,state,repo,commit,['codex-worker','v2-scheduler']);(audit/'deploy.json').write_text(json.dumps(result));print(json.dumps(result))

