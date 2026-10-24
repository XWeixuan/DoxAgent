import json,sqlite3
from datetime import datetime,UTC
from doxagent.v2_read.native_content import NativeContent
p='/data/runtime/runtime.sqlite3';d=sqlite3.connect('file:'+p+'?mode=ro',uri=True,timeout=10);d.row_factory=sqlite3.Row
d.execute('PRAGMA query_only=ON');d.execute('BEGIN');codec=NativeContent(p)
def load(raw):return codec.decode(json.loads(raw))
start='2026-10-13T16:00:00+00:00';end='2026-10-22T06:41:42.919330+00:00'
out={'captured_at':datetime.now(UTC).isoformat(),'window_end':end,'schemas':{},'trade_records':[],'w3_decisions':[],'namespaces':[]}
for t in ['runtime_v2_trade_records','runtime_v2_w3_cases']:
 out['schemas'][t]=[dict(r) for r in d.execute('PRAGMA table_info('+t+')')]
out['namespaces']=[dict(r) for r in d.execute("SELECT namespace,count(*) n FROM runtime_values WHERE namespace LIKE '%trade%' GROUP BY namespace")]
for r in d.execute('SELECT * FROM runtime_v2_trade_records'):
 raw=dict(r);payload=load(raw.get('payload_json') or raw.get('payload') or '{}')
 stamp=payload.get('created_at') or raw.get('created_at') or ''
 if start<=stamp<=end:
  out['trade_records'].append({'row':{k:v for k,v in raw.items() if k not in ('payload','payload_json')},'payload':{k:v for k,v in payload.items() if k not in ('source','reason','trade')}})
for r in d.execute('SELECT payload_json FROM runtime_v2_w3_cases'):
 w=load(r[0]);decision=w.get('trade_decision') or {};stamp=w.get('created_at','')
 if start<=stamp<=end and decision.get('decision') not in (None,'NO_TRADE','RESEARCH_ONLY'):
  out['w3_decisions'].append({'case_id':w['case_id'],'w3_case_id':w['w3_case_id'],'ticker':w['ticker'],'created_at':stamp,'decision':decision.get('decision'),'trade_decision':decision,'status':w['status']})
print(json.dumps(out,ensure_ascii=False))
