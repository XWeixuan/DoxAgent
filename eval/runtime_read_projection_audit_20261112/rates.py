import json
import sqlite3
import time
from datetime import datetime, timezone, timedelta

out={'at':datetime.now(timezone.utc).isoformat(),'queries':{}}
def run(path,key,sql,args=(),budget=3):
    d=sqlite3.connect('file:'+path+'?mode=ro',uri=True,timeout=.2);d.row_factory=sqlite3.Row;d.execute('PRAGMA query_only=ON')
    t=time.monotonic();d.set_progress_handler(lambda:int(time.monotonic()-t>budget),1000)
    try:out['queries'][key]={'rows':[dict(r) for r in d.execute(sql,args)],'ms':(time.monotonic()-t)*1000}
    except Exception as exc:out['queries'][key]={'error':str(exc),'ms':(time.monotonic()-t)*1000}
    finally:d.close()
for source,path in [('runtime','/data/runtime/runtime.sqlite3'),('bus','/data/bus/bus.sqlite3')]:
    d=sqlite3.connect('file:'+path+'?mode=ro',uri=True);head=d.execute('SELECT head FROM v2_source_state WHERE source=?',(source,)).fetchone()[0];d.close()
    run(path,source+'_minute_production',"SELECT substr(recorded_at,1,16) minute,count(*) n,min(seq) first_seq,max(seq) last_seq FROM v2_source_outbox WHERE seq>? GROUP BY minute ORDER BY minute",(head-100000,))
for hour in range(16,22):
    run('/data/read/serving-v4-20261018/v2.sqlite3','consumed_'+str(hour),"SELECT substr(r.projected_at,1,16) minute,c.source,count(*) mutated_commits,max(CASE WHEN c.source_event NOT GLOB '*[^0-9]*' THEN CAST(c.source_event AS INTEGER) END) max_position,max((julianday(r.projected_at)-julianday(c.at))*86400) max_event_delay FROM commit_retention r INDEXED BY commit_retention_at JOIN commits c ON c.seq=r.seq WHERE r.projected_at>=? AND r.projected_at<? GROUP BY minute,c.source ORDER BY minute,c.source",(f'2026-11-11T{hour:02}:00:00',f'2026-11-11T{hour+1:02}:00:00'))
print(json.dumps(out))
