"""Locked exact-parent two-service overlay. Never recreate native Chrome."""
import hashlib, importlib.util, json, os, subprocess, sys, time
from pathlib import Path
repo=Path('/home/ubuntu/doxagent');root=Path('/var/lib/doxagent/releases')
commit,site_base,bus_base=sys.argv[1:4]
os.environ.update(GIT_CONFIG_COUNT='1',GIT_CONFIG_KEY_0='safe.directory',GIT_CONFIG_VALUE_0=str(repo))
files=['message_bus_v2/barrons_sources.py','message_bus_v2/industry_sources.py','site_strategy/runtime.py','site_strategy/external_runtime.py','site_strategy/service.py','site_strategy/verification.py','site_strategy/failover.py']
def run(args,timeout=180):
 p=subprocess.run(args,cwd=repo,text=True,capture_output=True,timeout=timeout)
 if p.returncode:raise RuntimeError(str(args[:3])+': '+p.stderr[-1200:])
 return p.stdout.strip()
def inventory():
 ids=run(['docker','ps','-aq','--filter','label=com.docker.compose.project=doxagent-v2']).split()
 return {c['Config']['Labels']['com.docker.compose.service']:c for c in json.loads(run(['docker','inspect',*ids]))}
spec=importlib.util.spec_from_file_location('release',repo/'scripts/ops/storage_release.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
with m.lock(root):
 assert run(['git','rev-parse','HEAD'])==commit
 before=inventory();bases={'v2-site-access':site_base}
 if bus_base!='-':bases['v2-message-bus']=bus_base
 assert all(before[s]['Image']==base for s,base in bases.items())
 canonical=root/'compose.current.json';state=json.loads((root/'state.json').read_text());cfg=json.loads(canonical.read_text())
 audit=root/('barrons-native-'+commit[:12]);audit.mkdir(exist_ok=False)
 for name,value in [('compose.before.private.json',cfg),('state.before.private.json',state)]:
  m.atomic(audit/name,value);os.chmod(audit/name,0o600)
 images={}
 for service,base in bases.items():
  alias='doxagent-base:'+base.removeprefix('sha256:')[:12];tag='doxagent-'+service+':barrons-'+commit[:12]
  run(['docker','tag',base,alias])
  run(['docker','build','-f','deploy/Dockerfile.barrons-native-news-repair','--build-arg','APPROVED_BASE='+alias,'--build-arg','REPAIR_COMMIT='+commit,'-t',tag,'.'],timeout=600)
  image=run(['docker','image','inspect','--format','{{.Id}}',tag]);images[service]=image
  state.setdefault('owned_images',[]).append(image);state.setdefault('candidates',[]).append({'image':image,'expires':time.time()+48*3600})
  cfg['services'][service]={**cfg['services'][service],'image':image}
  cfg['services'][service]['environment']={**cfg['services'][service].get('environment',{}),'DOXAGENT_BUILD_COMMIT':commit}
 m.atomic(root/'state.json',state);command=[*state['compose_base_command'],'-f',str(canonical)]
 try:
  for service in bases:run(['docker','stop','--time','-1',before[service]['Id']],timeout=240)
  m.atomic(canonical,cfg);run([*command,'up','-d','--no-deps','--no-build',*bases])
  for _ in range(60):
   after=inventory()
   if all(after[s]['State']['Running'] and ('Health' not in after[s]['State'] or after[s]['State']['Health']['Status']=='healthy') for s in bases):break
   time.sleep(2)
  else:raise RuntimeError('readiness failed')
  assert all(before[n]['Id']==after[n]['Id'] for n in before if n not in bases)
  supervisor=after['v2-chrome-supervisor']
  assert os.readlink(f"/proc/{after['v2-site-access']['State']['Pid']}/ns/net")==os.readlink(f"/proc/{supervisor['State']['Pid']}/ns/net")
  expected={f:hashlib.sha256((repo/'src/doxagent'/f).read_bytes()).hexdigest() for f in files}
  for service,image in images.items():
   assert after[service]['Image']==image
   code='import hashlib,json;from pathlib import Path;p=Path("/app/src/doxagent");print(json.dumps({f:hashlib.sha256((p/f).read_bytes()).hexdigest() for f in '+repr(files)+'}))'
   assert json.loads(run(['docker','exec',after[service]['Id'],'python','-c',code]))==expected
   m.promote(state,service,image,commit=commit);state['components'][service]['current']['compose']=cfg['services'][service]
  m.atomic(root/'state.json',state)
  receipt={'commit':commit,'images':images,'bases':bases,'hashes':expected,'supervisor_unchanged':True,'other_containers_unchanged':True,'network_namespace_matches':True,'finished':time.time()}
  m.atomic(audit/'release.json',receipt);print(json.dumps(receipt))
 except BaseException:
  m.atomic(canonical,json.loads((audit/'compose.before.private.json').read_text()));m.atomic(root/'state.json',json.loads((audit/'state.before.private.json').read_text()))
  subprocess.run([*command,'up','-d','--no-deps','--no-build',*bases],cwd=repo,check=True);raise
