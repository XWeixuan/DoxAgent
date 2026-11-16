import sqlite3,json
from pathlib import Path
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/workspaces/worker-jobs.sqlite3');db=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.2);db.row_factory=sqlite3.Row
print(json.dumps([dict(r) for r in db.execute("select id,status,json_extract(payload,'$.run_id') run,json_extract(payload,'$.attempt_id') attempt,json_extract(payload,'$.created_at') created,json_extract(payload,'$.started_at') started from jobs where status in ('running','queued') limit 15")]))
