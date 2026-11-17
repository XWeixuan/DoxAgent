import asyncio,json,time
from pathlib import Path
from datetime import datetime,UTC
from curl_cffi.requests import AsyncSession
from doxagent.message_bus_v2.newswire_listing import parse_listing
from doxagent.message_bus_v2.newswire_sources import initial_sources
async def main():
 source=next(s for s in initial_sources() if s.source_id=='prnewswire_massive');out=[]
 async with AsyncSession(impersonate='chrome',timeout=8,trust_env=False) as s:
  for label,page,fresh in [('normal',1,False),('fresh',1,True),('page2',2,True)]:
   url=f'https://www.prnewswire.com/news-releases/news-releases-list/?page={page}&pagesize=100'+(f'&_={int(time.time())}' if fresh else '')
   start=time.monotonic();r=await s.get(url);rows=parse_listing(r.text,source,datetime.now(UTC)) if r.status_code==200 else []
   Path('/tmp/newswire-prn-'+label+'.html').write_text(r.text)
   out.append({'label':label,'status':r.status_code,'seconds':round(time.monotonic()-start,3),'count':len(rows),'cache':{k:v for k,v in r.headers.items() if k.lower() in ['age','date','cache-control','cf-cache-status']},'first':[{'title':v.title,'url':v.url,'published_at':v.published_at.isoformat()} for v in rows[:3]],'keys':[v.url for v in rows]})
 print(json.dumps(out,ensure_ascii=False))
asyncio.run(main())
