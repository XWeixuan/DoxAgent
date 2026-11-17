import json,sqlite3,collections,statistics
from pathlib import Path
from datetime import datetime,timezone
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/bus/acquisition.sqlite3');c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=3);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');out={'at':datetime.now(timezone.utc).isoformat(),'windows':{},'current_units':[]};units=c.execute('select * from units where source_id=?',('reuters_site_search',)).fetchall()
for label,since,until in [('before','2026-11-16T14:33:53Z','2026-11-16T14:43:53Z'),('steady','2026-11-16T14:44:15Z',out['at'])]:
 counts=collections.Counter();per_ticker={};intervals=[];latest=[]
 for u in units:
  rows=c.execute('select status,reason,dispatch_at,finished_at,data_json from cycles indexed by cycles_unit_time where binding_id=? and unit=? and dispatch_at>=? and dispatch_at<? order by dispatch_at',(u['binding_id'],u['unit'],since,until)).fetchall();physical={}
  for r in rows:
   v=json.loads(r['data_json']);physical[v.get('physical_run_id') or r['dispatch_at']]=dict(r)
  local=collections.Counter(v['status']+':'+str(v['reason']) for v in physical.values());counts.update(local);per_ticker.setdefault(u['ticker'],collections.Counter()).update(local)
  successes=[datetime.fromisoformat(r['dispatch_at'].replace('Z','+00:00')) for r in physical.values() if r['status'] in ('CAPTURED','SUCCEEDED')]
  intervals.extend((b-a).total_seconds() for a,b in zip(successes,successes[1:]))
 out['windows'][label]={'since':since,'until':until,'unit_cycles_deduped_per_unit':dict(counts),'by_ticker':{t:dict(v) for t,v in per_ticker.items()},'interval_seconds':{'n':len(intervals),'median':statistics.median(intervals) if intervals else None,'mean':statistics.mean(intervals) if intervals else None,'max':max(intervals) if intervals else None}}
for u in units:
 h=json.loads(u['head_json']);out['current_units'].append({'ticker':u['ticker'],'unit':u['unit'],'required':u['required'],'head':{k:h.get(k) for k in ['status','last_success_at','last_attempt_at','last_error_code','next_dispatch_at','target_due_at']}})
print(json.dumps(out,indent=2))
