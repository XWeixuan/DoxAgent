"""Exact host-owned Collector/Bus release, preserving all source configuration."""
import copy,importlib.util,json,os,subprocess,sys
from pathlib import Path
REPO=Path('/home/ubuntu/doxagent');ROOT=Path('/var/lib/doxagent/releases')
AUDIT=Path('/var/lib/doxagent/http-source-coverage-20261107')
COMPONENTS=('v2-message-collector','v2-message-bus')
commit=sys.argv[1];AUDIT.mkdir(parents=True,exist_ok=True);os.chmod(AUDIT,0o700)
spec=importlib.util.spec_from_file_location('release_owner',REPO/'scripts/ops/storage_release.py')
release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)
active=subprocess.run(['systemctl','is-active','--quiet','doxagent-release-resources.timer']).returncode==0
try:
 with release.lock(ROOT):
  if active:subprocess.run(['systemctl','stop','doxagent-release-resources.timer'],check=True)
  before=json.loads((ROOT/'state.json').read_text());release.atomic(AUDIT/'before.private.json',before)
  print(json.dumps({'phase':'building','commit':commit}),flush=True)
  built=release.build(REPO,commit,ROOT,['backend']);image=built['images']['backend']['image']
  state=json.loads((ROOT/'state.json').read_text())
  manifest={'compose_command':state['compose_base_command'],'commit':commit,'components':{}}
  for component in COMPONENTS:
   current=state['components'][component]['current'];candidate=copy.deepcopy(current.get('compose',{}));candidate['image']=image
   candidate.setdefault('environment',{}).update(DOXAGENT_BUILD_COMMIT=commit,DOXAGENT_SOURCE_SHA256=built['source_archive_sha256'])
   candidate['schema']=current.get('schema');manifest['components'][component]=candidate
  release.atomic(AUDIT/'manifest.private.json',manifest)
  print(json.dumps({'phase':'deploying','commit':commit}),flush=True)
  release.deploy(ROOT,state,manifest)
  from datetime import datetime,timezone
  report={'commit':commit,'image':image,'components':list(COMPONENTS),'at':datetime.now(timezone.utc).isoformat()}
  release.atomic(AUDIT/'release.json',report);print(json.dumps(report),flush=True)
finally:
 if active:subprocess.run(['systemctl','start','doxagent-release-resources.timer'],check=True)
