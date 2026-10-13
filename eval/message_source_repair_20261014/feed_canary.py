"""Real deployed adapter canary; no messages published."""
import asyncio
import json
import traceback
from contextlib import asynccontextmanager

import httpx
from doxagent.message_bus_v2.industry_sources import SharedFeedAdapter, normalize_browser_feed
from xml.etree import ElementTree
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import SharedPollContext
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.client import SiteAccessClient, SiteManagedBrowser
from doxagent.site_strategy.tokens import read_token

@asynccontextmanager
async def permit():
    yield

async def main():
    settings = DoxAgentSettings()
    client = SiteAccessClient(settings.site_access_url, token=read_token(
        settings.site_access_worker_token, settings.site_access_worker_token_file))
    repo = MessageBusV2Repository('/data/bus/bus.sqlite3')
    browser = SiteManagedBrowser(client)
    async with httpx.AsyncClient(follow_redirects=True) as http:
        for source_id in ('globenewswire_semiconductors_rss', 'huggingnews_rss'):
            source = repo.get_source(source_id)
            try:
                result = await SharedFeedAdapter(http, source.display_name,
                    browser=browser).poll_shared(SharedPollContext(
                    run_id='acceptance-20261014-'+source_id,
                    source=source, request_permit=permit))
                print(json.dumps({'source': source_id, 'count': len(result.messages),
                    'metadata': result.acquisition_metadata,
                    'deferred': result.site_access_deferred}), flush=True)
            except Exception:
                traceback.print_exc()
    try:
        status, final, _, body = await browser.get(
            'https://investor.bloomenergy.com/rss/pressrelease.aspx',
            remaining_budget_ms=30000, operation_id='acceptance-20261014-be-ir')
        root = ElementTree.fromstring(normalize_browser_feed(body))
        print(json.dumps({'source': 'BE:newswire_rss', 'browser_status': status,
            'bytes': len(body.encode()), 'final_url': final, 'xml_valid': True,
            'items': len(root.findall('.//item') + root.findall('.//{*}entry'))}), flush=True)
    except Exception as exc:
        print(json.dumps({'source': 'BE:newswire_rss', 'browser_error': type(exc).__name__,
            'reason': str(exc)[:160]}), flush=True)
    await client.close()

asyncio.run(main())
