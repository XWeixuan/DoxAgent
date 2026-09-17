import asyncio, json, time
from pathlib import Path
from curl_cffi.requests import AsyncSession
from doxagent.content_enrichment.pipeline import ArticlePipeline
from doxagent.content_enrichment.transport import PublicTransport, DEADLINE
from doxagent.monitoring.media_enrichment import DomainFetchController, MediaEnrichmentRecord

async def main():
    rows=json.loads(Path('/tmp/free_input.json').read_text())
    root=Path('/tmp/body-free-proxy-results'); root.mkdir(exist_ok=True)
    async with AsyncSession(impersonate='chrome', timeout=15,
                            proxy='http://doxagent-egress-clash:7893') as session:
        pipeline=ArticlePipeline(PublicTransport(session,DomainFetchController()),browser=None)
        with (root/'probe.jsonl').open('w') as out:
            for row in rows:
                token=DEADLINE.set(time.monotonic()+45)
                try:
                    result=await pipeline.extract(MediaEnrichmentRecord(
                        row['id'],row['id'],'proxy-diagnostic','MU',row['title'],'',row['url']))
                    data={'id':row['id'],'url':row['url'],'succeeded':result.succeeded,
                          'reason':result.reason,'chars':len(result.content or ''),
                          'method':result.extraction_method,
                          'attempts':[a.to_payload() for a in result.attempts]}
                    if result.content: (root/(row['id']+'.txt')).write_text(result.content)
                    out.write(json.dumps(data)+'\n');out.flush()
                    print(row['id'],data['succeeded'],data['reason'],data['chars'],flush=True)
                except Exception as exc: print(row['id'],type(exc).__name__,str(exc)[:100],flush=True)
                finally: DEADLINE.reset(token)
asyncio.run(main())
