import sqlite3,json,sys,subprocess
from datetime import datetime,UTC,timedelta
sid=sys.argv[1];since=sys.argv[2] if len(sys.argv)>2 else (datetime.now(UTC)-timedelta(minutes=5)).isoformat()
p='/var/lib/docker/volumes/doxagent-v2_v2-data/_data/bus/acquisition.sqlite3'
c=sqlite3.connect('file:'+p+'?mode=ro',uri=True,timeout=3);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on')
units=c.execute('select * from units where source_id=?',(sid,)).fetchall();out=[]
for u in units:
 rows=c.execute('select status,reason,dispatch_at,finished_at,data_json from cycles indexed by cycles_unit_time where binding_id=? and unit=? and dispatch_at>=? order by dispatch_at',(u['binding_id'],u['unit'],since)).fetchall()
 unique={}
 for r in rows:
  d=json.loads(r['data_json']);unique[d.get('physical_run_id') or r['dispatch_at']]={**dict(r),'data_json':d}
 from collections import Counter
 out.append({'ticker':u['ticker'],'unit':u['unit'],'counts':dict(Counter(r['status']+':'+str(r['reason']) for r in unique.values())),
             'last':[{k:r[k] for k in ['dispatch_at','finished_at','status','reason']} for r in list(unique.values())[-2:]],
             'head':json.loads(u['head_json'])})
info=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-v2-message-collector-1']))[0]
env=dict(x.split('=',1) for x in info['Config']['Env'])
print(json.dumps({'at':datetime.now(UTC).isoformat(),'since':since,'source':sid,'units':out,
 'collector':{'commit':env.get('DOXAGENT_BUILD_COMMIT'),'yahoo_routes':env.get('DOXAGENT_MESSAGE_BUS_V2_YAHOO_HTTP_ROUTES'),'started':info['State']['StartedAt']}}))
