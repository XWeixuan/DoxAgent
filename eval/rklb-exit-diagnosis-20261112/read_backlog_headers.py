import os, sqlite3, json, time
from datetime import datetime, timezone
from doxagent.v2_read.repository import ReadStore
d = sqlite3.connect('file:' + str(ReadStore(os.environ['DOXAGENT_V2_READ_SQLITE_PATH']).path) + '?mode=ro', uri=True)
d.row_factory = sqlite3.Row
d.execute('pragma query_only=ON')
cp = d.execute("select position from checkpoints where source='runtime'").fetchone()[0]
s = sqlite3.connect('file:/data/runtime/runtime.sqlite3?mode=ro', uri=True)
s.row_factory = sqlite3.Row
s.execute('pragma query_only=ON')
s.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
deadline = time.monotonic() + 8
head = s.execute("select head from v2_source_state where source='runtime'").fetchone()[0]
out = {'at': datetime.now(timezone.utc).isoformat(), 'checkpoint': cp, 'head': head}
out['next_header'] = dict(s.execute('select seq,table_name,recorded_at from v2_source_outbox where seq>? order by seq limit 1', (cp,)).fetchone())
out['pending_hot_headers'] = [dict(r) for r in s.execute('select table_name,count(*) as n,min(recorded_at) as first_at,max(recorded_at) as last_at from (select table_name,recorded_at from v2_source_outbox where seq>? and seq<=? order by seq limit 12000) group by table_name order by n desc', (cp, head))]
print(json.dumps(out))
