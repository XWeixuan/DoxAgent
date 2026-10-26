"""Read Site phase telemetry only; exclude URLs, credentials and response bodies."""
import json
import sqlite3
import sys
import time
from datetime import UTC,datetime

db=sqlite3.connect('file:/site-data/registry/site_strategy.sqlite3?mode=ro',uri=True,timeout=2)
db.row_factory=sqlite3.Row
deadline=time.monotonic()+12
db.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
rows=[]
for row in db.execute("SELECT operation_id,site_id,occurred_at,payload_json FROM access_events WHERE category='ACCESS_RESULT' AND julianday(occurred_at)>=julianday(?) ORDER BY julianday(occurred_at) LIMIT 50000",(sys.argv[1],)):
    value=json.loads(row['payload_json'])
    rows.append({**{k:row[k] for k in ('operation_id','site_id','occurred_at')},
                 **{k:value.get(k) for k in ('disposition','reason','reason_code','identity_id','purpose','network_ms','queue_wait_ms','stage_timings','work_class')}})
print(json.dumps({'since':sys.argv[1],'at':datetime.now(UTC).isoformat(),'events':rows,'censored':len(rows)==50000}))
