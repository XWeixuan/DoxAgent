"""One process-wide Yahoo cookie jar, serialized requests and rate-limit circuit.

AsyncSession lives on one dedicated loop because Runtime also uses short-lived
asyncio loops in worker threads. No per-poll sessions or cross-loop curl handles.
"""

from __future__ import annotations

import asyncio
import atexit
import math
import random
import threading
import time
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from curl_cffi.requests import AsyncSession, RequestsError


class YahooRateLimited(RuntimeError):
    def __init__(self, seconds: float) -> None:
        self.retry_after_seconds = max(0, seconds)
        super().__init__(f"Yahoo global circuit open; retry after {self.retry_after_seconds:.0f}s")


class YahooEndpointUnavailable(httpx.HTTPStatusError):
    """Explicit endpoint disappearance/method incompatibility, not throttling."""


class YahooTransport:
    def __init__(
        self,
        *,
        session_factory=AsyncSession,
        gap=1.5,
        jitter=0.5,
        clock=time.monotonic,
        wall_clock=time.time,
        sleep=asyncio.sleep,
        proxy_url: str | None = None,
    ) -> None:
        self._factory, self._gap, self._jitter = session_factory, gap, jitter
        self._clock, self._wall_clock, self._sleep = clock, wall_clock, sleep
        self._proxy_url = proxy_url
        self._session = None
        self._next_start = 0.0
        self._circuits = {}
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="yahoo-http", daemon=True
        )
        self._thread.start()
        self._lock = None

    async def request(self, method: str, url: str, **kwargs: Any):
        future = asyncio.run_coroutine_threadsafe(self._request(method, url, **kwargs), self._loop)
        return await asyncio.wrap_future(future)

    async def _request(self, method: str, url: str, *, rate_scope="api", **kwargs: Any):
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            until, strikes = self._circuits.get(rate_scope, (0, 0))
            if self._clock() < until:
                raise YahooRateLimited(until - self._clock())
            await self._sleep(max(0, self._next_start - self._clock()))
            if self._clock() < until:
                raise YahooRateLimited(until - self._clock())
            if self._session is None:
                options = {"impersonate": "chrome", "max_clients": 1}
                if self._proxy_url:
                    options["proxy"] = self._proxy_url
                self._session = self._factory(**options)
            self._next_start = self._clock() + self._gap + random.uniform(0, self._jitter)
            try:
                response = await self._session.request(method, url, **kwargs)
            except RequestsError as exc:
                raise httpx.RequestError(
                    "Yahoo browser transport request failed", request=httpx.Request(method, url)
                ) from exc
            if response.status_code == 429:
                strikes += 1
                delay = (300, 900, 1800)[min(strikes - 1, 2)]
                retry = response.headers.get("Retry-After")
                try:
                    seconds = float(retry)
                except (TypeError, ValueError):
                    try:
                        seconds = parsedate_to_datetime(retry).timestamp() - self._wall_clock()
                    except (TypeError, ValueError, OverflowError):
                        seconds = 0
                delay = max(delay, seconds if math.isfinite(seconds) else 0)
                self._circuits[rate_scope] = (self._clock() + delay, strikes)
                raise YahooRateLimited(delay)
            if not 200 <= response.status_code < 300:
                error = (
                    YahooEndpointUnavailable
                    if response.status_code in {404, 405, 410}
                    else httpx.HTTPStatusError
                )
                raise error(
                    f"Yahoo HTTP {response.status_code}",
                    request=httpx.Request(method, url),
                    response=httpx.Response(response.status_code),
                )
            # Only a successful request closes a half-open circuit.
            self._circuits.pop(rate_scope, None)
            return response

    async def _close(self):
        if self._session is not None:
            close = getattr(self._session, "aclose", None) or self._session.close
            await close()
            self._session = None

    def close(self):
        if self._loop.is_closed():
            return
        future = asyncio.run_coroutine_threadsafe(self._close(), self._loop)
        try:
            future.result(timeout=5)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
            if not self._thread.is_alive():
                self._loop.close()


_shared = None
_shared_lock = threading.Lock()


def shared_yahoo_transport(proxy_url: str | None = None) -> YahooTransport:
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = YahooTransport(proxy_url=proxy_url)
            atexit.register(_shared.close)
        return _shared
