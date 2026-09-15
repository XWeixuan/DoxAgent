from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.crawler_plane.runtime import PlaywrightBrowserRuntime


@pytest.mark.asyncio
async def test_cdp_runtime_reuses_default_persistent_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = SimpleNamespace(new_page=AsyncMock())
    browser = SimpleNamespace(contexts=[context])
    chromium = SimpleNamespace(connect_over_cdp=AsyncMock(return_value=browser))
    playwright = SimpleNamespace(chromium=chromium, stop=AsyncMock())
    manager = SimpleNamespace(start=AsyncMock(return_value=playwright))
    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: manager)

    runtime = PlaywrightBrowserRuntime(cdp_url="http://127.0.0.1:9222")
    assert await runtime._ensure() is context
    chromium.connect_over_cdp.assert_awaited_once_with("http://127.0.0.1:9222")

    await runtime.close()
    playwright.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_cdp_runtime_rejects_ephemeral_context(monkeypatch: pytest.MonkeyPatch) -> None:
    browser = SimpleNamespace(contexts=[])
    chromium = SimpleNamespace(connect_over_cdp=AsyncMock(return_value=browser))
    playwright = SimpleNamespace(chromium=chromium, stop=AsyncMock())
    manager = SimpleNamespace(start=AsyncMock(return_value=playwright))
    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: manager)

    runtime = PlaywrightBrowserRuntime(cdp_url="http://127.0.0.1:9222")
    with pytest.raises(RuntimeError, match="persistent default context"):
        await runtime._ensure()
    playwright.stop.assert_awaited_once()


async def test_reuters_card_scope_uses_real_dom_not_search_container(monkeypatch):
    """Exercise the actual extraction JavaScript against adjacent article cards."""
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content("""<main><section>Search results for Micron 908 results
              <article><h3><a href="/world/china/first-micron-story-2026-09-14/">
                First Micron manufacturing story</a></h3><time>3 hours ago</time>
                <p>Micron announced investment in a new manufacturing facility.</p></article>
              <article><h3><a href="/business/second-micron-story-2026-09-11/">
                Second Micron unrelated story</a></h3><time>September 11, 2026</time>
                <p>The second article has its own independent summary.</p></article>
              <article><a href="/business/third-micron-story-2026-09-14/">
                Third Micron story without summary</a>
                <p>4 hours ago</p></article></section></main>""")
            proxy = SimpleNamespace(
                goto=AsyncMock(return_value=SimpleNamespace(status=200)),
                wait_for_function=page.wait_for_function,
                evaluate=page.evaluate,
                close=AsyncMock(),
            )
            runtime = PlaywrightBrowserRuntime()
            monkeypatch.setattr(
                runtime,
                "_ensure",
                AsyncMock(return_value=SimpleNamespace(new_page=AsyncMock(return_value=proxy))),
            )
            rows = await runtime.reuters_search("Micron", 0)
            assert len(rows) == 3
            assert (
                rows[0]["summary"] == "Micron announced investment in a new manufacturing facility."
            )
            assert rows[0]["date"] == "2026-09-14"
            assert rows[0]["relative_time"] == "3 hours ago"
            assert rows[1]["date"] == "2026-09-11"
            assert rows[2]["summary"] == ""
            assert all("Search results" not in r["summary"] for r in rows)
        finally:
            await browser.close()
