"""Run real production BODY adapters, recording counts/hashes rather than paid text."""
import asyncio, hashlib, json, time
from datetime import datetime, UTC
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.tokens import read_token
from doxagent.content_enrichment.extractor import SharedContentExtractor
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord

URLS=[
('EU to Hit Russia With More Banking and Energy Sanctions','https://www.bloomberg.com/news/articles/2026-11-12/eu-to-hit-russia-with-more-banking-and-energy-sanctions'),
('Anthropic IPO Is Jenga Block Holding Up Next Leg of AI Trade','https://www.bloomberg.com/news/articles/2026-11-11/anthropic-ipo-is-jenga-block-holding-up-next-leg-of-ai-trade'),
('Microsoft Vows to Pay Its Own Way to Power Data Centers','https://www.bloomberg.com/news/articles/2026-11-11/microsoft-vows-to-pay-its-own-way-to-power-data-centers'),
('Kalshi in Talks for New Funding Round at $65 Billion Valuation','https://www.bloomberg.com/news/articles/2026-11-12/kalshi-in-talks-for-new-funding-round-at-65-billion-valuation')]
async def main():
 s=DoxAgentSettings();client=SiteAccessClient(s.site_access_url,token=read_token(s.site_access_worker_token,s.site_access_worker_token_file))
 extractor=SharedContentExtractor(site_access_client=client)
 try:
  for title,url in URLS:
   resolved=await client.resolve(url)
   print(json.dumps({'kind':'route','site':resolved.site_id,'runtime_key':resolved.runtime_key,'enabled':resolved.enabled,'body':resolved.body.model_dump(),'identity_ids':[c.identity_id for c in resolved.access.combinations]}),flush=True)
   start=time.monotonic()
   result=await extractor.extract_version(MediaEnrichmentRecord('bb-accept','bb-accept','bloomberg_massive','MU',title,'',url),'body_v2.2')
   text=result.content or '';d=result.diagnostics or {}
   safe={k:d.get(k) for k in ['outcome','candidate_summary','access_path','identity_id','runtime_instance_id','site_id','strategy_ref','publisher_published_at','stage_timings','queue_wait_ms','network_ms']}
   print(json.dumps({'kind':'body','at':datetime.now(UTC).isoformat(),'url':url,'seconds':round(time.monotonic()-start,3),'http':result.http_status,'reason':result.reason,'method':result.extraction_method,'chars':len(text),'paragraphs':len(text.split('\n\n')) if text else 0,'sha256':hashlib.sha256(text.encode()).hexdigest() if text else None,'diagnostics':safe}),flush=True)
   if result.reason in {'challenge_required','site_recovery_pending'}:break
   await asyncio.sleep(8)
 finally:
  await extractor.close();await client.close()
asyncio.run(main())
