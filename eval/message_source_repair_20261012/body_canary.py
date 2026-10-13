"""Bounded real pipeline acceptance; no publication or profile reconstruction."""
import asyncio,json
from datetime import UTC,datetime
import httpx
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.tokens import read_token
from doxagent.site_strategy.client import SiteAccessClient,SiteManagedBrowser
from doxagent.content_enrichment.pipeline import ArticlePipeline
from doxagent.content_enrichment.managed import SiteAccessTransport,SiteAccessBrowserReader
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
from doxagent.message_bus_v2.industry_sources import parse_digitimes_listing,parse_barrons_ticker_listing,parse_trendforce_listing,parse_shared_feed
async def main():
 s=DoxAgentSettings();client=SiteAccessClient('http://127.0.0.1:8011',token=read_token(s.site_access_worker_token,s.site_access_worker_token_file))
 browser=SiteManagedBrowser(client);transport=SiteAccessTransport(client)
 pipeline=ArticlePipeline(transport,browser=SiteAccessBrowserReader(client,transport),site_client=client,pipeline_version='body_v2.2')
 for name,url,recipe in [('digitimes','https://www.digitimes.com/topic/semiconductors/',None),('barrons','https://www.barrons.com/market-data/stocks/mu','builtin:barrons_ticker@1'),('trendforce_news','https://www.trendforce.com/news/',None),('trendforce_press','https://www.trendforce.com/presscenter/news',None)]:
  try:
   status,final,headers,body=await browser.get(url,remaining_budget_ms=45000,recipe_ref=recipe,operation_id='acceptance-20261012-'+name)
   now=datetime.now(UTC)
   if name=='digitimes':rows=parse_digitimes_listing(body,now=now)
   elif name=='barrons':rows=[r for r in parse_barrons_ticker_listing(body,ticker='MU',now=now) if 'barrons.com/' in r.url]
   else:rows=parse_trendforce_listing(body,press=name.endswith('press'),now=now)
   print(json.dumps({'source':name,'listing_status':status,'listing_rows':len(rows)}),flush=True)
   for message in rows[:2]:
    record=MediaEnrichmentRecord('acceptance','acceptance',name,'MU',message.title,None,message.url)
    async with asyncio.timeout(100):result=await pipeline.extract(record)
    print(json.dumps({'source':name,**result.to_payload(),'outcome':result.diagnostics.get('outcome')}),flush=True)
  except Exception as exc:print(json.dumps({'source':name,'error':type(exc).__name__,'reason':str(exc)[:200]}),flush=True)
 await client.close()
asyncio.run(main())
