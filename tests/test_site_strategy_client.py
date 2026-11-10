from __future__ import annotations

import httpx
import pytest

from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.schema import AccessMode, AccessRequest, SitePurpose


def _access_request(*, method: str = "GET") -> AccessRequest:
    return AccessRequest(
        operation_id="trendforce-poll",
        purpose=SitePurpose.CRAWLER,
        url="https://www.trendforce.com/news/",
        mode=AccessMode.BROWSER,
        method=method,
    )


async def _client_with_transport(transport: httpx.MockTransport) -> SiteAccessClient:
    client = SiteAccessClient(base_url="http://site-access")
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url="http://site-access", transport=transport)
    return client


@pytest.mark.parametrize("action", ["resolve", "execute"])
@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout])
async def test_owner_connection_failure_keeps_type_and_identifies_shared_dependency(action, error_type):
    error = error_type("owner unavailable")
    calls = 0

    def respond(_request):
        nonlocal calls
        calls += 1
        raise error

    client = await _client_with_transport(httpx.MockTransport(respond))
    try:
        with pytest.raises(error_type) as caught:
            if action == "resolve":
                await client.resolve("https://www.trendforce.com/news/")
            else:
                await client.execute(_access_request())
    finally:
        await client.close()
    assert caught.value is error
    assert error.reason_code == "site_access_unavailable"
    assert calls == 1


@pytest.mark.parametrize("action", ["resolve", "execute"])
async def test_owner_failure_after_stale_connection_retry_is_still_identified(action):
    calls = 0
    error = httpx.ConnectError("owner stopped after pooled connection closed")

    def respond(_request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadError("stale pooled connection")
        raise error

    client = await _client_with_transport(httpx.MockTransport(respond))
    try:
        with pytest.raises(httpx.ConnectError) as caught:
            if action == "resolve":
                await client.resolve("https://www.trendforce.com/news/")
            else:
                await client.execute(_access_request())
    finally:
        await client.close()
    assert caught.value is error and error.reason_code == "site_access_unavailable"
    assert calls == 2


async def test_publisher_refusal_result_is_not_reclassified_as_owner_failure():
    request = _access_request()
    payload = dict(request_id=request.request_id, operation_id=request.operation_id,
        disposition="ACCESS_EXHAUSTED", site_id="trendforce", runtime_key="trendforce",
        strategy_revision=1, reason_code="http_403")
    client = await _client_with_transport(httpx.MockTransport(lambda _: httpx.Response(200,json=payload)))
    try:
        result = await client.execute(request)
    finally:
        await client.close()
    assert result.disposition.value == "ACCESS_EXHAUSTED" and result.reason_code == "http_403"


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [httpx.ReadError, httpx.RemoteProtocolError])
async def test_read_only_access_retries_once_with_same_request_id(
    error_type: type[Exception],
) -> None:
    request = _access_request()
    seen_ids: list[str] = []

    def respond(http_request: httpx.Request) -> httpx.Response:
        seen_ids.append(http_request.read().decode())
        if len(seen_ids) == 1:
            raise error_type("connection closed while reading")
        return httpx.Response(
            200,
            json={
                "request_id": request.request_id,
                "operation_id": request.operation_id,
                "disposition": "SUCCESS",
                "site_id": "trendforce",
                "runtime_key": "trendforce",
                "strategy_revision": 1,
            },
        )

    client = await _client_with_transport(httpx.MockTransport(respond))
    try:
        result = await client.execute(request)
    finally:
        await client.close()
    assert result.disposition.value == "SUCCESS"
    assert len(seen_ids) == 2
    assert seen_ids[0] == seen_ids[1]
    assert request.request_id in seen_ids[0]


@pytest.mark.asyncio
async def test_read_only_access_stops_after_one_retry() -> None:
    calls = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadError("connection closed while reading")

    client = await _client_with_transport(httpx.MockTransport(respond))
    try:
        with pytest.raises(httpx.ReadError):
            await client.execute(_access_request())
    finally:
        await client.close()
    assert calls == 2


@pytest.mark.asyncio
async def test_mutating_access_is_not_retried() -> None:
    calls = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadError("connection closed while reading")

    client = await _client_with_transport(httpx.MockTransport(respond))
    try:
        with pytest.raises(httpx.ReadError):
            await client.execute(_access_request(method="POST"))
    finally:
        await client.close()
    assert calls == 1


@pytest.mark.asyncio
async def test_resolve_retries_read_error_once() -> None:
    calls = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadError("connection closed while reading")
        return httpx.Response(
            200,
            json={
                "site_id": "trendforce",
                "runtime_key": "trendforce",
                "strategy_revision": 1,
                "body": {"ref": "builtin:generic@1"},
                "access": {"combinations": []},
            },
        )

    client = await _client_with_transport(httpx.MockTransport(respond))
    try:
        resolved = await client.resolve("https://www.trendforce.com/news/")
    finally:
        await client.close()
    assert resolved.site_id == "trendforce"
    assert calls == 2


@pytest.mark.asyncio
async def test_access_http_timeout_follows_request_budget_not_global_default() -> None:
    request = _access_request().model_copy(update={"remaining_budget_ms": 45_000})
    observed = []
    def respond(http_request: httpx.Request) -> httpx.Response:
        observed.append(http_request.extensions["timeout"])
        return httpx.Response(200, json={
            "request_id": request.request_id, "operation_id": request.operation_id,
            "disposition": "SUCCESS", "site_id": "trendforce", "runtime_key": "trendforce",
            "strategy_revision": 1,
        })
    client = await _client_with_transport(httpx.MockTransport(respond))
    try:
        await client.execute(request)
    finally:
        await client.close()
    assert observed[0]["read"] == 60
    assert observed[0]["connect"] == 5
