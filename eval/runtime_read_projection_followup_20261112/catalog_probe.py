import sqlite3,json,time
from datetime import datetime,timezone
r=sqlite3.connect('file:/data/read/serving-v4-20261018/v2.sqlite3?mode=ro',uri=True);r.row_factory=sqlite3.Row
out={'at':datetime.now(timezone.utc).isoformat()}
start=time.monotonic();r.set_progress_handler(lambda:int(time.monotonic()-start>3),1000)
try:out['BE_catalog']=dict(r.execute("SELECT count(*) n,sum(json_extract(payload,'$.lifecycle')='ACTIVE') active,sum(json_type(payload,'$.directions') IS NOT NULL) directions FROM object_current INDEXED BY current_identity WHERE kind='policy_catalog' AND ticker='BE'").fetchone())
except Exception as exc:out['BE_catalog']={'error':str(exc)}
out['ms']=1000*(time.monotonic()-start)
print(json.dumps(out))
