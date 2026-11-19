"""One bounded read-only identity comparison; no ACK or expiry writes."""
import json,sqlite3,time
from pathlib import Path
from datetime import datetime,UTC
root=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
def connect(path):
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.2)
    db.row_factory=sqlite3.Row
    deadline=time.monotonic()+1
    db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
    return db
out=dict(at=datetime.now(UTC).isoformat(),consumers=[])
with connect(root/'runtime/runtime.sqlite3') as db:
    consumers=db.execute("SELECT id,checkpoint,updated_at FROM v2_source_consumers WHERE id LIKE 'read:%' LIMIT 8").fetchall()
    for row in consumers:
        pins=[r[0] for r in db.execute('SELECT seq FROM v2_source_exception_pins WHERE consumer=? LIMIT 20001',(row['id'],))]
        out['consumers'].append(dict(row)|dict(pin_count=len(pins),limit_reached=len(pins)>20000,pins=pins))
with connect(root/'read/serving-v4-20261018/v2.sqlite3') as db:
    rows=[r[0] for r in db.execute("SELECT event FROM gaps WHERE source='runtime' AND event<>'' AND event NOT GLOB '*[^0-9]*' LIMIT 20001")]
    expected={int(x) for x in rows if int(x)>0}
    out['read_gap_count']=len(expected);out['read_limit_reached']=len(rows)>20000
for consumer in out['consumers']:
    current=set(consumer.pop('pins'))
    consumer.update(missing_count=len(expected-current),extra_count=len(current-expected),same_members=current==expected,comparison_is_cross_db_observation=True)
print(json.dumps(out))
