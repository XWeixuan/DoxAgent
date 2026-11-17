import sqlite3,json
from pathlib import Path
root=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data');cid='case_830ef87bea7447208665129fab244736'
with sqlite3.connect((root/'runtime/runtime.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.1) as d:
 print('stages',json.dumps(d.execute("select phase,length(input_refs),length(result_json),json_extract(input_refs,'$.record_id') from runtime_v2_w3_stages where case_id=?",(cid,)).fetchall()))
 runs=[r[0] for r in d.execute("select workspace from runtime_v2_w3_threads where record_id in (select json_extract(input_refs,'$.record_id') from runtime_v2_w3_stages where case_id=?)",(cid,))]
with sqlite3.connect((root/'workspaces/worker-jobs.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.1) as d:
 out=[]
 for run in runs:
  slot=run.removeprefix('w3-ready-')
  row=d.execute("select created_at,bound_at,ready_at,execution_lane from w3_ready_slots where id=?",(slot,)).fetchone()
  op=d.execute("select created_at,updated_at,fork_dispatched_at,startup_receipt,step,startup_attempts from w3_thread_operations where operation_key=?",('w3-slot-fork:'+slot,)).fetchone()
  out.append(dict(slot=slot,binding=row,operation=op))
 print('boot',json.dumps(out))
