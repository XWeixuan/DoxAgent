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
  for slug,title in [('NASDAQ/micron-technology-MU/stock-news/99695989/micron-announces-participation-in-investor-events','Micron Announces Participation in Investor Events'),('NASDAQ/intel-INTC/stock-news/99695694/intel-radixark-collaborate-to-advance-open-ai-inf','Intel, RadixArk Collaborate to Advance Open AI Inference on Intel Platforms')]:
   record=MediaEnrichmentRecord('canary','canary','investorshub_ticker_news','MU',title,None,'https://investorshub.advfn.com/stock-market/'+slug)
   token=DEADLINE.set(time.monotonic()+20);start=time.monotonic()
   try:r=await extractor.extract_version(record,'body_v2.2')
   finally:DEADLINE.reset(token)
   out.append({'url':record.url,'seconds':round(time.monotonic()-start,3),'succeeded':r.succeeded,'outcome':r.diagnostics.get('outcome'),'reason':r.reason,'chars':len(r.content or ''),'method':r.extraction_method,'attempts':[{'phase':a.phase,'reason':a.reason,'latency_ms':a.latency_ms} for a in r.attempts],'trace':r.diagnostics.get('site_access_trace')})
 finally:await extractor.close()
 print(json.dumps(out))

asyncio.run(main())
