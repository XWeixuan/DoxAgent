import sqlite3,json
from pathlib import Path
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/runtime/runtime.sqlite3');d=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.2);d.row_factory=sqlite3.Row
out={}
out['failed_preparations']=[dict(r) for r in d.execute("select count(*) n,sum(worker_job_id is not null) with_job,min(accepted_at) first,max(accepted_at) last from runtime_v2_w3_stages where accepted_at>='2026-11-16' and phase='trade_decision' and status='FAILED' and error like '%public Template has not configured this profile%'")]
out['active_effects']=[dict(r) for r in d.execute("select effect_type,status,count(*) n from runtime_v2_effects where status in ('PENDING','PENDING_RETRY','RUNNING') group by 1,2")]
out['background']=[dict(r) for r in d.execute("select kind,status,count(*) n from runtime_tasks where kind!='CASE' and status in ('PENDING','RUNNING','HELD') group by 1,2")]
print(json.dumps(out))
