from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from html import escape
from types import SimpleNamespace
from unittest.mock import AsyncMock
from xml.etree.ElementTree import ParseError

import httpx
import pytest

from doxagent.message_bus_v2.industry_sources import SharedFeedAdapter, normalize_browser_feed
from doxagent.site_strategy import runtime as module
from doxagent.site_strategy.schema import BrowserProfile


def make_runtime(monkeypatch, *, select_error=None):
    monkeypatch.setattr(module, "public_url", AsyncMock())
    page = SimpleNamespace(url="about:blank")
    cdp = SimpleNamespace(send=AsyncMock())

    async def send(method, params=None, **kwargs):
        if method == "Target.attachToTarget":
            return {"sessionId": "navigation"}
        if method == "Page.navigate":
            return {"errorText": "net::ERR_CONNECTION_CLOSED"}
        return {}

    cdp.send.side_effect = send
    lease = SimpleNamespace(
        browser_cdp=cdp, __aenter__=AsyncMock(return_value=page), __aexit__=AsyncMock()
    )
    gate = SimpleNamespace(start=AsyncMock(), close=AsyncMock(), root_target_id="owned")
    monkeypatch.setattr(module, "_DocumentNavigationGate", lambda *args: gate)
    runtime = module.SiteAccessRuntime.__new__(module.SiteAccessRuntime)
    runtime.resolver = SimpleNamespace(resolve=lambda url: SimpleNamespace(site_id="test"))
    runtime.repository = SimpleNamespace(
        get_strategy=lambda site: SimpleNamespace(
            domains=[SimpleNamespace(host="example.com", exclude=False)]
        )
    )
    runtime.browser_runtimes = SimpleNamespace(
        page=AsyncMock(return_value=lease),
        select_maintenance=AsyncMock(side_effect=select_error),
    )
    runtime._maintenance_pages = {}
    profile = BrowserProfile(
        profile_id="test", site_id="test", bound_egress_id="proxy", directory_key="test"
    )
    return runtime, profile, lease, gate


@pytest.mark.asyncio
async def test_navigation_failure_retains_real_window_and_token(monkeypatch):
    runtime, profile, lease, _ = make_runtime(monkeypatch)
    result = await runtime.open_login(
        profile, SimpleNamespace(egress_id="proxy"), "https://example.com/login"
    )
    token = result["login_token"]
    await runtime._maintenance_pages[token].navigation_task
    inspection = await runtime.inspect_login(token)
    assert inspection["navigation_state"] == "FAILED"
    assert inspection["navigation_error"] == "net::ERR_CONNECTION_CLOSED"
    lease.__aexit__.assert_not_awaited()
    assert await runtime.close_login(token) == "test"
    lease.__aexit__.assert_awaited_once()
    assert token not in runtime._maintenance_pages


@pytest.mark.asyncio
async def test_viewer_selection_failure_releases_owned_page(monkeypatch):
    runtime, profile, lease, gate = make_runtime(monkeypatch, select_error=RuntimeError("secret"))
    with pytest.raises(module.MaintenanceOpenError, match="viewer_selection") as caught:
        await runtime.open_login(
            profile, SimpleNamespace(egress_id="proxy"), "https://example.com/login"
        )
    assert "secret" not in str(caught.value)
    gate.close.assert_awaited_once()
    lease.__aexit__.assert_awaited_once()
    assert not runtime._maintenance_pages


@pytest.mark.asyncio
@pytest.mark.parametrize("network_error", [False, True])
async def test_globe_explicit_feed_fallback_accepts_real_xml_only(network_error):
    xml = (
        "<rss><channel><item><title>Semiconductor capacity update</title>"
        "<link>https://www.globenewswire.com/news-release/123/example.html</link>"
        "<pubDate>Wed, 14 Oct 2026 01:00:00 GMT</pubDate></item></channel></rss>"
    )

    @asynccontextmanager
    async def permit():
        yield

    source = SimpleNamespace(
        source_id="globenewswire_semiconductors_rss",
        content_language="en",
        entry_url="https://www.globenewswire.com/feed",
        default_parameters={"feed_browser_fallback": True},
    )
    context = SimpleNamespace(
        source=source,
        run_id="test",
        request_permit=permit,
        requested_at=datetime(2026, 10, 14, 1, 5, tzinfo=UTC),
        window_start=None,
    )
    browser = SimpleNamespace(
        get=AsyncMock(
            return_value=(200, source.entry_url, {}, "<html><pre>" + escape(xml) + "</pre></html>")
        )
    )
    def respond(request):
        if network_error:
            raise httpx.ReadError("connection reset", request=request)
        return httpx.Response(403, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await SharedFeedAdapter(client, "GlobeNewswire", browser=browser).poll_shared(
            context
        )
    assert len(result.messages) == 1
    assert result.acquisition_metadata["http_route"] == "managed_browser"
    assert browser.get.call_args.kwargs["remaining_budget_ms"] <= 30_000
    with pytest.raises(ParseError):
        normalize_browser_feed("<html>Access is temporarily restricted</html>")


def test_net_error_reason_is_safe_and_specific():
    from doxagent.site_strategy.service import classify_exception

    category, reason = classify_exception(
        RuntimeError("Page.goto net::ERR_CONNECTION_CLOSED at https://example.com/?token=secret")
    )
    assert reason == "net::ERR_CONNECTION_CLOSED" and "secret" not in reason


@pytest.mark.asyncio
async def test_yahoo_endpoint_circuit_does_not_block_rss():
    from tests.test_yahoo_browser_transport import make_transport

    requests = []

    def respond(request):
        requests.append(request.url.path)
        return httpx.Response(404 if request.url.path == "/xhr/ncp" else 200, text="ok")

    transport, _ = make_transport(respond)
    try:
        for _ in range(2):
            with pytest.raises(httpx.HTTPStatusError):
                await transport.request("POST", "https://finance.yahoo.com/xhr/ncp")
        assert (await transport.request("GET", "https://finance.yahoo.com/rss")).status_code == 200
        assert requests == ["/xhr/ncp", "/rss"]
    finally:
        transport.close()


@pytest.mark.asyncio
async def test_yahoo_approved_transport_fallback_keeps_cookie_sessions_separate():
    from tests.test_yahoo_browser_transport import make_transport
    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ConnectError("primary failed", request=request)
        return httpx.Response(200, text="ok")

    transport, factories = make_transport(
        respond, http_routes=("de-standard-1", "tr-standard-1")
    )
    try:
        result = await transport.request("GET", "https://finance.yahoo.com/rss")
        assert result.status_code == 200 and len(requests) == 2
        assert transport.last_http_route == "tr-standard-1"
        assert [f["proxy"] for f in factories] == [
            "http://doxagent-egress-clash:18083", "http://doxagent-egress-clash:18085"
        ]
        assert all(not f["trust_env"] for f in factories)
    finally:
        transport.close()
