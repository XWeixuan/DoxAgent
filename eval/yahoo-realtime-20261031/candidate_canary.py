"""Run exact candidate query on the production egress, without business writes."""
import asyncio
import importlib.util
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from doxagent.settings import DoxAgentSettings
from doxagent.message_bus_v2.yahoo_transport import YahooTransport

spec = importlib.util.spec_from_file_location('candidate_graphql', '/tmp/yahoo_graphql_candidate.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


async def main():
    settings = DoxAgentSettings()
    transport = YahooTransport(proxy_url=settings.message_bus_v2_yahoo_http_proxy_url or settings.crawler_egress_proxy_url,
                               http_routes=settings.message_bus_v2_yahoo_http_routes)
    result = []
    try:
        for ticker in ('INTC','MU','COHR','NBIS','BE','CEG','SNOW','RKLB','ONDS','IONQ'):
            start = time.monotonic()
            try:
                rows, metadata = await module.acquire(transport, ticker)
                result.append(dict(ticker=ticker, observed_at=datetime.now(UTC).isoformat(),
                                   seconds=time.monotonic()-start, count=len(rows), metadata=metadata))
            except Exception as e:
                result.append(dict(ticker=ticker, error=type(e).__name__, detail=str(e)[:120]))
    finally:
        transport.close()
    Path('/tmp/yahoo-candidate-canary.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


asyncio.run(main())
