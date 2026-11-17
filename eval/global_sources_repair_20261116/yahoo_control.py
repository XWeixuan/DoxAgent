import asyncio, json, time
from doxagent.settings import DoxAgentSettings
from doxagent.message_bus_v2.yahoo_transport import YahooTransport
from doxagent.message_bus_v2.yahoo_sources import acquire_yahoo_rss
from doxagent.message_bus_v2.poll_trace import REQUESTS

async def main():
    s=DoxAgentSettings()
    t=YahooTransport(proxy_url=s.message_bus_v2_yahoo_http_proxy_url or s.crawler_egress_proxy_url,
        http_routes=s.message_bus_v2_yahoo_http_routes)
    out=[]
    try:
        for ticker in ['MU','BE']:
            events=[];token=REQUESTS.set(events);started=time.monotonic()
            try:
                rows,meta=await acquire_yahoo_rss(t,ticker,timeout_seconds=8)
                r={'ticker':ticker,'rows':len(rows),'ok':True}
            except Exception as e:
                c=e.__cause__
                r={'ticker':ticker,'error':type(e).__name__,'cause':type(c).__name__,
                    'curl_code':getattr(c,'code',None)}
            finally:REQUESTS.reset(token)
            r.update(seconds=round(time.monotonic()-started,3),wire_requests=len(events),
                statuses=[e.get('status_code') for e in events])
            out.append(r)
    finally:t.close()
    print(json.dumps({'routes':s.message_bus_v2_yahoo_http_routes,'results':out}))
asyncio.run(main())
