import asyncio,json,time
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.tokens import read_token
from doxagent.content_enrichment.extractor import SharedContentExtractor
from doxagent.content_enrichment.transport import DEADLINE
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord

async def main():
 s=DoxAgentSettings();client=SiteAccessClient(s.site_access_url,token=read_token(s.site_access_worker_token,s.site_access_worker_token_file))
 extractor=SharedContentExtractor(site_access_client=client,close_site_access_client=True)
 out=[]
 try:
  for slug,title in [('intel-stock-insider-ceo-buy-1e10df5f','After Intel’s Worst Day on Record, CEO Lip-Bu Tan Buys $9.7 Million in Stock'),('etfs-beating-sp500-without-nvidia-stock-97ea9f52','You Don’t Need Nvidia to Beat the S&P 500. These ETFs Prove It.'),('micron-memory-chip-stocks-nvidia-shakeup-adf6b711','Micron, Other Memory Chip Stocks Fall on Nvidia Shakeup. Why They’re at Risk.')]:
   record=MediaEnrichmentRecord('canary','canary','barrons_ticker_news','MU',title,None,'https://www.barrons.com/articles/'+slug)
   token=DEADLINE.set(time.monotonic()+20);start=time.monotonic()
   try:r=await extractor.extract_version(record,'body_v2.2')
   finally:DEADLINE.reset(token)
   out.append({'url':record.url,'seconds':round(time.monotonic()-start,3),'succeeded':r.succeeded,'outcome':r.diagnostics.get('outcome'),'reason':r.reason,'chars':len(r.content or ''),'method':r.extraction_method,'attempts':[{'phase':a.phase,'reason':a.reason,'latency_ms':a.latency_ms} for a in r.attempts],'trace':r.diagnostics.get('site_access_trace')})
 finally:await extractor.close()
 print(json.dumps(out))

asyncio.run(main())
