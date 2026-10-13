"""Unique real owner-runtime canary; never publish or reuse prior operation receipts."""
import asyncio
import json
import uuid
from datetime import UTC, datetime

from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.tokens import read_token
from doxagent.site_strategy.client import SiteAccessClient, SiteManagedBrowser
from doxagent.content_enrichment.pipeline import ArticlePipeline
from doxagent.content_enrichment.managed import SiteAccessTransport, SiteAccessBrowserReader
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
from doxagent.message_bus_v2.industry_sources import (
    parse_digitimes_listing, parse_barrons_ticker_listing, parse_trendforce_listing,
    parse_shared_feed, normalize_browser_feed,
)


async def main():
    settings = DoxAgentSettings()
    client = SiteAccessClient(settings.site_access_url, token=read_token(
        settings.site_access_worker_token, settings.site_access_worker_token_file))
    browser = SiteManagedBrowser(client)
    transport = SiteAccessTransport(client)
    pipeline = ArticlePipeline(transport, browser=SiteAccessBrowserReader(client, transport),
        site_client=client, pipeline_version='body_v2.2')
    nonce = uuid.uuid4().hex
    entries = [
        ('barrons', 'https://www.barrons.com/market-data/stocks/mu', 'builtin:barrons_ticker@1'),
        ('digitimes', 'https://www.digitimes.com/topic/semiconductors/', None),
        ('trendforce_news', 'https://www.trendforce.com/news/', None),
        ('trendforce_press', 'https://www.trendforce.com/presscenter/news', None),
        ('globe', 'https://www.globenewswire.com/RssFeed/industry/9576-Semiconductors/feedTitle/GlobeNewswire%20-%20Industry%20News%20on%20Semiconductors', None),
    ]
    for name, url, recipe in entries:
        try:
            status, _, _, body = await browser.get(url, remaining_budget_ms=45000,
                recipe_ref=recipe, operation_id=f'acceptance-{nonce}-{name}')
            now = datetime.now(UTC)
            if name == 'barrons':
                rows = [r for r in parse_barrons_ticker_listing(body, ticker='MU', now=now)
                        if 'barrons.com/' in r.url]
            elif name == 'digitimes':
                rows = parse_digitimes_listing(body, now=now)
            elif name == 'globe':
                rows = parse_shared_feed(normalize_browser_feed(body), feed_url=url,
                    publisher='GlobeNewswire', language='en', now=now)
            else:
                rows = parse_trendforce_listing(body, press=name.endswith('press'), now=now)
            print(json.dumps({'at': now.isoformat(), 'source': name,
                'listing_status': status, 'listing_rows': len(rows)}), flush=True)
            for index, message in enumerate(rows[:2]):
                identifier = f'acceptance-{nonce}-{name}-{index}'
                record = MediaEnrichmentRecord(identifier, identifier, name, 'MU',
                    message.title, None, message.url)
                async with asyncio.timeout(100):
                    result = await pipeline.extract(record)
                print(json.dumps({'source': name, 'url': message.url,
                    'succeeded': result.succeeded, 'reason': result.reason,
                    'outcome': result.diagnostics.get('outcome'),
                    'content_length': result.to_payload().get('content_length')}), flush=True)
        except Exception as exc:
            print(json.dumps({'source': name, 'error': type(exc).__name__,
                'reason': str(exc)[:160]}), flush=True)
    await client.close()


asyncio.run(main())
