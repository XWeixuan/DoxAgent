"""Read-only real network acceptance through production adapters; no Bus intake."""

import argparse
import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from doxagent.message_bus_v2.biopharma_sources import initial_sources, listing_recipes, TICKERS
from doxagent.message_bus_v2.corporate_ir.catalog import initial_endpoints
from doxagent.message_bus_v2.corporate_ir.http import PublicHttp
from doxagent.message_bus_v2.corporate_ir.parsers import parse_listing as parse_ir
from doxagent.message_bus_v2.corporate_ir.body import IrBodyReader
from doxagent.message_bus_v2.public_listing import ListingAdapter
from doxagent.message_bus_v2.technology_sources import TechnologyHttp
from doxagent.message_bus_v2.technology_body import TechnologyBodyReader
from doxagent.message_bus_v2.poll_trace import REQUESTS
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord


@asynccontextmanager
async def permit():
    yield


async def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source")
    p.add_argument("--query", default="Xenon")
    p.add_argument("--list-only", action="store_true")
    args = p.parse_args()
    http = TechnologyHttp()
    ir_http = PublicHttp()
    slots = asyncio.Semaphore(2)
    adapter = ListingAdapter(SimpleNamespace(), http)
    body = TechnologyBodyReader(http)
    definitions = {s.source_id: s for s in initial_sources()}

    async def one(sid, index, recipe):
        async with slots:
            source = definitions[sid]
            start = time.monotonic()
            now = datetime.now(UTC)
            output = {"source": sid, "entry": index, "at": now.isoformat()}
            events = []
            token = REQUESTS.set(events)
            try:
                context = SimpleNamespace(
                    source=source,
                    checkpoint={},
                    requested_at=now,
                    request_permit=permit,
                    ticker="XENE",
                    acquisition_unit="HEAD",
                    window_cutoff=None,
                    query_plan={"queries": [{"query": args.query}]},
                )
                result = (
                    await adapter.poll(context)
                    if sid == "aes_abstract_search"
                    else await adapter._one(context, recipe, shared=False)
                )
                output.update(
                    count=len(result.messages),
                    failures=[f.error_message for f in result.failures],
                    acquisition=result.acquisition_metadata,
                    seconds=round(time.monotonic() - start, 3),
                )
                if result.messages and not args.list_only:
                    m = result.messages[0]
                    rec = MediaEnrichmentRecord(
                        "readonly", "readonly", sid, "XENE", m.title, "", m.url
                    )
                    job = SimpleNamespace(
                        source=source, message=m, binding=SimpleNamespace(ticker="XENE")
                    )
                    async with asyncio.timeout(40):
                        r = await body.extract(job, rec)
                    output.update(
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
                        folder = Path("/tmp/biopharma-body-pages")
                        folder.mkdir(exist_ok=True)
                        (folder / (sid + ".private.html")).write_text(c.text)
            except Exception as exc:
                output["error"] = type(exc).__name__ + ":" + str(exc)[:250]
            finally:
                REQUESTS.reset(token)
            print(json.dumps(output), flush=True)

    async def ir_one(spec):
        async with slots:
            output = {
                "source": "corporate_ir_press_releases",
                "endpoint": spec.endpoint_id,
                "at": datetime.now(UTC).isoformat(),
            }
            try:
                c = await ir_http.fetch(spec)
                output.update(
                    status=c.status,
                    headers={k: c.headers.get(k) for k in ("age", "cache-control", "date")},
                )
                if c.status != 200:
                    raise ValueError("ir_http_" + str(c.status))
                messages, _ = parse_ir(spec, c.body)
                output["count"] = len(messages)
                if not args.list_only:
                    m = messages[0]
                    record = MediaEnrichmentRecord(
                        "readonly",
                        "readonly",
                        "corporate_ir_press_releases",
                        spec.owner_ticker,
                        m.title,
                        "",
                        m.url,
                    )
                    r = await IrBodyReader(ir_http).extract(
                        SimpleNamespace(
                            message=m, binding=SimpleNamespace(ticker=spec.owner_ticker)
                        ),
                        record,
                    )
                    output.update(
                        title=m.title,
                        url=m.url,
                        basis=m.publication_time_basis,
                        body_outcome=r.diagnostics.get("outcome"),
                        body_reason=r.reason,
                        body_chars=len(r.content or ""),
                    )
            except Exception as exc:
                output["error"] = type(exc).__name__ + ":" + str(exc)[:250]
            print(json.dumps(output), flush=True)

    try:
        await asyncio.gather(
            *(
                one(sid, i, r)
                for sid, rs in listing_recipes().items()
                for i, r in enumerate(rs)
                if not args.source or sid == args.source
            ),
            *(
                ir_one(s)
                for s in initial_endpoints()
                if s.owner_ticker in TICKERS and (not args.source or args.source == s.endpoint_id)
            ),
        )
    finally:
        await http.close()
        await ir_http.close()


if __name__ == "__main__":
    asyncio.run(main())
