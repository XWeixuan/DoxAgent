import json,sqlite3,collections,statistics,argparse
from pathlib import Path
from datetime import datetime,timezone,timedelta
parser=argparse.ArgumentParser();parser.add_argument('--since');args=parser.parse_args()
now=datetime.now(timezone.utc);since=args.since or (now-timedelta(hours=24)).isoformat()
d=sqlite3.connect('file:/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3?mode=ro',uri=True);d.row_factory=sqlite3.Row
vals=collections.defaultdict(list);counts=collections.Counter();slow=[];class_vals=collections.defaultdict(lambda:collections.defaultdict(list))
for r in d.execute("select occurred_at,operation_id,payload_json from access_events where site_id='reuters' and julianday(occurred_at)>=julianday(?) and category='ACCESS_RESULT'",(since,)):
 v=json.loads(r['payload_json'])
 if v.get('purpose')!='CRAWLER':continue
 counts[v.get('identity_id'),v.get('disposition'),v.get('reason'),v.get('work_class')]+=1
 ts={**v.get('stage_timings',{}),'network_ms':v.get('network_ms',0),'queue_wait_ms':v.get('queue_wait_ms',0)}
 if v.get('disposition')=='SUCCESS':
  for k,x in ts.items():
   if isinstance(x,(int,float)) and 'epoch' not in k and 'routing' not in k: vals[k].append(x);class_vals[v.get("work_class","unknown")][k].append(x)
  slow.append({'at':r['occurred_at'],'operation':r['operation_id'],'identity':v.get('identity_id'),'class':v.get('work_class'),'timings':ts})
def stats(a):
 a=sorted(a);return {'n':len(a),'mean':round(statistics.mean(a),2),'p50':a[len(a)//2],'p95':a[int((len(a)-1)*.95)],'max':max(a)}
out={'since':since,'until':now.isoformat(),'counts':[{'key':k,'n':n} for k,n in counts.items()],'timings':{k:stats(a) for k,a in vals.items()},'timings_by_work_class':{c:{k:stats(a) for k,a in times.items()} for c,times in class_vals.items()},'slow':sorted(slow,key=lambda x:x['timings']['network_ms'],reverse=True)[:20]}
print(json.dumps(out,indent=2))
