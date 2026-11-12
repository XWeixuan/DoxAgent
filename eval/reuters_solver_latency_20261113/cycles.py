import sqlite3,json,statistics,collections,argparse
from datetime import datetime,timezone,timedelta
from pathlib import Path
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/bus/acquisition.sqlite3');d=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);d.row_factory=sqlite3.Row
parser=argparse.ArgumentParser();parser.add_argument('--since');args=parser.parse_args();now=datetime.now(timezone.utc);out={'at':now.isoformat()};cut=args.since or '2026-11-12T22:07:44Z';vals=collections.defaultdict(list);counts=collections.Counter();gaps=collections.defaultdict(list)
for r in d.execute("select ticker,unit,status,reason,dispatch_at,finished_at from cycles where source_id='reuters_site_search' and dispatch_at>=? and status not in ('SCHEDULE','DISPATCHED') order by dispatch_at",((args.since or (now-timedelta(hours=24)).isoformat())[:19],)):
 if not r['finished_at']:continue
 a=datetime.fromisoformat(r['dispatch_at'].replace('Z','+00:00'));b=datetime.fromisoformat(r['finished_at'].replace('Z','+00:00'));part='after' if a>=datetime.fromisoformat(cut.replace('Z','+00:00')) else 'before';kind=r['unit'].split(':')[0]
 counts[part,kind,r['status'],r['reason']]+=1;vals[part,kind,r['status']].append((b-a).total_seconds())
 if part=='after' and r['status']=='CAPTURED':gaps[r['ticker'],r['unit']].append(b)
def stats(a):
 a=sorted(a);return {'n':len(a),'mean':round(statistics.mean(a),2),'p50':round(a[len(a)//2],2),'p95':round(a[int((len(a)-1)*.95)],2)}
out['counts']=[{'key':k,'n':v} for k,v in counts.items()];out['cycle_latency']=[{'key':k,**stats(v)} for k,v in vals.items()]
out['capture_gap']=[{'ticker':k[0],'unit':k[1],**stats([(b-a).total_seconds() for a,b in zip(v,v[1:])])} for k,v in gaps.items() if len(v)>1]
print(json.dumps(out,indent=2))
