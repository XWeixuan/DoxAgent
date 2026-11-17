import os,json,sys
from datetime import datetime,UTC
from doxagent.v2_read.repository import ReadStore
from doxagent.api_v2.article_samples import select_article_facts_many
s=ReadStore(os.environ['DOXAGENT_V2_READ_SQLITE_PATH']);sid=sys.argv[1] if len(sys.argv)>1 else 'investing_ticker_news'
with s.connect() as c:
 tickers=[r[0] for r in c.execute('select distinct ticker from article_sample_keys where source_id=?',(sid,))]
 facts=select_article_facts_many(s,[(t,sid) for t in tickers],db=c)
 out=[]
 for (ticker,_), rows in facts.items():
  deliveries={}
  for r in c.execute("select d.payload,d.parent from object_current d indexed by current_identity where d.kind='native:live_deliveries' and d.ticker=? and d.source_id=? and d.id in (select id from article_sample_keys indexed by article_sample_source_time where ticker=? and source_id=? order by discovered_at desc,id desc limit 20)",(ticker,sid,ticker,sid)):
   v=json.loads(r[0]);deliveries[r[1]]=v
  for f in rows:
   if f['e2e'] is None:continue
   delivery=deliveries.get(f['article_key'])
   if not delivery:continue
   mid=delivery.get('standard_id');r=c.execute("select payload from object_current indexed by current_identity where kind='message' and ticker=? and id=?",(ticker,mid)).fetchone()
   message=json.loads(r[0]) if r else {}
   out.append({'ticker':ticker,'key':f['article_key'],'found':f['found'].isoformat(),'e2e':f['e2e'],
               'delivery':delivery,'message':{k:message.get(k) for k in ['title','id','source_id','source_published_at','stream_published_at','pipeline','media_enrichment']}})
out.sort(key=lambda r:r['e2e'],reverse=True)
print(json.dumps({'at':datetime.now(UTC).isoformat(),'source':sid,'count':len(out),'samples':out},ensure_ascii=False))
