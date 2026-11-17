import os,json,sqlite3,collections,sys
from datetime import datetime,UTC,timedelta
from doxagent.v2_read.native_content import NativeContent
p=os.environ['DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH'];codec=NativeContent(p)
c=sqlite3.connect('file:'+p+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
out=[]
since=(datetime.now(UTC)-timedelta(days=2)).isoformat()
for r in c.execute("select * from live_articles indexed by live_article_observed where first_seen_at>=? and source_id=? order by first_seen_at desc limit 25",(since,sys.argv[1] if len(sys.argv)>1 else 'barrons_ticker_news')):
 v=dict(r);result=codec.decode(codec.decode(json.loads(r['result_ref']))) if r['result_ref'] else {}
 inp=codec.decode(codec.decode(json.loads(r['input_ref'])))
 meta=result.get('metadata',{});en=meta.get('media_enrichment',{})
 out.append({'key':r['article_key'],'url':r['url'],'title':r['title'],'first_seen_at':r['first_seen_at'],
  'published_at':inp.get('published_at'),'initial_result_at':r['initial_result_at'],'initial_outcome':r['initial_outcome'],
  'state':r['state'],'outcome':r['outcome'],'error':r['last_error'],'diagnostics':en,'completion':meta.get('v2_body_completion'),
  'deliveries':[dict(x) for x in c.execute('select ticker,first_discovered_at,created_at,consumable_at,state from live_deliveries where article_key=?',(r['article_key'],))]})
print(json.dumps(out,ensure_ascii=False))
