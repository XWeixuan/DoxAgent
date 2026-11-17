import sqlite3,json,subprocess,time,shutil
from pathlib import Path
from datetime import datetime,timezone
b=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
def ro(p):return sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=1)
with ro(b/'read/serving-v4-20261018/v2.sqlite3') as r:start=r.execute('SELECT max(seq) FROM commits').fetchone()[0]
for i in range(61):
 out={'at':datetime.now(timezone.utc).isoformat(),'free':shutil.disk_usage('/').free,'sources':{}}
 with ro(b/'read/serving-v4-20261018/v2.sqlite3') as r:
  cp=dict(r.execute('SELECT source,position FROM checkpoints'));out['gaps']=list(r.execute('SELECT source,reason,count(*) FROM gaps GROUP BY source,reason'))
  out['clock']=list(r.execute("SELECT key,value FROM read_meta WHERE key LIKE 'projection_clock:%'"))
 for name,path in [('runtime','runtime/runtime.sqlite3'),('bus','bus/bus.sqlite3'),('initialization','initialization/control.sqlite3'),('research','research/research.sqlite3')]:
  with ro(b/path) as s:
   head=s.execute('SELECT head FROM v2_source_state WHERE source=?',(name,)).fetchone()[0];n=s.execute('SELECT seq,recorded_at FROM v2_source_outbox WHERE seq>? ORDER BY seq LIMIT 1',(cp[name],)).fetchone();out['sources'][name]={'head':head,'cp':cp[name],'next':n,'ack':list(s.execute('SELECT checkpoint FROM v2_source_consumers'))}
 info=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-v2-projector-1']))[0];out['state']=info['State']['Status'];out['restarts']=info['RestartCount'];out['oom']=info['State']['OOMKilled']
 pid=info['State']['Pid'];out['proc_stat']=Path('/proc/'+str(pid)+'/stat').read_text() if pid else None
 out['io']=Path('/proc/'+str(pid)+'/io').read_text() if pid else None
 out['pressure']={k:Path('/proc/pressure/'+k).read_text() for k in ['cpu','io','memory']}
 out['mem_available_kib']=next(l for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable'))
 print(json.dumps(out),flush=True)
 if i<60:time.sleep(30)
with ro(b/'read/serving-v4-20261018/v2.sqlite3') as r:
 vals={}
 for source in ['runtime','bus','initialization','research']:
  v=[x[0] for x in r.execute('SELECT (julianday(t.projected_at)-julianday(c.at))*86400 FROM commits c JOIN commit_retention t ON t.seq=c.seq WHERE c.seq>? AND c.source=? AND c.source_event NOT LIKE ?', (start,source,'%:repair:%')) if x[0] is not None]
  v.sort();vals[source]={'n':len(v),'p95':v[min(len(v)-1,int(len(v)*.95))] if v else None,'p99':v[min(len(v)-1,int(len(v)*.99))] if v else None,'max':max(v) if v else None}
 print(json.dumps({'latencies':vals}),flush=True)
