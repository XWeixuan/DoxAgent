import json,sqlite3,time,collections,statistics
from pathlib import Path
from datetime import datetime
from doxagent.settings import DoxAgentSettings
from doxagent.semantic_clock import semantic_day
from doxagent.v2_read.usage import accounting
s=DoxAgentSettings(); db=sqlite3.connect(Path(s.persistent_runtime_v2_sqlite_path).resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
db.row_factory=sqlite3.Row;db.execute('pragma query_only=on')
deadline=time.monotonic()+150
db.set_progress_handler(lambda:int(time.monotonic()>deadline),50000)
start='2026-11-07';end='2026-11-17'
cs={}
q="""select case_id,ticker,trading_date,status,technical_status,created_at,
json_extract(payload_json,'$.runtime_mode') mode,json_extract(payload_json,'$.execution_bundle_id') bundle,
json_extract(payload_json,'$.source.snapshot.title') title,
json_extract(payload_json,'$.source.logical_message_id') logical_id,
json_extract(payload_json,'$.source.update_kind') update_kind,
json_extract(payload_json,'$.w1_round1.event_ids') event_ids,
json_extract(payload_json,'$.w2_round1.candidate_policy_ids') policy_ids,
json_extract(payload_json,'$.error_code') error_code
from runtime_v2_cases where trading_date>=? and trading_date<?"""
for row in db.execute(q,(start,end)): cs[row['case_id']]=dict(row)
turns=[]
for c in cs.values():
 for row in db.execute("select json_remove(payload_json,'$.output','$.raw_output') data from runtime_v2_turns where case_id=? and lane in ('W1','W2')",(c['case_id'],)):
  t=json.loads(row['data']);t.update(ticker=c['ticker'],day=c['trading_date'],case_mode=c['mode'],bundle=c['bundle'])
  try:t['call_day']=str(semantic_day(datetime.fromisoformat(t.get('started_at') or t['created_at'])))
  except Exception:t['call_day']=None
  n,m=accounting(t['model'],t['provider'],'API',t.get('started_at'),t)
  t['normalized']=n;t['money']={k:str(v) if v is not None else None for k,v in m.items()}
  turns.append(t)
def agg(items):
 r={'turns':len(items),'cases':len({t['case_id'] for t in items}), 'failed':sum(t['status']!='OK' for t in items),'retry_turns':sum(t['attempt_number']>1 for t in items)}
 for key in ['input_tokens','output_tokens','cached_input_tokens','cache_write_input_tokens','reasoning_tokens','input_tokens_uncached','total_tokens']:
  v=[t['normalized'].get(key) for t in items];r[key]=sum(x for x in v if x is not None);r[key+'_known']=sum(x is not None for x in v)
 for key in ['noncached_input_cost_usd','cached_input_cost_usd','cache_write_cost_usd','input_cost_usd','output_cost_usd','total_cost_usd']:
  v=[t['money'].get(key) for t in items];r[key]=sum(float(x) for x in v if x is not None);r[key+'_known']=sum(x is not None for x in v)
 r['latency_mean_ms']=statistics.mean([t['latency_ms'] for t in items if t.get('latency_ms') is not None]) if items else None
 r['models']=dict(collections.Counter(t['provider']+':'+t['model'] for t in items));r['errors']=dict(collections.Counter(t.get('error_code') for t in items if t['status']!='OK'))
 return r
def group(keys):
 d=collections.defaultdict(list)
 for t in turns:d['|'.join(str(t.get(k)) for k in keys)].append(t)
 return {k:agg(v) for k,v in sorted(d.items())}
report={'captured_at':datetime.now().astimezone().isoformat(),'case_days':[start,end], 'case_daily':{},'daily':group(['day']),'stages':group(['day','lane','round_name']),'tickers':group(['day','ticker']),'models':group(['day','provider','model']),'call_days':group(['call_day']),'case_modes':group(['day','case_mode']), 'top_cost':sorted(turns,key=lambda t:float(t['money'].get('total_cost_usd') or 0),reverse=True)[:30]}
for day in sorted({c['trading_date'] for c in cs.values()}):
 items=[c for c in cs.values() if c['trading_date']==day]
 report['case_daily'][day]={'cases':len(items),'status':dict(collections.Counter(c['status'] for c in items)),'mode':dict(collections.Counter(c['mode'] for c in items)),'ticker':dict(collections.Counter(c['ticker'] for c in items)),'bundle':dict(collections.Counter(c['bundle'] for c in items)),'errors':dict(collections.Counter(c['error_code'] for c in items if c['error_code']))}
print(json.dumps({'report':report,'cases':list(cs.values()),'turns':turns},ensure_ascii=False))
