import asyncio,json,time
from curl_cffi.requests import AsyncSession
from doxagent.message_bus_v2.http_routes import HTTP_EGRESSES
from doxagent.message_bus_v2.newswire_sources import parse_access
from datetime import datetime,UTC
async def main():
 out=[]
 async with AsyncSession(impersonate='chrome',timeout=6,trust_env=False,proxy=HTTP_EGRESSES['us-standard-5']) as s:
  for method,url in [('GET','https://www.accessnewswire.com/newsroom'),('POST','https://www.accessnewswire.com/newsroom'),('POST','https://www.accessnewswire.com/newsroom?page=1')]:
   t=time.monotonic()
   try:
    r=await s.request(method,url);rows=parse_access(r.text,datetime.now(UTC)) if r.status_code==200 else []
    out.append({'method':method,'url':url,'status':r.status_code,'seconds':round(time.monotonic()-t,3),'count':len(rows),'headers':{k:v for k,v in r.headers.items() if k.lower() in ['age','cache-control','date','x-cache','cf-cache-status']},'first':[v.url for v in rows[:3]]})
   except Exception as e:out.append({'method':method,'url':url,'error':type(e).__name__})
 print(json.dumps(out))
asyncio.run(main())
