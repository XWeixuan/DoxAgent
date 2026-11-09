"""Narrow immutable release; change only the recheck env key, never print secrets."""
import argparse,hashlib,json,os,re,subprocess
from datetime import datetime,timezone
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('commit');args=p.parse_args()
repo=Path('/home/ubuntu/doxagent')
commit=subprocess.check_output(['git','rev-parse','--verify',args.commit+'^{commit}'],cwd=repo,text=True).strip()
assert commit==args.commit and len(commit)==40
roles=('v2-api','v2-message-bus','v2-content-enrichment')
protected=('v2-message-collector','v2-site-access','v2-chrome-supervisor')
key='DOXAGENT_CONTENT_ENRICHMENT_RECHECK_ENABLED'
def status(role):
 v=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-'+role+'-1']))[0]
 env=dict(x.split('=',1) for x in v['Config']['Env'] if '=' in x)
 return {'id':v['Id'],'started_at':v['State']['StartedAt'],'status':v['State']['Status'],'health':v['State'].get('Health',{}).get('Status'),'commit':env.get('DOXAGENT_BUILD_COMMIT'),'recheck_switch':env.get(key)}
before={role:status(role) for role in protected}
env_path=repo/'.env.v2';stat=env_path.stat();raw=env_path.read_bytes()
line=key.encode()+b'=false\n'
changed=re.sub(rb'(?m)^'+key.encode()+rb'=[^\r\n]*(?:\r?\n|$)',line,raw)
if changed==raw and not re.search(rb'(?m)^'+key.encode()+rb'=',raw):changed=raw.rstrip(b'\r\n')+b'\n'+line
if changed!=raw:
 tmp=env_path.with_name('.env.v2.recheck-'+str(os.getpid()))
 fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'wb') as f:
  os.fchown(f.fileno(),stat.st_uid,stat.st_gid);f.write(changed);f.flush();os.fsync(f.fileno())
 os.replace(tmp,env_path)
started=datetime.now(timezone.utc).isoformat()
log=Path('/var/lib/doxagent/releases/body-recheck-off-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.log')
fd=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:
 command=['python3','scripts/ops/storage_release.py','release','--commit',commit]
 for role in roles:command+=['--component',role]
 code=subprocess.run(command,cwd=repo,stdout=f,stderr=subprocess.STDOUT).returncode
out={'commit':commit,'started_at':started,'finished_at':datetime.now(timezone.utc).isoformat(),'returncode':code,'protected_unchanged':all(status(r)['id']==s['id'] and status(r)['started_at']==s['started_at'] for r,s in before.items()),'roles':{r:status(r) for r in roles},'private_log':str(log)}
if code==0:
 files=['settings.py','message_bus_v2/repository.py','message_bus_v2/live_repository.py','message_bus_v2/body_results.py','message_bus_v2/factory.py','content_enrichment/rechecks.py']
 expected={f:hashlib.sha256(subprocess.check_output(['git','show',commit+':src/doxagent/'+f],cwd=repo)).hexdigest() for f in files}
 out['checks']={}
 script="import hashlib,json;from pathlib import Path;from doxagent.settings import DoxAgentSettings;s=DoxAgentSettings();print(json.dumps({'recheck_enabled':s.content_enrichment_recheck_enabled,'hashes':{f:hashlib.sha256(Path('/app/src/doxagent',f).read_bytes()).hexdigest() for f in "+repr(files)+"}}))"
 for role in roles:
  actual=json.loads(subprocess.check_output(['docker','exec','doxagent-v2-'+role+'-1','python','-c',script]))
  out['checks'][role]={'recheck_enabled':actual['recheck_enabled'],'hashes_match':expected==actual['hashes']}
Path('/tmp/body-recheck-off-release.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2));raise SystemExit(code)
