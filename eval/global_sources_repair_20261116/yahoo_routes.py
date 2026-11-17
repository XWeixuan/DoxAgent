import asyncio,json,time
from urllib.parse import urlsplit
from doxagent.settings import DoxAgentSettings
from doxagent.message_bus_v2.publisher_http import PublisherSession
from doxagent.message_bus_v2.yahoo_sources import rss_yahoo_rows
from doxagent.message_bus_v2.http_routes import HTTP_EGRESSES
async def main():
 s=DoxAgentSettings();proxy=s.message_bus_v2_yahoo_http_proxy_url or s.crawler_egress_proxy_url
 out=[]
 for label,p in [(r,HTTP_EGRESSES[r]) for r in ['de-standard-1','tr-standard-1']]:
  async with PublisherSession(impersonate='chrome',trust_env=False,proxy=p or '',max_clients=1) as c:
   started=time.monotonic()
   try:
    r=await c.get('https://feeds.finance.yahoo.com/rss/2.0/headline',params={'s':'MU','region':'US','lang':'en-US'},timeout=8)
    row={'status':r.status_code,'rows':len(rss_yahoo_rows(r.text)) if r.status_code==200 else None}
   except Exception as e:row={'error':type(e).__name__,'code':getattr(e,'code',None),'detail':str(e).replace(str(proxy),'[proxy]')[:280]}
   out.append({'route':label,'proxy_host':urlsplit(p).hostname if p else None,'seconds':round(time.monotonic()-started,3),**row})
 print(json.dumps(out))
asyncio.run(main())
