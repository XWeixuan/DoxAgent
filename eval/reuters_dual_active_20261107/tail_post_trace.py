import json,sqlite3

from pathlib import Path
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/bus/acquisition.sqlite3')
db=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;db.execute('pragma query_only=on')
rows=db.execute("select unit,status,reason,due_at,dispatch_at,finished_at,interval_seconds from cycles where ticker='CEG' and source_id='reuters_site_search' and unit='TAIL:511db9bb3e2e55498275ef22' and julianday(dispatch_at)>=julianday('2026-11-06T21:33:23Z')  order by dispatch_at").fetchall()
print(json.dumps([dict(r) for r in rows],indent=2));db.close()
