"""Read-only timings of the synchronous planning reads used by the live Bus."""
import json
import sqlite3
import time
from pathlib import Path
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.v2_read.native_content import NativeContent

dbpath=Path('/data/runtime/runtime.sqlite3')
db=sqlite3.connect(dbpath.as_uri()+'?mode=ro',uri=True,timeout=1)
print(json.dumps({'runtime_task_states':[list(r) for r in db.execute('SELECT kind,status,count(*) FROM runtime_tasks GROUP BY kind,status')],
                  'pause_plan':[list(r) for r in db.execute("EXPLAIN QUERY PLAN SELECT payload FROM runtime_values WHERE namespace='pause' AND key='MU'")]}),flush=True)
journal=RuntimeJournal(dbpath,initialize=False,busy_timeout_seconds=.05)
for name,call in [('pause_reads',lambda:[journal.get('pause',ticker) for ticker in ['MU','BE','INTC','RKLB','COHR','SNOW','NBIS','CEG','ONDS']]),
                  ('all_sweeps',lambda:journal.tasks(kind='SWEEP')),
                  ('active_sweeps',lambda:journal.tasks(kind='SWEEP',active_only=True))]:
    began=time.monotonic()
    result=call()
    print(json.dumps({'operation':name,'seconds':time.monotonic()-began,'rows':len(result)}),flush=True)
buspath=Path('/data/bus/bus.sqlite3')
conn=sqlite3.connect(buspath.as_uri()+'?mode=ro',uri=True,timeout=1)
conn.row_factory=NativeContent(buspath).row
for table in ('source_definitions','ticker_source_bindings','poll_states'):
    began=time.monotonic()
    result=conn.execute('SELECT data_json FROM '+table).fetchall()
    print(json.dumps({'operation':'bus_read_'+table,'seconds':time.monotonic()-began,'rows':len(result)}),flush=True)
