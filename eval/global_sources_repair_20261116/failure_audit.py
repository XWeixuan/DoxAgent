import json,sqlite3,time,re
from datetime import datetime,UTC,timedelta
from urllib.parse import urlsplit,urlunsplit

def db(p):
 c=sqlite3.connect('file:'+p+'?mode=ro',uri=True,timeout=3);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on')
 start=time.monotonic();c.set_progress_handler(lambda:time.monotonic()-start>45,10000);return c
def safe(text):
 return re.sub(r'https?://[^\s;\x22\x27]+',lambda m:urlunsplit((*urlsplit(m[0])[:3],'','')),str(text))[:800]
a=db('/data/bus/acquisition.sqlite3');since=(datetime.now(UTC)-timedelta(hours=2)).isoformat();out={'at':datetime.now(UTC).isoformat(),'since':since,'sources':{}}
for sid in ['yahoo_finance_news','sec_edgar']:
 units=[dict(r) for r in a.execute('select * from units where source_id=?',(sid,))];physical={};examples=[]
 for u in units:
  rows=a.execute('select status,reason,dispatch_at,finished_at,data_json from cycles indexed by cycles_unit_time where binding_id=? and unit=? and dispatch_at>=? order by dispatch_at desc',(u['binding_id'],u['unit'],since)).fetchall()
  for r in rows:
   p=json.loads(r['data_json']);rid=p.get('physical_run_id')
   if not rid or rid in physical:continue
   physical[rid]=dict(r)
   if r['reason'] and len(examples)<15:
    raw=a.execute('select requests_json from physical_cycles where run_id=?',(rid,)).fetchone();requests=json.loads(raw[0]) if raw else []
    examples.append({'ticker':u['ticker'],'unit':u['unit'],'run_id':rid,'dispatch_at':r['dispatch_at'],'finished_at':r['finished_at'],'reason':r['reason'],'message':safe(p.get('terminal_error_message')),
                     'timings':p.get('http_timing'),'requests':[{k:e.get(k) for k in ['transport','status_code','reason','request_started_at','response_received_at']}|{'url':safe(e.get('url') or e.get('endpoint'))} for e in requests]})
 from collections import Counter
 out['sources'][sid]={'states':[{'ticker':u['ticker'],'unit':u['unit'],'head':json.loads(u['head_json'])} for u in units],
    'physical_counts':dict(Counter(str(r['status'])+':'+str(r['reason']) for r in physical.values())),
    'examples':examples}
print(json.dumps(out))
