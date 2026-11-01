import asyncio,json,os,time
from pathlib import Path
from doxagent.content_enrichment.extractor import SharedContentExtractor
from doxagent.content_enrichment.transport import DEADLINE
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.tokens import read_token
from doxagent.settings import DoxAgentSettings
async def main():
 s=DoxAgentSettings(); c=SiteAccessClient(s.site_access_url,token=read_token(s.site_access_worker_token,s.site_access_worker_token_file))
 e=SharedContentExtractor(site_access_client=c,close_site_access_client=True,trusted_proxy_dns=True)
 out=[]
 try:
  for r in json.loads(Path('/tmp/google-phase2-smoke-cohort.json').read_text()):
   t=DEADLINE.set(time.monotonic()+30);start=time.monotonic()
   try:
    result=await e.extract_version(MediaEnrichmentRecord(r['id'],r['id'],'google_news_search_rss',None,r['title'],None,r['url']),'body_v2.2')
    out.append(dict(site=r['site'],outcome=result.diagnostics.get('outcome'),reason=result.reason,chars=len(result.content or ''),method=result.extraction_method,elapsed_ms=round((time.monotonic()-start)*1000),attempts=[dict(stage=a.phase,status=a.status_code,reason=a.reason,elapsed_ms=a.latency_ms) for a in result.attempts]))
   finally:DEADLINE.reset(t)
 finally:await e.close()
 print(json.dumps({'provider_key_present':bool(os.getenv('ZYTE_API_KEY')),'profile':os.getenv('DOXAGENT_GOOGLE_BODY_PROFILE_DIR'),'results':out}))
asyncio.run(main())
