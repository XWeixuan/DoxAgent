import asyncio,json,os,time,gzip,hashlib
from pathlib import Path
import doxagent.content_enrichment
if Path('/tmp/body-trial-code').exists():
 doxagent.content_enrichment.__path__.insert(0,'/tmp/body-trial-code')
from doxagent.content_enrichment.browser import PublisherBrowser
from doxagent.content_enrichment.pipeline import ArticlePipeline
from doxagent.content_enrichment.transport import PublicTransport,DEADLINE
from doxagent.monitoring.media_enrichment import _default_session_factory,DomainFetchController,MediaEnrichmentRecord
async def main():
 root=Path('/tmp/body-trial-results');root.mkdir(exist_ok=True)
 rows=json.loads(Path('/tmp/body-trial-input.json').read_text())
 b=PublisherBrowser(cdp_url=os.environ.get('DOXAGENT_CRAWLER_PLANE_BROWSER_CDP_URL'))
 async with _default_session_factory()() as session:
  pipeline=ArticlePipeline(PublicTransport(session,DomainFetchController()),browser=b)
  with (root/'probe.jsonl').open('w') as f:
   for r in rows:
    token=DEADLINE.set(time.monotonic()+65)
    try:
     result=await pipeline.extract(MediaEnrichmentRecord(r['id'],r['id'],r['source_id'],'MU',r['title'],r['body'],r['url']))
     data={'id':r['id'],'url':r['url'],'title':r['title'],'prior_reason':r['prior_reason'],'succeeded':result.succeeded,'reason':result.reason,'chars':len(result.content or ''),'method':result.extraction_method,'diagnostics':result.diagnostics,'attempts':[a.to_payload() for a in result.attempts]}
     if result.content: (root/(r['id']+'.txt')).write_text(result.content)
     f.write(json.dumps(data)+'\n');f.flush();print(r['id'],result.succeeded,result.reason,data['chars'],flush=True)
    finally: DEADLINE.reset(token)
 await b.close()
asyncio.run(main())
