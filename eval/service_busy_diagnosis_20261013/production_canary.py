"""Exercise the existing Site Access owner, never attach another controller."""

import asyncio
import json
import urllib.request
from datetime import UTC, datetime

import httpx

from doxagent.content_enrichment.managed import SiteAccessBrowserReader, SiteAccessTransport
from doxagent.content_enrichment.pipeline import ArticlePipeline
from doxagent.message_bus_v2.industry_sources import parse_barrons_ticker_listing
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.client import SiteAccessClient, SiteManagedBrowser
from doxagent.site_strategy.tokens import read_token


def targets():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    result = {}
    for port in (9222, 9223, 9224, 9226, 9227, 9228):
        try:
            pages = json.load(opener.open(f"http://127.0.0.1:{port}/json/list", timeout=3))
            pages = [p for p in pages if p["type"] == "page"]
            result[port] = {
                "pages": len(pages),
                "blank": sum(p["url"] == "about:blank" for p in pages),
                "owned_allocations": sum(
                    p["url"].startswith("about:blank#doxagent-") for p in pages
                ),
            }
        except Exception as exc:
            result[port] = type(exc).__name__
    return result


async def main():
    settings = DoxAgentSettings()
    print(
        json.dumps({"at": datetime.now(UTC).isoformat(), "targets_before": targets()}), flush=True
    )
    client = SiteAccessClient(
        "http://127.0.0.1:8011",
        token=read_token(settings.site_access_worker_token, settings.site_access_worker_token_file),
    )
    try:
        browser = SiteManagedBrowser(client)
        status, _, _, body = await browser.get(
            "https://www.barrons.com/market-data/stocks/mu",
            remaining_budget_ms=45000,
            recipe_ref="builtin:barrons_ticker@1",
            operation_id="lifecycle-acceptance-20261013-barrons",
        )
        rows = parse_barrons_ticker_listing(body, ticker="MU", now=datetime.now(UTC))
        print(json.dumps({"listing_status": status, "listing_rows": len(rows)}), flush=True)
        if rows:
            message = next((r for r in rows if "barrons.com/articles/" in r.url), rows[0])
            transport = SiteAccessTransport(client)
            pipeline = ArticlePipeline(
                transport,
                browser=SiteAccessBrowserReader(client, transport),
                site_client=client,
                pipeline_version="body_v2.2",
            )
            record = MediaEnrichmentRecord(
                "acceptance", "acceptance", "barrons", "MU", message.title, None, message.url
            )
            async with asyncio.timeout(100):
                result = await pipeline.extract(record)
            print(
                json.dumps(
                    {
                        "body_outcome": result.diagnostics.get("outcome"),
                        "body_result": result.to_payload(),
                    }
                ),
                flush=True,
            )
    except Exception as exc:
        print(
            json.dumps({"canary_error": type(exc).__name__, "reason": str(exc)[:180]}), flush=True
        )
    finally:
        await client.close()
    async with httpx.AsyncClient(
        trust_env=False,
        timeout=10,
        headers={
            "Authorization": "Bearer "
            + read_token(settings.site_access_admin_token, settings.site_access_admin_token_file)
        },
    ) as admin:
        diag = (await admin.get("http://127.0.0.1:8011/v1/diagnostics/access")).json()
    print(json.dumps({"diagnostics": diag, "targets_after": targets()}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
