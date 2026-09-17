import asyncio,json,os
from playwright.async_api import async_playwright
from doxagent.content_enrichment.quality import inspect_html,choose_candidate

URLS={
 'reuters':'https://www.reuters.com/site-search/?query=Micron&offset=0',
 'barchart':'https://www.barchart.com/story/news/4461422/dells-monster-quarter-just-confirmed-microns-biggest-opportunity-is-not-just-hbm',
 'thestreet':'https://www.thestreet.com/investing/billionaire-david-tepper-just-dumped-a-red-hot-ai-stock',
 '247wallst':'https://247wallst.com/investing/2026/08/31/apple-just-raised-mac-and-ipad-prices-20-so-who-is-getting-rich-off-the-shortage/',
 'yahoo':'https://finance.yahoo.com/markets/stocks/articles/3-great-quality-stocks-own-201414086.html'}
async def main():
 async with async_playwright() as pw:
  browser=await pw.chromium.launch(headless=True,proxy={'server':'http://doxagent-egress-clash:7893'})
  context=await browser.new_context()
  try:
   for key,url in URLS.items():
    page=await context.new_page()
    try:
     response=await page.goto(url,wait_until='domcontentloaded',timeout=25000)
     await page.wait_for_timeout(3000);html=await page.content();info=inspect_html(html,page.url,None);candidate,_,_=choose_candidate(info,None)
     print(json.dumps({'site':key,'status':response.status if response else 0,'title':await page.title(),
       'visible_chars':len(await page.locator('body').inner_text()),'access_reason':info.access_reason,
       'candidate_chars':len(candidate.text) if candidate else 0,'method':candidate.method if candidate else None},ensure_ascii=False),flush=True)
    except Exception as exc:print(json.dumps({'site':key,'error':type(exc).__name__}),flush=True)
    finally:await page.close()
  finally:
   await context.close();await browser.close()
asyncio.run(main())
