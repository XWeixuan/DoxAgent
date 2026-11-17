import asyncio,json,time
from datetime import datetime,UTC
from curl_cffi.requests import AsyncSession
from doxagent.message_bus_v2.newswire_listing import parse_listing
from doxagent.message_bus_v2.newswire_sources import initial_sources,parse_feed
from doxagent.message_bus_v2.http_routes import HTTP_EGRESSES
async def main():
 sources={s.source_id:s for s in initial_sources()};out=[]
 cases=[
  ('access-normal','access_newswire_massive','https://www.accessnewswire.com/newsroom?page=1','us-standard-5',{}),
  ('access-no-cache','access_newswire_massive','https://www.accessnewswire.com/newsroom?page=1','us-standard-5',{'Cache-Control':'no-cache','Pragma':'no-cache'}),
  ('access-fresh','access_newswire_massive','https://www.accessnewswire.com/newsroom?page=1&_='+str(int(time.time())),'us-standard-5',{}),
  ('gnw-http','globenewswire_massive','https://www.globenewswire.com/en/Search/load/more?page=1&pageSize=50',None,{}),
  ('gnw-rss','globenewswire_massive','https://www.globenewswire.com/RssFeed','tr-standard-1',{}),
 ]
 async with AsyncSession(impersonate='chrome',timeout=8,trust_env=False) as session:
  for label,sid,url,route,headers in cases:
   t=time.monotonic()
   try:
    r=await session.get(url,headers=headers,proxy=HTTP_EGRESSES[route] if route else None)
    rows=(parse_feed(r.text,sources[sid],{'format':'rss','url':url},datetime.now(UTC)) if label=='gnw-rss' else parse_listing(r.text,sources[sid],datetime.now(UTC))) if r.status_code==200 else []
    out.append({'label':label,'at':datetime.now(UTC).isoformat(),'status':r.status_code,'seconds':round(time.monotonic()-t,3),'count':len(rows),'headers':{k:v for k,v in r.headers.items() if k.lower() in ['age','date','cache-control','x-cache','cf-cache-status']},'first':[{'url':v.url,'published_at':v.published_at.isoformat()} for v in rows[:5]],'keys':[v.url for v in rows]})
   except Exception as e:out.append({'label':label,'error':type(e).__name__})
 print(json.dumps(out))
asyncio.run(main())
