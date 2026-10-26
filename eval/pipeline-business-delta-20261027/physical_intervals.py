"""Separate real request/list cadence from stale due_at attribution."""
import gzip,json,csv,math
from collections import defaultdict,Counter
from datetime import datetime,timedelta
from pathlib import Path
from doxagent.message_bus_v2.cadence import distribution
root=Path(__file__).parent
def read(n):return json.load(gzip.open(root/n,'rt',encoding='utf-8'))
def rows(n):
 with gzip.open(root/n,'rt',encoding='utf-8') as f:
  return [r for line in f if (r:=json.loads(line)).get('kind') not in ('window','complete')]
def at(s):return datetime.fromisoformat(s.replace('Z','+00:00'))
import sys
prefix=sys.argv[1]
output_prefix=sys.argv[4] if len(sys.argv)>4 else prefix
b=read(prefix+'-bus.json.gz')
runs={r['run_id']:r for r in rows(prefix+'-runs.jsonl.gz')}
caps=rows(prefix+'-captures.jsonl.gz');groups=defaultdict(lambda:dict(schedules=[],runs=[],caps=[]));byrun=defaultdict(list)
for c in caps:byrun[c['run_id']].append(c)
for s in b['schedules']:groups[(s['binding_id'],s['unit'])]['schedules'].append(s)
for r in runs.values():
 if r['unit'].startswith('TAIL:'):continue
 g=groups[(r['binding_id'],r['unit'])];g['runs'].append(r)
 if r.get('shared_run_id') and r['status']=='CAPTURED' and r.get('requests'):
  for c in byrun[r['shared_run_id']]:
   if c['source_id']==r['source_id'] and c.get('source_version') in (None,r.get('source_version')):
    g['caps'].append((c,r))
for c in caps:
 if c['unit'].startswith('TAIL:'):continue
 groups[(c['binding_id'],c['unit'])]['caps'].append((c,runs.get(c['run_id'])))
out=[]
for label,S,E in [('window',sys.argv[2] if len(sys.argv)>2 else b['since'],sys.argv[3] if len(sys.argv)>3 else b['until'])]:
 start,end=at(S),at(E);source_rows=defaultdict(list);physical={};costs=defaultdict(list)
 for (binding,unit),g in groups.items():
  if unit.startswith('TAIL:'):continue
  source=binding.partition(':')[2];ss=sorted(g['schedules'],key=lambda r:r['dispatch_at']);active=[];expected=0
  for i,s in enumerate(ss):
   lo=max(start,at(s['dispatch_at']));hi=min(end,at(ss[i+1]['dispatch_at'])) if i+1<len(ss) else end
   if s.get('reason')!='ENABLED' or lo>=hi:continue
   epoch=at(s.get('epoch_at') or s['due_at']);period=s['interval_seconds']
   expected+=max(0,math.floor(((hi-epoch).total_seconds()-.000001)/period)-max(0,math.ceil((lo-epoch).total_seconds()/period))+1)
   active.append((lo,hi,epoch,period,s))
  if not expected:continue
  def slot(t,value):
   for lo,hi,epoch,period,s in active:
    if lo<=t<hi and all(value.get(k) in (None,s.get(k)) or s.get(k) is None for k in ('source_version','binding_version')):
     index=math.floor((t-epoch).total_seconds()/period)
     if epoch+timedelta(seconds=period*index)>=lo:return (epoch.isoformat(),index)
   return None
  request_times=set();fresh_times=set();request_slots=set();fresh_slots=set()
  for r in g['runs']:
   for req in r.get('requests',[]):
    if req.get('role','listing')!='listing' or not req.get('request_started_at'):continue
    t=at(req['request_started_at']);key=slot(t,r)
    if key:
     request_times.add(t);request_slots.add(key)
     pid=req.get('physical_request_id') or (source,req['request_started_at'])
     if pid not in physical and req.get('response_received_at'):
      physical[pid]=True
      costs[source].append((at(req['response_received_at'])-t).total_seconds())
  for c,r in g['caps']:
   if not r or r['status']!='CAPTURED':continue
   ts=[at(q['request_started_at']) for q in r.get('requests',[]) if q.get('role','listing')=='listing' and q.get('request_started_at')]
   if not ts:continue
   t=at(c['observed_at']);req=at(c['request_started_at']) if c.get('request_started_at') else min(ts)
   key=slot(t,r)
   if key and slot(req,r)==key:fresh_slots.add(key)
   if slot(t,r):fresh_times.add(t)
  def gaps(times,boundary=True):
   result=[]
   exposure=[]
   for lo,hi,epoch,period,config in active:
    generation=(period,config.get('source_version'),config.get('binding_version'))
    if exposure and exposure[-1][1]==lo and exposure[-1][2]==generation:
     exposure[-1]=(exposure[-1][0],hi,generation)
    else:exposure.append((lo,hi,generation))
   for lo,hi,_ in exposure:
    values=sorted(t for t in times if lo<=t<hi)
    if boundary:values=[lo,*values,hi]
    result += [(y-x).total_seconds() for x,y in zip(values,values[1:])]
   return result
  source_rows[source].append(dict(expected=expected,requests=len(request_slots),fresh=len(fresh_slots),request_gaps=gaps(request_times),fresh_gaps=gaps(fresh_times),request_intervals=gaps(request_times,False),fresh_intervals=gaps(fresh_times,False)))
 for source,gg in source_rows.items():
  expected=sum(x['expected'] for x in gg)
  out.append(dict(window=label,source_id=source,expected=expected,actual_grid_request_slots=sum(x['requests'] for x in gg),
   actual_grid_fresh_slots=sum(x['fresh'] for x in gg),actual_grid_fresh_coverage=sum(x['fresh'] for x in gg)/expected,
   request_gaps=distribution([t for x in gg for t in x['request_gaps']]),fresh_gaps=distribution([t for x in gg for t in x['fresh_gaps']]),
   request_intervals=distribution([t for x in gg for t in x["request_intervals"]]),fresh_intervals=distribution([t for x in gg for t in x["fresh_intervals"]]),
   physical_response_seconds=distribution(costs[source])))
(root/(output_prefix+'-physical-intervals.json')).write_text(json.dumps(out,indent=2),encoding='utf-8')
a=json.loads((root/(output_prefix+'-analysis.json')).read_text(encoding='utf-8'))
cols=['window','source_id','expected_cycles','fresh_cycles','coverage','missing_cycles','pending_cycles','units']
with (root/(output_prefix+'-source-coverage.csv')).open('w',encoding='utf-8-sig',newline='') as f:
 w=csv.DictWriter(f,fieldnames=cols);w.writeheader()
 for window,v in a['windows'].items():
  for r in v['sources']:w.writerow(dict(window=window,**{c:r[c] for c in cols[1:]}))
for r in out:
 if r['source_id'] in {'reuters_site_search','yahoo_finance_news','investorshub_ticker_news','investing_ticker_news','barrons_ticker_news','google_news_search_rss'}:print(r)
