import os,json,sqlite3,time
from pathlib import Path
from datetime import datetime,UTC
from collections import Counter
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.query_budget import deadline
beg=time.monotonic();out={'at':datetime.now(UTC).isoformat()}
db=sqlite3.connect(Path(os.environ['DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH']).resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
db.set_progress_handler(lambda:time.monotonic()-beg>6,1000)
rows=db.execute("SELECT standard_message_id,ticker,json_extract(data_json,'$.publication_time_basis') basis FROM standard_messages ORDER BY rowid DESC LIMIT 200").fetchall()
out['source_bases']=dict(Counter(x[2] for x in rows));out['examples']=[]
store=ReadStore(os.environ['DOXAGENT_V2_READ_SQLITE_PATH']);token=deadline.set(time.monotonic()+4)
for sid,ticker,basis in rows:
 if basis=='EXACT' or len(out['examples'])>=5:continue
 with store.connect() as rd:
  native=rd.execute("SELECT json_extract(payload,'$.publication_time_basis') FROM object_current WHERE kind='native:standard_messages' AND ticker=? AND id=?",(ticker,sid)).fetchone()
  msg=rd.execute("SELECT json_extract(payload,'$.publication_time_basis'),json_extract(payload,'$.source_published_at') FROM object_current WHERE kind='message' AND ticker=? AND id=?",(ticker,sid)).fetchone()
 out['examples'].append({'id':sid,'ticker':ticker,'source_basis':basis,'read_native':tuple(native) if native else None,'read_message':tuple(msg) if msg else None})
with store.connect() as rd:
 out['indexed_newest_plan']=[tuple(r) for r in rd.execute("EXPLAIN QUERY PLAN SELECT id FROM object_current INDEXED BY current_source WHERE kind='native:live_deliveries' AND ticker=? AND source_id=? ORDER BY day DESC,sort_key DESC,id DESC LIMIT 20",('INTC','yahoo_finance_news'))]
out['read_bytes']=store.path.stat().st_size
deadline.reset(token);db.close();out['seconds']=time.monotonic()-beg
print(json.dumps(out))
