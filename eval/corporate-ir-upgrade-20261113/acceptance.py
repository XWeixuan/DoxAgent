"""Read-only production HTTP/list/body acceptance. Never publishes historical samples."""
import asyncio
import json
import time
from types import SimpleNamespace

from doxagent.message_bus_v2.corporate_ir.body import IrBodyReader
from doxagent.message_bus_v2.corporate_ir.http import PublicHttp
from doxagent.message_bus_v2.corporate_ir.parsers import parse_listing
from doxagent.message_bus_v2.corporate_ir.schema import IrEndpointSpec
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
from doxagent.settings import DoxAgentSettings


async def main():
    repo = MessageBusV2Repository(DoxAgentSettings().message_bus_v2_sqlite_path, initialize=False)
    source = repo.get_source('corporate_ir_press_releases')
    http = PublicHttp()
    try:
        for value in source.default_parameters['endpoints']:
            if value['endpoint_id'] not in {'ionq.press', 'snow.press', 'onds.press'}:
                continue
            spec = IrEndpointSpec.model_validate(value)
            start = time.monotonic()
            response = await http.fetch(spec)
            rows, following = parse_listing(spec, response.body)
            message = rows[0]
            record = MediaEnrichmentRecord('ir-readonly', 'ir-readonly', source.source_id,
                spec.owner_ticker, message.title, '', message.url)
            result = await IrBodyReader(http).extract(SimpleNamespace(message=message,
                binding=SimpleNamespace(ticker=spec.owner_ticker)), record)
            print(json.dumps(dict(endpoint=spec.endpoint_id, status=response.status,
                list_bytes=response.bytes_received, count=len(rows), next_page=following,
                headers={k: response.headers.get(k) for k in ['age','cache-control','last-modified','etag']},
                title=message.title, url=message.url, published_at=message.published_at.isoformat(),
                time_basis=message.publication_time_basis, body_outcome=result.diagnostics.get('outcome'),
                body_chars=len(result.content or ''), body_method=result.extraction_method,
                reason=result.reason, total_seconds=round(time.monotonic()-start, 3))), flush=True)
    finally:
        await http.close()


asyncio.run(main())
