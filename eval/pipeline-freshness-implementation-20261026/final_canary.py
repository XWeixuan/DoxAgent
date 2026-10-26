"""Four scoped, acquisition-only 30s probe rounds; no Bus configuration/publication."""
import asyncio
import json
import time
from datetime import UTC, datetime

from doxagent.message_bus_v2.industry_sources import parse_barrons_ticker_listing
from doxagent.message_bus_v2.market_sources import parse_investing, parse_investorshub
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.schema import AccessMode, AccessRequest, SitePurpose
from doxagent.site_strategy.tokens import read_token


async def main():
    settings = DoxAgentSettings()
    client = SiteAccessClient(settings.site_access_url, token=read_token(
        settings.site_access_worker_token, settings.site_access_worker_token_file))
    cases = [
        ("barrons_ticker_news", "MU", "HEAD", "https://www.barrons.com/market-data/stocks/mu",
         "builtin:barrons_ticker@1", {"channel": "Barrons"}),
        ("barrons_ticker_news", "MU", "OTHER_HEAD", "https://www.barrons.com/market-data/stocks/mu",
         "builtin:barrons_ticker@1", {"channel": "BarronsDowJonesNetwork"}),
        ("investorshub_ticker_news", "MU", "HEAD", "https://investorshub.advfn.com/stock-market/NASDAQ/MU/news",
         "builtin:investorshub_ticker@1", {"ticker": "MU"}),
        ("investing_ticker_news", "INTC", "HEAD", "https://www.investing.com/equities/intel-corp-news",
         "builtin:investing_ticker@1", {"ticker": "INTC"}),
    ]
    origin = time.monotonic()

    async def one(case, round_number):
        source, ticker, unit, url, recipe, params = case
        started = datetime.now(UTC)
        out = {"round": round_number, "source_id": source, "ticker": ticker, "unit": unit,
               "dispatched_at": started.isoformat()}
        try:
            result = await client.execute(AccessRequest(
                operation_id=f"final-canary-20261026:{source}:{unit}:{round_number}",
                origin_scope_id="source:" + source, purpose=SitePurpose.CRAWLER,
                url=url, mode=AccessMode.BROWSER, recipe_ref=recipe, recipe_parameters={**params,"_poll_due_at":started.isoformat()},
                remaining_budget_ms=25_000,
            ))
            out.update(disposition=result.disposition.value, reason=result.reason_code,
                       identity_id=result.identity_id, queue_wait_ms=result.queue_wait_ms,
                       network_ms=result.network_ms, stage_timings=result.stage_timings,
                       capture_acquired_at=result.capture_acquired_at.isoformat() if result.capture_acquired_at else None,
                       served_from_shared_capture=result.served_from_shared_capture)
            if result.disposition.value == "SUCCESS":
                parser = parse_barrons_ticker_listing if source.startswith("barrons") else (
                    parse_investorshub if source.startswith("investorshub") else parse_investing)
                out["message_count"] = len(parser(result.body, ticker=ticker, now=started))
        except Exception as exc:
            out.update(error_type=type(exc).__name__)
        out["completed_at"] = datetime.now(UTC).isoformat()
        print(json.dumps(out), flush=True)

    try:
        for round_number in range(3):
            await asyncio.sleep(max(0, origin + round_number*30 - time.monotonic()))
            await asyncio.gather(*(one(case, round_number) for case in cases))
    finally:
        await client.close()


asyncio.run(main())
