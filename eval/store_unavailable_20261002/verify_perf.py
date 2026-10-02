import json,time,statistics
from doxagent.v2_read.repository import ReadStore
from doxagent.api_v2.bus import aggregates_many
s=ReadStore('/data/read/v2.sqlite3')
with s.connect() as db:
 v=json.loads(db.execute("SELECT payload FROM views WHERE json_extract(payload,'$.wire.page')='OVERVIEW' ORDER BY expires_at DESC LIMIT 1").fetchone()[0])
samples=[]
for i in range(5):
 t=time.monotonic(); aggregates_many(s,v['tickers'],v['seq']); samples.append((time.monotonic()-t)*1000)
print(json.dumps({'samples_ms':samples,'max_ms':max(samples),'frozen_seq':v['seq']}))
