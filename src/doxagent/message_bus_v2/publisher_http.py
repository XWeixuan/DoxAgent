"""Publisher-local curl lifetimes and small role-wide network budgets."""

from __future__ import annotations

import asyncio
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass
from contextlib import asynccontextmanager, contextmanager
from weakref import WeakKeyDictionary

from curl_cffi import CurlOpt
from curl_cffi.requests import AsyncSession
import httpx

log = logging.getLogger(__name__)
_LEASE = ContextVar("publisher_curl_lease", default=None)
_BUDGETS = WeakKeyDictionary()
HTTP_CONTEXT = ContextVar("message_http_context", default=None)
HTTP_SOURCE = ContextVar("message_http_source", default=None)
PROCESS_ROLE = "collector"
RESPONSE_LIMIT = ContextVar("publisher_response_limit", default=4 * 1024 * 1024)


def http_timing(phase, elapsed=None):
    """A few scalars in the existing cycle; never write from the transport."""
    from .scheduler import POLL_TIMINGS
    timings = POLL_TIMINGS.get()
    if timings is not None:
        timings['http_last_phase'] = phase
        if elapsed is not None:
            key = phase + '_ms'
            timings[key] = timings.get(key, 0) + round(elapsed * 1000)


_NETWORK_TIMEOUTS = ContextVar('publisher_network_timeouts', default=())


@asynccontextmanager
async def network_timeout(seconds):
    """Network-phase limit; the collector's total operation deadline still runs."""
    async with asyncio.timeout(seconds) as deadline:
        token = _NETWORK_TIMEOUTS.set((*_NETWORK_TIMEOUTS.get(), deadline))
        try:
            yield deadline
        finally:
            _NETWORK_TIMEOUTS.reset(token)


@contextmanager
def network_wait():
    """Only capacity/pacing waits pause local network timers, never business timers."""
    loop = asyncio.get_running_loop()
    paused = []
    for deadline in _NETWORK_TIMEOUTS.get():
        end = deadline.when()
        if end is not None and not deadline.expired():
            paused.append((deadline, max(0, end - loop.time())))
            deadline.reschedule(None)
    try:
        yield
    finally:
        for deadline, remaining in paused:
            if not deadline.expired():
                deadline.reschedule(loop.time() + remaining)


class _JointAdmission:
    """Loop-local FIFO among eligible requests; never reserve partial capacity."""

    def __init__(self):
        self.pending = []
        self.timer = None

    def drain(self):
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        waiting, delay = [], .05
        for ticket in self.pending:
            slots, source, future = ticket[:3]
            if future.cancelled():
                continue
            pacing = source._next_request_at - source._clock() if source else 0
            if pacing > 0 or any(slot.locked() for slot in slots):
                waiting.append(ticket)
                if pacing > 0:
                    delay = min(delay, max(.001, pacing))
                continue
            for slot in slots:
                slot._value -= 1
            if source:
                source._next_request_at = source._clock() + source.constraints.minimum_request_gap_seconds
            ticket[3] = True
            future.set_result(None)
        self.pending = waiting
        if waiting:
            # Joint releases notify immediately. This single timer also notices
            # releases by legacy Semaphore users; no per-waiter polling or I/O.
            self.timer = asyncio.get_running_loop().call_later(delay, self.drain)

    def release(self, ticket):
        slots, source = ticket[:2]
        ticket[3] = False
        for slot in slots:
            if source and slot is source._semaphore and source._shrink_debt:
                source._shrink_debt -= 1
            else:
                slot.release()
        self.drain()


_ADMISSIONS = WeakKeyDictionary()


@asynccontextmanager
async def joint_permits(*slots, source=None):
    slots = tuple(dict.fromkeys((*slots, source._semaphore) if source else slots))
    loop = asyncio.get_running_loop()
    admission = _ADMISSIONS.setdefault(loop, _JointAdmission())
    ticket = [slots, source, loop.create_future(), False]
    waiting = time.monotonic()
    http_timing('http_admission')
    with network_wait():
        admission.pending.append(ticket)
        admission.drain()
        try:
            await ticket[2]
        except BaseException:
            if ticket[3]:
                admission.release(ticket)
            else:
                admission.drain()
            raise
        finally:
            http_timing('http_admission', time.monotonic() - waiting)
    try:
        if source and source._on_request:
            source._on_request()
        http_timing('http_transport')
        yield
    finally:
        admission.release(ticket)


@asynccontextmanager
async def source_http_scope(permit):
    limiter = getattr(permit, '__self__', None)
    if limiter is None or not hasattr(limiter, 'cost'):
        async with permit():
            yield
        return
    token = HTTP_SOURCE.set(limiter)
    try:
        yield
    finally:
        HTTP_SOURCE.reset(token)


def http_budget(role):
    # Collector direct + Site crawler together form the twelve-slot crawler
    # envelope. Site BODY/Reader has its own four slots, never Chrome permits.
    # Yahoo owns a dedicated loop. Split its two slots explicitly rather than
    # accidentally creating a second "global" collector budget on that loop.
    if HTTP_CONTEXT.get() == "body" and role in {"collector", "yahoo_collector", "yahoo_graphql_collector", "body"}:
        role = "body"
    elif PROCESS_ROLE == "sweep" and role in {"collector", "yahoo_collector", "yahoo_graphql_collector"}:
        role = "sweep" if role == "collector" else "yahoo_sweep"
    limits = {
        "collector": 5,
        "yahoo_collector": 1,
        "yahoo_graphql_collector": 1,
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
        # curl_cffi clears these before curl_multi_remove_handle, which can
        # still invoke DEBUGFUNCTION. Keep CFFI handles alive until detach/reset
        # (or exceptional close) finishes; a dangling DEBUGDATA aborts Python.
        callbacks = tuple(
            getattr(curl, name, None)
            for name in ("_debug_handle", "_write_handle", "_header_handle", "_read_handle", "_body_handle")
        )
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
        finally:
            del callbacks

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
                if len(body) + len(chunk) > RESPONSE_LIMIT.get():
                    return 0
                body.extend(chunk)
                return len(chunk)

            kwargs["content_callback"] = receive
        try:
            response = await super()._request_once(**kwargs)
            if body is not None:
                response.content = bytes(body)
            response.bus_request_event = lease["event"]
            if lease["event"] is not None and not kwargs.get("stream"):
                from .schema import utc_now
                lease["event"].update(response_received_at=utc_now().isoformat(),
                    status_code=response.status_code,
                    validators={k:v for k,v in response.headers.items()
                        if k.lower() in {"date","age","etag","last-modified","cache-control"}})
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
    pre_send_timeouts: int = 0


class PublisherClients:
    def __init__(self, *, session=None, factory=None):
        self.clients = {}
        self.injected = session
        self.factory = factory or (
            lambda: PublisherSession(impersonate="chrome", trust_env=False, max_clients=2)
        )
        self._maintenance_at = 0
        self._closing = set()
        self._pre_send_replaced = set()

    def _close_later(self, client):
        if getattr(client, 'closing', False):
            return
        client.closing = True
        async def close():
            try:
                async with asyncio.timeout(8):
                    await client.session.close()
            except Exception:
                log.exception('publisher client close failed')
        task = asyncio.create_task(close())
        self._closing.add(task)
        task.add_done_callback(self._closing.discard)

    def _transport_timeout(self, key, client, progress):
        if getattr(client.session, 'traced_transport', False) and not progress['sent']:
            client.pre_send_timeouts += 1
            if client.pre_send_timeouts >= 2 and key not in self._pre_send_replaced:
                self._pre_send_replaced.add(key)
                client.retired = True
                log.warning('publisher transport replaced after pre-send timeouts phase=%s', progress['phase'])

    async def request(self, key, method, url, *, role="collector", timeout=8, **kwargs):
        if time.monotonic() - self._maintenance_at > 60:
            self._maintenance_at = time.monotonic()
            # Retirement is a small in-memory inventory; close runs independently.
            for name, idle in list(self.clients.items()):
                if not idle.active and time.monotonic()-idle.used_at>180:
                    self.clients.pop(name)
                    self._pre_send_replaced.discard(name)
                    idle.retired=True
                    self._close_later(idle)
        while True:
            client = self.clients.get(key)
            if client is None:
                client = self.clients[key] = _Client(
                    self.injected or self.factory(),
                    asyncio.Semaphore(2),
                    used_at=time.monotonic(),
                )
            source = HTTP_SOURCE.get()
            waiting = time.monotonic()
            async with joint_permits(client.slots, http_budget(role), source=source):
                if client.retired:
                    continue
                from .collection_override import check_request_allowed

                check_request_allowed()
                client.active += 1
                executing = time.monotonic()
                from .poll_trace import TRANSPORT_PROGRESS
                progress = {'phase': 'transport', 'sent': False}
                progress_token = TRANSPORT_PROGRESS.set(progress)
                if source:
                    from .scheduler import POLL_TIMINGS
                    timings=POLL_TIMINGS.get()
                    if timings is not None:
                        timings['source_http_wait_ms']=timings.get('source_http_wait_ms',0)+int((executing-waiting)*1000)
                try:
                    async with asyncio.timeout(timeout):
                        response = await client.session.request(method, url, timeout=timeout, **kwargs)
                    client.pre_send_timeouts = 0
                    self._pre_send_replaced.discard(key)
                    return response
                except (TimeoutError, httpx.TimeoutException) as exc:
                    # This scope starts after joint admission. Never retire a
                    # healthy publisher just because its caller waited for capacity.
                    exc.transport_phase = progress['phase']
                    if getattr(client.session, 'traced_transport', False):
                        exc.reason_code = 'publisher_response_timeout' if progress['sent'] else 'publisher_pre_send_timeout'
                    self._transport_timeout(key, client, progress)
                    pool = getattr(client.session, "pool", None)
                    if pool is not None and pool.qsize() == 0:
                        client.retired = True
                    raise
                except asyncio.CancelledError:
                    # A parent's equal/shorter deadline may fire before ours.
                    # Ordinary caller cancellation is not evidence of a bad pool.
                    if time.monotonic() - executing >= timeout * .9:
                        self._transport_timeout(key, client, progress)
                    raise
                finally:
                    http_timing('http_transport', time.monotonic() - executing)
                    http_timing(progress['phase'])
                    TRANSPORT_PROGRESS.reset(progress_token)
                    client.active -= 1
                    client.used_at = time.monotonic()
                    if source:
                        source.cost.observe(max(.001,time.monotonic()-executing))
                    if client.retired or getattr(client.session, "broken", False):
                        if self.clients.get(key) is client:
                            self.clients.pop(key)
                        client.retired = True
                    if client.retired and not client.active:
                        self._close_later(client)

    async def close_idle(self, seconds=180):
        for key, client in list(self.clients.items()):
            if not client.active and time.monotonic() - client.used_at > seconds:
                self.clients.pop(key)
                self._pre_send_replaced.discard(key)
                client.retired = True
                await client.session.close()

    async def close(self):
        clients = list(self.clients.values())
        self.clients.clear()
        self._pre_send_replaced.clear()
        for session in {id(c.session): c.session for c in clients}.values():
            await session.close()
        if self._closing:
            await asyncio.gather(*self._closing)


class _HttpxSession:
    traced_transport = True
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
                    if len(data) + len(chunk) > RESPONSE_LIMIT.get():
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
