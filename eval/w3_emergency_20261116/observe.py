import json,sqlite3
from pathlib import Path
from datetime import datetime,UTC
root=Path('/var/lib/doxagent/releases/w3-emergency-20261116');data=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
out=dict(at=datetime.now(UTC).isoformat())
with sqlite3.connect((data/'runtime/runtime.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.2) as d:
 d.row_factory=sqlite3.Row
 out['new_stages']=[dict(r) for r in d.execute("select case_id,phase,status,worker_job_id,substr(error,1,700) error,accepted_at,committed_at from runtime_v2_w3_stages where accepted_at>='2026-11-16T11:06' order by accepted_at desc limit 15")]
 out['new_failures']=[dict(r) for r in d.execute("select phase,status,error,count(*) n from runtime_v2_w3_stages where accepted_at>='2026-11-16T11:06' and error is not null group by 1,2,3")]
with sqlite3.connect((data/'workspaces/worker-jobs.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.2) as d:
 d.row_factory=sqlite3.Row
 out['jobs']=[dict(r) for r in d.execute("select id,status,json_extract(request,'$.task_phase') phase,json_extract(payload,'$.run_id') run,json_extract(payload,'$.error_code') error,json_extract(payload,'$.error_message') detail,json_extract(payload,'$.created_at') created,json_extract(request,'$.model') model,json_extract(request,'$.effort') effort from jobs where json_extract(payload,'$.created_at')>='2026-11-16T11:06' order by sequence desc limit 10")]
out['memory']={k:v.strip() for k,v in (x.split(':',1) for x in Path('/proc/meminfo').read_text().splitlines()) if k in ('MemAvailable','SwapFree')}
out['swapout']=int(dict(x.split() for x in Path('/proc/vmstat').read_text().splitlines())['pswpout'])
with (root/'observations.jsonl').open('a') as f:f.write(json.dumps(out)+'\n')
print(json.dumps(out))
