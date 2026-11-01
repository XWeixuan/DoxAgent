import asyncio
import contextlib
import hashlib
import json
from pathlib import Path
import httpx
from doxagent.message_bus_v2.google_news import resolve_google_news_urls

D=Path(__file__).parent
OLD=D.parent/'google_news_body_whitelist_20261102'
SITES={'stocktitan.net','moomoo.com','futunn.com','gurufocus.com','techpowerup.com','thestreet.com'}

async def main():
    original=json.loads((OLD/'cohort.json').read_text(encoding='utf-8'))
    seen={r['guid'] for r in original}
    seen_urls={r['url'] for r in original}
    urls={}
    for folder in ('google_news_five_ticker_20261028','google_news_five_ticker_company_only_20261028'):
        for p in (D.parent/folder/'bodies').glob('*.json'):
            r=json.loads(p.read_text(encoding='utf-8'));urls[r['id']]=r.get('url')
    candidates={}
    for folder in ('google_news_cohr_mu_governance_20261028','google_news_five_ticker_company_only_20261028','google_news_five_ticker_20261028','google_news_blacklist_audit_20261027'):
        value=json.loads((D.parent/folder/'articles.json').read_text(encoding='utf-8'))
        for r in value.get('articles',[]) if isinstance(value,dict) else value:
            if r.get('site') not in SITES:continue
            guid=r.get('guid') or (r.get('guids') or [None])[0]
            if not guid or guid in seen:continue
            rid=hashlib.sha256(guid.encode()).hexdigest()[:20]
            url=urls.get(rid) or r.get('resolved_url')
            if url in seen_urls:continue
            candidates[guid]=dict(id=rid,site=r['site'],guid=guid,title=r['title'],url=url,wrapper_url=r.get('wrapper_url'),cohort='holdout')
    chosen=[]
    for site in sorted(SITES):
        group=sorted((r for r in candidates.values() if r['site']==site),key=lambda r:r['id'])
        chosen+=group[:5]
    gate=asyncio.Semaphore(3)
    @contextlib.asynccontextmanager
    async def permit():
        async with gate:yield
    async with httpx.AsyncClient(timeout=9,follow_redirects=True) as client:
        mappings=await resolve_google_news_urls(client,[r['wrapper_url'] for r in chosen if not r['url'] and r['wrapper_url']],permit)
    for r in chosen:r['url']=r['url'] or mappings.get(r['wrapper_url'])
    assert all(r['url'] for r in chosen)
    assert len({r['url'] for r in chosen})==len(chosen)
    all_rows=[{**r,'cohort':'development'} for r in original if r['site'] in SITES]+chosen
    (D/'validation-cohort.json').write_text(json.dumps(all_rows,ensure_ascii=False,indent=2),encoding='utf-8')
    print('development',len(all_rows)-len(chosen),'holdout',len(chosen))

asyncio.run(main())
