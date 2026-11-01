"""Google discovery's measured public routes: one primary and one fallback."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import os
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from doxagent.monitoring.media_enrichment import FetchAttempt

from .publisher_documents import publisher_host
from .transport import Observation, remaining

READER_FIRST = frozenset({"barchart.com", "stocktwits.com", "tipranks.com", "streetinsider.com"})
READER_BROWSER = frozenset({"futunn.com", "stocktwits.com", "tipranks.com", "streetinsider.com"})
ZYTE_FIRST = frozenset({"gurufocus.com"})


def provider_target(url):
    """The remote fetcher resolves publisher DNS; never require local DNS first."""
    value = urlsplit(url)
    hostname = (value.hostname or "").lower()
    if (
        value.scheme not in {"http", "https"}
        or not hostname
        or value.username
        or value.password
        or value.port not in {None, 80, 443}
        or "." not in hostname
        or hostname == "localhost"
        or hostname.endswith((".localhost", ".local", ".internal"))
    ):
        raise ValueError("non_public_url")
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        return
    if not literal.is_global:
        raise ValueError("non_public_url")


def plan(url: str) -> list[str]:
    site = publisher_host(url)
    if site == "futunn.com" and urlsplit(url).path.startswith("/discussion/"):
        return []
    if site == "stocktwits.com" and urlsplit(url).path.startswith("/symbol/"):
        return []
    if site in {"techpowerup.com", "benzinga.com"}:
        return ["zyte_http", "zyte"]
    if site in ZYTE_FIRST:
        return ["zyte", "google_browser"]
    if site == "futunn.com":
        return ["reader", "zyte"]
    if site == "moomoo.com":
        return ["http_public", "zyte"]
    if site == "stocktitan.net":
        return ["http_public", "zyte"]
    if site == "thestreet.com":
        return ["http_public", "reader"]
    if site in READER_FIRST:
        return ["reader", "zyte" if site == "barchart.com" else "zyte_http"]
    return ["http_public", "reader"]


class GooglePublicAccess:
    def __init__(self, *, trusted_proxy_dns=False):
        self.trusted_proxy_dns = trusted_proxy_dns
        self._client = None
        self._proxy_session = None
        self._browser_lock = asyncio.Lock()

    async def close(self):
        if self._client:
            await self._client.aclose()
        if self._proxy_session:
            await self._proxy_session.close()

    async def read(self, url, stage, attempts):
        provider_target(url)
        started = time.monotonic()
        if stage == "google_browser":
            observation = await self._browser(url)
        else:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    follow_redirects=False, limits=httpx.Limits(max_connections=4)
                )
            if stage == "reader":
                headers = {
                    "X-Engine": "browser" if publisher_host(url) in READER_BROWSER else "auto"
                }
                if publisher_host(url) in READER_BROWSER:
                    headers["X-No-Cache"] = "true"
                if publisher_host(url) == "streetinsider.com":
                    headers["X-Target-Selector"] = ".article_body"
                if key := os.getenv("JINA_API_KEY"):
                    headers["Authorization"] = "Bearer " + key
                response = await self._client.get(
                    "https://r.jina.ai/" + url, headers=headers, timeout=remaining(20)
                )
                observation = Observation(
                    url,
                    response.text,
                    response.status_code,
                    None
                    if response.status_code == 200
                    else "reader_http_" + str(response.status_code),
                )
            else:
                key = os.getenv("ZYTE_API_KEY")
                if not key:
                    observation = Observation(url, "", 0, "zyte_not_configured")
                else:
                    browser = stage != "zyte_http" and publisher_host(url) != "stocktitan.net"
                    response = await self._client.post(
                        "https://api.zyte.com/v1/extract",
                        auth=(key, ""),
                        json={"url": url, "browserHtml" if browser else "httpResponseBody": True},
                        timeout=remaining(20),
                    )
                    if response.status_code == 200:
                        data = response.json()
                        body = data.get("browserHtml") or base64.b64decode(
                            data.get("httpResponseBody") or ""
                        ).decode("utf-8", errors="replace")
                        status = data.get("statusCode", 200)
                        observation = Observation(
                            data.get("url") or url,
                            body,
                            status,
                            None if 200 <= status < 300 else "publisher_http_" + str(status),
                        )
                    else:
                        observation = Observation(
                            url, "", response.status_code, "zyte_http_" + str(response.status_code)
                        )
            if len(observation.text.encode("utf-8")) > 8_000_000:
                observation = Observation(url, "", 0, "response_too_large")
        attempts.append(
            FetchAttempt(
                stage,
                url,
                final_url=observation.url,
                status_code=observation.status or None,
                reason=observation.reason,
                fetch_profile="google-public:" + stage,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        )
        return observation

    async def http(self, url, attempts, transport):
        proxy = (
            os.getenv("DOXAGENT_GOOGLE_BODY_STOCKTITAN_PROXY")
            if publisher_host(url) == "stocktitan.net"
            else None
        )
        if not proxy:
            return await transport.fetch(url, attempts)
        if self._proxy_session is None:
            from curl_cffi.requests import AsyncSession

            self._proxy_session = AsyncSession(impersonate="chrome", proxy=proxy, timeout=10)
        started = time.monotonic()
        for _ in range(5):
            provider_target(url)
            response = await self._proxy_session.get(
                url, allow_redirects=False, timeout=remaining(8)
            )
            if 300 <= response.status_code < 400 and response.headers.get("location"):
                url = urljoin(url, response.headers["location"])
                continue
            reason = (
                None if 200 <= response.status_code < 300 else "http_" + str(response.status_code)
            )
            text = response.text if len(response.content) <= 8_000_000 else ""
            if not text and len(response.content) > 8_000_000:
                reason = "response_too_large"
            attempts.append(
                FetchAttempt(
                    "direct",
                    url,
                    final_url=url,
                    status_code=response.status_code,
                    fetch_profile="google-fixed-egress:stocktitan",
                    reason=reason,
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
            )
            return Observation(url, text, response.status_code, reason, dict(response.headers))
        return Observation(url, "", 0, "redirect_hop_limit")

    async def _browser(self, url):
        # One shared Google-only profile; no browser process survives a request.
        if self._browser_lock.locked():
            return Observation(url, "", 0, "site_budget_deferred")
        from doxagent.resource_safety import SafetyLevel, SafetyStateReader

        safety = os.getenv("DOXAGENT_SAFETY_STATE_PATH")
        if safety and SafetyStateReader(Path(safety)).read().level is not SafetyLevel.NORMAL:
            return Observation(url, "", 0, "site_budget_deferred")
        async with self._browser_lock:
            from playwright.async_api import async_playwright

            async with async_playwright() as playwright:
                context = await playwright.chromium.launch_persistent_context(
                    os.getenv("DOXAGENT_GOOGLE_BODY_PROFILE_DIR", ".tmp/google-news-body/profile"),
                    headless=True,
                    accept_downloads=False,
                    **(
                        {"executable_path": os.environ["DOXAGENT_GOOGLE_BODY_CHROME_PATH"]}
                        if os.getenv("DOXAGENT_GOOGLE_BODY_CHROME_PATH")
                        else {}
                    ),
                )
                try:
                    page = context.pages[0] if context.pages else await context.new_page()
                    await page.goto(
                        url, wait_until="domcontentloaded", timeout=int(remaining(10) * 1000)
                    )
                    from playwright.async_api import TimeoutError as BrowserTimeout

                    try:
                        await page.wait_for_function(
                            "() => [...document.querySelectorAll("
                            "'article, [itemprop=articleBody]')]"
                            ".some(e => e.innerText.length > 800)",
                            timeout=int(remaining(5) * 1000),
                        )
                    except BrowserTimeout:
                        pass
                    return Observation(page.url, await page.content(), 200)
                finally:
                    await context.close()
