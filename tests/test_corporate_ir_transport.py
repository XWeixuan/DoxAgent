import asyncio
import threading
from unittest.mock import AsyncMock

import pytest

from doxagent.message_bus_v2.corporate_ir.catalog import initial_endpoints
from doxagent.message_bus_v2.corporate_ir.http import Capture, PublicHttp


@pytest.mark.asyncio
async def test_fixed_public_stdlib_recipe_uses_no_browser_or_httpx(monkeypatch):
    http = PublicHttp()
    spec = next(s for s in initial_endpoints() if s.endpoint_id == "be.press")
    calls = []

    def read(url, headers):
        calls.append((url, headers, threading.get_ident()))
        return Capture(304, url, "", {}, 0)

    monkeypatch.setattr(http, "_stdlib_read", read)
    http._client = lambda *a: (_ for _ in ()).throw(AssertionError("wrong HTTP family"))
    http.browser.fetch = AsyncMock(side_effect=AssertionError("public feed must not create Chrome"))
    try:
        result = await http.fetch(spec, headers={"If-None-Match": "known"})
        assert result.status == 304 and calls[0][1]["If-None-Match"] == "known"
        assert calls[0][2] != threading.get_ident()
    finally:
        await http.close()


@pytest.mark.asyncio
async def test_cancelled_stdlib_request_drains_before_releasing_permit(monkeypatch):
    http = PublicHttp()
    started = threading.Event()
    release = threading.Event()

    def read(url, headers):
        started.set()
        assert release.wait(3)
        return Capture(200, url, "ok", {}, 2)

    monkeypatch.setattr(http, "_stdlib_read", read)
    task = asyncio.create_task(http.fetch(initial_endpoints()[3]))
    try:
        while not started.is_set():
            await asyncio.sleep(0.005)
        task.cancel()
        await asyncio.sleep(0.03)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done() and next(iter(http.host_slots.values()))._value == 1
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled() and next(iter(http.host_slots.values()))._value == 2
    finally:
        release.set()
        await http.close()


def test_owner_recipe_upgrade_is_narrow_and_idempotent(tmp_path):
    from doxagent.message_bus_v2.corporate_ir import SOURCE_ID
    from doxagent.message_bus_v2.schema import UpdateActor
    from scripts.ops.configure_corporate_ir_sources import TICKERS, configure
    from tests.fixtures.message_bus_v2 import _bus

    repo, bus = _bus(tmp_path / "bus.db")
    for ticker in TICKERS:
        bus.start_ticker(ticker)
    source = repo.get_source(SOURCE_ID)
    old = []
    for value in source.default_parameters["endpoints"]:
        old.append({**value, "parser_parameters": {}, "revision": 1})
    bus.update_source(SOURCE_ID, {"default_parameters": {"endpoints": old}}, actor=UpdateActor.USER)
    result = configure(bus)
    assert set(result["catalog_recipe_upgrades"]) == {
        "mu.ir",
        "mu.newsroom",
        "be.press",
        "rklb.updates",
    }
    version = repo.get_source(SOURCE_ID).version
    assert not configure(bus)["catalog_recipe_upgrades"]
    assert repo.get_source(SOURCE_ID).version == version


def test_owner_migration_disables_sole_legacy_feed_without_invalid_empty_parameters(tmp_path):
    from doxagent.message_bus_v2.schema import UpdateActor
    from scripts.ops.configure_corporate_ir_sources import TICKERS, configure
    from tests.fixtures.message_bus_v2 import _bus

    repo, bus = _bus(tmp_path / "bus.db")
    for ticker in TICKERS:
        bus.start_ticker(ticker)
    url = initial_endpoints()[0].entry_url
    old = bus.configure_binding(
        ticker="INTC",
        source_id="newswire_rss",
        source_parameters={"rss_urls": [url]},
        actor=UpdateActor.USER,
    )
    first = configure(bus)
    disabled = repo.get_binding(old.binding_id)
    assert not disabled.enabled and disabled.source_parameters["rss_urls"] == [url]
    assert len(first["overlaps"]) == 1
    assert not configure(bus)["overlaps"]
    assert repo.get_binding(old.binding_id).version == disabled.version


@pytest.mark.asyncio
async def test_httpx_conditional_304_is_success_without_location():
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(304, headers={"ETag": "baseline"})
        )
    ) as client:
        http = PublicHttp(client)
        result = await http.fetch(initial_endpoints()[0], headers={"If-None-Match": "baseline"})
        assert result.status == 304 and result.bytes_received == 0
        await http.close()

@pytest.mark.asyncio
async def test_body_curl_route_preserves_native_rss_transport_and_uses_body_budget(monkeypatch):
    from types import SimpleNamespace
    from doxagent.message_bus_v2.publisher_http import HTTP_CONTEXT
    http=PublicHttp()
    original=next(s for s in initial_endpoints() if s.endpoint_id=='mu.ir')
    spec=original.model_copy(update={'parser_parameters':{**original.parser_parameters,'body_http_route':'us-standard-5'}})
    calls=[]
    async def fetch(url,recipe,**kwargs):
        calls.append((url,recipe.route,HTTP_CONTEXT.get()))
        return SimpleNamespace(status=200,url=url,text='article',headers={})
    http.routed_http=SimpleNamespace(fetch=fetch,close=AsyncMock())
    http._stdlib_read=lambda url,headers:Capture(200,url,'<rss/>',{},6)
    try:
        body=await http.fetch(spec,url='http://investors.micron.com/news/press-release/2026/example/default.aspx')
        assert calls==[('https://investors.micron.com/news/press-release/2026/example/default.aspx','us-standard-5','body')]
        assert body.status==200 and HTTP_CONTEXT.get() is None
        await http.fetch(spec)
        assert len(calls)==1
    finally:await http.close()
