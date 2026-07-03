from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import httpx

from doxagent.settings import DoxAgentSettings
from doxagent.stocktwits.client import StocktwitsClientError, StocktwitsHTTPClient


def snippet(text: str) -> str:
    return text.replace("\n", " ").replace("\r", " ")[:500]


def fp(value: str | None) -> str:
    if value and len(value) > 8:
        return value[:4] + "..." + value[-4:]
    return "<set>" if value else "<empty>"

settings = DoxAgentSettings()
base_url = settings.stocktwits_rapidapi_base_url.rstrip("/")
path = "/functions/v1/stocktwits-query"
url = base_url + path
host = settings.stocktwits_rapidapi_host or urlparse(base_url).netloc
today = datetime.now(UTC).date()
params = {
    "action": "messages",
    "symbol": "MU",
    "start": (today - timedelta(days=1)).isoformat(),
    "end": today.isoformat(),
    "primaryOnly": "true",
    "limit": 5,
    "force_refresh": "false",
}
keys = [("primary", settings.stocktwits_rapidapi_key), ("fallback", settings.stocktwits_rapidapi_fallback_key)]
with httpx.Client(timeout=45) as client:
    for label, key in keys:
        if not key:
            print(f"rapidapi_{label}=missing")
            continue
        response = client.get(
            url,
            params=params,
            headers={
                "X-RapidAPI-Key": key,
                "X-RapidAPI-Host": host,
                "Content-Type": "application/json",
            },
        )
        print(f"rapidapi_{label}_key={fp(key)}")
        print(f"rapidapi_{label}_status={response.status_code}")
        print(f"rapidapi_{label}_url_path={response.url.path}")
        print(f"rapidapi_{label}_body={snippet(response.text)}")

try:
    page = StocktwitsHTTPClient(settings).fetch_symbol_page(symbol="MU", page_size=5)
    print("public_status=ok")
    print("public_messages=" + str(len(page.messages)))
    print("public_next_max_id=" + str(page.next_max_id))
except StocktwitsClientError as exc:
    print("public_status=error")
    print("public_error_code=" + exc.code)
    print("public_rate_limited=" + str(exc.rate_limited))
    print("public_error=" + snippet(str(exc)))
except Exception as exc:
    print("public_status=exception")
    print("public_error_type=" + type(exc).__name__)
    print("public_error=" + snippet(str(exc)))
