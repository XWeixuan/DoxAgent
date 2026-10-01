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
