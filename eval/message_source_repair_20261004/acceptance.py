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
from doxagent.message_bus_v2.market_sources import MarketTickerAdapter
from doxagent.message_bus_v2.news_adapters import ReutersSiteSearchAdapter
from doxagent.message_bus_v2.schema import PollContext, SharedPollContext, TickerSourceBinding
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.client import SiteAccessClient, SiteManagedBrowser
from doxagent.site_strategy.tokens import read_token


@asynccontextmanager
async def permit():
    yield


def public_identity(settings, rollback=False):
    token = read_token(settings.site_access_admin_token, settings.site_access_admin_token_file)
    with httpx.Client(
        base_url="http://127.0.0.1:8011", timeout=30, headers={"Authorization": "Bearer " + token}
    ) as api:
        response = api.get("/v1/identities/public-markets-nl")
        if response.status_code == 404 and not rollback:
            original = api.get("/v1/identities/digitimes-nl-1").json()["spec"]
            profile_response = api.put(
                "/v1/profiles/public-markets-nl",
                json={
                    "profile_id": "public-markets-nl",
                    "site_id": "investorshub",
                    "bound_egress_id": original["egress_id"],
                    "directory_key": "public-markets-nl",
                    "environment": original["environment"],
                },
            )
            profile_response.raise_for_status()
            original.update(
                identity_id="public-markets-nl", profile_id="public-markets-nl", revision=0
            )
            original["lifecycle"]["residency"] = "always_on"
            response = api.post(
                "/v1/identities:apply",
                json={
                    "spec": original,
                    "expected_revision": None,
                    "actor": "source-repair-20261004",
                },
            )
        response.raise_for_status()
        target = "digitimes-nl-1" if rollback else "public-markets-nl"
        previous = "public-markets-nl" if rollback else "digitimes-nl-1"
        for site_id in ("investorshub", "investing"):
            spec = api.get(f"/v1/sites/{site_id}").json()
            combo = next(
                c
                for c in spec["access"]["combinations"]
                if c.get("id", c.get("combination_id")) == f"{site_id}-external-nl"
            )
            if combo["identity_id"] == target:
                continue
            if combo["identity_id"] != previous:
                raise RuntimeError("unexpected current identity; refusing configuration overwrite")
            combo["identity_id"] = target
            response = api.post(
                "/v1/sites:apply",
                json={
                    "spec": spec,
                    "expected_revision": spec["revision"],
                    "actor": "source-repair-20261004",
                },
            )
            response.raise_for_status()
            print(json.dumps({"site": site_id, "identity": target}), flush=True)


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
    try:
        if source_id == "digitimes_semiconductors":
            result = await IndustryListingAdapter(browser, "digitimes").poll_shared(
                SharedPollContext(
                    run_id="repair-canary", source=source, requested_at=now, request_permit=permit
                )
            )
        else:
            adapter = (
                ReutersSiteSearchAdapter(browser)
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
        for m in result.messages[:body_samples]:
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
    parser.add_argument("--public-identity", action="store_true")
    parser.add_argument("--rollback-public-identity", action="store_true")
    args = parser.parse_args()
    if args.public_identity or args.rollback_public_identity:
        public_identity(DoxAgentSettings(), args.rollback_public_identity)
    else:
        asyncio.run(smoke(args.source, args.ticker, args.body_samples))
