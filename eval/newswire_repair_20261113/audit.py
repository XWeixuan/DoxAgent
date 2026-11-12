import collections,json,sqlite3,time,sys,statistics
from datetime import datetime,UTC
from doxagent.v2_read.native_content import NativeContent

SINCE=sys.argv[1] if len(sys.argv)>1 else '2026-11-12T20:00:46+00:00'
UNTIL=sys.argv[2] if len(sys.argv)>2 else datetime.now(UTC).isoformat()
IDS=['businesswire_massive','prnewswire_massive','globenewswire_massive','access_newswire_massive','tmx_newsfile_massive']
def connect(p):
 c=sqlite3.connect('file:'+p+'?mode=ro',uri=True,timeout=3);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on')
 start=time.monotonic();c.set_progress_handler(lambda:time.monotonic()-start>45,10000);return c
b=connect('/data/bus/bus.sqlite3');a=connect('/data/bus/acquisition.sqlite3');codec=NativeContent('/data/bus/bus.sqlite3')
def load(ref):return codec.decode(codec.decode(json.loads(ref))) if ref else {}
out={'at':datetime.now(UTC).isoformat(),'since':SINCE,'until':UNTIL,'sources':{}}
for sid in IDS:
 rows=[dict(r) for r in b.execute("select * from live_articles indexed by live_article_observed where first_seen_at>=? and first_seen_at<? and source_id=? and delivery_mode='production' order by first_seen_at desc",(SINCE,UNTIL,sid))]
 deliveries=[dict(r) for article in rows for r in b.execute('select * from live_deliveries where article_key=?',(article['article_key'],))]
 units=[dict(r) for r in a.execute("select * from units where source_id=? and unit not like 'TEST:%'",(sid,))]
 cycles=[dict(r)|{'unit':u['unit']} for u in units for r in a.execute('select status,reason,dispatch_at,finished_at,data_json from cycles indexed by cycles_unit_time where binding_id=? and unit=? and dispatch_at>=? and dispatch_at<?',(u['binding_id'],u['unit'],SINCE,UNTIL))]
 examples=[]
 discovery=[];decision_wait=[];publication_delay=[];invalid_dates=0
 for row in rows:
  inp=load(row['input_ref']);at=inp.get('published_at')
  if at:
   lag=(datetime.fromisoformat(row['first_seen_at'])-datetime.fromisoformat(at.replace('Z','+00:00'))).total_seconds()
   if lag>=0:discovery.append(lag)
   else:invalid_dates+=1
 for d in deliveries:
  ar=next(v for v in rows if v['article_key']==d['article_key'])
  if d['initial_decision_at'] and ar['initial_result_at']:
   decision_wait.append((datetime.fromisoformat(d['initial_decision_at'])-datetime.fromisoformat(ar['initial_result_at'])).total_seconds())
  if d['consumable_at']:
   publication_delay.append((datetime.fromisoformat(d['consumable_at'])-datetime.fromisoformat(d['first_discovered_at'] or d['created_at'])).total_seconds())
 def stats(vals):
  vals=sorted(vals);return {'n':len(vals),'mean':round(statistics.mean(vals),3) if vals else None,'p50':vals[len(vals)//2] if vals else None,'p95':vals[min(len(vals)-1,int(len(vals)*.95))] if vals else None,'max':max(vals) if vals else None}
 for r in rows[:20]:
  inp=load(r['input_ref']);result=load(r['result_ref']);meta=result.get('metadata',{});en=meta.get('media_enrichment',{})
  examples.append({k:r[k] for k in ['article_key','title','url','first_seen_at','initial_result_at','state','outcome','last_error','attempts_json','execution_spent_ms']}|{'published_at':inp.get('published_at'),'language':inp.get('metadata',{}).get('language'),'enrichment':{k:en.get(k) for k in ['outcome','reason_code','extraction_method','candidate_summary','stage_timings','site_access_trace','publisher_published_at']},'body_chars':len(result.get('body') or '')})
 pending=[]
 for d in sorted([d for d in deliveries if d['state']=='PENDING'],key=lambda v:v['created_at'])[:15]:
  article=next(v for v in rows if v['article_key']==d['article_key'])
  pending.append({k:d.get(k) for k in ['article_key','ticker','state','due_at','created_at','last_error','terms_revision','regex_result','decision_digest']}|{'article_state':article['state'],'outcome':article['outcome'],'revision':article['revision'],'has_result':bool(article['result_ref'])})
 source=json.loads(b.execute('select data_json from source_definitions where source_id=?',(sid,)).fetchone()[0])
 physical={}
 for c in cycles:
  packet=json.loads(c['data_json']);physical_id=packet.get('physical_run_id')
  if physical_id:physical[physical_id]=c
 observations=[json.loads(c['data_json']).get('wire_observation',{}) for c in physical.values()]
 ages=[int(cache['age']) for observation in observations for cache in observation.get('cache',[]) if str(cache.get('age','')).isdigit()]
 out['sources'][sid]={'config':{k:source.get(k) for k in ['version','enabled','delivery_mode','default_parameters']}|{'jev_enabled':source.get('distribution_policy',{}).get('jev_enabled'),'require_complete_body':source.get('distribution_policy',{}).get('require_complete_body'),'bindings':[v[0] for v in b.execute('select ticker from ticker_source_bindings where source_id=? and enabled=1 and tombstoned_at is null',(sid,))]},'articles':dict(collections.Counter(r['outcome'] for r in rows)),
 'discovery_seconds':stats(discovery),'invalid_publication_order':invalid_dates,'body_to_decision_seconds':stats(decision_wait),'discovery_to_published_seconds':stats(publication_delay),
 'delivery_states':dict(collections.Counter(d['state'] for d in deliveries)),'pending':pending,'examples':examples,
 'units':[{'unit':u['unit'],'head':json.loads(u['head_json'])} for u in units],
 'cycles':dict(collections.Counter(str(r['status'])+':'+str(r['reason']) for r in cycles)),
 'physical_cycles':dict(collections.Counter(str(r['status'])+':'+str(r['reason']) for r in physical.values())),
 'physical_units':{unit:dict(collections.Counter(str(r['status'])+':'+str(r['reason']) for r in physical.values() if r['unit']==unit)) for unit in {r['unit'] for r in physical.values()}},
 'overflow_risks':sum(bool(v.get('window_overflow_risk')) for v in observations),'max_cache_age_seconds':max(ages) if ages else None,
 'freshness_degraded_polls':sum(bool(v.get('freshness_degraded')) for v in observations),
 'latest_failures':[json.loads(r['data_json']) for r in cycles if r['reason']][-3:]}
print(json.dumps(out,ensure_ascii=False))
