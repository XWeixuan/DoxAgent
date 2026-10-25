"""Actual transport observations; a permit grant alone is never a network request."""

import asyncio
from contextvars import ContextVar
from hashlib import sha256
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary

from .schema import utc_now

REQUESTS = ContextVar("poll_requests", default=None)
DEMAND = ContextVar("poll_demand", default=None)
_HTTP_POOLS = WeakKeyDictionary()


def request_started(method, url, transport="http"):
    from .collection_override import check_request_allowed

    check_request_allowed()
    events = REQUESTS.get()
    if events is None:
        return None
    value = str(url)
    parsed = urlsplit(value)
    event = {
        "request_key": sha256((method + ":" + value).encode()).hexdigest(),
        "endpoint": ((parsed.hostname or "")
                     + (f":{parsed.port}" if parsed.port else "") + parsed.path),
        "transport": transport,
        "request_started_at": utc_now().isoformat(),
    }
    events.append(event)
    return event


async def httpx_request(request):
    prior = request.extensions.get("_bus_original_trace", request.extensions.get("trace"))
    request.extensions["_bus_original_trace"] = prior

    async def trace(name, info):
        if prior is not None:
            await prior(name, info)
        if name in {"http11.send_request_headers.started", "http2.send_request_headers.started"}:
            request.extensions["_bus_poll_event"] = request_started(request.method, request.url)

    request.extensions["trace"] = trace


async def httpx_response(response):
    event = response.request.extensions.get("_bus_poll_event")
    if event is not None:
        event.update(headers_received_at=utc_now().isoformat(), status_code=response.status_code)
        await response.aread()
        event["response_received_at"] = utc_now().isoformat()


def install(client):
    hooks = getattr(client, "event_hooks", None)
    if isinstance(hooks, dict) and httpx_request not in hooks.get("request", []):
        hooks.setdefault("request", []).append(httpx_request)
        hooks.setdefault("response", []).append(httpx_response)
        send = client.send

        async def bounded_send(request, **kwargs):
            loop = asyncio.get_running_loop()
            slots, hosts = _HTTP_POOLS.setdefault(loop, (asyncio.Semaphore(16), {}))
            host = request.url.host
            async with hosts.setdefault(host, asyncio.Semaphore(4)), slots:
                from .collection_override import check_request_allowed

                check_request_allowed()
                return await send(request, **kwargs)

        client.send = bounded_send
