import sqlite3,json,time,hashlib
from datetime import datetime,timezone
from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.projector import native
source=SourceOutbox('/data/runtime/runtime.sqlite3','runtime')
r=sqlite3.connect('file:/data/read/serving-v4-20261018/v2.sqlite3?mode=ro',uri=True);r.row_factory=sqlite3.Row
cp=r.execute("SELECT position FROM checkpoints WHERE source='runtime'").fetchone()[0]
with source.connection() as db:
 coords=[x[0] for x in db.execute("SELECT seq FROM v2_source_outbox WHERE seq>? AND seq<=? AND table_name='runtime_values' AND json_extract(entity_id,'$[0]')='reference_deliveries' ORDER BY seq LIMIT 12",(cp-600,cp+300))]
out={'at':datetime.now(timezone.utc).isoformat(),'samples':[],'catalog_counts':{}}
with source.event_reader(serving=True) as load:
 for seq in coords:
  e=load(seq);v=native(e);item={k:v.get(k) for k in ('ticker','delta_id','status','prepared_at','semantic_day','root','before_root')};item['seq']=seq;item['sides']={}
  for side in ('before','after'):
   cap=v[side];raw=json.dumps(cap,sort_keys=True,ensure_ascii=False).encode();item['sides'][side]={'version':cap['version'],'events':len(cap['events']),'facts':sum(len(ev['facts']) for ev in cap['events']),'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
  out['samples'].append(item)
for kind in ('policy_catalog','library','reference_input','reference_day_chain','reference_event','fact','event_detail'):
 start=time.monotonic();r.set_progress_handler(lambda:int(time.monotonic()-start>2),1000)
 try:out['catalog_counts'][kind]=[dict(x) for x in r.execute('SELECT ticker,count(*) n FROM object_current INDEXED BY current_identity WHERE kind=? GROUP BY ticker',(kind,))]
 except Exception as exc:out['catalog_counts'][kind]={'error':str(exc)}
print(json.dumps(out))
