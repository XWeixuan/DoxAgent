"""Yahoo browser news acquisition and the legacy headline RSS feed.

Distinguishes native responses, hydration JSON and explicit page-JS requests.
All acquisition paths preserve provider fields for the existing Yahoo mapper.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, quote, urlparse
from xml.etree import ElementTree


class YahooPageUnavailable(RuntimeError):
    def __init__(self, message: str, *, status_code=None, retry_after_seconds=0):
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message)


def page_http_failure(status: int, headers: dict) -> YahooPageUnavailable:
    retry = headers.get("retry-after") or headers.get("Retry-After")
    try:
        seconds = float(retry)
    except (ValueError, TypeError):
        try:
            seconds = parsedate_to_datetime(retry).timestamp() - datetime.now(UTC).timestamp()
        except (ValueError, TypeError, OverflowError):
            seconds = 0
    return YahooPageUnavailable(
        f"Yahoo browser HTTP {status}",
        status_code=status,
        retry_after_seconds=max(0, seconds) if seconds < float("inf") else 0,
    )


def hydration_news_rows(scripts: list[dict]) -> list[dict] | None:
    """Read SvelteKit's serialized server fetches, not arbitrary page JSON."""
    from .news_adapters import _yahoo_contents

    for script in scripts:
        parsed = urlparse(script.get("url", ""))
        if parsed.hostname not in (None, "finance.yahoo.com") or parsed.path != "/xhr/ncp":
            continue
        if parse_qs(parsed.query).get("queryRef") != ["latestNews"]:
            continue
        try:
            envelope = json.loads(script["text"])
            if envelope.get("status") != 200:
                continue
            payload = json.loads(envelope["body"])
            rows = _yahoo_contents(payload)
            if rows:
                return rows
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return None


def latest_news_response(response, ticker: str) -> bool:
    parsed = urlparse(response.url)
    if parsed.hostname != "finance.yahoo.com" or parsed.path != "/xhr/ncp":
        return False
    if parse_qs(parsed.query).get("queryRef") != ["latestNews"]:
        return False
    body = response.request.post_data
    if body:
        try:
            symbols = json.loads(body).get("serviceConfig", {}).get("s", [])
            if isinstance(symbols, str):
                symbols = [symbols]
            if not isinstance(symbols, list):
                return False
            return ticker.upper() in [str(s).upper() for s in symbols]
        except (ValueError, AttributeError):
            return False
    return False  # Do not consume unrelated/global ticker requests.


async def capture_latest_news(page, ticker: str, *, timeout_seconds=20, snippet_count=20):
    from .news_adapters import _yahoo_contents

    captured = asyncio.get_running_loop().create_future()
    tasks = set()
    capture_method = "native_network"

    async def receive(response):
        if not latest_news_response(response, ticker) or captured.done():
            return
        if response.status != 200:
            captured.set_exception(page_http_failure(response.status, response.headers))
            return
        try:
            payload = await response.json()
            rows = _yahoo_contents(payload)
            if not rows and not (isinstance(payload, dict) and "data" in payload):
                raise ValueError("unrecognized latestNews JSON")
            if not captured.done():
                captured.set_result(
                    (
                        rows,
                        {
                            "endpoint": response.url,
                            "network_status": response.status,
                            "capture_method": capture_method,
                        },
                    )
                )
        except Exception as exc:
            if not captured.done():
                captured.set_exception(
                    YahooPageUnavailable(f"Yahoo page NCP JSON: {type(exc).__name__}")
                )

    def listener(response):
        if not latest_news_response(response, ticker):
            return
        task = asyncio.create_task(receive(response))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    page.on("response", listener)
    started = time.monotonic()
    try:
        async with asyncio.timeout(timeout_seconds):
            response = await page.goto(
                f"https://finance.yahoo.com/quote/{quote(ticker, safe='')}/latest-news/",
                wait_until="domcontentloaded",
                timeout=int(timeout_seconds * 1000),
            )
            if response is not None and response.status >= 400:
                raise page_http_failure(response.status, response.headers)
            location = urlparse(page.url)
            if location.hostname != "finance.yahoo.com" or not location.path.startswith(
                f"/quote/{quote(ticker, safe='')}/"
            ):
                raise YahooPageUnavailable("Yahoo browser left the requested ticker page")
            # SSR often already contains the cards; do not spend the whole deadline
            # waiting for an XHR the page never needs to issue.
            for _ in range(2):
                if captured.done():
                    break
                await page.evaluate("window.scrollBy(0, 1200)")
                await asyncio.sleep(0.5)
            if captured.done():
                rows, metadata = await captured
            else:
                scripts = await page.locator("script[data-sveltekit-fetched]").evaluate_all(
                    "els => els.map(e => ({url: e.dataset.url, text: e.textContent}))"
                )
                rows = hydration_news_rows(scripts)
                if rows is not None:
                    metadata = {
                        "capture_method": "ssr_hydration",
                        "network_status": None,
                        "endpoint": "sveltekit_latest_news",
                    }
                else:
                    # A real same-origin browser fetch: browser TLS, cookies, JS
                    # environment and connection pool, never Playwright APIRequest.
                    # Mark it explicitly so the listener cannot label it native.
                    capture_method = "page_js_fetch"
                    result = await page.evaluate(
                        """async ({ticker, count, timeout}) => {
                          const controller = new AbortController();
                          const timer = setTimeout(() => controller.abort(), timeout);
                          try {
                            const endpoint = '/xhr/ncp?queryRef=latestNews&serviceKey=ncp_fin';
                            const r = await fetch(endpoint, {
                              method: 'POST', credentials: 'include', signal: controller.signal,
                              headers: {'Content-Type': 'application/json'},
                              body: JSON.stringify({serviceConfig: {
                                snippetCount: count, s:[ticker]
                              }})
                            });
                            return {status: r.status, retryAfter: r.headers.get('Retry-After'),
                                    text: await r.text()};
                          } finally { clearTimeout(timer); }
                        }""",
                        {
                            "ticker": ticker,
                            "count": snippet_count,
                            "timeout": max(
                                1, int((timeout_seconds - (time.monotonic() - started)) * 1000)
                            ),
                        },
                    )
                    if result["status"] != 200:
                        raise page_http_failure(
                            result["status"], {"Retry-After": result["retryAfter"]}
                        )
                    payload = json.loads(result["text"])
                    rows = _yahoo_contents(payload)
                    if not rows and not (isinstance(payload, dict) and "data" in payload):
                        raise YahooPageUnavailable("Yahoo browser latestNews schema unrecognized")
                    metadata = {
                        "capture_method": capture_method,
                        "network_status": 200,
                        "endpoint": "page_origin_latest_news",
                        "requested_count": snippet_count,
                    }
            return rows, {
                **metadata,
                "page_url": page.url,
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
    except TimeoutError as exc:
        raise YahooPageUnavailable(
            "Yahoo page latestNews network response not seen before deadline"
        ) from exc
    finally:
        page.remove_listener("response", listener)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if not captured.done():
            captured.cancel()
        elif not captured.cancelled():
            captured.exception()  # Observe callback errors even when navigation failed first.


def rss_yahoo_rows(xml_text: str) -> list[dict]:
    from .news_adapters import _rss_rows

    root = ElementTree.fromstring(xml_text)
    if root.tag.rsplit("}", 1)[-1].lower() != "rss" or root.find("channel") is None:
        raise ValueError("Yahoo headline response is not an RSS channel")
    rows = []
    for raw in _rss_rows(xml_text):
        url = str(raw.get("link") or "")
        guid = str(raw.get("guid") or "")
        # Keep native Yahoo UUIDs as identity candidates, not proof of URL aliases.
        pattern = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
        match = re.fullmatch(pattern, guid, re.I) or re.search(r"/m/(" + pattern + r")/", url, re.I)
        identifier = guid
        if match:
            identifier = match.group(1) if match.lastindex else match.group(0)
        rows.append(
            {
                **raw,
                "uuid": identifier or None,
                "link": url,
                "description": raw.get("description"),
                "publisher": raw.get("source"),
                "_acquisition_format": "rss",
            }
        )
    return rows


async def acquire_yahoo_rss(transport, ticker: str, *, timeout_seconds=20):
    response = await transport.request(
        "GET",
        "https://feeds.finance.yahoo.com/rss/2.0/headline",
        params={"s": ticker, "region": "US", "lang": "en-US"},
        timeout=timeout_seconds,
        rate_scope="rss",
    )
    rows = rss_yahoo_rows(response.text)
    return rows, {
        "endpoint": "yahoo_legacy_headline_rss",
        "rss_item_count": len(rows),
        "format": "rss",
    }
