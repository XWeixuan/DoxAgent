import os,json,sqlite3,time
from pathlib import Path
from datetime import datetime,timezone,timedelta
os.nice(10);b=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data');out={'facts':{},'errors':{}};cut=(datetime.now(timezone.utc)-timedelta(days=7)).isoformat();cut2=(datetime.now(timezone.utc)-timedelta(hours=48)).isoformat()
queries={'research/research.sqlite3':{'sources':"SELECT count(*),sum(octet_length(captured_text)),sum(octet_length(source_metadata_json)) FROM codex_local_sources",'tasks':"SELECT count(*),sum(octet_length(payload)) FROM codex_document3_v21_tasks",'staged':"SELECT count(*),sum(octet_length(payload)),sum(octet_length(receipt)) FROM codex_document3_v21_staged_sets",'receipts':"SELECT count(*),sum(octet_length(payload)) FROM v2_source_receipts",'consumers':'SELECT * FROM v2_source_consumers','pins':'SELECT * FROM v2_source_pins'},'bus/acquisition.sqlite3':{'cycle_ages':"SELECT status,CASE WHEN dispatch_at<'"+cut+"' THEN 'older7d' WHEN dispatch_at<'"+cut2+"' THEN '2to7d' ELSE 'last48h' END,count(*) FROM cycles INDEXED BY cycles_schedule GROUP BY 1,2",'physical_count':'SELECT count(*) FROM physical_cycles','captures':'SELECT status,count(*) FROM captures GROUP BY status','hours':'SELECT count(*) FROM unit_hours','auto_vacuum':'PRAGMA auto_vacuum'}}
for rel,qs in queries.items():
 db=sqlite3.connect((b/rel).as_uri()+'?mode=ro',uri=True,timeout=1);db.execute('PRAGMA query_only=ON')
 for key,sql in qs.items():
  start=time.monotonic();db.set_progress_handler(lambda:time.monotonic()-start>30,1000)
  try:out['facts'][rel+':'+key]=db.execute(sql).fetchall()
  except sqlite3.Error as e:out['errors'][rel+':'+key]=str(e)
 db.close()
print(json.dumps(out))
