"""Bounded read-only live listing/BODY canary; never enqueue or publish messages."""
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

from doxagent.message_bus_v2.manifests import initial_sources
from doxagent.message_bus_v2.poll_trace import REQUESTS
from doxagent.message_bus_v2.public_listing import ListingAdapter, endpoint_units
from doxagent.message_bus_v2.schema import SharedPollContext
from doxagent.message_bus_v2.technology_body import TechnologyBodyReader
from doxagent.message_bus_v2.technology_sources import TechnologyHttp
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord


@asynccontextmanager
async def permit():
    yield


async def main():
    source = next(s for s in initial_sources() if s.source_id == "quantum_zeitgeist")
    source = source.model_copy(update={"default_parameters": {"discovery_mode": "http_listing"}})
    http = TechnologyHttp()
    adapter = ListingAdapter(SimpleNamespace(), http)
    reader = TechnologyBodyReader()
    try:
        for unit, recipe in endpoint_units(source).items():
            events = []
            token = REQUESTS.set(events)
            try:
                ctx = SharedPollContext(run_id="read-only-canary", source=source, acquisition_unit=unit,
                                        requested_at=datetime.now(UTC), request_permit=permit)
                result = await adapter.poll_shared(ctx)
                row = {"unit": unit, "url": recipe.url, "count": len(result.messages),
                       "failures": [f.model_dump(mode="json") for f in result.failures],
                       "proofs": len(result.acquisition_metadata.get("validated_request_ids", [])),
                       "requests": [{k: e.get(k) for k in ("request_key", "status_code", "listing_cache", "error_type")} for e in events]}
                if result.messages:
                    m = max(result.messages, key=lambda m: m.published_at)
                    row["latest"] = {"title": m.title, "url": m.url, "published_at": m.published_at.isoformat(), "time_basis": m.publication_time_basis}
                    record = MediaEnrichmentRecord(standard_message_id="canary", raw_message_id="canary", source_id=source.source_id,
                                                   ticker="IONQ", title=m.title, body=None, url=m.url)
                    await asyncio.sleep(2)
                    body = await reader.extract(SimpleNamespace(source=source, message=m), record)
                    row["body"] = {"succeeded": body.succeeded, "outcome": body.diagnostics.get("outcome"),
                                   "chars": len(body.content or ""), "reason": body.reason, "http_status": body.http_status}
                print(json.dumps(row, ensure_ascii=False), flush=True)
                await asyncio.sleep(2)
            finally:
                REQUESTS.reset(token)
    finally:
        await http.close()
        await reader.close()


asyncio.run(main())
