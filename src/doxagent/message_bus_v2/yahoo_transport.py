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
from curl_cffi.requests import RequestsError
from .publisher_http import PublisherSession as AsyncSession,http_budget

from .http_routes import approved_routes, transport_routes


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
        http_routes: tuple[str, ...] | None = None,
    ) -> None:
        self._factory, self._gap, self._jitter = session_factory, gap, jitter
        self._clock, self._wall_clock, self._sleep = clock, wall_clock, sleep
        self._proxy_url = proxy_url
        self._session = None
        self._public_session = None
        self._graphql_session = None
        self._scope_locks = {}
        self._spacing_locks = {}
        self._http_routes = approved_routes(http_routes) if http_routes is not None else None
        self._route_sessions = {}
        self._session_active = {}
        self.last_http_route = "legacy"
        self._next_start = {}
        self._circuit_versions = {}
        self._circuits = {}
        self._half_open_scopes = set()
        self._endpoint_circuits = {}
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
        until, strikes = self._circuits.get(rate_scope, (0, 0))
        probe = bool(strikes and self._clock() >= until)
        if rate_scope in self._half_open_scopes:
            raise YahooRateLimited(1)
        if probe:
            self._half_open_scopes.add(rate_scope)
        try:
            return await self._request_impl(method, url, rate_scope=rate_scope, **kwargs)
        finally:
            if probe:
                self._half_open_scopes.discard(rate_scope)
                # A failed probe cannot unleash all RSS readers on the next tick.
                if rate_scope in self._circuits and self._circuits[rate_scope][0] <= self._clock():
                    self._circuits[rate_scope] = (self._clock() + 5, strikes)

    async def _request_impl(self, method: str, url: str, *, rate_scope="api", **kwargs: Any):
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with asyncio.timeout(float(kwargs.get('timeout') or 15)),self._scope_locks.setdefault(rate_scope, asyncio.Semaphore(4 if rate_scope == "rss" else 1)),http_budget('yahoo_graphql_collector' if rate_scope == 'graphql' else 'yahoo_collector'):
            endpoint_until, endpoint_status = self._endpoint_circuits.get(url, (0, 0))
            if self._clock() < endpoint_until:
                raise YahooEndpointUnavailable(
                    f"Yahoo endpoint circuit open HTTP {endpoint_status}",
                    request=httpx.Request(method, url),
                    response=httpx.Response(endpoint_status),
                )
            version = self._circuit_versions.get(rate_scope, 0)
            until, strikes = self._circuits.get(rate_scope, (0, 0))
            if self._clock() < until:
                raise YahooRateLimited(until - self._clock())
            async with self._spacing_locks.setdefault(rate_scope,asyncio.Lock()):
                await self._sleep(max(0, self._next_start.get(rate_scope, 0) - self._clock()))
                gap = min(self._gap, .25) if rate_scope == "rss" else self._gap
                jitter = min(self._jitter, .05) if rate_scope == "rss" else self._jitter
                self._next_start[rate_scope] = self._clock() + gap + random.uniform(0, jitter)
            until, strikes = self._circuits.get(rate_scope, (0, 0))
            if self._clock() < until:
                raise YahooRateLimited(until - self._clock())
            if rate_scope not in {'rss', 'graphql'} and self._session is None and self._http_routes is None:
                options = {"impersonate": "chrome", "max_clients": 1}
                if self._proxy_url:
                    options["proxy"] = self._proxy_url
                self._session = self._factory(**options)
            try:
                if self._http_routes is None:
                    session = self._session
                    if rate_scope == "graphql":
                        if self._graphql_session is None:
                            options = {"impersonate": "chrome", "max_clients": 1}
                            if self._proxy_url:
                                options["proxy"] = self._proxy_url
                            self._graphql_session = self._factory(**options)
                        session = self._graphql_session
                    if rate_scope == "rss":
                        if self._public_session is None:
                            options = {"impersonate": "chrome", "max_clients": 4}
                            if self._proxy_url:
                                options["proxy"] = self._proxy_url
                            self._public_session = self._factory(**options)
                        session = self._public_session
                    from .poll_trace import request_started
                    response = await self._send(session, method, url, **kwargs)
                    event=getattr(response,'bus_request_event',None)
                    if event is not None:
                        from .schema import utc_now
                        event.update(response_received_at=utc_now().isoformat(), status_code=response.status_code)
                else:

                    async def send(proxy):
                        key = (rate_scope, proxy)
                        if key not in self._route_sessions:
                            self._route_sessions[key] = self._factory(
                                impersonate="chrome",
                                max_clients=4 if rate_scope == "rss" else 1,
                                proxy=proxy or "",
                                trust_env=False,
                            )
                        options = {**kwargs, "timeout": min(10, float(kwargs.get("timeout") or 30))}
                        from .poll_trace import request_started
                        from .schema import utc_now
                        response = await self._send(self._route_sessions[key],method,url,**options)
                        event=getattr(response,'bus_request_event',None)
                        if event is not None:
                            event.update(response_received_at=utc_now().isoformat(), status_code=response.status_code)
                        return response

                    async with asyncio.timeout(float(kwargs.get("timeout") or 30)):
                        response, self.last_http_route = await transport_routes(
                            send,
                            self._http_routes,
                            (RequestsError, httpx.TransportError),
                        )
            except RequestsError as exc:
                raise httpx.RequestError(
                    "Yahoo browser transport request failed", request=httpx.Request(method, url)
                ) from exc
            if response.status_code == 429:
                strikes = self._circuits.get(rate_scope, (0, 0))[1] + 1
                self._circuit_versions[rate_scope] = self._circuit_versions.get(rate_scope, 0) + 1
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
                if response.status_code in {404, 405, 410}:
                    self._endpoint_circuits[url] = (self._clock() + 600, response.status_code)
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
            if self._circuit_versions.get(rate_scope, 0) == version:
                self._circuits.pop(rate_scope, None)
            return response

    async def _send(self, session, method, url, **options):
        identity=id(session)
        self._session_active[identity]=self._session_active.get(identity,0)+1
        try:
            return await session.request(method,url,**options)
        finally:
            remaining=self._session_active[identity]-1
            if remaining:self._session_active[identity]=remaining
            else:self._session_active.pop(identity,None)
            if getattr(session,'broken',False):
                if self._session is session:self._session=None
                if self._public_session is session:self._public_session=None
                if self._graphql_session is session:self._graphql_session=None
                self._route_sessions={k:v for k,v in self._route_sessions.items() if v is not session}
                if not remaining:
                    await (getattr(session,'aclose',None) or session.close)()

    async def _close(self):
        if self._graphql_session is not None:
            close = getattr(self._graphql_session, "aclose", None) or self._graphql_session.close
            await close()
            self._graphql_session = None
        if self._public_session is not None:
            close = getattr(self._public_session, "aclose", None) or self._public_session.close
            await close()
            self._public_session = None
        for session in self._route_sessions.values():
            close = getattr(session, "aclose", None) or session.close
            await close()
        self._route_sessions.clear()
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


def shared_yahoo_transport(
    proxy_url: str | None = None, *, http_routes: tuple[str, ...] | None = None
) -> YahooTransport:
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = YahooTransport(proxy_url=proxy_url, http_routes=http_routes)
            atexit.register(_shared.close)
        return _shared
