import sqlite3,time,json
from doxagent.v2_read.repository import ReadStore
s=ReadStore('/data/read/v2.sqlite3');db=sqlite3.connect(s.path.as_uri()+'?mode=ro',uri=True,timeout=1)
db.execute('PRAGMA query_only=ON');start=time.perf_counter();db.set_progress_handler(lambda:int(time.perf_counter()-start>3),1000)
for table,where in [('objects',"kind in ('policy_catalog','policy_change')"),('contributions',"metric in ('policy_ar_hits','policy_executed')")]:
 print(table, db.execute('SELECT count(*) FROM '+table+' WHERE '+where+" AND ticker in ('BE','MU','INTC')").fetchone()[0], flush=True)
