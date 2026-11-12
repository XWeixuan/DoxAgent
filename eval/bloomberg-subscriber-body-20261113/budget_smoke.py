import asyncio, inspect, json, os, time
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.tokens import read_token
from doxagent.content_enrichment.extractor import SharedContentExtractor
from doxagent.content_enrichment.transport import DEADLINE
from doxagent.message_bus_v2.live import LiveQueue
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
async def main():
 s=DoxAgentSettings();client=SiteAccessClient(s.site_access_url,token=read_token(s.site_access_worker_token,s.site_access_worker_token_file))
 extractor=SharedContentExtractor(site_access_client=client)
 marker=DEADLINE.set(time.monotonic()+35);start=time.monotonic()
 try:
  async with asyncio.timeout(35):
   r=await extractor.extract_version(MediaEnrichmentRecord('accept','accept','bloomberg_massive','MU','EU to Hit Russia With More Banking and Energy Sanctions','','https://www.bloomberg.com/news/articles/2026-11-12/eu-to-hit-russia-with-more-banking-and-energy-sanctions'),'body_v2.2')
  print(json.dumps({'commit':os.environ.get('DOXAGENT_BUILD_COMMIT'),'loaded_live_budget_35':'else 35 if source.source_id == "bloomberg_massive"' in inspect.getsource(LiveQueue._job),'outcome':r.diagnostics.get('outcome'),'reason':r.reason,'chars':len(r.content or ''),'elapsed_seconds':round(time.monotonic()-start,3),'phases':[a.phase for a in r.attempts],'identity':r.diagnostics.get('identity_id')}),flush=True)
 finally:DEADLINE.reset(marker);await extractor.close();await client.close()
asyncio.run(main())
