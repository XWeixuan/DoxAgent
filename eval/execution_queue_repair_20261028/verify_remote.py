import hashlib,json,sqlite3
from datetime import UTC,datetime
from pathlib import Path
import doxagent.trade_execution.worker as worker
import doxagent.trade_execution.recovery as recovery
import doxagent.trade_execution.repository as repository
import doxagent.trade_execution.executor as executor
D=sqlite3.connect('file:/data/runtime/runtime.sqlite3?mode=ro',uri=True,timeout=5)
D.row_factory=sqlite3.Row;D.execute('PRAGMA query_only=ON');D.execute('BEGIN')
out={'at':datetime.now(UTC).isoformat(),'sources':{m.__name__:hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in [worker,recovery,repository,executor]},'queue_age_limit':getattr(recovery,'ENTRY_MAX_QUEUE_SECONDS',None)}
out['jobs']=[json.loads(r[0]) for r in D.execute("SELECT payload FROM te_jobs WHERE state NOT IN ('DONE','FAILED')")]
out['open_lots']=[json.loads(r[0]) for r in D.execute("SELECT payload FROM te_lots WHERE CAST(json_extract(payload,'$.remaining_qty') AS REAL)>0")]
out['unsettled_attempts']=[json.loads(r[0]) for r in D.execute("SELECT payload FROM te_attempts WHERE state NOT IN ('SETTLED','NOT_SENT')")]
out['values']={ns:[{'key':r[0],'value':json.loads(r[1])} for r in D.execute('SELECT key,payload FROM runtime_values WHERE namespace=?',(ns,))] for ns in ['execution_scheduler','v2_workers','execution_connections','execution_recovery']}
out['event_head']=D.execute('SELECT COALESCE(MAX(id),0) FROM te_events').fetchone()[0]
r=D.execute("SELECT payload FROM runtime_values WHERE namespace='trade_execution' AND key='event_cursor'").fetchone()
out['event_cursor']=json.loads(r[0]) if r else 0
out['event_gap_count']=D.execute("SELECT COUNT(*) FROM runtime_values WHERE namespace='execution_event_gaps'").fetchone()[0]
out['controls']=[json.loads(r[0]) for r in D.execute("SELECT payload FROM te_jobs WHERE id IN ('trade:case_4ac4069a38ae4a19bc21100595a68e23:exit','trade:case_5e9a10c4358e4fe38167166ca80037d6:entry')")]
print(json.dumps(out))
