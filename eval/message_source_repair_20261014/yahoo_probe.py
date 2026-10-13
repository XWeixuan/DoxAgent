import asyncio
import json

from doxagent.message_bus_v2.yahoo_sources import acquire_yahoo_rss
from doxagent.message_bus_v2.yahoo_transport import YahooTransport


async def main():
    out = []
    for port in (18083,18085):
        transport = YahooTransport(proxy_url=f'http://doxagent-egress-clash:{port}',gap=0,jitter=0)
        try:
            rows, metadata = await acquire_yahoo_rss(transport,'MU',timeout_seconds=12)
            out.append({'port':port,'items':len(rows),'metadata':metadata})
        except Exception as exc:
            out.append({'port':port,'error':type(exc).__name__})
        finally:
            transport.close()
    print(json.dumps(out))


asyncio.run(main())
