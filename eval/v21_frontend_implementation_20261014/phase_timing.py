import importlib.util,json,time
from doxagent.v2_read.repository import ReadStore
spec=importlib.util.spec_from_file_location('q','/tmp/ui-impl/policy_queries.py');q=importlib.util.module_from_spec(spec);spec.loader.exec_module(q)
s=ReadStore('/tmp/ui-impl/isolated-final.sqlite3')
with s.connect() as db:
 seq=s.highwater(db)
 print('payloads',tuple(db.execute("SELECT count(*),avg(length(payload)),max(length(payload)) FROM object_current WHERE kind='policy_catalog' AND ticker='INTC'").fetchone()),flush=True)
 ctes,args=q._sets('INTC',seq,'ALL',None,db=db,include_payload=False)
 queries={'state':"SELECT sum(ACTIVE OR ACTIVE_UNKNOWN),sum(long_direction),sum(short_direction),sum(ACTIVE_UNKNOWN) FROM catalog",'change':"SELECT sum(ADDED),sum(MODIFIED),sum(RETIRED) FROM change_flags",'trade':"SELECT sum(HIT),sum(EXECUTED) FROM trade_flags"}
 for name,sql in queries.items():
  for _ in range(3):
   t=time.perf_counter();c=time.process_time();row=db.execute(ctes+sql,args).fetchone();print(name,round((time.perf_counter()-t)*1000,2),round((time.process_time()-c)*1000,2),tuple(row),flush=True)
