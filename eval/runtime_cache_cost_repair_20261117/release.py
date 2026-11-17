"""Use the canonical committed-image builder and release owner."""
import copy,importlib.util,json,os,subprocess,sys,time,sqlite3
from pathlib import Path
os.umask(0o077)
repo=Path('/home/ubuntu/doxagent');root=Path('/var/lib/doxagent/releases')
audit=root/'runtime-cache-cost-20261117';audit.mkdir(mode=0o700,exist_ok=True)
commit=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
spec=importlib.util.spec_from_file_location('release_owner',repo/'scripts/ops/storage_release.py');owner=importlib.util.module_from_spec(spec);spec.loader.exec_module(owner)
roles=['v2-api','v2-scheduler']
def containers():
 return {c['Name'].lstrip('/'):dict(image=c['Image'],started=c['State']['StartedAt'],running=c['State']['Running']) for c in owner.inspect_containers()}
if sys.argv[1]=='build':
 with owner.lock(root):
  if (audit/'build.json').exists():
   built=json.loads((audit/'build.json').read_text())
   if built['commit']==commit:print('already built',commit);sys.exit(0)
  (audit/'containers-before.json').write_text(json.dumps(containers()))
  (audit/'state-before.json').write_bytes((root/'state.json').read_bytes())
  (audit/'compose-before.json').write_bytes((root/'compose.current.json').read_bytes())
  built=owner.build(repo,commit,root,['backend']);owner.atomic(audit/'build.json',built)
  print(json.dumps({'commit':commit,'image':built['images']['backend']['image']}))
elif sys.argv[1]=='deploy':
 with owner.lock(root):
  built=json.loads((audit/'build.json').read_text());assert built['commit']==commit
  state=json.loads((root/'state.json').read_text())
  (audit/'compose-cutover-before.json').write_bytes((root/'compose.current.json').read_bytes())
  before=containers();owner.atomic(audit/'cutover-before.json',before)
  for role in roles:
   c=before['doxagent-v2-'+role+'-1']
   assert c['image']==state['components'][role]['current']['image']
  runtime=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/runtime/runtime.sqlite3')
  with sqlite3.connect(runtime.as_uri()+'?mode=ro',uri=True,timeout=2) as db:
   old=db.execute("select payload from runtime_values where namespace='execution' and key='active'").fetchone()[0]
  owner.atomic(audit/'activation-before.json',{'active':json.loads(old),'cutover_at':time.time()})
  # A bounded normal SIGTERM preserves accepted external Worker jobs; no Worker
  # recreation and no cancelling trades or manually rewriting Case state.
  subprocess.run(['docker','stop','-t','60','doxagent-v2-v2-scheduler-1'],check=True)
  subprocess.run(['docker','stop','-t','30','doxagent-v2-v2-api-1'],check=True)
  owner.build=lambda *a,**k:built
  try:
   result=owner.release_components(root,state,repo,commit,roles)
  except BaseException:
   current=json.loads((root/'state.json').read_text())
   original=json.loads((audit/'compose-cutover-before.json').read_text())
   rollback={'compose_command':current['compose_base_command'],'commit':commit,'components':{}}
   for role in roles:rollback['components'][role]=original['services'][role]
   owner.deploy(root,current,rollback)
   raise
  owner.atomic(audit/'deploy.json',result)
  print(json.dumps({'commit':commit,'roles':roles,'image':built['images']['backend']['image']}))
