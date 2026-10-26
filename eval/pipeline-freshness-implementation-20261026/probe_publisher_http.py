"""Single bounded approved-route publisher canary; no Bus writes or profile changes."""
import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime

from curl_cffi.requests import AsyncSession
from doxagent.message_bus_v2.http_routes import HTTP_EGRESSES
from doxagent.message_bus_v2.industry_sources import parse_trendforce_listing


async def main():
    url = 'https://www.trendforce.com/presscenter/news'
    at = datetime.now(UTC)
    started = time.monotonic()
    out = {'url':url, 'request_started_at':at.isoformat(), 'route':'server-direct'}
    try:
        async with AsyncSession(impersonate='chrome',trust_env=False) as client:
            response = await client.get(url, proxy=HTTP_EGRESSES['server-direct'], timeout=8)
        out.update(status=response.status_code, content_type=response.headers.get('content-type'),
                   response_sha256=hashlib.sha256(response.content).hexdigest())
        if response.status_code == 200:
            rows = parse_trendforce_listing(response.text,press=True,now=at)
            out.update(items=len(rows), latest=max((m.published_at.isoformat() for m in rows),default=None))
    except Exception as exc:
        out['error_type'] = type(exc).__name__
    out.update(elapsed_ms=round((time.monotonic()-started)*1000), response_received_at=datetime.now(UTC).isoformat())
    print(json.dumps(out))


asyncio.run(main())
