import json,sqlite3
from pathlib import Path
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3')
db=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
rows=db.execute("select occurred_at,payload_json from access_events where site_id='reuters' and occurred_at>='2026-11-06T21:42:32' and json_extract(payload_json,'$.purpose')='BODY' order by occurred_at").fetchall()
print(json.dumps([{'at':r['occurred_at'],**{k:v for k,v in json.loads(r['payload_json']).items() if k in ('effective_scope_id','reason','stage_timings','work_class','network_ms','queue_wait_ms')}} for r in rows],indent=2))
