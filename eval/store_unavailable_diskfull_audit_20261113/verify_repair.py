import sqlite3,json,subprocess
from pathlib import Path
b=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
r=sqlite3.connect((b/'read/serving-v4-20261018/v2.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=1)
print('generation',r.execute('SELECT id FROM generation').fetchone()[0])
print('incident gaps',r.execute("SELECT event,reason FROM gaps WHERE source='runtime' AND event IN ('4624585','4624587','4624591','4624593','4624595')").fetchall())
print('repair commits',r.execute("SELECT source_event FROM commits WHERE source='runtime' AND source_event IN ('4624585:repair:1','4624587:repair:1','4624591:repair:1','4624593:repair:1','4624595:repair:1')").fetchall())
for unit in ['doxagent-projector-recovery.service','doxagent-projector-recovery.timer']:
 p=subprocess.run(['systemctl','show',unit,'--property=ActiveState,SubState,Result,MemoryPeak,CPUUsageNSec,ExecMainStatus'],capture_output=True,text=True);print(unit,p.stdout)
info=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-v2-projector-1']))[0]
print('image',info['Image'],'restarts',info['RestartCount'])
p=subprocess.run(['docker','exec','doxagent-v2-v2-api-1','python','-c',"import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=5).status)"],capture_output=True,text=True,timeout=10);print('API healthz',p.stdout,p.stderr)
