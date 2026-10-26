"""Compare public JSON and existing Chrome channel membership, without publication."""
import asyncio
import json
from datetime import UTC, datetime

from curl_cffi.requests import AsyncSession

from doxagent.message_bus_v2.barrons_sources import render_news_feeds
from doxagent.message_bus_v2.http_routes import HTTP_EGRESSES
from doxagent.message_bus_v2.industry_sources import parse_barrons_ticker_listing
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.schema import AccessMode, AccessRequest, SitePurpose
from doxagent.site_strategy.tokens import read_token


async def main():
    settings = DoxAgentSettings()
    client = SiteAccessClient(settings.site_access_url, token=read_token(
        settings.site_access_worker_token, settings.site_access_worker_token_file))
    try:
        async with AsyncSession(impersonate="chrome", trust_env=False) as session:
            for channel in ("Barrons", "BarronsDowJonesNetwork"):
                now = datetime.now(UTC)
                url = ("https://www.barrons.com/market-data/api/news?channel="+channel+
                       "&chartingSymbol=stock%2F%2F%2Fmu&pageNumber=0&count=40")
                response = await session.get(url, proxy=HTTP_EGRESSES["us-standard-5"], timeout=8)
                html = render_news_feeds([{"status": response.status_code, "text": response.text}], channels=[channel])
                public = parse_barrons_ticker_listing(html, ticker="MU", now=now)
                result = await client.execute(AccessRequest(operation_id="plan-compare-20261026:"+channel,
                    origin_scope_id="source:barrons_ticker_news", purpose=SitePurpose.CRAWLER,
                    url="https://www.barrons.com/market-data/stocks/mu", mode=AccessMode.BROWSER,
                    recipe_ref="builtin:barrons_ticker@1", recipe_parameters={"channel": channel},
                    remaining_budget_ms=25_000))
                native = parse_barrons_ticker_listing(result.body, ticker="MU", now=now)
                left, right = {r.url for r in public}, {r.url for r in native}
                print(json.dumps({"at": now.isoformat(), "channel": channel,
                    "public": len(left), "chrome": len(right), "common": len(left & right),
                    "missing_from_public": len(right-left), "additional_public": len(left-right),
                    "public_oldest": min(r.published_at for r in public).isoformat(),
                    "chrome_oldest": min(r.published_at for r in native).isoformat()}), flush=True)
    finally:
        await client.close()


asyncio.run(main())
