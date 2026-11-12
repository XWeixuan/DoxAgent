import sqlite3,json
from pathlib import Path
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3');d=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)
rows=d.execute("select json_extract(data_json,'$.task_id'),json_extract(data_json,'$.status'),created_at from datadome_solver_jobs where json_extract(data_json,'$.task_id') is not null and identity_id in ('digitimes-de-1','reuters-backup') order by created_at desc").fetchall()
print(json.dumps({'paid_tasks_retained':len(rows),'latest_paid_task':rows[0],'jobs_after_audit_start':sum(r[2]>'2026-11-12T22:40:00' for r in rows),'uncertain_or_pending':d.execute("select count(*) from datadome_solver_jobs where json_extract(data_json,'$.status') in ('PENDING','SUBMITTING','APPLYING')").fetchone()[0]},indent=2))
