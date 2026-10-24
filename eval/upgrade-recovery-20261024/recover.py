import importlib.util,json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path('/var/lib/doxagent/releases'); OUT=Path('/var/lib/doxagent/upgrade-recovery-20261024'); REPO=Path('/home/ubuntu/doxagent')
COMMIT='f2f0bcd66a13b469073392cd93fbdbecca2e56d2'
os.umask(0o077)
spec=importlib.util.spec_from_file_location('release',REPO/'scripts/ops/storage_release.py'); release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)
def cmd(args):return subprocess.check_output(args,text=True).strip()
def inspect():
 ids=cmd(['docker','ps','-aq']).split()
 return json.loads(cmd(['docker','inspect',*ids])) if ids else []
def snapshot(name):
 cs=inspect(); rows=[]
 for c in cs:
  if not c['Name'].startswith('/doxagent'):continue
  rows.append(dict(name=c['Name'],id=c['Id'],image=c['Image'],commit=c['Config'].get('Labels',{}).get('org.opencontainers.image.revision'),state=c['State']['Status'],health=c['State'].get('Health',{}).get('Status'),restarts=c['RestartCount'],exit=c['State']['ExitCode'],oom=c['State']['OOMKilled'],started=c['State']['StartedAt']))
 r=dict(at=time.time(),cpus=os.cpu_count(),free_bytes=__import__('shutil').disk_usage('/').free,roles=rows)
 (OUT/(name+'.json')).write_text(json.dumps(r,indent=2));print(json.dumps(r),flush=True)
action=sys.argv[1]
if action=='before':snapshot('before-public')
elif action=='build':
 with release.lock(ROOT):
  r=release.build(REPO,COMMIT,ROOT,['backend','site','supervisor','web','guardian','repair-agent'])
 print(json.dumps({'commit':r['commit'],'images':r['images']}),flush=True)
elif action=='deploy':
 # Stop work producers before cycling the shared Chrome network owner. Do not delete any profile or volume.
 names=['initialization-guardian','v2-control','v2-scheduler','v2-initialization','v2-cdecr-executor','v2-o4','v2-message-bus','v2-content-enrichment','codex-worker','v2-site-access']
 cs=inspect(); byrole={c['Config'].get('Labels',{}).get('com.docker.compose.service'):c for c in cs}
 with release.lock(ROOT):
  for n in names:
   c=byrole.get(n)
   if c and c['State']['Status'] in ['running','restarting']:
    print('stopping '+n,flush=True);subprocess.run(['docker','stop','--time','120',c['Id']],check=True,stdout=subprocess.DEVNULL)
  s=json.loads((ROOT/'state.json').read_text()); b=json.loads((ROOT/(COMMIT+'.build.json')).read_text())
  import copy
  order=['v2-chrome-supervisor','v2-site-access','v2-api','v2-web','v2-projector','v2-maintenance','v2-transport-maintenance','v2-executor','v2-delivery','codex-worker','v2-cdecr-executor','v2-initialization','v2-o4','v2-control','v2-content-enrichment','v2-message-bus','v2-scheduler','initialization-guardian']
  kinds={'v2-chrome-supervisor':'supervisor','v2-site-access':'site','v2-web':'web','initialization-guardian':'guardian'}
  manifest=dict(compose_command=s['compose_base_command'],commit=COMMIT,components={})
  for n in order:
   current=s['components'][n]['current'];kind=kinds.get(n,'backend'); c=copy.deepcopy(current.get('compose',{}));c['image']=b['images'][kind]['image'];c['schema']=current.get('schema')
   if kind=='guardian':c.setdefault('environment',{})['DOXAGENT_INITIALIZATION_REPAIR_AGENT_IMAGE']=b['images']['repair-agent']['tag']
   manifest['components'][n]=c
  (OUT/'release.private.json').write_text(json.dumps(manifest))
  r=release.deploy(ROOT,s,manifest)
 print(json.dumps(r),flush=True);snapshot('after-deploy-public')
elif action=='health':snapshot('final-public')
elif action=='cleanup':
 with release.lock(ROOT):
  removed=[]
  baseline={c['id'] for c in json.loads((OUT/'before-public.json').read_text())['roles'] if c['state']=='exited'}
  for c in inspect():
   if c['Id'] in baseline and c['Name'].startswith('/doxagent-eval-') and c['State']['Status']=='exited':
    subprocess.run(['docker','rm',c['Id']],check=True,stdout=subprocess.DEVNULL);removed.append(c['Name'])
  r=release.reconcile(ROOT,json.loads((ROOT/'state.json').read_text()))
  report=dict(removed_containers=removed,resources=r);(OUT/'cleanup-public.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
else:raise ValueError(action)
