import asyncio,json,time
from doxagent.message_bus_v2.defense_sources import DefenseHttp,ENDPOINTS
from doxagent.message_bus_v2.corporate_ir.http import PublicHttp
from doxagent.message_bus_v2.corporate_ir.catalog import initial_endpoints
from doxagent.message_bus_v2.publisher_http import PublisherHttpx
async def one(label,operation,close):
 t=time.monotonic()
 try:
  async with asyncio.timeout(18):r=await operation()
  return {'name':label,'seconds':round(time.monotonic()-t,3),'status':getattr(r,'status',getattr(r,'status_code',None))}
 except Exception as e:return {'name':label,'seconds':round(time.monotonic()-t,3),'error_type':type(e).__name__,'reason':getattr(e,'reason_code',None)}
 finally:await close()
async def main():
 army=DefenseHttp();ir=PublicHttp();feed=PublisherHttpx(follow_redirects=True,trust_env=False)
 spec=next(s for s in initial_endpoints() if s.endpoint_id=='nbis.press')
 result=await asyncio.gather(one('army-fresh',lambda:army.fetch(ENDPOINTS['army_news_releases'].urls[0],ENDPOINTS['army_news_releases']),army.close),one('nbis-fresh',lambda:ir.fetch(spec),ir.close),one('toms-fresh',lambda:feed.get('https://www.tomshardware.com/feeds.xml'),feed.aclose))
 print(json.dumps(result))
asyncio.run(main())
