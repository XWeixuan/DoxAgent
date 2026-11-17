import json,sqlite3,time,statistics,subprocess,collections,sys
from datetime import datetime,UTC
p='/var/lib/docker/volumes/doxagent-v2_v2-data/_data/bus/bus.sqlite3';c=sqlite3.connect('file:'+p+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');t=time.monotonic();c.set_progress_handler(lambda:time.monotonic()-t>12,10000)
since=sys.argv[1] if len(sys.argv)>1 else '2026-11-17T17:53:25+00:00';dt=lambda x:datetime.fromisoformat(x.replace('Z','+00:00')) if x else None
rows=[dict(r) for r in c.execute("SELECT a.article_key,a.initial_result_at,a.initial_outcome,d.ticker,d.source_id,d.created_at,d.first_discovered_at,d.state,d.initial_decision_at,d.initial_decision,d.consumable_at FROM live_articles a INDEXED BY live_article_observed JOIN live_deliveries d USING(article_key) WHERE a.first_seen_at>=?",(since,))]
wait=[];pub=[]
for r in rows:
 if not r['initial_result_at']:continue
 ready=max(dt(r['initial_result_at']),dt(r['first_discovered_at']),dt(r['created_at']))
 if r['initial_decision_at']:wait.append((dt(r['initial_decision_at'])-ready).total_seconds())
 if r['consumable_at']:pub.append((dt(r['consumable_at'])-ready).total_seconds())
def stats(v):
 v=sorted(x for x in v if x>=0);n=len(v)
 return dict(n=n,p50=statistics.median(v) if n else None,p95=v[min(n-1,int(n*.95))] if n else None,max=v[-1] if n else None)
out=dict(at=datetime.now(UTC).isoformat(),since=since,rows=rows,states=dict(collections.Counter(r['state'] for r in rows)),local_decision_wait=stats(wait),local_publish_wait=stats(pub))
out['pending']=[dict(r) for r in c.execute("SELECT d.state,d.source_id,count(*) n,min(d.due_at) oldest FROM live_deliveries d INDEXED BY live_delivery_due WHERE d.state IN ('PENDING','L2_DUE','WAIT_BODY') GROUP BY d.state,d.source_id")]
out['new_stream_items']=c.execute('SELECT count(*) FROM stream_items WHERE published_at>=?',(since,)).fetchone()[0]
out['new_stream_members_by_source']=[dict(r) for r in c.execute('SELECT s.source_id,count(*) n FROM stream_items i JOIN stream_members m USING(stream_item_id) JOIN standard_messages s USING(standard_message_id) WHERE i.published_at>=? GROUP BY s.source_id',(since,))]
out['indexes']=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE name IN ('live_article_active_due','live_article_recovery_check')")]
c.close();out['roles']={}
for role in ['message-bus','message-collector','content-enrichment','chrome-supervisor','site-access']:
 raw=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-v2-'+role+'-1']))[0];env=dict(x.split('=',1) for x in raw['Config']['Env']);out['roles'][role]=dict(id=raw['Id'][:12],commit=env.get('DOXAGENT_BUILD_COMMIT'),started=raw['State']['StartedAt'],running=raw['State']['Running'])
print(json.dumps(out))
