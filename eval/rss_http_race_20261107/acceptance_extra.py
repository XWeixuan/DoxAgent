import collections,json,sqlite3,sys,time
from datetime import UTC,datetime
from doxagent.message_bus_v2.rss_http_race import RECIPES,IR_RECIPES
start,end=sys.argv[1:3]
db=sqlite3.connect('file:/data/bus/acquisition.sqlite3?mode=ro',uri=True,timeout=3);db.row_factory=sqlite3.Row
ids=[*RECIPES,'corporate_ir_press_releases'];captures={}
for r in db.execute('select source_id,status,count(*) n,max(accepted_at) latest from captures indexed by captures_time where observed_at>=? and observed_at<? and source_id in(select value from json_each(?)) group by source_id,status',(start,end,json.dumps(ids))):captures.setdefault(r['source_id'],{})[r['status']]={'count':r['n'],'latest_accepted_at':r['latest']}
proofs=[];seen=set();gaps=collections.defaultdict(list);transports=collections.Counter()
for r in db.execute("select * from cycles indexed by cycles_time where dispatch_at>=? and dispatch_at<? and status!='SCHEDULE' and source_id in(select value from json_each(?))",(start,end,json.dumps(ids))):
 if r['source_id']=='corporate_ir_press_releases' and r['unit'] not in ['IR:'+k for k in IR_RECIPES]:continue
 p=json.loads(r['data_json']);pid=p.get('physical_run_id') or r['run_id'].split(':')[0]
 if pid in seen:continue
 seen.add(pid);row=db.execute('select requests_json from physical_cycles where run_id=?',(pid,)).fetchone()
 if not row:continue
 events=json.loads(row[0]);transports.update(e.get('transport','unknown') for e in events)
 dates=[datetime.fromisoformat(e['response_received_at']) for e in events if e.get('list_validated') and e.get('response_received_at')]
 if dates:gaps[(r['source_id'],r['binding_id'] if r['source_id']=='power_magazine_energy' else '',r['unit'])].append(min(dates))
summary={}
for (sid,bid,unit),times in gaps.items():
 dates=sorted(times);span=[(b-a).total_seconds() for a,b in zip(dates,dates[1:])]
 g=summary.setdefault(sid,{'units':0,'max_first_list_gap_seconds':None,'samples':0});g['units']+=1;g['samples']+=len(span)
 if span:g['max_first_list_gap_seconds']=round(max(max(span),g['max_first_list_gap_seconds'] or 0),3)
print(json.dumps({'start':start,'end':end,'business_captures':captures,'transports':transports,'cadence':summary}))
