"""Unpublished real-article validation on existing server HTTP routes."""
import asyncio
import gzip
import json
import os
import sys
import time

import doxagent.content_enrichment as package
package.__path__.insert(0, '/tmp/google-phase2-code')
from doxagent.content_enrichment.google_access import GooglePublicAccess
from doxagent.content_enrichment.managed import SiteAccessTransport
from doxagent.content_enrichment.pipeline import ArticlePipeline
from doxagent.content_enrichment.transport import DEADLINE
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.tokens import read_token

os.environ['ZYTE_API_KEY'] = sys.stdin.read().strip()
os.environ['DOXAGENT_GOOGLE_BODY_PROFILE_DIR'] = '/tmp/google-phase2-browser-profile'


async def main():
    s=DoxAgentSettings()
    sites=SiteStrategyRepository(s.site_access_sqlite_path)
    os.environ['DOXAGENT_GOOGLE_BODY_STOCKTITAN_PROXY']=sites.get_egress('tr-standard-1').endpoint
    client=SiteAccessClient(s.site_access_url,token=read_token(s.site_access_worker_token,s.site_access_worker_token_file))
    access=GooglePublicAccess(trusted_proxy_dns=True)
    captures=[]
    original_read=access.read
    original_http=access.http
    async def capture_read(url,stage,attempts):
        result=await original_read(url,stage,attempts)
        captures.append(dict(url=url,stage=stage,final_url=result.url,status=result.status,text=result.text))
        return result
    async def capture_http(url,attempts,transport):
        result=await original_http(url,attempts,transport)
        captures.append(dict(url=url,stage='http_public',final_url=result.url,status=result.status,text=result.text))
        return result
    access.read=capture_read;access.http=capture_http
    pipeline=ArticlePipeline(SiteAccessTransport(client),browser=None,site_client=client,google_access=access,pipeline_version='body_v2.2')
    rows=[r for r in json.load(open('/tmp/google-phase2-reader-cohort.json')) if r['site'] in ('barchart.com','stocktwits.com','streetinsider.com')]
    out=[];gate=asyncio.Semaphore(1)
    async def one(r):
        async with gate:
            budget=30 if r['site'] in ('gurufocus.com','futunn.com','benzinga.com') else 20
            token=DEADLINE.set(time.monotonic()+budget);start=time.monotonic()
            try:
                result=await pipeline.extract(MediaEnrichmentRecord(r['id'],r['id'],'google_news_search_rss',None,r['title'],None,r['url']))
                value={**r,'outcome':result.diagnostics.get('outcome'),'reason':result.reason,'chars':len(result.content or ''),'content':result.content,
                    'method':result.extraction_method,'diagnostics':result.diagnostics,'attempts':[a.to_payload() for a in result.attempts]}
            except Exception as exc:value={**r,'outcome':'UNAVAILABLE','reason':type(exc).__name__}
            finally:DEADLINE.reset(token)
            value['elapsed_ms']=int((time.monotonic()-start)*1000)
            out.append(value)
            with gzip.open('/tmp/google-phase2-reader-validation-accepted.json.gz','wt',encoding='utf-8') as stream:json.dump(out,stream,ensure_ascii=False)
            print(r['site'],r['cohort'],value['outcome'],value['reason'],value['elapsed_ms'],flush=True)
            await asyncio.sleep(3.3)
    try:await asyncio.gather(*(one(r) for r in rows))
    finally:
        await access.close();await client.close()
        with gzip.open('/tmp/google-phase2-reader-captures-accepted.json.gz','wt',encoding='utf-8') as stream:json.dump(captures,stream,ensure_ascii=False)
    print('real validation complete',len(out),flush=True)


asyncio.run(main())
