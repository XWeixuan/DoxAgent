"""Drain SDK jobs, release only Worker/Scheduler via the canonical owner."""
import copy,importlib.util,json,subprocess,time,sys
from pathlib import Path
from datetime import UTC,datetime
repo=Path('/home/ubuntu/doxagent');root=Path('/var/lib/doxagent/releases')
commit=sys.argv[1]
commit=subprocess.check_output(['git','-C',str(repo),'rev-parse',commit],text=True).strip()
tag='doxagent-v2:storage-'+commit[:12]
audit=root/('w3-policy-sol-'+commit[:12]);audit.mkdir(exist_ok=True)
spec=importlib.util.spec_from_file_location('release',repo/'scripts/ops/storage_release.py')
release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)
def inventory():return {c['Config']['Labels'].get('com.docker.compose.service'):c for c in release.inspect_containers() if c['Config']['Labels'].get('com.docker.compose.project')=='doxagent-v2'}
idle="import sqlite3,json;d=sqlite3.connect('file:/data/workspaces/worker-jobs.sqlite3?mode=ro',uri=True);print(json.dumps(d.execute(\"SELECT id,status FROM jobs WHERE status IN ('queued','running')\").fetchall()))"
with release.lock(root):
 state=json.loads((root/'state.json').read_text());current=json.loads((root/'compose.current.json').read_text())
 manifest={'commit':commit,'compose_command':state['compose_base_command'],'components':{}}
 for name in ('codex-worker','v2-scheduler'):
  candidate=copy.deepcopy(current['services'][name]);candidate['image']=tag
  candidate.setdefault('environment',{})['DOXAGENT_BUILD_COMMIT']=commit
  candidate['health_seconds']=120;manifest['components'][name]=candidate
 release.atomic(audit/'manifest.private.json',manifest)
 before=inventory();producers=[s for s in ('initialization-guardian','v2-initialization','v2-cdecr-executor','v2-o4','v2-scheduler') if s in before and before[s]['State']['Running']]
 log=(audit/'drain.log').open('a',buffering=1)
 stops={s:subprocess.Popen(['docker','stop','--time','-1',before[s]['Id']],stdout=log,stderr=log) for s in producers}
 drained=False
 try:
  deadline=time.monotonic()+900
  while True:
   jobs=json.loads(subprocess.check_output(['docker','exec','doxagent-v2-codex-worker-1','python','-c',idle],text=True))
   progress={'at':datetime.now(UTC).isoformat(),'active_jobs':jobs,'stopping':[s for s,p in stops.items() if p.poll() is None]}
   release.atomic(audit/'progress.json',progress)
   print(json.dumps(progress),flush=True)
   if not jobs and all(p.poll() is not None for p in stops.values()):break
   if time.monotonic()>deadline:raise RuntimeError('Drain exceeded 15 minutes; keep existing Worker, restore admissions')
   time.sleep(10)
  assert all(p.returncode==0 for p in stops.values())
  drained=True
  # Worker shutdown cancels jobs, so only invoke it after the durable queue is empty.
  subprocess.run(['docker','stop','--time','-1',before['codex-worker']['Id']],check=True,timeout=120,stdout=log,stderr=log)
  release.deploy(root,state,manifest)
 finally:
  # All temporary producers are restored with the original image/container ID;
  # only the requested Scheduler and Worker are recreated.
  for s,p in stops.items():
   if p.poll() is None:p.wait(timeout=120)
  now=inventory()
  for name in producers:
   if name!='v2-scheduler' or not drained:
    if name in now and not now[name]['State']['Running']:
     subprocess.run(['docker','start',now[name]['Id']],check=True,stdout=log,stderr=log)
  if drained and not inventory()['v2-scheduler']['State']['Running']:
   subprocess.run(['docker','start',inventory()['v2-scheduler']['Id']],check=True,stdout=log,stderr=log)
 after=inventory();selected={}
 for name in ('codex-worker','v2-scheduler'):
  c=after[name];env=dict(x.split('=',1) for x in c['Config']['Env'])
  assert c['State']['Running'] and env.get('DOXAGENT_BUILD_COMMIT')==commit
  selected[name]={'container_id':c['Id'],'image':c['Image'],'release':env['DOXAGENT_BUILD_COMMIT'],'health':c['State'].get('Health',{}).get('Status')}
 assert all(after[s]['Id']==c['Id'] for s,c in before.items() if s not in ('codex-worker','v2-scheduler'))
 receipt={'commit':commit,'drained_without_model_cancellation':drained,'containers':selected,'unrelated_container_ids_unchanged':True,'restored_producers':producers,'at':datetime.now(UTC).isoformat()}
 release.atomic(audit/'receipt.json',receipt);print(json.dumps(receipt),flush=True)
