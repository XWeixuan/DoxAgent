import asyncio, gzip, hashlib, json, os, time
from pathlib import Path
from doxagent.content_enrichment.browser import PublisherBrowser
from doxagent.content_enrichment.pipeline import ArticlePipeline
from doxagent.content_enrichment.quality import inspect_html, choose_candidate
from doxagent.content_enrichment.transport import PublicTransport, DEADLINE, browser_session_factory
from doxagent.monitoring.media_enrichment import DomainFetchController, MediaEnrichmentRecord

async def main():
    rows=json.loads(Path('/tmp/body-access-input.json').read_text())
    root=Path('/tmp/body-access-results-final');root.mkdir(exist_ok=True)
    hosts={'www.reuters.com','www.barrons.com','www.wsj.com','seekingalpha.com'}
    browser=PublisherBrowser(cdp_url=os.environ['DOXAGENT_CRAWLER_PLANE_BROWSER_CDP_URL'],
        identity_dir=Path('/identities'),authenticated_hosts=hosts,max_pages=1)
    async with browser_session_factory()() as session:
        pipeline=ArticlePipeline(PublicTransport(session,DomainFetchController()),browser=browser)
        with (root/'probe.jsonl').open('w') as f:
            for row in rows:
                token=DEADLINE.set(time.monotonic()+70)
                try:
                    result=await pipeline.extract(MediaEnrichmentRecord(row['id'],row['id'],'diagnostic','MU',row['title'],'',row['url']))
                    data={'id':row['id'],'url':row['url'],'title':row['title'],
                          'succeeded':result.succeeded,'reason':result.reason,'chars':len(result.content or ''),
                          'method':result.extraction_method,'diagnostics':result.diagnostics,
                          'attempts':[a.to_payload() for a in result.attempts]}
                    if result.content:
                        (root/(row['id']+'.txt')).write_text(result.content)
                        data['sha256']=hashlib.sha256(result.content.encode()).hexdigest()
                    else:
                        # Only our temporary page; never serialize browser cookies/storage.
                        ctx=await browser._context(row['url'].split('/')[2]);page=await ctx.new_page()
                        try:
                            await page.goto(row['url'],wait_until='domcontentloaded',timeout=15000)
                            await page.wait_for_timeout(2500)
                            html=await page.content()
                            (root/(row['id']+'.html.gz')).write_bytes(gzip.compress(html.encode()))
                            data['visible_chars']=len(await page.locator('body').inner_text())
                            data['page_title']=await page.title()
                        except Exception as exc: data['detail_error']=type(exc).__name__
                        finally: await page.close()
                    f.write(json.dumps(data)+'\n');f.flush()
                    print(row['id'],data['succeeded'],data['reason'],data['chars'],flush=True)
                except Exception as exc:print(row['id'],type(exc).__name__,flush=True)
                finally: DEADLINE.reset(token)
    await browser.close()
asyncio.run(main())
