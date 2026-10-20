import asyncio
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from ..schema import sha256_text
from .browser_pool import PublicBrowserPool

HEADERS = {
    "User-Agent": "DoxAgent-IR-Evaluation/1.0",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class Capture:
    status: int
    url: str
    body: str
    headers: dict
    bytes_received: int

    @property
    def digest(self):
        return sha256_text(self.body)


class PublicHttp:
    def __init__(self, client=None):
        self.client = client or httpx.AsyncClient(
            headers=HEADERS,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(8, connect=3, pool=1),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=6),
        )
        self.owns_client = client is None
        self.slots = asyncio.Semaphore(6)
        self.host_slots = {}
        self.host_locks = {}
        self.host_started = {}
        self.browser = PublicBrowserPool()

    async def fetch(self, spec, *, url=None, headers=None):
        if spec.method == "browser" and url is None:
            status, body, final = await self.browser.fetch(spec)
            return Capture(status, final, body, {}, len(body.encode()))
        url = url or spec.entry_url
        async with asyncio.timeout(12):
            for _ in range(5):
                host = urlsplit(url).hostname
                if host not in spec.allowed_article_hosts:
                    raise ValueError("IR redirect host not approved")
                slots = self.host_slots.setdefault(host, asyncio.Semaphore(2))
                lock = self.host_locks.setdefault(host, asyncio.Lock())
                async with self.slots, slots:
                    async with lock:
                        await asyncio.sleep(
                            max(0, self.host_started.get(host, 0) + 0.5 - time.monotonic())
                        )
                        self.host_started[host] = time.monotonic()
                    async with self.client.stream(
                        "GET", url, headers={**HEADERS, **(headers or {})}
                    ) as response:
                        if response.is_redirect:
                            from urllib.parse import urljoin

                            url = urljoin(url, response.headers["location"])
                            continue
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw) > 4 * 1024 * 1024:
                                raise ValueError("IR response exceeds 4 MiB")
                        body = bytes(raw).decode(response.encoding or "utf-8", errors="replace")
                        return Capture(
                            response.status_code,
                            str(response.url),
                            body,
                            dict(response.headers),
                            len(raw),
                        )
            raise ValueError("IR redirect limit")

    async def close(self):
        await self.browser.close()
        if self.owns_client:
            await self.client.aclose()
