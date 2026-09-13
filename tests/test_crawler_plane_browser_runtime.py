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
