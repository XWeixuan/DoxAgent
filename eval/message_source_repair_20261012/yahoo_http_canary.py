"""One NCP and one RSS call on the configured fixed HTTP egress."""
import asyncio,json
from doxagent.message_bus_v2.yahoo_transport import YahooTransport
from doxagent.message_bus_v2.yahoo_sources import acquire_yahoo_rss
from doxagent.settings import DoxAgentSettings
async def main():
 s=DoxAgentSettings();t=YahooTransport(proxy_url=s.message_bus_v2_yahoo_http_proxy_url,gap=2,jitter=0)
 try:
  try:
   r=await t.request('POST','https://finance.yahoo.com/xhr/ncp',params={'queryRef':'latestNews','serviceKey':'ncp_fin'},json={'serviceConfig':{'snippetCount':20,'s':['MU']}},timeout=15)
   print(json.dumps({'route':'ncp','status':r.status_code,'keys':list(r.json())[:8]}),flush=True)
  except Exception as e:print(json.dumps({'route':'ncp','error':type(e).__name__,'status':getattr(getattr(e,'response',None),'status_code',None)}),flush=True)
  try:
   rows,meta=await acquire_yahoo_rss(t,'MU',timeout_seconds=15)
   print(json.dumps({'route':'rss','rows':len(rows),'metadata':meta,'sample':rows[:1]}),flush=True)
  except Exception as e:print(json.dumps({'route':'rss','error':type(e).__name__,'status':getattr(getattr(e,'response',None),'status_code',None)}),flush=True)
 finally:t.close()
asyncio.run(main())
