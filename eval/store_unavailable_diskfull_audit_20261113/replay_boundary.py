import sqlite3,json,hashlib,subprocess
from pathlib import Path
base=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data');read=base/'read/serving-v4-20261018/v2.sqlite3';r=sqlite3.connect(read.as_uri()+'?mode=ro',uri=True,timeout=.3);r.row_factory=sqlite3.Row;r.execute('PRAGMA query_only=ON')
out={'generation':r.execute('SELECT id FROM generation').fetchone()[0],'gaps':list(map(dict,r.execute('SELECT source,reason,count(*) n FROM gaps GROUP BY source,reason'))),'recent_runtime_gaps':list(map(dict,r.execute("SELECT event,reason,attempts,next_attempt_at FROM gaps WHERE source='runtime' ORDER BY CAST(event AS INTEGER) DESC LIMIT 8"))),'sources':{}}
for name,path in [('runtime','runtime/runtime.sqlite3'),('bus','bus/bus.sqlite3'),('initialization','initialization/control.sqlite3'),('research','research/research.sqlite3')]:
 s=sqlite3.connect((base/path).as_uri()+'?mode=ro',uri=True,timeout=.3);s.row_factory=sqlite3.Row;cp=r.execute('SELECT position FROM checkpoints WHERE source=?',(name,)).fetchone()[0]
 out['sources'][name]={'checkpoint':cp,'floor':s.execute('SELECT max(expired_floor) FROM v2_transport_gc_state').fetchone()[0],'epoch':s.execute('SELECT epoch FROM v2_source_state WHERE source=?',(name,)).fetchone()[0],'read_epoch':r.execute('SELECT epoch FROM source_epochs WHERE source=?',(name,)).fetchone()[0],'consumers':list(map(dict,s.execute('SELECT checkpoint,updated_at FROM v2_source_consumers')))}
 if name=='runtime':
  out['boundary']=[]
  for x in s.execute('SELECT o.seq,o.table_name,o.operation,r.payload FROM v2_source_outbox o JOIN v2_source_receipts r ON r.id=o.receipt_id WHERE o.seq BETWEEN ? AND ? ORDER BY o.seq',(cp-3,cp+3)):
   row=json.loads(x['payload']);v=row.get('payload_json') or row.get('payload') or row.get('data_json') or row
   if isinstance(v,str):v=json.loads(v)
   cid=v.get('case_id');item={'seq':x['seq'],'table':x['table_name'],'operation':x['operation'],'case_hash':hashlib.sha256(cid.encode()).hexdigest()[:12] if cid else None}
   if cid:
    item['read_native_case']=r.execute("SELECT count(*) FROM object_current WHERE kind='native:runtime_v2_cases' AND id=?",(cid,)).fetchone()[0];item['source_case']=s.execute('SELECT count(*) FROM runtime_v2_cases WHERE case_id=?',(cid,)).fetchone()[0]
   out['boundary'].append(item)
print(json.dumps(out))
