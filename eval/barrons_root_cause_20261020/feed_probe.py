"""Exercise candidate same-origin capture without changing the installed service."""
import subprocess

CODE = r'''
import asyncio,json,os,time,importlib.util
from datetime import UTC,datetime
from playwright.async_api import async_playwright
from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient
from doxagent.message_bus_v2.industry_sources import parse_barrons_ticker_listing
spec=importlib.util.spec_from_file_location('candidate','/tmp/barrons_sources.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
async def main():
 status=await ChromeSupervisorClient(os.environ['DOXAGENT_SITE_ACCESS_CHROME_SUPERVISOR_SOCKET'],owner_id='candidate-probe').status('dowjones-main')
 p=await async_playwright().start();page=None;out={}
 try:
  b=await p.chromium.connect_over_cdp(status['cdp_http_url'],no_defaults=True,timeout=15000)
  page=await b.contexts[0].new_page();start=time.monotonic()
  r=await page.goto('https://www.barrons.com/market-data/stocks/mu',wait_until='commit',timeout=12000)
  out['status']=r.status
  html=await module.capture_barrons_ticker_news(page,page.url)
  rows=parse_barrons_ticker_listing(html,ticker='MU',now=datetime.now(UTC))
  out.update(seconds=time.monotonic()-start,rows=len(rows),sections={s:sum(r.raw_payload.get('section')==s for r in rows) for s in ('barrons','other_dow_jones')},samples=[{'title':r.title,'url':r.url,'date':r.published_at.isoformat()} for r in rows[:3]])
 except Exception as e:out.update(error=type(e).__name__,reason=str(e)[:220])
 finally:
  if page:
   try:
    async with asyncio.timeout(3):await page.close()
   except Exception:pass
  await p.stop()
 print(json.dumps(out))
asyncio.run(main())
'''
subprocess.run(['docker','cp','/tmp/barrons_sources.py','doxagent-v2-v2-site-access-1:/tmp/barrons_sources.py'],check=True,capture_output=True)
p=subprocess.run(['docker','exec','-i','doxagent-v2-v2-site-access-1','python','-'],input=CODE,text=True,capture_output=True,timeout=50)
print(p.stdout)
print(p.stderr[-900:])
assert p.returncode==0
