import sqlite3,json,time,os,sys,statistics
from datetime import datetime,UTC
from doxagent.message_bus_v2.repository import MessageBusV2Repository
p=os.environ['DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH'];c=sqlite3.connect('file:'+p+'?mode=ro',uri=True,timeout=3);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on')
counts=[0]
def progress():counts[0]+=100
c.set_progress_handler(progress,100);start=time.monotonic()
value=MessageBusV2Repository.published_candidates(c,'MU',['url:https://investing.com/audit-no-match'],'audit-no-match')
cost={'seconds':round(time.monotonic()-start,6),'instructions':counts[0],'results':len(value)}
cost['warm']=[]
for _ in range(3):
 counts[0]=0;start=time.monotonic()
 value=MessageBusV2Repository.published_candidates(c,'MU',['url:https://investing.com/audit-no-match'],'audit-no-match')
 cost['warm'].append({'seconds':round(time.monotonic()-start,6),'instructions':counts[0]})
c.set_progress_handler(None,0)
since=sys.argv[1];rows=c.execute('select a.article_key,a.source_id,a.first_seen_at,a.first_fetch_at,a.initial_result_at,a.outcome,d.ticker,d.source_id delivery_source,d.state,d.consumable_at from live_articles a indexed by live_article_observed join live_deliveries d using(article_key) where a.first_seen_at>=? and d.source_id not in (?,?)',(since,'reuters_site_search','bloomberg_massive')).fetchall()
out={}
dt=lambda s:datetime.fromisoformat(s.replace('Z','+00:00'))
for row in rows:
 r=dict(row);source=r['delivery_source'];v=out.setdefault(source,{'deliveries':0,'published':0,'pending':0,'e2e':[],'post_body':[],'outcomes':{}})
 v['deliveries']+=1;v['published']+=r['state']=='PUBLISHED';v['pending']+=r['state'] in ['PENDING','WAIT_BODY','L2_DUE'];v['outcomes'][str(r['outcome'])]=v['outcomes'].get(str(r['outcome']),0)+1
 if r['consumable_at']:
  v['e2e'].append((dt(r['consumable_at'])-dt(r['first_seen_at'])).total_seconds())
  if r['initial_result_at']:v['post_body'].append((dt(r['consumable_at'])-dt(r['initial_result_at'])).total_seconds())
for v in out.values():
 for k in ['e2e','post_body']:
  a=v[k];v[k]={'n':len(a),'mean':round(statistics.mean(a),3) if a else None,'max':max(a) if a else None}
print(json.dumps({'at':datetime.now(UTC).isoformat(),'since':since,'actual_lookup':cost,'sources':out}))
