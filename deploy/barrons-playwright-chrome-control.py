#!/opt/playwright-venv/bin/python
from __future__ import annotations

import asyncio
import os
import signal

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright


async def main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir="/chrome-profile",
            executable_path="/usr/bin/google-chrome-stable",
            headless=False,
            proxy={
                "server": os.environ.get(
                    "CHROME_PROXY_SERVER", "http://doxagent-egress-clash:18081"
                )
            },
            locale="en-US",
            timezone_id="America/Los_Angeles",
            no_viewport=True,
            args=[
                "--window-size=1440,1000",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )
        context.on("close", lambda: stop.set())
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(
                os.environ.get("CHROME_START_URL", "https://www.barrons.com/login"),
                wait_until="domcontentloaded",
                timeout=90_000,
            )
        except PlaywrightTimeoutError:
            # Keep the visible browser available when a challenge deliberately
            # holds navigation open; the operator, not automation, resolves it.
            pass
        await stop.wait()
        if context.browser is not None and context.browser.is_connected():
            await context.close()


if __name__ == "__main__":
    asyncio.run(main())
