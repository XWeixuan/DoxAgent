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
  for slug,title in [('coreweave-vs-nebius-valuation-balance-sheet-and-catalysts-compared-93CH-4978721','CoreWeave vs Nebius: valuation, balance sheet, and catalysts compared') ]:
   record=MediaEnrichmentRecord('canary','canary','investing_ticker_news','MU',title,None,'https://uk.investing.com/news/stock-market-news/'+slug)
   token=DEADLINE.set(time.monotonic()+20);start=time.monotonic()
   try:r=await extractor.extract_version(record,'body_v2.2')
   finally:DEADLINE.reset(token)
   out.append({'url':record.url,'seconds':round(time.monotonic()-start,3),'succeeded':r.succeeded,'outcome':r.diagnostics.get('outcome'),'reason':r.reason,'chars':len(r.content or ''),'method':r.extraction_method,'attempts':[{'phase':a.phase,'reason':a.reason,'latency_ms':a.latency_ms} for a in r.attempts],'trace':r.diagnostics.get('site_access_trace')})
 finally:await extractor.close()
 print(json.dumps(out))

asyncio.run(main())
