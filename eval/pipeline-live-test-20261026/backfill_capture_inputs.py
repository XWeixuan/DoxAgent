"""One-time bounded input-hash migration; preserves captures, leases and all configs."""
import json
import os
import time

from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.settings import DoxAgentSettings

if hasattr(os,'nice'):
    os.nice(10)
repo=MessageBusV2Repository(DoxAgentSettings().message_bus_v2_sqlite_path)
start=time.monotonic();total=0
while time.monotonic()-start<180:
    count=repo.polling.backfill_input_keys(limit=16)
    total+=count
    if not count:break
    time.sleep(.025)
with repo._connect() as db:
    missing=db.execute("SELECT count(*) FROM poll_captures p LEFT JOIN poll_capture_inputs k ON k.capture_id=p.capture_id WHERE p.status IN ('PENDING','RUNNING') AND k.capture_id IS NULL").fetchone()[0]
print(json.dumps({'indexed':total,'remaining':missing,'elapsed_seconds':round(time.monotonic()-start,3)}))
