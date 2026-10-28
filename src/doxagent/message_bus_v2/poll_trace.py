"""Actual transport observations; a permit grant alone is never a network request."""

import asyncio
from contextvars import ContextVar
from hashlib import sha256
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary
from uuid import uuid4

from .schema import utc_now

REQUESTS = ContextVar("poll_requests", default=None)
DEMAND = ContextVar("poll_demand", default=None)
REQUEST_ROLE = ContextVar("poll_request_role", default="listing")
_HTTP_POOLS = WeakKeyDictionary()


def validate_listing(events, request_ids=None, *, since=0):
    """Bind parser validation to an unambiguous transport, never a caller timestamp."""
    candidates=[r for r in events[since:] if r.get('role','listing')=='listing'
        and r.get('proof_basis')=='TRANSPORT_SEND' and r.get('response_received_at')
        and not r.get('error_type') and (r.get('status_code') is None or 200<=r['status_code']<400)]
    if request_ids is not None:
        candidates=[r for r in candidates if r['physical_request_id'] in request_ids]
    elif len({r['physical_request_id'] for r in candidates})!=1:
        return []
    for record in candidates:record['list_validated']=True
    return candidates


def request_started(method, url, transport="http", *, admitted=False):
    from .collection_override import check_request_allowed

    if not admitted:
        check_request_allowed()
    events = REQUESTS.get()
    if events is None:
        return None
    value = str(url)
    parsed = urlsplit(value)
    event = {
        "physical_request_id": uuid4().hex,
        "role": REQUEST_ROLE.get(),
        "request_key": sha256((method + ":" + value).encode()).hexdigest(),
        "endpoint": (
            (parsed.hostname or "") + (f":{parsed.port}" if parsed.port else "") + parsed.path
        ),
        "transport": transport,
        "request_started_at": utc_now().isoformat(),
        "proof_basis": "TRANSPORT_SEND" if admitted else "CALLER_OBSERVATION",
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
            request.extensions["_bus_poll_event"] = request_started(request.method, request.url, admitted=True)

    request.extensions["trace"] = trace


async def httpx_response(response):
    event = response.request.extensions.get("_bus_poll_event")
    if event is not None:
        event.update(headers_received_at=utc_now().isoformat(), status_code=response.status_code,
                     content_type=response.headers.get('content-type'))
        await response.aread()
        event["response_received_at"] = utc_now().isoformat()
        event["validators"] = {
            key: response.headers[key]
            for key in ("date", "age", "etag", "last-modified", "cache-control", "expires", "via")
            if key in response.headers
        }


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
