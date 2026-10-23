"""Real local Chromium canary using a disposable profile, never a production identity."""

import asyncio
from pathlib import Path

import pytest

from doxagent.site_strategy.runtime import PersistentBrowserPool
from doxagent.site_strategy.schema import BrowserProfile, ProxyEgress


@pytest.mark.parametrize("pages", [2, 4])
async def test_real_pages_share_profile_and_cancel_does_not_close_sibling(tmp_path, pages):
    from playwright.async_api import async_playwright

    async with async_playwright() as browser_tool:
        if not Path(browser_tool.chromium.executable_path).exists():
            pytest.skip("local Chromium is not installed")
    pool = PersistentBrowserPool(
        tmp_path / "profiles",
        headless=True,
        channel=None,
        max_processes=1,
        max_pages=pages,
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
            if len(entered) == pages:
                both.set()
            await release.wait()
            return await page.locator("p").inner_text()

    tasks = [asyncio.create_task(read(str(i))) for i in range(pages)]
    first = tasks[0]
    try:
        await asyncio.wait_for(both.wait(), 20)
        assert len(pool._entries) == 1
        assert pool._entries[profile.profile_id].active_pages == pages
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        release.set()
        assert await asyncio.wait_for(asyncio.gather(*tasks[1:]), 5) == [
            str(i) for i in range(1, pages)
        ]
        assert pool._entries[profile.profile_id].active_pages == 0
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await pool.close()
