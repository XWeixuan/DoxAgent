"""Narrow host release; private environment stays on the production host."""
import copy,json,subprocess,sys,os
from pathlib import Path
ROOT=Path('/var/lib/doxagent/sec-edgar-20261024');RELEASE=Path('/var/lib/doxagent/releases');REPO=Path('/home/ubuntu/doxagent')
COMPONENTS=['v2-content-enrichment','v2-message-bus','v2-api','v2-control','v2-initialization','v2-scheduler']
def run(args,**kwargs):return subprocess.check_output(args,text=True,**kwargs).strip()
def atomic(path,data):
 path.write_text(json.dumps(data,indent=2));os.chmod(path,0o600)
def main():
 ROOT.mkdir(exist_ok=True,parents=True);os.chmod(ROOT,0o700)
 commit=run(['git','-C',str(REPO),'rev-parse','HEAD']);state=json.loads((RELEASE/'state.json').read_text())
 before={name:copy.deepcopy(state['components'][name]['current']) for name in COMPONENTS};atomic(ROOT/'before.private.json',before)
 ids=run(['docker','ps','-aq']).splitlines();containers=json.loads(run(['docker','inspect',*ids]));atomic(ROOT/'runtime-before.json',[{'name':c['Name'],'image':c['Image'],'started_at':c['State']['StartedAt']} for c in containers])
 active=subprocess.run(['systemctl','is-active','--quiet','doxagent-release-resources.timer']).returncode==0
 atomic(ROOT/'timer-before.json',{'active':active})
 try:
  if active:subprocess.run(['systemctl','stop','doxagent-release-resources.timer'],check=True)
  with (ROOT/'build.log').open('w') as log:subprocess.run([sys.executable,str(REPO/'scripts/ops/storage_release.py'),'build','--repo',str(REPO),'--commit',commit,'--kind','backend'],stdout=log,stderr=subprocess.STDOUT,check=True)
  built=json.loads((RELEASE/(commit+'.build.json')).read_text());image=built['images']['backend']['image'];state=json.loads((RELEASE/'state.json').read_text());base=state['compose_base_command']
  manifest={'compose_command':base,'commit':commit,'components':{}}
  for name in COMPONENTS:
   current=state['components'][name]['current'];candidate=copy.deepcopy(current.get('compose',{}));candidate['image']=image;env=candidate.setdefault('environment',{});env['DOXAGENT_BUILD_COMMIT']=commit;env['DOXAGENT_SOURCE_SHA256']=built['source_archive_sha256'];candidate['schema']=current.get('schema');manifest['components'][name]=candidate
  atomic(ROOT/'deployment.private.json',manifest)
  with (ROOT/'deployment.log').open('w') as log:subprocess.run([sys.executable,str(REPO/'scripts/ops/storage_release.py'),'deploy','--manifest',str(ROOT/'deployment.private.json')],stdout=log,stderr=subprocess.STDOUT,check=True)
  print(json.dumps({'commit':commit,'image':image,'components':COMPONENTS,'released':True}))
 finally:
  if active:subprocess.run(['systemctl','start','doxagent-release-resources.timer'],check=True)
if __name__=='__main__':main()
