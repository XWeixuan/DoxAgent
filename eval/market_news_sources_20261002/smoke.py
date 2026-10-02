"""Read-only live acceptance: acquisition plus the production body_v2.2 extractor.

Run in an updated service container. Does not publish historical samples, alter
monitoring terms, fill credentials, or resolve challenges automatically.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx

from doxagent.content_enrichment.extractor import SharedContentExtractor
from doxagent.message_bus_v2.industry_sources import SharedFeedAdapter
from doxagent.message_bus_v2.manifests import initial_sources
from doxagent.message_bus_v2.market_sources import GlobeNewswireSearchAdapter, MarketTickerAdapter
from doxagent.message_bus_v2.schema import PollContext, SharedPollContext, TickerSourceBinding
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.client import SiteAccessClient, SiteManagedBrowser
from doxagent.site_strategy.tokens import read_token


@asynccontextmanager
async def permit():
    yield


async def run(source_id: str, ticker: str, query: str, body_samples: int) -> None:
    settings = DoxAgentSettings()
    site = SiteAccessClient(
        settings.site_access_url,
        token=read_token(settings.site_access_worker_token, settings.site_access_worker_token_file),
    )
    browser = SiteManagedBrowser(site)
    source = next(s for s in initial_sources() if s.source_id == source_id)
    extractor = SharedContentExtractor(site_access_client=site)
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            if source.acquisition_mode.value == "by_distribution":
                result = await SharedFeedAdapter(http, "GlobeNewswire").poll_shared(
                    SharedPollContext(
                        run_id="read-only-market-source-smoke",
                        source=source, requested_at=datetime.now(UTC), request_permit=permit
                    )
                )
            else:
                adapter = (
                    GlobeNewswireSearchAdapter(browser)
                    if "search" in source_id
                    else MarketTickerAdapter(
                        browser, "investorshub" if "investorshub" in source_id else "investing"
                    )
                )
                context = PollContext(
                    ticker=ticker,
                    source=source,
                    requested_at=datetime.now(UTC),
                    binding=TickerSourceBinding(
                        binding_id=f"{ticker}:{source_id}",
                        ticker=ticker,
                        source_id=source_id,
                        source_parameters={"max_pages": 1} if "search" in source_id else {},
                    ),
                    request_permit=permit,
                    query_plan={"queries": [{"query": query, "query_key": query}]}
                    if "search" in source_id
                    else None,
                )
                result = await adapter.poll(context)
        print(
            json.dumps(
                {
                    "source": source_id,
                    "ticker": ticker,
                    "captured": len(result.messages),
                    "coverage": result.window_coverage,
                    "site_access_deferred": result.site_access_deferred,
                    "retry_not_before": str(result.site_access_retry_not_before),
                    "failures": [
                        {"code": f.error_code, "message": f.error_message} for f in result.failures
                    ],
                    "samples": [
                        {
                            "title": m.title,
                            "url": m.url,
                            "published_at": m.published_at.isoformat(),
                            "basis": m.publication_time_basis,
                        }
                        for m in result.messages[:body_samples]
                    ],
                }
            ),
            flush=True,
        )
        for message in result.messages[:body_samples]:
            outcome = await extractor.extract_version(
                MediaEnrichmentRecord(
                    standard_message_id="read-only-smoke",
                    raw_message_id="read-only-smoke",
                    source_id=source_id,
                    ticker=ticker,
                    title=message.title,
                    body=None,
                    url=message.url,
                ),
                "body_v2.2",
            )
            print(
                json.dumps(
                    {
                        "source": source_id,
                        "url": message.url,
                        "body_ok": outcome.succeeded,
                        "characters": len(outcome.content or ""),
                        "reason": outcome.reason,
                        "method": outcome.extraction_method,
                        "attempts": [
                            {"phase": a.phase, "reason": a.reason, "status": a.status_code}
                            for a in outcome.attempts
                        ],
                        "publication": outcome.diagnostics.get("publisher_published_at"),
                    }
                ),
                flush=True,
            )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "source": source_id,
                    "ticker": ticker,
                    "error": type(exc).__name__,
                    "reason": str(exc)[:300],
                }
            ),
            flush=True,
        )
        raise
    finally:
        await extractor.close()
        await site.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("--ticker", default="MU")
    parser.add_argument("--query", default="micron")
    parser.add_argument("--body-samples", type=int, default=1)
    args = parser.parse_args()
    asyncio.run(run(args.source, args.ticker, args.query, args.body_samples))
