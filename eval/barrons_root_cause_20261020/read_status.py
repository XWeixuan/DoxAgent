"""Read the frontend's actual serving-state builder, without creating a View."""
import subprocess

CODE=r'''
import json
from datetime import UTC,datetime
from doxagent.v2_read.repository import ReadStore
from doxagent.api_v2.bus import source_status
s=ReadStore('/data/read/active.json')
with s.connect() as db:
 seq=s.highwater(db)
 bindings=[json.loads(r[0]) for r in db.execute("select payload from object_current where kind='native:ticker_source_bindings' and json_extract(payload,'$.source_id')='barrons_ticker_news'")]
 checkpoints=[dict(r) for r in db.execute("select * from source_health where source='bus'")]
view={'seq':seq,'as_of':datetime.now(UTC).isoformat(),'wire':{'clock':{'session':{'value':'REALTIME'}}}}
rows=[source_status(s,b,seq,view) for b in bindings if b.get('enabled')]
print(json.dumps({'checked_at':view['as_of'],'seq':seq,'checkpoints':checkpoints,'sources':[{'ticker':b['ticker'],'status':r['poll_status'],'health':r['health_group'],'error':r['current_error'],'last_success':r['last_success_at'],'interval':r['target_interval_seconds']} for b,r in zip([b for b in bindings if b.get('enabled')],rows)]}))
'''
p=subprocess.run(['docker','exec','-i','doxagent-v2-v2-api-1','python','-'],input=CODE,text=True,capture_output=True,timeout=30)
print(p.stdout)
assert p.returncode==0,p.stderr[-500:]
