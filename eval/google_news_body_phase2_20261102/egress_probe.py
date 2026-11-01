"""Probe existing fixed egress listeners without changing any identity's routing."""
import asyncio
import gzip
import json
import time

from curl_cffi.requests import AsyncSession
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.content_enrichment.quality import inspect_html, choose_candidate


async def main():
    s = DoxAgentSettings()
    repo = SiteStrategyRepository(s.site_access_sqlite_path)
    rows = json.load(open('/tmp/google-phase2-cohort.json'))
    selected = [r for r in rows if r['site'] in ('stocktitan.net', 'moomoo.com', 'futunn.com', 'gurufocus.com', 'techpowerup.com', 'thestreet.com') and r['case_number'] == min(x['case_number'] for x in rows if x['site'] == r['site'])]
    out = []
    gate = asyncio.Semaphore(2)
    for identity in ('server-direct', 'jp-standard-6', 'de-standard-1', 'tr-standard-1'):
        egress = repo.get_egress(identity)
        if not egress or not egress.enabled:
            continue
        async with AsyncSession(impersonate='chrome', proxy=None if identity == 'server-direct' else egress.endpoint, timeout=9) as client:
            async def one(r):
                async with gate:
                    started = time.monotonic()
                    value = {k: r[k] for k in ('id', 'site', 'title', 'url')}
                    try:
                        response = await client.get(r['url'], allow_redirects=True)
                        info = await asyncio.to_thread(inspect_html, response.text, response.url, r['title'])
                        c, o, reason = choose_candidate(info, r['title'])
                        value.update(status=response.status_code, outcome=o, reason=reason, html=response.text, final_url=response.url, chars=len(c.text) if c else 0)
                    except Exception as exc:
                        value.update(outcome='UNAVAILABLE', reason=type(exc).__name__)
                    value.update(egress=identity, observed_ip=egress.observed_ip, elapsed_ms=int((time.monotonic()-started)*1000))
                    out.append(value)
            await asyncio.gather(*(one(r) for r in selected))
    with gzip.open('/tmp/google-phase2-egress.json.gz', 'wt', encoding='utf-8') as stream:
        json.dump(out, stream, ensure_ascii=False)
    print(json.dumps([{k:v for k,v in r.items() if k not in ('html','title','url','final_url')} for r in out]))


asyncio.run(main())
