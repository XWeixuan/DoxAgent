import asyncio
import time
import urllib.error
import urllib.request
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
        self.client = client
        self.clients={}
        self.owns_client = client is None
        self.host_slots = {}
        self.browser = PublicBrowserPool()

    def _client(self,host):
        if self.client is not None:return self.client
        if host not in self.clients:
            from ..poll_trace import httpx_request
            self.clients[host]=httpx.AsyncClient(headers=HEADERS,trust_env=False,follow_redirects=False,
                timeout=httpx.Timeout(8,connect=3,pool=1),
                limits=httpx.Limits(max_connections=2,max_keepalive_connections=2,keepalive_expiry=30),
                event_hooks={'request':[httpx_request]})
        return self.clients[host]

    async def _stdlib(self, url, headers):
        work = asyncio.create_task(asyncio.to_thread(self._stdlib_read, url, headers))
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            # A cancelled await cannot stop a socket thread. Drain before giving
            # back its host/global permit, just like an admitted storage commit.
            while not work.done():
                try:
                    await asyncio.shield(work)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            await asyncio.gather(work, return_exceptions=True)
            raise

    @staticmethod
    def _stdlib_read(url, headers, proxy=None):
        import http.client
        from ..poll_trace import request_started
        events=[]
        class WireSend:
            def send(self,data):
                if isinstance(data,(bytes,bytearray)) and data.startswith(b'GET ') and not events:
                    events.append(request_started('GET',url,'stdlib',admitted=True))
                return super().send(data)
        class Http(WireSend,http.client.HTTPConnection):pass
        class Https(WireSend,http.client.HTTPSConnection):pass
        class HttpHandler(urllib.request.HTTPHandler):
            def http_open(self,request):return self.do_open(Http,request)
        class HttpsHandler(urllib.request.HTTPSHandler):
            def https_open(self,request):return self.do_open(Https,request,context=self._context)
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        deadline = time.monotonic() + 12
        proxies = {"http": proxy, "https": proxy} if proxy else {}
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies),HttpHandler(),HttpsHandler(), NoRedirect())
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": HEADERS["User-Agent"],
                "Accept": "*/*",
                "Accept-Encoding": "identity",
                **headers,
            },
        )
        from ..schema import utc_now
        from ..collection_override import check_request_allowed
        check_request_allowed()
        try:
            response = opener.open(request, timeout=3)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            raw = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("IR public HTTP deadline")
                try:
                    response.fp.raw._sock.settimeout(min(8, remaining))
                except AttributeError:
                    pass
                chunk = response.read(65536)
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > 4 * 1024 * 1024:
                    raise ValueError("IR response exceeds 4 MiB")
            charset = response.headers.get_content_charset() or "utf-8"
            event=events[-1] if events else None
            if event is not None:
                event.update(response_received_at=utc_now().isoformat(), status_code=response.status,
                    validators={k.lower():v for k,v in response.headers.items() if k.lower() in {'date','age','etag','last-modified'}})
            return Capture(
                response.status,
                response.url,
                raw.decode(charset, errors="replace"),
                {k.lower(): v for k, v in response.headers.items()},
                len(raw),
            )

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
                from ..publisher_http import http_budget
                async with slots,http_budget('body' if url!=spec.entry_url else 'collector'):
                    if host in spec.parser_parameters.get("urllib_hosts", []):
                        capture = await self._stdlib(url, headers or {})
                        if capture.status in {301, 302, 303, 307, 308}:
                            from urllib.parse import urljoin

                            url = urljoin(url, capture.headers["location"])
                            continue
                        return capture
                    from ..schema import utc_now
                    async with self._client(host).stream(
                        "GET", url, headers={**HEADERS, **(headers or {})}
                    ) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            from urllib.parse import urljoin

                            url = urljoin(url, response.headers["location"])
                            continue
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw) > 4 * 1024 * 1024:
                                raise ValueError("IR response exceeds 4 MiB")
                        body = bytes(raw).decode(response.encoding or "utf-8", errors="replace")
                        event=response.request.extensions.get('_bus_poll_event')
                        if event is not None:
                            event.update(response_received_at=utc_now().isoformat(),status_code=response.status_code)
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
        for client in self.clients.values():await client.aclose()
        self.clients.clear()
