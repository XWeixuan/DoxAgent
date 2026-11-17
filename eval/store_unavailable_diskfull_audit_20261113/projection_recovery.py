import subprocess,json,shutil,sqlite3
from pathlib import Path
from datetime import datetime,timezone
out={'at':datetime.now(timezone.utc).isoformat(),'free':shutil.disk_usage('/').free,'roles':{}}
for role in ['v2-projector','v2-api','v2-message-bus','v2-maintenance']:
 j=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-'+role+'-1']))[0]
 out['roles'][role]={'state':j['State'],'restart_policy':j['HostConfig']['RestartPolicy'],'restart_count':j['RestartCount'],'command':j['Config']['Cmd']}
 r=subprocess.run(['docker','logs','--timestamps','--tail','70','doxagent-v2-'+role+'-1'],capture_output=True,text=True,timeout=10)
 if role=='v2-projector':out['projector_logs']=(r.stdout+r.stderr)[-18000:]
base=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
read=base/'read/serving-v4-20261018/v2.sqlite3'
with sqlite3.connect(read.as_uri()+'?mode=ro',uri=True,timeout=.3) as db:
 db.execute('PRAGMA query_only=ON');out['checkpoints']=dict(db.execute('SELECT source,position FROM checkpoints'));out['health']=list(db.execute('SELECT * FROM source_health'));out['gaps']=list(db.execute('SELECT source,reason,count(*) FROM gaps GROUP BY source,reason'))
out['sources']={}
for name,path in [('runtime','runtime/runtime.sqlite3'),('bus','bus/bus.sqlite3'),('initialization','initialization/control.sqlite3'),('research','research/research.sqlite3')]:
 try:
  with sqlite3.connect((base/path).as_uri()+'?mode=ro',uri=True,timeout=.3) as db:
   cp=out['checkpoints'].get(name,0);head=db.execute('SELECT head FROM v2_source_state WHERE source=?',(name,)).fetchone()[0];n=db.execute('SELECT seq,recorded_at FROM v2_source_outbox WHERE seq>? ORDER BY seq LIMIT 1',(cp,)).fetchone();out['sources'][name]={'head':head,'checkpoint':cp,'distance':head-cp,'next':n}
 except Exception as e:out['sources'][name]={'error':str(e)}
print(json.dumps(out))
