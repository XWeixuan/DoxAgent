import sqlite3,json,os,time
from datetime import datetime,UTC
p=os.environ['DOXAGENT_V2_READ_SQLITE_PATH'];c=sqlite3.connect('file:'+p+'?mode=ro',uri=True,timeout=2);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on')
tickers=['BE','CEG','COHR','INTC','IONQ','MU','NBIS','ONDS','RKLB','SNOW'];days=['2026-10-19','2026-10-26','2026-11-02','2026-11-09','2026-11-13','2026-11-16'];out={'at':datetime.now(UTC).isoformat(),'days':days,'messages':[]}
def q(sql,args=()):
 start=time.monotonic();c.set_progress_handler(lambda:int(time.monotonic()-start>12),10000);return [dict(r) for r in c.execute(sql,args)]
out['metrics']=q("select metric,ticker,day,dimensions,value from metric_buckets_current where metric in ('messages','bus_messages','processed_cases','api_token_cost') and day>='2026-10-19' and day<='2026-11-16'")
for ticker in tickers:
 for day in days:
  rows=q("select ticker,id,day,source_id,route,parent,json_extract(payload,'$.title.value') title,json_extract(payload,'$.url') url,json_extract(payload,'$.stream_published_at') stream_published_at,json_extract(payload,'$.source_published_at') source_published_at,json_extract(payload,'$.collected_at') collected_at,json_extract(payload,'$.revision') revision,json_extract(payload,'$.publication_time_basis') time_basis,json_extract(payload,'$.source.name') source_name,json_extract(payload,'$.case.status') case_status,json_extract(payload,'$.case.initial_route') initial_route,json_extract(payload,'$.pipeline.discovered_at') discovered_at from object_current indexed by current_day where kind='message' and ticker=? and day=?",(ticker,day));out['messages']+=rows
out['coverage']=q("select * from coverage where source='bus' and start_at>='2026-10-19' limit 100")
print(json.dumps(out,ensure_ascii=False));c.close()
