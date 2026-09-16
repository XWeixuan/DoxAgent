from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from doxagent.message_bus_v2.news_adapters import YahooFinanceNewsAdapter
from doxagent.message_bus_v2.yahoo_sources import (
    YahooPageUnavailable,
    capture_latest_news,
    hydration_news_rows,
    rss_yahoo_rows,
)
from doxagent.message_bus_v2.yahoo_transport import YahooRateLimited
from doxagent.settings import DoxAgentSettings
from tests.test_message_bus_v2_news_sources import _context
from tests.test_yahoo_browser_transport import make_transport

RSS = """<rss version="2.0"><channel><item>
<title>Micron &amp; chips</title><link>https://finance.yahoo.com/news/micron-123.html</link>
<guid>article-guid</guid><pubDate>Mon, 14 Sep 2026 11:00:00 GMT</pubDate>
<description><![CDATA[<p>Real summary</p>]]></description>
</item></channel></rss>"""


async def test_ncp_429_rss_success_retains_api_circuit(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return (
            httpx.Response(429) if request.url.path == "/xhr/ncp" else httpx.Response(200, text=RSS)
        )

    transport, _ = make_transport(handler)
    try:
        context, _ = _context(tmp_path, "yahoo_finance_news", {})
        async with httpx.AsyncClient() as client:
            adapter = YahooFinanceNewsAdapter(
                DoxAgentSettings(_env_file=None), client, transport=transport
            )
            for _ in range(2):
                result = await adapter.poll(context)
                assert result.acquisition_metadata["query_mode"] == "legacy_headline_rss"
                assert result.window_coverage == "PARTIAL"
                assert result.messages[0].summary == "Real summary"
                assert result.messages[0].body is None
                assert result.messages[0].publication_time_basis == "EXACT"
            with pytest.raises(YahooRateLimited):
                await transport.request("GET", "https://query1.finance.yahoo.com/test")
        assert [r.url.host for r in requests] == [
            "finance.yahoo.com",
            "feeds.finance.yahoo.com",
            "feeds.finance.yahoo.com",
        ]
    finally:
        transport.close()


async def test_page_capture_priority_and_failed_probe_cooldown(tmp_path):
    class Browser:
        calls = 0

        async def yahoo_latest_news(self, ticker, **kwargs):
            self.calls += 1
            raise YahooPageUnavailable("no native response")

    browser = Browser()
    transport, _ = make_transport(lambda _: httpx.Response(200, json={"data": []}))
    try:
        context, _ = _context(tmp_path, "yahoo_finance_news", {"page_network_enabled": True})
        async with httpx.AsyncClient() as client:
            adapter = YahooFinanceNewsAdapter(
                DoxAgentSettings(_env_file=None), client, transport=transport, browser=browser
            )
            await adapter.poll(context)
            await adapter.poll(context)
            assert browser.calls == 1
            adapter._browser_retry_at = 0

            async def successful(ticker, **kwargs):
                return rss_yahoo_rows(RSS), {"network_status": 200}

            browser.yahoo_latest_news = successful
            result = await adapter.poll(context)
            assert result.acquisition_metadata["query_mode"] == "page_network_ncp"
    finally:
        transport.close()


async def test_validated_page_route_is_enabled_by_default(tmp_path):
    class Browser:
        async def yahoo_latest_news(self, ticker, **kwargs):
            return rss_yahoo_rows(RSS), {"network_status": 200, "capture_method": "ssr"}

    transport, _ = make_transport(lambda _: httpx.Response(200, json={"data": []}))
    try:
        context, _ = _context(tmp_path, "yahoo_finance_news", {})
        async with httpx.AsyncClient() as client:
            result = await YahooFinanceNewsAdapter(
                DoxAgentSettings(_env_file=None),
                client,
                transport=transport,
                browser=Browser(),
            ).poll(context)
            assert result.acquisition_metadata["query_mode"] == "page_network_ncp"
    finally:
        transport.close()


class Page:
    url = "https://finance.yahoo.com/quote/MU/latest-news/"

    def __init__(self, symbol="MU"):
        self.symbol = symbol
        self.listener = None

    def on(self, event, listener):
        self.listener = listener

    def remove_listener(self, event, listener):
        assert self.listener == listener
        self.listener = None

    async def goto(self, url, **kwargs):
        async def payload():
            return {"data": []}

        self.listener(
            SimpleNamespace(
                url="https://finance.yahoo.com/xhr/ncp?queryRef=latestNews",
                request=SimpleNamespace(
                    post_data=json.dumps({"serviceConfig": {"s": [self.symbol]}})
                ),
                status=200,
                json=payload,
            )
        )
        await asyncio.sleep(0)
        return SimpleNamespace(status=200)

    async def evaluate(self, script):
        assert script == "window.scrollBy(0, 1200)"  # No synthetic fetch.


async def test_concurrent_page_429_skips_ncp_and_probes_once(tmp_path):
    class Browser:
        calls = 0

        async def yahoo_latest_news(self, ticker, **kwargs):
            self.calls += 1
            await asyncio.sleep(0)
            raise YahooPageUnavailable("rate limited", status_code=429)

    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/xhr/ncp":
            return httpx.Response(429)
        return httpx.Response(200, text=RSS)

    transport, _ = make_transport(handler)
    try:
        context, _ = _context(tmp_path, "yahoo_finance_news", {"page_network_enabled": True})
        browser = Browser()
        async with httpx.AsyncClient() as client:
            adapter = YahooFinanceNewsAdapter(
                DoxAgentSettings(_env_file=None),
                client,
                transport=transport,
                browser=browser,
            )
            await adapter.poll(context)
            assert all(r.url.host == "feeds.finance.yahoo.com" for r in requests)
            await asyncio.gather(*[adapter.poll(context) for _ in range(3)])
            assert browser.calls == 1
            assert len(requests) == 4
    finally:
        transport.close()


async def test_native_network_listener_cleanup_and_ticker_scope():
    page = Page()
    rows, metadata = await capture_latest_news(page, "MU", timeout_seconds=0.1)
    assert rows == [] and metadata["network_status"] == 200
    assert page.listener is None
    other = Page("NVDA")
    with pytest.raises(YahooPageUnavailable):
        await capture_latest_news(other, "MU", timeout_seconds=0.02)
    assert other.listener is None


class SSRPage(Page):
    def __init__(self, *, status=200, scripts=None, fetch_status=200):
        super().__init__()
        self.status, self.scripts, self.fetch_status = status, scripts or [], fetch_status
        self.fetch_calls = []

    async def goto(self, url, **kwargs):
        assert self.listener is not None  # Registration precedes navigation.
        return SimpleNamespace(status=self.status, headers={"retry-after": "600"})

    def locator(self, selector):
        assert selector == "script[data-sveltekit-fetched]"

        async def evaluate_all(script):
            return self.scripts

        return SimpleNamespace(evaluate_all=evaluate_all)

    async def evaluate(self, script, argument=None):
        if argument is None:
            return None
        self.fetch_calls.append(argument)
        assert "credentials: 'include'" in script and "fetch(endpoint" in script
        assert argument["ticker"] == "MU"
        return {
            "status": self.fetch_status,
            "retryAfter": "600",
            "text": json.dumps(
                {
                    "data": [
                        {
                            "content": {
                                "id": "article",
                                "title": "Micron news",
                                "pubDate": "2026-09-14T11:00:00Z",
                            }
                        }
                    ]
                }
            ),
        }


async def test_ssr_page_without_native_xhr_uses_browser_js_fetch():
    page = SSRPage()
    rows, metadata = await capture_latest_news(page, "MU", snippet_count=100)
    assert rows[0]["id"] == "article"
    assert metadata["capture_method"] == "page_js_fetch"
    assert metadata["requested_count"] == 100
    assert page.fetch_calls[0]["count"] == 100 and page.listener is None


async def test_browser_page_429_stops_before_js_fetch_and_preserves_retry_after():
    page = SSRPage(status=429)
    with pytest.raises(YahooPageUnavailable) as error:
        await capture_latest_news(page, "MU")
    assert error.value.status_code == 429
    assert error.value.retry_after_seconds == 600
    assert page.fetch_calls == [] and page.listener is None


async def test_browser_js_429_is_not_empty_success():
    page = SSRPage(fetch_status=429)
    with pytest.raises(YahooPageUnavailable) as error:
        await capture_latest_news(page, "MU")
    assert error.value.status_code == 429 and len(page.fetch_calls) == 1
    assert page.listener is None


async def test_scoped_hydration_json_skips_active_request():
    scripts = [
        {
            "url": "/xhr/ncp?queryRef=latestNews",
            "text": json.dumps(
                {
                    "status": 200,
                    "body": json.dumps(
                        {
                            "data": [
                                {
                                    "content": {
                                        "id": "ssr-article",
                                        "title": "SSR news",
                                        "pubDate": "2026-09-14T11:00:00Z",
                                    }
                                }
                            ]
                        }
                    ),
                }
            ),
        }
    ]
    page = SSRPage(scripts=scripts)
    rows, metadata = await capture_latest_news(page, "MU")
    assert rows[0]["id"] == "ssr-article"
    assert metadata["capture_method"] == "ssr_hydration" and not page.fetch_calls
    assert (
        hydration_news_rows([{"url": "/xhr/ncp?queryRef=other", "text": scripts[0]["text"]}])
        is None
    )


def test_rss_rejects_html_and_keeps_missing_fields():
    with pytest.raises(ValueError):
        rss_yahoo_rows("<html><body>denied</body></html>")
    row = rss_yahoo_rows(
        RSS.replace("<description><![CDATA[<p>Real summary</p>]]></description>", "")
    )[0]
    assert row["description"] is None and row["uuid"] == "article-guid"
