import json,sqlite3,sys,os
from datetime import datetime,UTC
from doxagent.v2_read.native_content import NativeContent
p=os.environ['DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH'];codec=NativeContent(p)
c=sqlite3.connect('file:'+p+'?mode=ro',uri=True,timeout=3);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on')
keys=json.loads(open('/tmp/latency-keys.json').read());out=[]
for key in keys:
 row=c.execute('select * from live_articles where article_key=?',(key,)).fetchone()
 if not row:continue
 d=dict(row);inp=codec.decode(codec.decode(json.loads(d['input_ref'])));result=codec.decode(codec.decode(json.loads(d['result_ref']))) if d.get('result_ref') else {}
 meta=result.get('metadata',{})
 out.append({**{k:v for k,v in d.items() if k not in ['input_ref','result_ref','validators_json']},
 'input':{k:inp.get(k) for k in ['title','published_at','source_item_key']},
 'input_metadata':{k:inp.get('metadata',{}).get(k) for k in ['google_handoff','pipeline','media_enrichment','v2_body_completion']},
 'result_metadata':{k:meta.get(k) for k in ['media_enrichment','pipeline','v2_body_completion']},
 'origins':[dict(r) for r in c.execute('select source_id,unit,first_seen_at from live_origins where article_key=? limit 10',(key,))],
 'deliveries':[dict(r) for r in c.execute('select ticker,source_id,state,first_discovered_at,created_at,consumable_at,due_at,last_error from live_deliveries where article_key=?',(key,))]})
print(json.dumps({'at':datetime.now(UTC).isoformat(),'samples':out},ensure_ascii=False))
