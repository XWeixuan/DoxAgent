"""One public HTTP attempt per endpoint on its already approved region; no cookies."""
import asyncio
import json
import time
from datetime import UTC, datetime

from curl_cffi.requests import AsyncSession

from doxagent.message_bus_v2.http_routes import HTTP_EGRESSES
from doxagent.message_bus_v2.market_sources import parse_investing, parse_investorshub


async def main():
    cases = [
        ("investorshub", "MU", "https://investorshub.advfn.com/stock-market/NASDAQ/MU/news", "de-standard-1"),
        ("investing", "INTC", "https://www.investing.com/equities/intel-corp-news", "de-standard-1"),
        ("barrons", "MU", "https://www.barrons.com/market-data/api/news?channel=Barrons&chartingSymbol=stock%2F%2F%2Fmu&pageNumber=0&count=40", "us-standard-5"),
    ]
    async with AsyncSession(impersonate="chrome", trust_env=False) as session:
        for site, ticker, url, route in cases:
            started = time.monotonic()
            out = {"site": site, "ticker": ticker, "route": route, "at": datetime.now(UTC).isoformat()}
            try:
                response = await session.get(url, proxy=HTTP_EGRESSES[route], timeout=10, allow_redirects=False)
                out.update(status=response.status_code, bytes=len(response.content), elapsed_ms=round((time.monotonic()-started)*1000))
                if response.status_code == 200 and site != "barrons":
                    try:
                        parser = parse_investorshub if site == "investorshub" else parse_investing
                        out["message_count"] = len(parser(response.text, ticker=ticker, now=datetime.now(UTC)))
                    except Exception as exc:
                        out["parse_error"] = type(exc).__name__
            except Exception as exc:
                out.update(error=type(exc).__name__, elapsed_ms=round((time.monotonic()-started)*1000))
            print(json.dumps(out), flush=True)


asyncio.run(main())
