"""Read-only production-adapter canary. Never inserts messages or calls models."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, UTC
from pathlib import Path
from types import SimpleNamespace

from doxagent.message_bus_v2.securities_sources import initial_sources, listing_recipes
from doxagent.message_bus_v2.corporate_ir.catalog import initial_endpoints
from doxagent.message_bus_v2.corporate_ir.http import PublicHttp
from doxagent.message_bus_v2.corporate_ir.parsers import parse_listing as parse_ir
from doxagent.message_bus_v2.corporate_ir.body import IrBodyReader
from doxagent.message_bus_v2.technology_sources import TechnologyHttp
from doxagent.message_bus_v2.technology_body import TechnologyBodyReader
from doxagent.message_bus_v2.public_listing import ListingAdapter
from doxagent.message_bus_v2.poll_trace import REQUESTS
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord


@asynccontextmanager
async def permit():
    yield


async def main():
    http = TechnologyHttp()
    adapter = ListingAdapter(SimpleNamespace(), http)
    body = TechnologyBodyReader()
    ir_http = PublicHttp()
    slots = asyncio.Semaphore(2)
    definitions = {s.source_id: s for s in initial_sources()}

    async def one(sid, i, recipe):
        async with slots:
            out = {"source": sid, "entry": i, "at": datetime.now(UTC).isoformat()}
            events = []
            token = REQUESTS.set(events)
            try:
                source = definitions[sid]
                context = SimpleNamespace(
                    source=source,
                    checkpoint={},
                    requested_at=datetime.now(UTC),
                    request_permit=permit,
                    ticker="SECZ",
                    acquisition_unit="HEAD",
                    window_cutoff=None,
                )
                result = await adapter._one(context, recipe, shared=False)
                out.update(
                    count=len(result.messages),
                    failures=[f.error_message for f in result.failures],
                    acquisition=result.acquisition_metadata,
                )
                if result.messages:
                    m = result.messages[0]
                    r = await body.extract(
                        SimpleNamespace(
                            source=source, message=m, binding=SimpleNamespace(ticker="SECZ")
                        ),
                        MediaEnrichmentRecord(
                            "readonly", "readonly", sid, "SECZ", m.title, "", m.url
                        ),
                    )
                    out.update(
                        title=m.title,
                        url=m.url,
                        published_at=m.published_at.isoformat(),
                        basis=m.publication_time_basis,
                        body_outcome=r.diagnostics.get("outcome"),
                        body_reason=r.reason,
                        body_chars=len(r.content or ""),
                        body_method=r.extraction_method,
                    )
                    if not r.succeeded:
                        c = await http.fetch(m.url, body.sources[sid])
                        p = Path("/tmp/secz-body-pages")
                        p.mkdir(exist_ok=True)
                        (p / (sid + "-" + str(i) + ".html")).write_text(c.text)
            except Exception as e:
                out["error"] = type(e).__name__ + ":" + str(e)[:220]
            finally:
                REQUESTS.reset(token)
            print(json.dumps(out), flush=True)

    async def ir():
        async with slots:
            spec = next(s for s in initial_endpoints() if s.owner_ticker == "SECZ")
            out = {
                "source": "corporate_ir_press_releases",
                "endpoint": spec.endpoint_id,
                "at": datetime.now(UTC).isoformat(),
            }
            try:
                c = await ir_http.fetch(spec)
                rows, _ = parse_ir(spec, c.body)
                m = rows[0]
                r = await IrBodyReader(ir_http).extract(
                    SimpleNamespace(message=m, binding=SimpleNamespace(ticker="SECZ")),
                    MediaEnrichmentRecord(
                        "readonly",
                        "readonly",
                        "corporate_ir_press_releases",
                        "SECZ",
                        m.title,
                        "",
                        m.url,
                    ),
                )
                out.update(
                    status=c.status,
                    count=len(rows),
                    title=m.title,
                    url=m.url,
                    published_at=m.published_at.isoformat(),
                    basis=m.publication_time_basis,
                    body_outcome=r.diagnostics.get("outcome"),
                    body_reason=r.reason,
                    body_chars=len(r.content or ""),
                )
            except Exception as e:
                out["error"] = type(e).__name__ + ":" + str(e)[:220]
            print(json.dumps(out), flush=True)

    try:
        await asyncio.gather(
            *(one(s, i, r) for s, rs in listing_recipes().items() for i, r in enumerate(rs)), ir()
        )
    finally:
        await http.close()
        await body.close()
        await ir_http.close()


if __name__ == "__main__":
    asyncio.run(main())
