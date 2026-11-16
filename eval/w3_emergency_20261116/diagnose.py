import sqlite3,json
from pathlib import Path
from doxagent.settings import DoxAgentSettings
p=Path(DoxAgentSettings().persistent_runtime_v2_sqlite_path)
db=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=.2);db.row_factory=sqlite3.Row
out={}
out['stages']=[dict(r) for r in db.execute("select phase,status,substr(error,1,800) error,count(*) n,min(accepted_at) first,max(accepted_at) last from runtime_v2_w3_stages where accepted_at>='2026-11-16' group by 1,2,3 order by n desc limit 20")]
out['effects']=[dict(r) for r in db.execute("select effect_type,status,count(*) n from runtime_v2_effects where updated_at>='2026-11-16' group by 1,2 order by n desc limit 20")]
out['failed']=[dict(r) for r in db.execute("select effect_id,case_id,status,substr(payload_json,1,1300) payload from runtime_v2_effects where effect_type='route_to_w3' and status='FAILED' order by updated_at desc limit 2")]
print(json.dumps(out))
