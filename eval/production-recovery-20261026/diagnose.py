"""Read-only production serving diagnostics, no credentials or article bodies."""
import json
import sqlite3
import time
from pathlib import Path

base = Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
alias = json.loads((base/'read/active.json').read_text())
path = Path(alias['path'])
path = base / path.relative_to('/data') if path.is_absolute() else base/'read'/path
db = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=2)
print('read_path', str(path), 'bytes', path.stat().st_size)
queries = {
 'health': 'SELECT * FROM source_health',
 'checkpoints': 'SELECT * FROM checkpoints',
 'highwater': 'SELECT max(seq) FROM commits',
 'views': 'SELECT * FROM views ORDER BY rowid DESC LIMIT 1',
 'kinds': "SELECT kind,count(*) FROM object_current WHERE kind IN ('message','native:source_definitions','native:ticker_source_bindings','native:poll_runs','native:poll_captures','poll_schedule') GROUP BY kind",
 'latest': "SELECT ticker,max(sort_key) FROM object_current WHERE kind='message' GROUP BY ticker",
 'native_latest': "SELECT kind,max(sort_key),count(*) FROM object_current WHERE kind IN ('native:stream_items','native:standard_messages','message') GROUP BY kind",
 'gaps': 'SELECT * FROM gaps LIMIT 12',
 'gap_counts': 'SELECT source,reason,count(*),min(event),max(event) FROM gaps GROUP BY source,reason',
 'recent_native': "SELECT ticker,json_extract(payload,'$.published_at'),id FROM object_current WHERE kind='native:stream_items' AND ticker='INTC' ORDER BY json_extract(payload,'$.published_at') DESC LIMIT 2",
 'schema': "SELECT name,sql FROM sqlite_master WHERE type='index' AND tbl_name='object_history'",
}
for name,sql in queries.items():
    start=time.monotonic()
    db.set_progress_handler(lambda: int(time.monotonic()-start>4),1000)
    try:
        rows=db.execute(sql).fetchall()
        if name=='views':
            rows=[{k:v for k,v in zip([x[0] for x in db.execute('SELECT * FROM views LIMIT 0').description],r) if k not in ('owner','payload','wire')} for r in rows]
        print(name,round(time.monotonic()-start,3),json.dumps(rows,default=str))
    except Exception as e: print(name,type(e).__name__,str(e))
db.close()
db=sqlite3.connect((base/'bus/bus.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=2)
for sql in ["PRAGMA table_info(stream_items)", "PRAGMA table_info(poll_captures)","SELECT ticker,max(published_at),count(*) FROM stream_items GROUP BY ticker", "SELECT * FROM sqlite_master WHERE type='trigger' AND tbl_name IN ('stream_items','standard_messages')"]:
    try: print('bus',json.dumps(db.execute(sql).fetchall(),default=str)[:2200])
    except Exception as e:print('bus_error',str(e))
