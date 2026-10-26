"""Small host/resource/release snapshot, without environment or credentials."""
import json,subprocess
from datetime import UTC,datetime
from pathlib import Path

def run(args):return subprocess.check_output(args,text=True,timeout=20).strip()
out={'at':datetime.now(UTC).isoformat(),'containers':[]}
for cid in run(['docker','ps','-q']).splitlines():
 c=json.loads(run(['docker','inspect',cid]))[0]
 labels=c['Config'].get('Labels') or {}
 out['containers'].append(dict(name=c['Name'],id=c['Id'],image=c['Image'],started=c['State']['StartedAt'],
   limits={k:c['HostConfig'].get(k) for k in ['NanoCpus','CpuQuota','CpuPeriod','Memory','MemorySwap','PidsLimit']},
   health=c['State'].get('Health',{}).get('Status'),restarts=c['RestartCount'],
   version={k:v for k,v in labels.items() if 'revision' in k or 'commit' in k}))
out['memory']={k:int(v.split()[0]) for k,v in (l.split(':',1) for l in Path('/proc/meminfo').read_text().splitlines())}
out['cpu']=Path('/proc/stat').read_text().splitlines()[0]
out['load']=Path('/proc/loadavg').read_text().strip()
out['psi']={k:Path('/proc/pressure',k).read_text().strip() for k in ['cpu','io','memory']}
out['stats']=[json.loads(l) for l in run(['docker','stats','--no-stream','--format','{{json .}}']).splitlines()]
state=json.loads(Path('/var/lib/doxagent/releases/state.json').read_text())
out['components']={k:{f:v.get('current',{}).get(f) for f in ('commit','image','accepted_at')}
                   for k,v in state['components'].items()}
print(json.dumps(out))
