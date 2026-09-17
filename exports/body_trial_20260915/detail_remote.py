import asyncio,json,os,gzip,time
from pathlib import Path
from playwright.async_api import async_playwright
from doxagent.monitoring.media_enrichment import _default_session_factory,DomainFetchController
from doxagent.content_enrichment.transport import PublicTransport,DEADLINE
from doxagent.content_enrichment.quality import inspect_html,inspect_reader,choose_candidate
async def main():
 out=Path('/tmp/body-detail');out.mkdir(exist_ok=True)
 rows=json.loads(Path('/tmp/body-trial-input.json').read_text())
 hosts={'www.benzinga.com','247wallst.com','www.investopedia.com','investorshub.advfn.com','www.thestreet.com','www.cnbc.com','finance.yahoo.com'}
 chosen=[];seen=set()
 for r in rows:
  host=r['url'].split('/')[2]
  if host in hosts and (host,r['prior_reason']) not in seen:
   seen.add((host,r['prior_reason']));chosen.append(r)
 async with async_playwright() as p, _default_session_factory()() as s:
  b=await p.chromium.connect_over_cdp(os.environ['DOXAGENT_CRAWLER_PLANE_BROWSER_CDP_URL']);ctx=b.contexts[0]
  for r in chosen:
   url=r['url'];name=r['id']; page=await ctx.new_page();tok=DEADLINE.set(time.monotonic()+80)
   try:
    response=await page.goto(url,wait_until='domcontentloaded',timeout=25000)
    await page.wait_for_timeout(8000)
    html=await page.content();(out/(name+'.html.gz')).write_bytes(gzip.compress(html.encode()))
    i=inspect_html(html,page.url,r['title']); c,o,w=choose_candidate(i,r['title']);print(name,'render',await page.title(),w,len(c.text) if c else 0,flush=True)
   except Exception as e:print(name,type(e).__name__,flush=True)
   finally:await page.close();DEADLINE.reset(tok)
   if host!='www.cnbc.com':
    a=[];obs=await PublicTransport(s,DomainFetchController()).fetch('https://r.jina.ai/'+url,a,phase='reader',publisher_url=url)
    (out/(name+'.reader.gz')).write_bytes(gzip.compress(obs.text.encode()))
    i=inspect_reader(obs.text,url,r['title']);c,o,w=choose_candidate(i,r['title']);print(name,'reader',obs.status,w,len(c.text) if c else 0,flush=True)
asyncio.run(main())
