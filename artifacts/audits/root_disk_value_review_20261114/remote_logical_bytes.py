import os,json,sqlite3,time,hashlib
from pathlib import Path
from contextlib import closing
from datetime import datetime,timezone
os.nice(10);b=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data');out={'at':datetime.now(timezone.utc).isoformat(),'facts':{},'errors':{}}
queries={
'runtime/runtime.sqlite3':{
 'cases':"SELECT status,technical_status,count(*) FROM runtime_v2_cases GROUP BY status,technical_status",
 'tasks':"SELECT kind,status,count(*),min(updated_at),max(updated_at) FROM runtime_tasks GROUP BY kind,status",
 'namespace_bytes':"SELECT namespace,count(*),sum(octet_length(payload)) FROM runtime_values GROUP BY namespace ORDER BY 3 DESC",
 'task_bytes':"SELECT kind,status,count(*),sum(octet_length(inputs)),sum(octet_length(receipt)) FROM runtime_tasks GROUP BY kind,status"},
'research/research.sqlite3':{
 'records_bytes':"SELECT record_type,count(*),sum(octet_length(payload_json)) FROM codex_runtime_records GROUP BY record_type ORDER BY 3 DESC",
 'd3_bytes':"SELECT count(*),sum(octet_length(payload)) FROM codex_document3_v21_runs",
 'd3_biggest':"SELECT run_id,octet_length(payload) FROM codex_document3_v21_runs ORDER BY 2 DESC LIMIT 20"},
'bus/acquisition.sqlite3':{
 'cycles_status':"SELECT status,count(*),min(dispatch_at),max(dispatch_at),sum(octet_length(data_json)) FROM cycles GROUP BY status",
 'capture_status':"SELECT status,count(*),sum(octet_length(payload)) FROM captures GROUP BY status",
 'physical_bytes':"SELECT count(*),sum(octet_length(requests_json)) FROM physical_cycles"}}
for rel,items in queries.items():
 with closing(sqlite3.connect((b/rel).as_uri()+'?mode=ro',uri=True,timeout=.2)) as d:
  d.execute('PRAGMA query_only=ON')
  for key,sql in items.items():
   started=time.monotonic();d.set_progress_handler(lambda:time.monotonic()-started>12,1000)
   try:out['facts'][rel+':'+key]=d.execute(sql).fetchall()
   except sqlite3.Error as e:out['errors'][rel+':'+key]=str(e)
print(json.dumps(out))
