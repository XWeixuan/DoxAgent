"""Recent bounded source-CDC counts, not full payload or historical scans."""
import json,sqlite3,time,collections
from pathlib import Path
from datetime import datetime,UTC
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/runtime/runtime.sqlite3')
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.2) as db:
    deadline=time.monotonic()+1
    db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
    rows=db.execute("SELECT o.seq,o.table_name,o.recorded_at,json_extract(r.payload,'$.namespace') "
                    "FROM (SELECT * FROM v2_source_outbox ORDER BY seq DESC LIMIT 512) o "
                    "JOIN v2_source_receipts r ON r.id=o.receipt_id").fetchall()
out=dict(at=datetime.now(UTC).isoformat(),rows=len(rows),oldest=min((r[2] for r in rows),default=None),newest=max((r[2] for r in rows),default=None),counts=dict(collections.Counter(r[1]+':'+str(r[3]) for r in rows)))
print(json.dumps(out))
