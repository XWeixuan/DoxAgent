import sqlite3,json,math,time
from datetime import datetime,timezone
p='/data/read/serving-v4-20261018/v2.sqlite3';db=sqlite3.connect('file:'+p+'?mode=ro',uri=True,timeout=.2)
deadline=time.monotonic()+3;db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
rows=db.execute("SELECT c.source,c.seq,c.at,r.projected_at,(julianday(r.projected_at)-julianday(c.at))*86400 FROM commits c JOIN commit_retention r ON r.seq=c.seq WHERE c.seq>? AND c.seq<=13274849 AND r.projected_at<='2026-11-12T13:47:34.124718Z' AND c.source IN ('research','initialization','bus','runtime') AND c.source_event<>'' AND c.source_event NOT GLOB '*[^0-9]*' ORDER BY c.seq",(13272048,)).fetchall()
def stats(v):
 v=sorted(v)
 return {'n':len(v),'p50':v[max(0,math.ceil(len(v)*.5)-1)],'p95':v[max(0,math.ceil(len(v)*.95)-1)],'p99':v[max(0,math.ceil(len(v)*.99)-1)],'max':v[-1]} if v else {'n':0}
out={'at':datetime.now(timezone.utc).isoformat(),'floor':13272048,'all_mutation_receipts':{},'steady_mutation_latency':{}}
for source in ['research','initialization','bus','runtime']:
 selected=[r for r in rows if r[0]==source]
 out['all_mutation_receipts'][source]=len(selected)
 out['steady_mutation_latency'][source]=stats([r[4] for r in selected if r[2]>='2026-11-12T13:18:00' and r[3]>='2026-11-12T13:18:00'])
print(json.dumps(out))
