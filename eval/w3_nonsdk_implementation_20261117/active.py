import sqlite3,json
from pathlib import Path
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/workspaces/worker-jobs.sqlite3')
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.1) as d:
 d.row_factory=sqlite3.Row;print(json.dumps([dict(r) for r in d.execute("select status,json_extract(payload,'$.run_id') run,json_extract(request,'$.task_phase') phase,json_extract(payload,'$.created_at') created,json_extract(payload,'$.execution_phase') step from jobs where status in ('running','queued')")]))
