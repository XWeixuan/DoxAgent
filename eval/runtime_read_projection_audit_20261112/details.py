import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from doxagent.v2_read.outbox import SourceOutbox

out={"at":datetime.now(timezone.utc).isoformat(),"queries":{}}
read=Path('/data/read/serving-v4-20261018/v2.sqlite3')
def ro(path):
    d=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=.2)
    d.row_factory=sqlite3.Row
    d.execute('PRAGMA query_only=ON')
    return d
def query(db,key,sql,args=(),budget=2):
    start=time.monotonic(); deadline=start+budget
    db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
    try:
        plan=[tuple(r) for r in db.execute('EXPLAIN QUERY PLAN '+sql,args)]
        rows=[dict(r) for r in db.execute(sql,args)]
        out['queries'][key]={'ms':(time.monotonic()-start)*1000,'plan':plan,'rows':rows}
        return rows
    except Exception as exc:
        out['queries'][key]={'ms':(time.monotonic()-start)*1000,'error':str(exc)}
    finally:db.set_progress_handler(None,0)

with ro(read) as d:
    out['read_path']=str(read)
    out['read_pragmas']={x:d.execute('PRAGMA '+x).fetchone()[0] for x in ('journal_mode','synchronous','page_size','page_count','freelist_count','wal_autocheckpoint','cache_size')}
    out['schema']=[dict(r) for r in d.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE type IN ('index','table','view','trigger') AND (tbl_name IN ('object_current','objects','contributions_current','contributions_history','commits','commit_retention','gaps','read_gc_receipts','read_gc_state') OR name LIKE '%gc%')")]
    out['checkpoints']=cp=dict(d.execute('SELECT source,position FROM checkpoints'))
    query(d,'gaps',"SELECT source,reason,count(*) n,min(attempts) min_attempts,max(attempts) max_attempts FROM gaps GROUP BY source,reason")
    query(d,'sync_object_scan',"SELECT count(*) n FROM object_current WHERE kind IN (SELECT value FROM json_each(?)) AND valid_from>?",(json.dumps(['policy_admission','policy_hit_set','policy_change','event_change','fact_change','policy_catalog','event_catalog','fact_catalog','usage','attempt']),0))
    query(d,'object_kinds',"SELECT kind,count(*) n,sum(length(payload)) bytes FROM object_current GROUP BY kind",budget=3)
    query(d,'recent_commits',"SELECT c.seq,c.source,c.source_event,c.at,r.projected_at FROM commits c LEFT JOIN commit_retention r USING(seq) WHERE c.seq>? ORDER BY c.seq",(int(d.execute("SELECT value FROM read_meta WHERE key='highwater'").fetchone()[0])-250,))
    query(d,'read_gc_receipts',"SELECT * FROM read_gc_receipts ORDER BY rowid DESC LIMIT 5")
for source,path in [('runtime','/data/runtime/runtime.sqlite3'),('bus','/data/bus/bus.sqlite3')]:
    with ro(path) as d:
        head=d.execute('SELECT head FROM v2_source_state WHERE source=?',(source,)).fetchone()[0]
        out[source]={'head':head,'checkpoint':cp[source]}
        query(d,source+'_backlog',"SELECT table_name,count(*) n,min(recorded_at) first_at,max(recorded_at) last_at FROM (SELECT table_name,recorded_at FROM v2_source_outbox WHERE seq>? ORDER BY seq LIMIT 16000) GROUP BY table_name",(cp[source],))
        query(d,source+'_hours',"SELECT substr(recorded_at,1,13) hour,count(*) n,min(seq) first_seq,max(seq) last_seq FROM (SELECT seq,recorded_at FROM v2_source_outbox WHERE seq>? ORDER BY seq) GROUP BY hour",(max(0,head-140000),),budget=3)
        query(d,source+'_tail_mix',"SELECT table_name,count(*) n FROM (SELECT table_name FROM v2_source_outbox WHERE seq>? ORDER BY seq) GROUP BY table_name",(head-12000,))
        if source=='runtime':
            query(d,'runtime_namespaces',"SELECT json_extract(r.payload,'$.namespace') namespace,count(*) n,round(avg(length(r.payload))) mean_envelope_bytes,max(length(r.payload)) max_envelope_bytes FROM v2_source_outbox o JOIN v2_source_receipts r ON r.id=o.receipt_id WHERE o.seq>? AND o.table_name='runtime_values' GROUP BY namespace",(head-12000,),budget=3)
            query(d,'runtime_receipt_sizes',"SELECT o.table_name,count(*) n,round(avg(length(r.payload))) mean_envelope_bytes,max(length(r.payload)) max_envelope_bytes FROM v2_source_outbox o JOIN v2_source_receipts r ON r.id=o.receipt_id WHERE o.seq>? GROUP BY o.table_name",(head-12000,),budget=3)
    s=SourceOutbox(path,source)
    ts=time.monotonic();coords=s.coordinates(cp[source],100)
    timings=[]
    with s.event_reader(serving=True) as load:
        for coordinate in coords[:40]:
            start=time.monotonic(); e=load(coordinate)
            timings.append({'seq':coordinate,'table':e['table_name'],'ms':(time.monotonic()-start)*1000,'skipped':e.get('serving_skipped',False),'decoded_chars':len(json.dumps(e['row']))})
    out[source]['event_read_probe']={'total_ms':(time.monotonic()-ts)*1000,'samples':timings}
print(json.dumps(out))
