"""Five low-priority scalar reads per mode; no model, repository or writer."""
import json,os,sqlite3,time
from pathlib import Path
from datetime import datetime,UTC
os.nice(10)
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/runtime/runtime.sqlite3')
out=dict(at=datetime.now(UTC).isoformat(),fresh=[],same_connection=[])
def query(db):
    db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2_ticker_control'").fetchone()
    db.execute("SELECT ticker FROM v2_ticker_control WHERE ticker='MU'").fetchone()
for _ in range(5):
    cpu=time.process_time();wall=time.monotonic()
    with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.1) as db:query(db)
    db.close()
    out['fresh'].append(dict(wall_ms=(time.monotonic()-wall)*1000,cpu_ms=(time.process_time()-cpu)*1000))
    time.sleep(.1)
db=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.1)
query(db)
for _ in range(5):
    cpu=time.process_time();wall=time.monotonic();query(db)
    out['same_connection'].append(dict(wall_ms=(time.monotonic()-wall)*1000,cpu_ms=(time.process_time()-cpu)*1000))
    time.sleep(.1)
db.close();print(json.dumps(out))
