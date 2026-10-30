"""Local disposable browser only; no production Profile/CDP mutation."""
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path('eval/yahoo-realtime-20261031/browser')


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(locale='en-US')
        traffic = []

        async def record(response):
            if 'nexus-finance-prod.media.yahoo.com' not in response.url:
                return
            request = response.request
            body = request.post_data_json if request.post_data else {}
            if (body.get('query') or '').startswith('query FinancePolarisTicker'):
                (OUT/f'native-{len(traffic)}.json').write_text(await response.text(), encoding='utf-8')
            traffic.append({'status': response.status, 'variables': body.get('variables'),
                            'query_name': (body.get('query') or '')[:90]})

        page.on('response', record)
        for ticker in ('INTC', 'MU'):
            start = len(traffic)
            url = f'https://finance.yahoo.com/quote/{ticker}/latest-news/'
            response = await page.goto(url, wait_until='domcontentloaded', timeout=30000)
            await page.wait_for_selector('[data-testid="storyitem"]', timeout=10000)

            async def cards():
                return await page.locator('[data-testid="storyitem"]').evaluate_all('''els=>els.map(e=>{
                  const a=e.querySelector('a:has(h3),a:has(h2)');return {text:e.innerText, url:a?.href,
                   tag:a?.dataset.yga};})''')

            first = await cards()
            fetched = await page.evaluate('''async url=>{const r=await fetch(url,{cache:'no-cache',credentials:'include'});
                return {status:r.status, html:await r.text(), headers:Object.fromEntries(r.headers)};}''', url)
            (OUT/f'{ticker}-origin.html').write_text(fetched.pop('html'), encoding='utf-8')
            for _ in range(3):
                await page.evaluate('window.scrollTo(0,document.body.scrollHeight)')
                await asyncio.sleep(1)
            after = await cards()
            results.append({'ticker': ticker, 'observed_at': datetime.now(UTC).isoformat(),
                            'navigation_status': response.status, 'first': first, 'after_scroll': after,
                            'origin_fetch': fetched, 'graphql_traffic': traffic[start:]})
        await browser.close()
    (OUT/'comparison.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps([{'ticker': x['ticker'], 'status': x['navigation_status'], 'first': len(x['first']),
                       'after': len(x['after_scroll']), 'graphql': x['graphql_traffic']} for x in results]))


asyncio.run(main())
