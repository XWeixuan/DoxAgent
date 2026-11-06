"""Read-only active generation, channel health and physical cycles."""
import collections,json,sqlite3,sys,time
from datetime import datetime,UTC
from pathlib import Path
from doxagent.message_bus_v2.rss_http_race import RECIPES,IR_RECIPES
start=sys.argv[1];end=sys.argv[2] if len(sys.argv)>2 else datetime.now(UTC).isoformat()
db=sqlite3.connect('file:/data/bus/acquisition.sqlite3?mode=ro',uri=True,timeout=3);db.row_factory=sqlite3.Row
deadline=time.monotonic()+10;db.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
ids=[*RECIPES,'corporate_ir_press_releases'];groups={}
rows=db.execute("select * from cycles indexed by cycles_time where dispatch_at>=? and dispatch_at<? and status!='SCHEDULE' and source_id in(select value from json_each(?))",(start,end,json.dumps(ids))).fetchall()
seen=set()
for row in rows:
 r=dict(row)
 if r['source_id']=='corporate_ir_press_releases' and r['unit'] not in ['IR:'+x for x in IR_RECIPES]:continue
 proof=json.loads(r['data_json']);pid=proof.get('physical_run_id') or r['run_id'].split(':')[0]
 if pid in seen:continue
 seen.add(pid);g=groups.setdefault(r['source_id'],{'statuses':collections.Counter(),'units':{},'seconds':[],'errors':[],'channels':collections.defaultdict(collections.Counter),'first_list_seconds':[]})
 g['statuses'][r['status']]+=1;g['units'][r['unit']]=g['units'].get(r['unit'],0)+1
 if r['finished_at']:g['seconds'].append((datetime.fromisoformat(r['finished_at'])-datetime.fromisoformat(r['dispatch_at'])).total_seconds())
 if r['reason']:g['errors'].append({'at':r['dispatch_at'],'unit':r['unit'],'reason':r['reason'],'detail':proof.get('terminal_error_message')})
 pr=db.execute('select requests_json from physical_cycles where run_id=?',(pid,)).fetchone()
 if not pr:continue
 events=json.loads(pr[0]);valid=[]
 for lane in ['http','rss']:
  ev=[e for e in events if e.get('race_lane')==lane]
  if ev:g['channels'][lane]['valid' if any(e.get('list_validated') for e in ev) else 'failed']+=1
 for e in events:
  if e.get('list_validated') and e.get('response_received_at'):valid.append(datetime.fromisoformat(e['response_received_at']))
 if valid:g['first_list_seconds'].append((min(valid)-datetime.fromisoformat(r['dispatch_at'])).total_seconds())
for g in groups.values():
 for field in ['seconds','first_list_seconds']:
  v=sorted(g.pop(field));g[field]={'samples':len(v),'mean':round(sum(v)/len(v),3) if v else None,'p95':round(v[max(0,int(len(v)*.95)-1)],3) if v else None,'max':round(max(v),3) if v else None}
 g['errors']=g['errors'][-30:]
heads=[]
for r in db.execute('select source_id,binding_id,unit,head_json from units where required=1 and source_id in(select value from json_each(?))',(json.dumps(ids),)):
 h=json.loads(r['head_json']);race=h.get('race_observation')
 if race:heads.append({'source':r['source_id'],'binding':r['binding_id'],'unit':r['unit'],'status':h.get('status'),'last_capture':h.get('last_capture_at'),'race':race})
print(json.dumps({'start':start,'end':end,'sources':groups,'heads':heads}))
