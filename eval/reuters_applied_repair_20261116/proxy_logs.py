import json,sqlite3,subprocess,collections
from pathlib import Path
from datetime import datetime,timezone
root=Path('/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data');cfg=json.loads((root/'credentials/datadome-solver.json').read_text());users={v['proxyLogin']:k for k,v in cfg['bindings'].items()};raw=subprocess.check_output(['docker','logs','--since','2026-11-12T23:24:40Z','doxagent-captcha-proxy'],stderr=subprocess.DEVNULL,text=True);counts=collections.Counter();examples=[];times=[]
for line in raw.splitlines():
 p=line.split()
 if len(p)<8:continue
 try:ts=float(p[0])
 except ValueError:continue
 status,domain=p[2],p[6];identity=users.get(p[5],'UNAUTHENTICATED');counts[status,domain,identity]+=1;times.append(ts)
 if 'DENIED' in status and len(examples)<8:examples.append({'at':datetime.fromtimestamp(ts,timezone.utc).isoformat(),'status':status,'domain':domain,'identity':identity})
out={'at':datetime.now(timezone.utc).isoformat(),'retained_from':datetime.fromtimestamp(min(times),timezone.utc).isoformat() if times else None,'retained_until':datetime.fromtimestamp(max(times),timezone.utc).isoformat() if times else None,'counts':[{'status':k[0],'domain':k[1],'identity':k[2],'n':n} for k,n in counts.most_common()],'examples':examples};print(json.dumps(out,indent=2))
