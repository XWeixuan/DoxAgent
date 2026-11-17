import sqlite3,json
from pathlib import Path
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/runtime/runtime.sqlite3');d=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.1);d.row_factory=sqlite3.Row
rows=d.execute("select case_id,ticker,status,created_at,json_extract(payload_json,'$.error_code') error from runtime_v2_cases order by rowid desc limit 120").fetchall()
xs=[dict(r) for r in rows if r['created_at']>='2026-11-17T09:54:56'];print(json.dumps(dict(new_cases=xs,latest=[dict(r) for r in rows[:3]])))
print(json.dumps([dict(r) for r in d.execute("select effect_type,status,count(*) n from (select effect_type,status from runtime_v2_effects order by rowid desc limit 300) group by 1,2")]))
