"""Read-only regex + real Jev acceptance on one public GlobeNewswire article.

Uses only current subscribers and their current definitions. No publication.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import httpx

from doxagent.content_enrichment.extractor import SharedContentExtractor
from doxagent.message_bus_v2.industry_sources import SharedFeedAdapter
from doxagent.message_bus_v2.jev import JevClient
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
from doxagent.message_bus_v2.relevance import regex_relevant
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import SharedPollContext, utc_now
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.tokens import read_token


@asynccontextmanager
async def permit():
    yield


async def main():
    settings = DoxAgentSettings()
    repo = MessageBusV2Repository(settings.message_bus_v2_sqlite_path)
    source = repo.get_source("globenewswire_semiconductors_rss")
    assert source is not None
    manager = MonitoringTermsService(repo)
    definitions = {
        binding.ticker: found[1]
        for binding in repo.list_bindings(source_id=source.source_id)
        if binding.enabled and (found := manager.get(binding.ticker)) is not None
    }
    site = SiteAccessClient(
        settings.site_access_url,
        token=read_token(settings.site_access_worker_token, settings.site_access_worker_token_file),
    )
    extractor = SharedContentExtractor(site_access_client=site)
    assert settings.message_bus_jev_enabled and settings.openrouter_api_key
    jev = JevClient(
        settings.openrouter_api_key.get_secret_value(),
        timeout=settings.message_bus_jev_timeout_seconds,
    )
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            result = await SharedFeedAdapter(client, "GlobeNewswire").poll_shared(
                SharedPollContext(
                    run_id="read-only-distribution-acceptance",
                    source=source,
                    requested_at=utc_now(),
                    request_permit=permit,
                )
            )
        message = result.messages[0]
        body = await extractor.extract_version(
            MediaEnrichmentRecord(
                standard_message_id="read-only",
                raw_message_id="read-only",
                source_id=source.source_id,
                ticker="MU",
                title=message.title,
                body=None,
                url=message.url,
            ),
            "body_v2.2",
        )
        assert body.succeeded, body.reason
        message = message.model_copy(update={"body": body.content})
        regex_hits = {
            ticker: regex_relevant(terms, source.content_language or "en", message)
            for ticker, terms in definitions.items()
        }
        answers = await jev.classify(message, definitions)
        print(
            json.dumps(
                {
                    "source": source.source_id,
                    "title": message.title,
                    "url": message.url,
                    "body_characters": len(body.content or ""),
                    "subscribers": list(definitions),
                    "regex": regex_hits,
                    "jev": answers,
                    "published": False,
                },
            ),
            flush=True,
        )
    finally:
        await jev.close()
        await extractor.close()
        await site.close()
        repo.close()


if __name__ == "__main__":
    asyncio.run(main())
