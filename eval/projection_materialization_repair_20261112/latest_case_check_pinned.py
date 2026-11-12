import json,sqlite3,os,hashlib
from datetime import datetime,timezone
from doxagent.v2_read.repository import ReadStore
store=ReadStore('/data/read/serving-v4-20261018/v2.sqlite3')
d=sqlite3.connect('file:'+str(store.path)+'?mode=ro',uri=True);d.row_factory=sqlite3.Row
d.execute('BEGIN')
cp=d.execute("SELECT position FROM checkpoints WHERE source='runtime'").fetchone()[0]
read_seq=d.execute("SELECT value FROM read_meta WHERE key='highwater'").fetchone()[0]
s=sqlite3.connect('file:/data/runtime/runtime.sqlite3?mode=ro',uri=True);s.row_factory=sqlite3.Row
head=s.execute("SELECT head FROM v2_source_state WHERE source='runtime'").fetchone()[0]
rows=s.execute("SELECT o.seq,r.payload FROM v2_source_outbox o JOIN v2_source_receipts r ON r.id=o.receipt_id WHERE o.seq>? AND o.seq<=? AND o.table_name='runtime_v2_cases' ORDER BY o.seq DESC LIMIT 300",(min(head,cp)-10000,min(head,cp))).fetchall()
from doxagent.v2_read.native_content import NativeContent
codec=NativeContent('/data/runtime/runtime.sqlite3')
seen=set();mismatches=[];n=0
for row in rows:
 envelope=json.loads(row['payload']);value=envelope.get('payload_json');value=json.loads(value) if isinstance(value,str) else value
 identity=value['case_id']
 if identity in seen:continue
 seen.add(identity);ticker=codec.decode(value['source'])['snapshot']['ticker'];current=store.get('case',ticker,identity,seq=int(read_seq))
 if not current or current.get('status')!=value.get('status') or current.get('w3_operations_status')!=value.get('w3_operations_status'):
  mismatches.append({'case_id':identity,'source_seq':row['seq'],'source_status':value.get('status'),'read_status':(current or {}).get('status'),'source_ops':value.get('w3_operations_status'),'read_ops':(current or {}).get('w3_operations_status')})
 n+=1
 if n>=50:break
print(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'checkpoint':cp,'head':head,'read_seq':int(read_seq),'comparison':'source prefix through checkpoint vs pinned Read sequence','checked_cases':n,'mismatches':mismatches,'gaps':[dict(r) for r in d.execute('SELECT source,reason,count(*) n FROM gaps GROUP BY source,reason')]}))
