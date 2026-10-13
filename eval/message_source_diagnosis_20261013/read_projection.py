"""Compare durable bus polling with current read model, no projector writes."""
import json
import sqlite3
from datetime import UTC, datetime

read = sqlite3.connect('file:/data/read/v2.sqlite3?mode=ro',uri=True,timeout=2)
bus = sqlite3.connect('file:/data/bus/bus.sqlite3?mode=ro',uri=True,timeout=2)
out = {'at':datetime.now(UTC).isoformat(),'checkpoints':read.execute('select * from checkpoints').fetchall(),'bus_head':bus.execute('select source,head,retained_floor from v2_source_state').fetchall(),'polls':[]}
for ticker, identity, payload in read.execute("select ticker,id,payload from object_current where kind='native:poll_states' and ticker='MU'"):
    v = json.loads(payload)
    out['polls'].append({'ticker':ticker,'id':identity,**{k:v.get(k) for k in ('status','last_attempt_at','last_success_at','last_error_code','updated_at')}})
print(json.dumps(out))
