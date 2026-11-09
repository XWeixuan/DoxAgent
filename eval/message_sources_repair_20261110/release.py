"""Standard exact-SHA Collector-only release; no private config or browser changes."""
import argparse,hashlib,json,os,subprocess
from datetime import datetime,timezone
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('commit');args=p.parse_args()
repo=Path('/home/ubuntu/doxagent')
subprocess.run(['git','fetch','origin','main'],cwd=repo,check=True,stdout=subprocess.DEVNULL)
commit=subprocess.check_output(['git','rev-parse',args.commit+'^{commit}'],cwd=repo,text=True).strip()
assert len(commit)==40 and commit==args.commit
def status(role):
 v=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-'+role+'-1']))[0]
 env=dict(x.split('=',1) for x in v['Config']['Env'] if '=' in x)
 return {'id':v['Id'][:12],'started_at':v['State']['StartedAt'],'state':v['State']['Status'],'health':v['State'].get('Health',{}).get('Status'),'commit':env.get('DOXAGENT_BUILD_COMMIT'),'recheck':env.get('DOXAGENT_CONTENT_ENRICHMENT_RECHECK_ENABLED')}
protected=('v2-site-access','v2-chrome-supervisor','v2-api','v2-message-bus','v2-content-enrichment')
before={r:status(r) for r in protected}
out={'commit':commit,'started_at':datetime.now(timezone.utc).isoformat(),'collector_before':status('v2-message-collector')}
log=Path('/var/lib/doxagent/releases/sources-quota-aviation-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.log')
with os.fdopen(os.open(log,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as f:
 code=subprocess.run(['python3','scripts/ops/storage_release.py','release','--commit',commit,'--component','v2-message-collector'],cwd=repo,stdout=f,stderr=subprocess.STDOUT).returncode
out.update(returncode=code,finished_at=datetime.now(timezone.utc).isoformat(),collector_after=status('v2-message-collector'),protected_unchanged={r:status(r)==v for r,v in before.items()},private_log=str(log))
if code==0:
 path='src/doxagent/message_bus_v2/public_listing.py'
 expected=hashlib.sha256(subprocess.check_output(['git','show',commit+':'+path],cwd=repo)).hexdigest()
 actual=subprocess.check_output(['docker','exec','doxagent-v2-v2-message-collector-1','python','-c',"import hashlib;from pathlib import Path;print(hashlib.sha256(Path('/app/"+path+"').read_bytes()).hexdigest())"],text=True).strip()
 out['source_hash_matches']=actual==expected
Path('/tmp/sources-quota-aviation-release.json').write_text(json.dumps(out,indent=2))
print(json.dumps(out,indent=2))
raise SystemExit(code or (0 if out.get('source_hash_matches') and all(out['protected_unchanged'].values()) else 1))
