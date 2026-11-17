import sqlite3,json,time,subprocess
from pathlib import Path
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/runtime/runtime.sqlite3');d=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.2);d.row_factory=sqlite3.Row;out={}
key=d.execute("select template_key from runtime_v2_w3_threads where kind='TEMPLATE' and status='READY' limit 1").fetchone()[0]
queries={'current_template':("SELECT * FROM runtime_v2_w3_threads WHERE template_key=? AND status='READY'",(key,)), 'qualified_template':("SELECT * FROM runtime_v2_w3_threads WHERE kind='TEMPLATE' AND template_key=? AND status='READY'",(key,)), 'supplements':("SELECT d.payload FROM runtime_values d JOIN runtime_v2_cases c ON c.case_id=d.key WHERE d.namespace='dirty_case' AND c.ticker=? AND (c.trading_date<? OR json_extract(c.payload_json,'$.sweep_id') IS NOT NULL OR EXISTS(SELECT 1 FROM runtime_values o WHERE o.namespace='case_maintenance_owner' AND o.key=c.case_id))",('NBIS','2026-11-17'))}
for n,(q,a) in queries.items():out[n]=[dict(r) for r in d.execute('EXPLAIN QUERY PLAN '+q,a)]
# A limit on index keys, never open Case/attempt bodies.
for n,q in [('thread_keys','select rowid from runtime_v2_w3_threads order by rowid desc limit 10000'),('dirty_keys',"select key from runtime_values where namespace='dirty_case' limit 10000")]:
 st=time.monotonic();v=d.execute(q).fetchall();out[n]=dict(bounded_count=len(v),truncated=len(v)==10000,elapsed_ms=round((time.monotonic()-st)*1000,2))
print(json.dumps(out))
