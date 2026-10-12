"""Production canary: use the real owner API; never publish historical samples."""

from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx

from doxagent.content_enrichment.extractor import SharedContentExtractor
from doxagent.message_bus_v2.industry_sources import IndustryListingAdapter
from doxagent.message_bus_v2.manifests import initial_sources
from doxagent.message_bus_v2.market_sources import MarketTickerAdapter, GlobeNewswireSearchAdapter
from doxagent.message_bus_v2.news_adapters import ReutersSiteSearchAdapter
from doxagent.message_bus_v2.schema import PollContext, SharedPollContext, TickerSourceBinding
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.client import SiteAccessClient, SiteManagedBrowser
from doxagent.site_strategy.tokens import read_token


@asynccontextmanager
async def permit():
    yield


async def smoke(source_id, ticker, body_samples=2):
    settings = DoxAgentSettings()
    site = SiteAccessClient(
        settings.site_access_url,
        token=read_token(settings.site_access_worker_token, settings.site_access_worker_token_file),
    )
    browser = SiteManagedBrowser(site)
    extractor = SharedContentExtractor(site_access_client=site)
    source = next(s for s in initial_sources() if s.source_id == source_id)
    now = datetime.now(UTC)
    query_plan = None
    if source_id in ("reuters_site_search", "globenewswire_search"):
        import sqlite3
        from doxagent.message_bus_v2.monitoring_terms import TickerMonitoringTerms
        from doxagent.message_bus_v2.search_plan import build_query_plan
        with sqlite3.connect("file:/data/bus/bus.sqlite3?mode=ro", uri=True) as db:
            row = db.execute("SELECT r.revision,r.config_json FROM ticker_monitoring_term_revisions r "
                "JOIN ticker_monitoring_terms t ON t.ticker=r.ticker "
                "AND t.current_revision=r.revision WHERE t.ticker=?", (ticker,)).fetchone()
        if row:
            query_plan = build_query_plan(source, TickerMonitoringTerms.model_validate_json(row[1]), row[0]).model_dump(mode="json")
    try:
        if source_id in ("digitimes_semiconductors", "trendforce_news", "trendforce_press_releases"):
            result = await IndustryListingAdapter(browser, {"digitimes_semiconductors": "digitimes", "trendforce_news": "trendforce_news", "trendforce_press_releases": "trendforce_press"}[source_id]).poll_shared(
                SharedPollContext(
                    run_id="repair-canary", source=source, requested_at=now, request_permit=permit
                )
            )
        else:
            adapter = (
                GlobeNewswireSearchAdapter(browser)
                if source_id == "globenewswire_search"
                else ReutersSiteSearchAdapter(browser)
                if source_id == "reuters_site_search"
                else IndustryListingAdapter(browser, "barrons")
                if source_id == "barrons_ticker_news"
                else MarketTickerAdapter(
                    browser, "investorshub" if "investorshub" in source_id else "investing"
                )
            )
            result = await adapter.poll(
                PollContext(
                    ticker=ticker,
                    source=source,
                    binding=TickerSourceBinding(
                        binding_id=f"{ticker}:{source_id}",
                        ticker=ticker,
                        source_id=source_id,
                        source_parameters={"max_pages": 1},
                    ),
                    requested_at=now,
                    request_permit=permit,
                    query_plan=query_plan,
                )
            )
        print(
            json.dumps(
                {
                    "source": source_id,
                    "ticker": ticker,
                    "count": len(result.messages),
                    "coverage": result.window_coverage,
                    "metadata": result.acquisition_metadata,
                    "failures": [
                        {
                            "code": f.error_code,
                            "reason": f.error_message,
                            "evidence": f.original_payload,
                        }
                        for f in result.failures
                    ],
                    "samples": [
                        {"title": m.title, "url": m.url, "published_at": str(m.published_at)}
                        for m in result.messages[:2]
                    ],
                }
            ),
            flush=True,
        )
        for m in sorted(result.messages, key=lambda m: m.published_at, reverse=True)[:body_samples]:
            outcome = await extractor.extract_version(
                MediaEnrichmentRecord(
                    standard_message_id="repair-canary",
                    raw_message_id="repair-canary",
                    source_id=source_id,
                    ticker=ticker,
                    title=m.title,
                    body=None,
                    url=m.url,
                ),
                "body_v2.2",
            )
            print(
                json.dumps(
                    {
                        "source": source_id,
                        "url": m.url,
                        "ok": outcome.succeeded,
                        "characters": len(outcome.content or ""),
                        "reason": outcome.reason,
                    }
                ),
                flush=True,
            )
    finally:
        await extractor.close()
        await site.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", nargs="?", default="barrons_ticker_news")
    parser.add_argument("--ticker", default="MU")
    parser.add_argument("--body-samples", type=int, default=2)
    args = parser.parse_args()
    asyncio.run(smoke(args.source, args.ticker, args.body_samples))
