"""Bounded public-engine experiments; keys remain in process environment only."""
import asyncio
import base64
import json
import os
import time
from pathlib import Path

import httpx
import argparse

from doxagent.content_enrichment.quality import choose_candidate, inspect_html, inspect_reader

D = Path(__file__).parent
OLD = D.parent / 'google_news_body_whitelist_20261102'
SITES = ('futunn.com', 'moomoo.com', 'gurufocus.com', 'techpowerup.com', 'thestreet.com', 'stocktitan.net')


async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--http-only',action='store_true');parser.add_argument('--zyte-browser-only',action='store_true');parser.add_argument('--sites');parser.add_argument('--label');args=parser.parse_args()
    cohort = json.loads((OLD / 'cohort.json').read_text(encoding='utf-8'))
    out = []
    jina_key = os.getenv('JINA_API_KEY')
    zyte_key = os.getenv('ZYTE_API_KEY')
    gate = asyncio.Semaphore(2)
    async with httpx.AsyncClient(timeout=18, follow_redirects=True) as client:
        async def one(row, engine):
            async with gate:
                value = {k: row[k] for k in ('id', 'site', 'title', 'url')}
                start = time.monotonic()
                try:
                    if engine.startswith('jina-'):
                        headers = {'X-Engine': engine[5:], 'X-No-Cache': 'true'}
                        if jina_key:
                            headers['Authorization'] = 'Bearer ' + jina_key
                        response = await client.get('https://r.jina.ai/' + row['url'], headers=headers)
                        body = response.text
                        inspect = inspect_reader
                        final_url = row['url']
                    else:
                        use_browser = engine == 'zyte-browser'
                        response = await client.post('https://api.zyte.com/v1/extract', auth=(zyte_key, ''), json={'url': row['url'], ('browserHtml' if use_browser else 'httpResponseBody'): True})
                        payload = response.json()
                        body = payload.get('browserHtml') or (base64.b64decode(payload['httpResponseBody']).decode('utf-8', errors='replace') if payload.get('httpResponseBody') else '')
                        inspect = inspect_html
                        final_url = payload.get('url') or row['url']
                        value['price'] = response.headers.get('zyte-api-response-price')
                        value['target_status'] = payload.get('statusCode')
                    value.update(status=response.status_code, text=body, final_url=final_url)
                    if response.status_code == 200:
                        info = await asyncio.to_thread(inspect, body, final_url, row['title'])
                        c, outcome, reason = choose_candidate(info, row['title'])
                        value.update(outcome=outcome, reason=reason, chars=len(c.text) if c else 0, content=c.text if c else None)
                    else:
                        value.update(outcome='UNAVAILABLE', reason='provider_http_' + str(response.status_code))
                except Exception as exc:
                    value.update(outcome='UNAVAILABLE', reason=type(exc).__name__)
                value.update(engine=engine, elapsed_ms=int((time.monotonic() - start) * 1000))
                out.append(value)
                (D / ((args.label+'.json') if args.label else 'zyte-http-results.json' if args.http_only else 'engine-results.json')).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
                print(row['site'], engine, value['outcome'], value['reason'], value['elapsed_ms'], flush=True)
                await asyncio.sleep(1)
                return value

        cf_available = True
        for site in (args.sites.split(',') if args.sites else ('gurufocus.com','techpowerup.com','thestreet.com') if args.http_only else SITES):
            group = [r for r in cohort if r['site'] == site][:2]
            if args.sites and (D/'reader-validation.json.gz').exists():
                import gzip
                prior=json.load(gzip.open(D/'reader-validation.json.gz','rt',encoding='utf-8'))
                failed={r['id'] for r in prior if r['site']==site and r['outcome']!='FULL'}
                group=([r for r in cohort if r['site']==site and r['id'] in failed]+[r for r in cohort if r['site']==site and r['id'] not in failed])[:2]
            if args.http_only or args.zyte_browser_only:
                await asyncio.gather(*(one(r,'zyte-browser' if args.zyte_browser_only else 'zyte-http') for r in group))
                continue
            first = await asyncio.gather(*(one(r, 'jina-browser') for r in group))
            if args.sites:
                if not all(r['outcome']=='FULL' for r in first):
                    await asyncio.gather(*(one(r,'zyte-http') for r in group))
                continue
            if cf_available and not all(r['outcome'] == 'FULL' for r in first):
                cf = await one(group[0], 'jina-cf-browser-rendering')
                cf_available = cf.get('status') not in (400, 401)
            if zyte_key and not all(r['outcome'] == 'FULL' for r in first):
                await asyncio.gather(*(one(r, 'zyte-http' if site == 'stocktitan.net' else 'zyte-browser') for r in group))
    print('finished', len(out), 'key_present', bool(zyte_key), flush=True)


asyncio.run(main())
