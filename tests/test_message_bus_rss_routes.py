from types import SimpleNamespace
from contextlib import asynccontextmanager

import httpx
import pytest

from doxagent.message_bus_v2.adapters import NewswireRSSAdapter
from tests.test_message_bus_market_sources import permit


@pytest.mark.asyncio
@pytest.mark.parametrize("direct_status", [None, 403, 429])
async def test_rss_approved_fallback_only_for_transport_errors(monkeypatch, direct_status):
    calls = []
    url = "https://ir.example.test/feed"

    async def direct_get(*args, **kwargs):
        calls.append("direct")
        if direct_status is None:
            raise httpx.ConnectTimeout("blocked direct path")
        return httpx.Response(direct_status, request=httpx.Request("GET", url))

    class ProxyClient:
        def __init__(self, *, proxy, **kwargs):
            assert proxy == "http://doxagent-egress-clash:18083"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, *args, **kwargs):
            calls.append("proxy")
            return httpx.Response(
                200, text="<rss><channel/></rss>", request=httpx.Request("GET", url)
            )

        @asynccontextmanager
        async def stream(self,method,url,**kwargs):
            yield await self.get(url,**kwargs)

    monkeypatch.setattr("doxagent.message_bus_v2.adapters.httpx.AsyncClient", ProxyClient)
    adapter = NewswireRSSAdapter(
        SimpleNamespace(monitoring_rss_user_agent="DoxAgent RSS"), SimpleNamespace(get=direct_get)
    )
    context = SimpleNamespace(
        request_permit=permit,
        binding=SimpleNamespace(source_parameters={"http_routes": ["direct", "de-standard-1"]}),
    )
    if direct_status:
        with pytest.raises(httpx.HTTPStatusError):
            await adapter._feed_text(context, url)
        assert calls == ["direct"]
    else:
        assert "<rss>" in await adapter._feed_text(context, url)
        assert calls == ["direct", "proxy"]


@pytest.mark.asyncio
async def test_rss_unapproved_egress_rejected_before_network():
    async def forbidden(*args, **kwargs):
        pytest.fail("network must not run")

    adapter = NewswireRSSAdapter(
        SimpleNamespace(monitoring_rss_user_agent="test"), SimpleNamespace(get=forbidden)
    )
    context = SimpleNamespace(
        request_permit=permit,
        binding=SimpleNamespace(source_parameters={"http_routes": ["http://arbitrary.proxy"]}),
    )
    with pytest.raises(ValueError, match="unapproved"):
        await adapter._feed_text(context, "https://ir.example.test/feed")
