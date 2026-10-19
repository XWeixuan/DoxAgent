"""Exact-parent Site-only overlay, keeping Supervisor/native human pages alive."""
import hashlib, importlib.util, json, os, subprocess, sys, time
from pathlib import Path
repo=Path('/home/ubuntu/doxagent');root=Path('/var/lib/doxagent/releases')
commit,base=sys.argv[1:3]
os.environ.update(GIT_CONFIG_COUNT='1',GIT_CONFIG_KEY_0='safe.directory',GIT_CONFIG_VALUE_0=str(repo))
def run(args,timeout=180):
 p=subprocess.run(args,cwd=repo,text=True,capture_output=True,timeout=timeout)
 if p.returncode:raise RuntimeError(str(args[:3])+': '+p.stderr[-1000:])
 return p.stdout.strip()
def inventory():
 ids=run(['docker','ps','-aq','--filter','label=com.docker.compose.project=doxagent-v2']).split()
 return {c['Config']['Labels']['com.docker.compose.service']:c for c in json.loads(run(['docker','inspect',*ids]))}
spec=importlib.util.spec_from_file_location('release',repo/'scripts/ops/storage_release.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
with m.lock(root):
 assert run(['git','rev-parse','HEAD'])==commit
 before=inventory();site=before['v2-site-access'];supervisor=before['v2-chrome-supervisor'];assert site['Image']==base
 canonical=root/'compose.current.json';state=json.loads((root/'state.json').read_text());cfg=json.loads(canonical.read_text())
 audit=root/('reuters-cold-'+commit[:12]);audit.mkdir(exist_ok=False)
 for name,value in [('compose.before.private.json',cfg),('state.before.private.json',state)]:
  m.atomic(audit/name,value);os.chmod(audit/name,0o600)
 alias='doxagent-site-base:'+base.removeprefix('sha256:')[:12];tag='doxagent-site-access:reuters-'+commit[:12]
 run(['docker','tag',base,alias])
 run(['docker','build','-f','deploy/Dockerfile.reuters-maintenance-repair','--build-arg','APPROVED_BASE='+alias,'--build-arg','REPAIR_COMMIT='+commit,'-t',tag,'.'],timeout=600)
 image=run(['docker','image','inspect','--format','{{.Id}}',tag]);state.setdefault('owned_images',[]).append(image)
 state.setdefault('candidates',[]).append({'image':image,'expires':time.time()+48*3600});m.atomic(root/'state.json',state)
 cfg['services']['v2-site-access']={**cfg['services']['v2-site-access'],'image':image}
 cfg['services']['v2-site-access']['environment']={**cfg['services']['v2-site-access'].get('environment',{}),'DOXAGENT_BUILD_COMMIT':commit}
 command=[*state['compose_base_command'],'-f',str(canonical)]
 try:
  run(['docker','stop','--time','-1',site['Id']],timeout=240)
  m.atomic(canonical,cfg);run([*command,'up','-d','--no-deps','--no-build','v2-site-access'])
  for _ in range(60):
   after=inventory();c=after['v2-site-access']
   if c['State']['Running'] and c['State'].get('Health',{}).get('Status')=='healthy':break
   time.sleep(2)
  else:raise RuntimeError('Site readiness failed')
  assert c['Image']==image
  assert all(before[n]['Id']==after[n]['Id'] for n in before if n!='v2-site-access')
  assert os.readlink(f"/proc/{c['State']['Pid']}/ns/net")==os.readlink(f"/proc/{supervisor['State']['Pid']}/ns/net")
  expected=hashlib.sha256((repo/'src/doxagent/site_strategy/worker_runtime.py').read_bytes()).hexdigest()
  actual=run(['docker','exec',c['Id'],'python','-c',"import hashlib;print(hashlib.sha256(open('/app/src/doxagent/site_strategy/worker_runtime.py','rb').read()).hexdigest())"]);assert actual==expected
  m.promote(state,'v2-site-access',image,commit=commit);state['components']['v2-site-access']['current']['compose']=cfg['services']['v2-site-access'];m.atomic(root/'state.json',state)
  receipt={'commit':commit,'image':image,'base':base,'supervisor_unchanged':True,'other_containers_unchanged':True,'file_sha256':expected,'finished':time.time()}
  m.atomic(audit/'release.json',receipt);print(json.dumps(receipt))
 except BaseException:
  m.atomic(canonical,json.loads((audit/'compose.before.private.json').read_text()));m.atomic(root/'state.json',json.loads((audit/'state.before.private.json').read_text()))
  subprocess.run([*command,'up','-d','--no-deps','--no-build','v2-site-access'],cwd=repo,check=True);raise
