"""Publisher-local curl lifetimes and small role-wide network budgets."""

from __future__ import annotations

import asyncio
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass
from weakref import WeakKeyDictionary

from curl_cffi import CurlOpt
from curl_cffi.requests import AsyncSession

log = logging.getLogger(__name__)
_LEASE = ContextVar("publisher_curl_lease", default=None)
_BUDGETS = WeakKeyDictionary()
HTTP_CONTEXT = ContextVar("message_http_context", default=None)
PROCESS_ROLE = "collector"


def http_budget(role):
    # Collector direct + Site crawler together form the twelve-slot crawler
    # envelope. Site BODY/Reader has its own four slots, never Chrome permits.
    # Yahoo owns a dedicated loop. Split its two slots explicitly rather than
    # accidentally creating a second "global" collector budget on that loop.
    if HTTP_CONTEXT.get() == "body" and role in {"collector", "yahoo_collector", "body"}:
        role = "body"
    elif PROCESS_ROLE == "sweep" and role in {"collector", "yahoo_collector"}:
        role = "sweep" if role == "collector" else "yahoo_sweep"
    limits = {
        "collector": 5,
        "yahoo_collector": 1,
        "sweep": 1,
        "yahoo_sweep": 1,
        "site_crawler": 4,
        "body": 2,
        "site_body": 2,
    }
    pools = _BUDGETS.setdefault(asyncio.get_running_loop(), {})
    return pools.setdefault(role, asyncio.Semaphore(limits[role]))


class PublisherSession(AsyncSession):
    """Return capacity even when curl 0.15 setup/cleanup raises.

    This is a lifetime fix, not a second retry mechanism. Header bytes are never
    logged: the debug callback only timestamps the actual outgoing request.
    """

    broken = False

    async def pop_curl(self):
        curl = await super().pop_curl()
        lease = _LEASE.get()
        if lease is not None:
            lease.update(curl=curl, released=False)
            from .collection_override import check_request_allowed

            check_request_allowed()

            def sent(kind, _data):
                if (
                    kind == 2
                    and lease.get("sent_at") is None
                    and _data.startswith((lease["method"] + " ").encode())
                ):
                    from .poll_trace import REQUESTS, request_started
                    from .schema import utc_now

                    lease["sent_at"] = utc_now().isoformat()
                    token = REQUESTS.set(lease["requests"])
                    try:
                        lease["event"] = request_started(
                            lease["method"], lease["url"], "publisher_curl", admitted=True
                        )
                    finally:
                        REQUESTS.reset(token)

            curl.setopt(CurlOpt.VERBOSE, 1)
            curl.setopt(CurlOpt.DEBUGFUNCTION, sent)
        return curl

    def release_curl(self, curl):
        lease = _LEASE.get()
        if lease is not None and lease.get("curl") is curl:
            if lease.get("released"):
                return
            lease["released"] = True
        try:
            super().release_curl(curl)
        except Exception as exc:
            self.broken = True
            try:
                curl.close()
            except Exception:
                pass
            if not self._closed:
                self.push_curl(None)
            log.warning("publisher curl discarded after cleanup failure: %s", type(exc).__name__)

    async def _request_once(self, **kwargs):
        from .poll_trace import REQUESTS

        lease = {
            "method": kwargs["method"],
            "url": kwargs["url"],
            "event": None,
            "requests": REQUESTS.get(),
            "sent_at": None,
        }
        token = _LEASE.set(lease)
        started = time.monotonic()
        body = None
        if not kwargs.get("stream") and not kwargs.get("content_callback"):
            body = bytearray()

            def receive(chunk):
                if len(body) + len(chunk) > 4 * 1024 * 1024:
                    return 0
                body.extend(chunk)
                return len(chunk)

            kwargs["content_callback"] = receive
        try:
            response = await super()._request_once(**kwargs)
            if body is not None:
                response.content = bytes(body)
            response.bus_request_event = lease["event"]
            response.bus_sent_at = lease["sent_at"]
            return response
        finally:
            timeout = kwargs.get("timeout", 8)
            timeout = float(timeout) if isinstance(timeout, (int, float)) else 8
            if not lease["sent_at"] and time.monotonic() - started >= 0.9 * timeout:
                self._silent_timeouts = getattr(self, "_silent_timeouts", 0) + 1
                if not lease.get("curl") or self._silent_timeouts >= 2:
                    self.broken = True
            elif lease["sent_at"]:
                self._silent_timeouts = 0
            # set_curl_options is outside upstream's finally. Streams retain
            # their own callback lifetime; non-stream setup errors need us here.
            if not kwargs.get("stream") and lease.get("curl") and not lease.get("released"):
                self.release_curl(lease["curl"])
            _LEASE.reset(token)


@dataclass
class _Client:
    session: object
    slots: asyncio.Semaphore
    active: int = 0
    used_at: float = 0
    retired: bool = False


class PublisherClients:
    def __init__(self, *, session=None, factory=None):
        self.clients = {}
        self.injected = session
        self.factory = factory or (
            lambda: PublisherSession(impersonate="chrome", trust_env=False, max_clients=2)
        )
        self._maintenance_at = 0

    async def request(self, key, method, url, *, role="collector", timeout=8, **kwargs):
        if time.monotonic() - self._maintenance_at > 60:
            self._maintenance_at = time.monotonic()
            await self.close_idle()
        async with asyncio.timeout(timeout):
            while True:
                client = self.clients.get(key)
                if client is None:
                    client = self.clients[key] = _Client(
                        self.injected or self.factory(),
                        asyncio.Semaphore(2),
                        used_at=time.monotonic(),
                    )
                async with client.slots, http_budget(role):
                    if client.retired:
                        continue
                    from .collection_override import check_request_allowed

                    check_request_allowed()
                    client.active += 1
                    try:
                        return await client.session.request(method, url, timeout=timeout, **kwargs)
                    except TimeoutError:
                        pool = getattr(client.session, "pool", None)
                        if pool is not None and pool.qsize() == 0:
                            client.retired = True
                        raise
                    finally:
                        client.active -= 1
                        client.used_at = time.monotonic()
                        if client.retired or getattr(client.session, "broken", False):
                            if self.clients.get(key) is client:
                                self.clients.pop(key)
                            client.retired = True
                        if client.retired and not client.active:
                            await client.session.close()

    async def close_idle(self, seconds=180):
        for key, client in list(self.clients.items()):
            if not client.active and time.monotonic() - client.used_at > seconds:
                self.clients.pop(key)
                client.retired = True
                await client.session.close()

    async def close(self):
        clients = list(self.clients.values())
        self.clients.clear()
        for session in {id(c.session): c.session for c in clients}.values():
            await session.close()


class _HttpxSession:
    def __init__(self, options):
        import httpx
        from .poll_trace import httpx_request

        self.client = httpx.AsyncClient(
            **options,
            limits=httpx.Limits(
                max_connections=2, max_keepalive_connections=2, keepalive_expiry=30
            ),
            event_hooks={"request": [httpx_request]},
        )
        self.broken = False

    async def request(self, method, url, **kwargs):
        import httpx
        from .schema import utc_now

        try:
            async with self.client.stream(method, url, **kwargs) as response:
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > 4 * 1024 * 1024:
                        raise ValueError("publisher_response_exceeds_4mib")
                    data.extend(chunk)
                response._content = bytes(
                    data
                )  # match httpx.Response.aread without unbounded buffering
                event = response.request.extensions.get("_bus_poll_event")
                if event is not None:
                    event.update(
                        response_received_at=utc_now().isoformat(),
                        status_code=response.status_code,
                        validators={
                            k: v
                            for k, v in response.headers.items()
                            if k.lower() in {"date", "age", "etag", "last-modified"}
                        },
                    )
                return response
        except httpx.PoolTimeout:
            self.broken = True
            raise

    async def close(self):
        await self.client.aclose()


class PublisherHttpx:
    """The adapter HTTP API, with independent publisher transports and a role budget."""

    def __init__(self, **options):
        self.timeout = float(options.pop("timeout", 8))
        self.clients = PublisherClients(factory=lambda: _HttpxSession(options))

    async def get(self, url, **kwargs):
        from urllib.parse import urlsplit
        from httpx import Timeout
        from doxagent.site_strategy.scope import current_routing

        timeout = kwargs.pop("timeout", self.timeout)
        if isinstance(timeout, Timeout):
            timeout = timeout.read or timeout.connect or self.timeout
        routing = current_routing()
        publisher = urlsplit(str(url)).netloc
        if routing and HTTP_CONTEXT.get() != "body":
            publisher = (routing.origin_scope_id, publisher)
        return await self.clients.request(publisher, "GET", url, timeout=timeout, **kwargs)

    async def aclose(self):
        await self.clients.close()
