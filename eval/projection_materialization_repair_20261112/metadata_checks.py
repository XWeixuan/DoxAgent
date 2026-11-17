import sqlite3,json,time,hashlib
from datetime import datetime,timezone
r=sqlite3.connect('file:/data/read/serving-v4-20261018/v2.sqlite3?mode=ro',uri=True);r.row_factory=sqlite3.Row
s=sqlite3.connect('file:/data/runtime/runtime.sqlite3?mode=ro',uri=True);s.row_factory=sqlite3.Row
cp=r.execute("SELECT position FROM checkpoints WHERE source='runtime'").fetchone()[0]
consumer='read:'+hashlib.sha256('/data/read/serving-v4-20261018/v2.sqlite3'.encode()).hexdigest()[:24]
out={'at':datetime.now(timezone.utc).isoformat(),'checkpoint':cp,'ack':dict(s.execute('SELECT * FROM v2_source_consumers WHERE id=?',(consumer,)).fetchone()),'highwater':r.execute("SELECT value FROM read_meta WHERE key='highwater'").fetchone()[0],'policy_counts':{}}
for ticker in ['INTC','MU','BE','COHR','IONQ','RKLB']:
 t=time.monotonic();r.set_progress_handler(lambda:int(time.monotonic()-t>1),1000)
 try:out['policy_counts'][ticker]={'n':r.execute("SELECT count(*) FROM object_current INDEXED BY current_identity WHERE kind='policy_catalog' AND ticker=?",(ticker,)).fetchone()[0],'ms':1000*(time.monotonic()-t)}
 except Exception as exc:out['policy_counts'][ticker]={'error':str(exc)}
r.set_progress_handler(None,0)
out['plans']=[list(x) for x in r.execute("EXPLAIN QUERY PLAN SELECT id,payload FROM object_current WHERE kind='policy_catalog' AND ticker='INTC' AND valid_to IS NULL")]
out['cursors']=[dict(x) for x in s.execute("SELECT * FROM v2_transport_gc_state WHERE kind LIKE 'serving:%'")]
print(json.dumps(out))
