import sqlite3,json,time
from datetime import datetime,timezone
s=sqlite3.connect('file:/data/runtime/runtime.sqlite3?mode=ro',uri=True);s.row_factory=sqlite3.Row;s.execute('PRAGMA query_only=ON')
r=sqlite3.connect('file:/data/read/serving-v4-20261018/v2.sqlite3?mode=ro',uri=True)
cp=r.execute("SELECT position FROM checkpoints WHERE source='runtime'").fetchone()[0];head=s.execute("SELECT head FROM v2_source_state WHERE source='runtime'").fetchone()[0]
out={'at':datetime.now(timezone.utc).isoformat(),'cp':cp,'head':head}
for label,lo,hi in [('pending',cp,head),('consumed',cp-5000,cp)]:
 out[label]=[dict(x) for x in s.execute("SELECT table_name,CASE WHEN table_name='runtime_values' THEN json_extract(entity_id,'$[0]') ELSE '' END namespace,count(*) n,min(seq) first_seq,max(seq) last_seq FROM v2_source_outbox WHERE seq>? AND seq<=? GROUP BY table_name,namespace",(lo,hi))]
out['next']=[dict(x) for x in s.execute('SELECT seq,table_name,entity_id,recorded_at FROM v2_source_outbox WHERE seq>? ORDER BY seq LIMIT 20',(cp,))]
print(json.dumps(out))
