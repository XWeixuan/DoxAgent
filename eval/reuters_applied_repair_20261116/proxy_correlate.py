import json,subprocess,collections
from datetime import datetime,timezone
jobs=json.load(open('/tmp/reuters-ledger-20261116.json'))['jobs'] if False else None
# Input safe retained ledger provided on stdin as a file, no provider queries.
from pathlib import Path
ledger=Path('/tmp/reuters-forensic-ledger.json')
jobs=json.loads(ledger.read_text())['jobs'];raw=subprocess.check_output(['docker','logs','--since','2026-11-12T23:24:40Z','doxagent-captcha-proxy'],stderr=subprocess.DEVNULL,text=True)
cfg=json.loads(Path('/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data/credentials/datadome-solver.json').read_text());users={v['proxyLogin']:k for k,v in cfg['bindings'].items()}
logs=[]
for line in raw.splitlines():
 p=line.split()
 if len(p)<8:continue
 try:t=float(p[0])
 except:continue
 logs.append((t,p[2],p[6],p[7],users.get(p[5],'UNAUTHENTICATED')))
out=[]
for j in jobs:
 if not j.get('applied_at'):continue
 start=datetime.fromisoformat(j['submitted_at'] or j['created_at']).timestamp()-3;end=datetime.fromisoformat(j['applied_at']).timestamp()+3
 c=collections.Counter((s,d,parent) for t,s,d,parent,user in logs if start<=t<=end and user in (j['identity_id'],'UNAUTHENTICATED'))
 out.append({'task_id':j['task_id'],'identity':j['identity_id'],'verification_state':j['verification_state'],'denied_attribution':'TIME_WINDOW_ONLY_AUTH_NOT_REACHED','domains':[{'status':s,'domain':d,'parent':parent,'n':n} for (s,d,parent),n in sorted(c.items())]})
print(json.dumps(out,indent=2))
