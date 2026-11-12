import asyncio,json,time,sys
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.tokens import read_token
from doxagent.content_enrichment.extractor import SharedContentExtractor
from doxagent.content_enrichment.transport import DEADLINE
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord

CASES=[
 ('businesswire_massive','https://www.businesswire.com/news/home/20261112795782/en/Helen-of-Troy-Issues-Statement/','Helen of Troy Issues Statement'),
 ('businesswire_massive','https://www.businesswire.com/news/home/20261112238564/en/CKX-Lands-Investor-Alert-Kahn-Swick-Foti-LLC-Investigates-CKX-Lands-Inc.---CKX/','CKX Lands Investor Alert: Kahn Swick & Foti, LLC Investigates CKX Lands, Inc. - CKX'),
 ('prnewswire_massive','https://www.prnewswire.com/news-releases/edun-brings-live-rosin-to-the-holiday-kitchen-with-limited-edition-organic-evoo-launch-302938320.html','Edun Brings Live Rosin to the Holiday Kitchen With Limited-edition Organic EVOO Launch'),
 ('prnewswire_massive','https://www.prnewswire.com/news-releases/willamette-valley-vineyards-posts-results-for-q3-2026-302938457.html','Willamette Valley Vineyards Posts Results for Q3 2026'),
 ('tmx_newsfile_massive','https://www.newsfilecorp.com/release/323833/Mayfair-Gold-Reports-Q3-2026-Operating-and-Financial-Results','Mayfair Gold Reports Q3 2026 Operating and Financial Results'),
 ('tmx_newsfile_massive','https://www.newsfilecorp.com/release/323827/The-LGL-Group-Inc.-Reports-Third-Quarter-2026-Results','The LGL Group, Inc. Reports Third Quarter 2026 Results'),
]
async def main():
 s=DoxAgentSettings();client=SiteAccessClient(s.site_access_url,token=read_token(s.site_access_worker_token,s.site_access_worker_token_file))
 extractor=SharedContentExtractor(site_access_client=client,close_site_access_client=True);out=[]
 try:
  for source,url,title in CASES:
   if len(sys.argv)>1 and sys.argv[1] not in source:continue
   record=MediaEnrichmentRecord('newswire-canary','newswire-canary',source,'MU',title,None,url)
   token=DEADLINE.set(time.monotonic()+20);start=time.monotonic()
   try:r=await extractor.extract_version(record,'body_v2.2')
   finally:DEADLINE.reset(token)
   out.append({'source':source,'url':url,'seconds':round(time.monotonic()-start,3),'succeeded':r.succeeded,'outcome':r.diagnostics.get('outcome'),'reason':r.reason,'chars':len(r.content or ''),'method':r.extraction_method,'stages':r.diagnostics.get('stage_timings'),'trace':r.diagnostics.get('site_access_trace')})
 finally:await extractor.close()
 print(json.dumps(out))
asyncio.run(main())
