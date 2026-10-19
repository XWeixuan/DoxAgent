"""Bounded sustained acceptance: owner receipts only, no extra publisher traffic."""
import json,sqlite3,subprocess,time,sys
from datetime import datetime,UTC
c=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-v2-site-access-1','doxagent-v2-v2-message-bus-1']))
site=next(v['Source'] for v in c[0]['Mounts'] if v['Destination']=='/site-data')+'/registry/site_strategy.sqlite3'
bus=next(v['Source'] for v in c[1]['Mounts'] if v['Destination']=='/data')+'/bus/bus.sqlite3'
since=datetime.now(UTC).isoformat()
for i in range(int(sys.argv[1]) if len(sys.argv)>1 else 21):
 out={'at':datetime.now(UTC).isoformat(),'round':i,'since':since}
 try:
  with sqlite3.connect('file:'+bus+'?mode=ro',uri=True,timeout=2) as db:
   out['polls']=[{k:v.get(k) for k in ('ticker','status','last_attempt_at','last_success_at','last_error_code','last_error_message','consecutive_failures','acquisition_metadata')} for v in [json.loads(r[0]) for r in db.execute("select data_json from poll_states where source_id='reuters_site_search'")]]
  with sqlite3.connect('file:'+site+'?mode=ro',uri=True,timeout=2) as db:
   out['events']=[{'operation_id':r[0],'at':r[1],**json.loads(r[2])} for r in db.execute("select operation_id,occurred_at,payload_json from access_events where site_id='reuters' and julianday(occurred_at)>=julianday(?) order by occurred_at",(since,))]
   out['runtime']=json.loads(db.execute("select data_json from site_runtime where runtime_key='scope:source:reuters_site_search'").fetchone()[0])
  print(json.dumps(out),flush=True)
 except Exception as exc:print(json.dumps({'at':out['at'],'error':type(exc).__name__}),flush=True)
 if i+1<(int(sys.argv[1]) if len(sys.argv)>1 else 21):time.sleep(60)
