import json,sqlite3
from pathlib import Path
from datetime import datetime,UTC
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/runtime/runtime.sqlite3');d=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.2);d.row_factory=sqlite3.Row
out=dict(at=datetime.now(UTC).isoformat())
out['new_stage_counts']=[dict(r) for r in d.execute("select phase,status,count(*) n from runtime_v2_w3_stages where accepted_at>='2026-11-16T11:06' group by 1,2")]
out['committed_trade']=[dict(r) for r in d.execute("select case_id,json_extract(result_json,'$.decision') decision,accepted_at,committed_at from runtime_v2_w3_stages where accepted_at>='2026-11-16T11:06' and phase='trade_decision' and status='COMMITTED' order by committed_at desc limit 6")]
out['post_release_errors']=[dict(r) for r in d.execute("select phase,error,count(*) n from runtime_v2_w3_stages where accepted_at>='2026-11-16T11:06' and error is not null group by 1,2")]
print(json.dumps(out))
