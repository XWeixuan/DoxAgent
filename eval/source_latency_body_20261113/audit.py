import sqlite3,json,time,collections,statistics
from datetime import datetime,UTC,timedelta
ROOT='/var/lib/docker/volumes/doxagent-v2_v2-data/_data/bus/'
SOURCES=['barrons_ticker_news','investing_ticker_news','investorshub_ticker_news']
since=(datetime.now(UTC)-timedelta(hours=6)).isoformat()
out={'at':datetime.now(UTC).isoformat(),'since':since,'sources':{}}
def connect(path):
 c=sqlite3.connect('file:'+path+'?mode=ro',uri=True,timeout=5);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');start=time.monotonic();c.set_progress_handler(lambda:time.monotonic()-start>120,10000);return c
b=connect(ROOT+'bus.sqlite3');a=connect(ROOT+'acquisition.sqlite3')
def stats(v):
 v=sorted(x for x in v if x is not None);return {'n':len(v),'mean':round(statistics.mean(v),3) if v else None,'p50':v[len(v)//2] if v else None,'p95':v[min(len(v)-1,int(len(v)*.95))] if v else None}
def secs(x,y):
 try:return round((datetime.fromisoformat(x)-datetime.fromisoformat(y)).total_seconds(),3)
 except (ValueError,TypeError):return None
for source in SOURCES:
 articles=[dict(r) for r in b.execute('select * from live_articles where source_id=? and first_seen_at>=? order by first_seen_at desc',(source,since))]
 deliveries=[dict(r) for item in articles for r in b.execute('select * from live_deliveries where article_key=? and source_id=?',(item['article_key'],source))]
 cycles=[dict(r) for u in a.execute('select binding_id,unit from units where source_id=?',(source,)).fetchall() for r in a.execute('select * from cycles indexed by cycles_unit_time where binding_id=? and unit=? and dispatch_at>=?',(u[0],u[1],since))]
 captures=[dict(r) for r in a.execute('select capture_id,observed_at,request_started_at,payload from captures where source_id=? and observed_at>=? order by observed_at desc limit 30',(source,since))]
 # No content/Cookie snapshots: persist only per-stage diagnostics and article identity.
 examples=[]
 for r in articles[:50]:
  attempts=json.loads(r.pop('attempts_json') or '[]');r.pop('validators_json',None)
  r['attempts']=attempts
  examples.append(r)
 out['sources'][source]={'articles':len(articles),'outcomes':dict(collections.Counter((r['outcome'],r['last_error']) for r in [])),
  'outcome_counts':dict(collections.Counter(str(r['outcome']) for r in articles)),
  'errors':dict(collections.Counter(str(r['last_error']) for r in articles)),
  'execution_ms':stats([r['execution_spent_ms'] for r in articles]),
  'body_wall_seconds':stats([secs(r['initial_result_at'],r['first_fetch_at']) for r in articles]),
  'admission_seconds':stats([secs(r['consumable_at'],r['created_at']) for r in deliveries]),
  'cycle_status':dict(collections.Counter(str(r['status'])+':'+str(r['reason']) for r in cycles)),
  'cycle_seconds':stats([secs(r['finished_at'],r['dispatch_at']) for r in cycles]),
  'cycle_examples':[json.loads(r['data_json']) for r in cycles[-6:]],
  'article_examples':examples,'delivery_examples':deliveries[-10:]}
 cap=[]
 for r in captures:
  p=json.loads(r['payload']);p.pop('html',None);p.pop('body',None)
  messages=p.get('messages',[])
  cap.append({'capture_id':r['capture_id'],'observed_at':r['observed_at'],'request_started_at':r['request_started_at'],
   'metadata':{k:v for k,v in p.items() if k not in {'messages'}},
   'messages':[{k:m.get(k) for k in ['title','url','published_at','publisher_name','publication_time_basis','fetched_at']} for m in messages[:6]]})
 out['sources'][source]['captures']=cap
print(json.dumps(out,ensure_ascii=False))

