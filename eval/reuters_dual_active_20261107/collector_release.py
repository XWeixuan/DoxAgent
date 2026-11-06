"""Exact-SHA Collector-only release, with Site Access and Chrome preservation checks."""
import argparse,hashlib,json,os,subprocess
from datetime import datetime,timezone
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('commit');a=p.parse_args()
repo=Path('/home/ubuntu/doxagent')
commit=subprocess.check_output(['git','rev-parse','--verify',a.commit+'^{commit}'],cwd=repo,text=True).strip()
assert commit==a.commit and len(commit)==40
def status(role):
    v=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-'+role+'-1']))[0]
    return {'id':v['Id'],'started_at':v['State']['StartedAt'],'health':v['State'].get('Health',{}).get('Status')}
before={r:status(r) for r in ('v2-site-access','v2-chrome-supervisor')}
started=datetime.now(timezone.utc).isoformat()
log=Path('/var/lib/doxagent/releases/reuters-tail-retirement-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.log')
fd=os.open(log,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
with os.fdopen(fd,'w') as f:
    code=subprocess.run(['python3','scripts/ops/storage_release.py','release','--commit',commit,'--component','v2-message-collector'],cwd=repo,stdout=f,stderr=subprocess.STDOUT).returncode
out={'commit':commit,'started_at':started,'finished_at':datetime.now(timezone.utc).isoformat(),'returncode':code,'private_log':str(log),'collector':status('v2-message-collector'),'other_containers_unchanged':all(status(r)==s for r,s in before.items())}
if code==0:
    rel='src/doxagent/message_bus_v2/collection_plane/worker.py'
    expected=hashlib.sha256(subprocess.check_output(['git','show',commit+':'+rel],cwd=repo)).hexdigest()
    actual=subprocess.check_output(['docker','exec','doxagent-v2-v2-message-collector-1','python','-c',"import hashlib;from pathlib import Path; print(hashlib.sha256(Path('/app/"+rel+"').read_bytes()).hexdigest())"],text=True).strip()
    out.update(expected_hash=expected,actual_hash=actual,hashes_match=expected==actual)
Path('/tmp/reuters-tail-retirement-release.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2));raise SystemExit(code)
