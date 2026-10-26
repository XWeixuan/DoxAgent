"""Two 30s rounds of both public JSON channels for the nine current tickers."""
import asyncio
import json
import time
from datetime import UTC, datetime

from curl_cffi.requests import AsyncSession

from doxagent.message_bus_v2.barrons_sources import render_news_feeds
from doxagent.message_bus_v2.http_routes import HTTP_EGRESSES
from doxagent.message_bus_v2.industry_sources import parse_barrons_ticker_listing


async def main():
    tickers = ["INTC", "MU", "BE", "COHR", "NBIS", "CEG", "RKLB", "SNOW", "ONDS"]
    origin = time.monotonic()
    async with AsyncSession(impersonate="chrome", trust_env=False) as session:
        for round_number in range(2):
            await asyncio.sleep(max(0, origin+round_number*30-time.monotonic()))
            for ticker in tickers:
                for channel in ("Barrons", "BarronsDowJonesNetwork"):
                    at = datetime.now(UTC)
                    started = time.monotonic()
                    out = {"round": round_number, "ticker": ticker, "channel": channel,
                           "request_started_at": at.isoformat()}
                    url = ("https://www.barrons.com/market-data/api/news?channel="+channel+
                           "&chartingSymbol=stock%2F%2F%2F"+ticker.lower()+"&pageNumber=0&count=40")
                    try:
                        response = await session.get(url, proxy=HTTP_EGRESSES["us-standard-5"],
                            timeout=8, allow_redirects=False, headers={"Cache-Control": "no-cache"})
                        out.update(status=response.status_code, bytes=len(response.content),
                            elapsed_ms=round((time.monotonic()-started)*1000),
                            response_headers={k: response.headers.get(k) for k in
                                              ("date", "age", "cache-control", "etag")})
                        html = render_news_feeds([{"status": response.status_code, "text": response.text}], channels=[channel])
                        rows = parse_barrons_ticker_listing(html, ticker=ticker, now=at)
                        out.update(message_count=len(rows),
                            latest_publication=max((m.published_at.isoformat() for m in rows), default=None))
                    except Exception as exc:
                        out["error_type"] = type(exc).__name__
                    out["response_received_at"] = datetime.now(UTC).isoformat()
                    print(json.dumps(out), flush=True)
                    await asyncio.sleep(max(0, .5-(time.monotonic()-started)))


asyncio.run(main())
