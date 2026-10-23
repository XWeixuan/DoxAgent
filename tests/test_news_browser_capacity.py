"""Real local Chromium canary using a disposable profile, never a production identity."""

import asyncio
from pathlib import Path

import pytest

from doxagent.site_strategy.runtime import PersistentBrowserPool
from doxagent.site_strategy.schema import BrowserProfile, ProxyEgress


async def test_two_real_pages_share_profile_and_cancel_does_not_close_sibling(tmp_path):
    from playwright.async_api import async_playwright

    async with async_playwright() as browser_tool:
        if not Path(browser_tool.chromium.executable_path).exists():
            pytest.skip("local Chromium is not installed")
    pool = PersistentBrowserPool(
        tmp_path / "profiles",
        headless=True,
        channel=None,
        max_processes=1,
        max_pages=2,
        idle_seconds=60,
    )
    profile = BrowserProfile(
        profile_id="capacity-test",
        site_id="generic",
        bound_egress_id="test-route",
        directory_key="capacity-test",
    )
    egress = ProxyEgress(
        egress_id="test-route",
        node_ref="test",
        node_fingerprint="test",
        listener_port=9,
        endpoint="http://127.0.0.1:9",
    )
    entered, release = set(), asyncio.Event()
    both = asyncio.Event()

    async def read(name):
        lease = await pool.page(profile, egress)
        async with lease as page:
            await page.goto("data:text/html,<title>Capacity</title><p>" + name + "</p>")
            entered.add(name)
            if len(entered) == 2:
                both.set()
            await release.wait()
            return await page.locator("p").inner_text()

    first, second = asyncio.create_task(read("first")), asyncio.create_task(read("second"))
    try:
        await asyncio.wait_for(both.wait(), 20)
        assert len(pool._entries) == 1
        assert pool._entries[profile.profile_id].active_pages == 2
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        release.set()
        assert await asyncio.wait_for(second, 5) == "second"
        assert pool._entries[profile.profile_id].active_pages == 0
    finally:
        for task in (first, second):
            if not task.done():
                task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
        await pool.close()
