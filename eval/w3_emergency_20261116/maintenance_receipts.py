import sqlite3,json
from pathlib import Path
from doxagent.settings import DoxAgentSettings
from doxagent.v2_read.native_content import NativeContent
p=Path(DoxAgentSettings().persistent_runtime_v2_sqlite_path);d=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=.2);d.row_factory=sqlite3.Row;content=NativeContent(p)
out=[]
for r in d.execute("select id,ticker,status,updated_at,receipt from runtime_tasks where kind='MAINTENANCE' and status in ('HELD','FAILED','PENDING','RUNNING') and updated_at>='2026-11-16T11:05' order by updated_at desc limit 30"):
 v=content.decode(json.loads(r['receipt']));frame=v.get('frame',{});run=frame.get('run_id','')
 if run.startswith(('runtime-maintain-7fb8f6db6503bbdbf2852652','runtime-maintain-06f74e913557e7750835b844')):
  out.append(dict(id=r['id'],ticker=r['ticker'],status=r['status'],at=r['updated_at'],run=run,receipt_keys=list(v),failure=v.get('last_phase_failure')))
print(json.dumps(out))
