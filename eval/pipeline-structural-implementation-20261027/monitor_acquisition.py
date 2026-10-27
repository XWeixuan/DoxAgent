"""Bounded live acquisition audit; full transport records only at fixed window end."""
import json,sqlite3,sys,time,gzip
from pathlib import Path
from datetime import datetime,UTC
from doxagent.settings import DoxAgentSettings
from doxagent.message_bus_v2.collection_plane.store import AcquisitionStore,path_for
from doxagent.message_bus_v2.cadence import cadence
settings=DoxAgentSettings();store=AcquisitionStore(path_for(settings.message_bus_v2_sqlite_path))
until=datetime.now(UTC).isoformat();since=sys.argv[1] if len(sys.argv)>1 else store.get_meta('schema')['installed_at']
full=len(sys.argv)>2 and sys.argv[2]=='full'
out={'since':since,'until':until,'configuration':store.get_meta('configuration',{}),'errors':{}}
with store.connect() as db:
 deadline=time.monotonic()+15;db.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
 out['units']=[{**dict(r),'head':json.loads(r['head_json'])} for r in db.execute('SELECT binding_id,source_id,ticker,unit,required,generation,head_json FROM units')]
 for r in out['units']:r.pop('head_json',None)
 out['cycles']=[dict(r) for r in db.execute('SELECT source_id,status,count(*) n,min(dispatch_at) oldest,max(dispatch_at) latest FROM cycles WHERE dispatch_at>=? GROUP BY source_id,status',(since,))]
 out['pending']=[dict(r) for r in db.execute("SELECT source_id,status,count(*) n,min(observed_at) oldest FROM captures WHERE status='PENDING' GROUP BY source_id,status")]
 out['delta_delivery']=[dict(r) for r in db.execute('SELECT source_id,count(*) n,avg((julianday(accepted_at)-julianday(observed_at))*86400) list_to_accept_seconds,max((julianday(accepted_at)-julianday(observed_at))*86400) list_to_accept_max FROM captures WHERE observed_at>=? GROUP BY source_id',(since,))]
 out['recent_failures']=[dict(r) for r in db.execute("SELECT source_id,ticker,unit,status,reason,dispatch_at FROM cycles WHERE dispatch_at>=? AND status IN ('FAILED','DEFERRED','UNPROVEN') ORDER BY dispatch_at DESC LIMIT 20",(since,))]
 if full:
  schedules=[dict(r) for r in db.execute("SELECT * FROM cycles WHERE status='SCHEDULE' AND dispatch_at<=? ORDER BY dispatch_at",(until,))]
  attempts=[dict(r) for r in db.execute("SELECT * FROM cycles WHERE dispatch_at>=? AND dispatch_at<=? AND status!='SCHEDULE' ORDER BY dispatch_at LIMIT 100001",(since,until))]
  out['sample_limit_reached']=len(attempts)>100000
  runs=[];captures=[]
  for r in schedules+attempts:
   proof=json.loads(r.pop('data_json'));r.update(proof);runs.append(r)
   if not proof.get('list_validated') or not proof.get('winning_request_id'):continue
   winning=next((q for q in proof.get('requests',[]) if q.get('physical_request_id')==proof['winning_request_id']),None)
   if winning and winning.get('request_started_at'):
    captures.append({**r,'capture_id':'proof:'+r['run_id'],'observed_at':proof['listing_received_at'],
                     'request_started_at':winning['request_started_at']})
  out['raw_runs']=runs
  out['cadence']=cadence(runs,captures,datetime.fromisoformat(since),datetime.fromisoformat(until))
  out['by_source']={s:cadence([r for r in runs if r['source_id']==s],[r for r in captures if r['source_id']==s],datetime.fromisoformat(since),datetime.fromisoformat(until)) for s in {r['source_id'] for r in runs}}
print(json.dumps(out,ensure_ascii=False))
