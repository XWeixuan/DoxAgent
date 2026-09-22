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
        browser = await playwright.chromium.connect_over_cdp(
            "http://127.0.0.1:9222"
        )
        browser.on("disconnected", lambda: stop.set())
        if not browser.contexts:
            raise RuntimeError("Chrome CDP attach returned no default context")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(
                os.environ.get("CHROME_START_URL", "https://www.barrons.com/login"),
                wait_until="domcontentloaded",
                timeout=90_000,
            )
        except PlaywrightTimeoutError:
            # A held challenge must remain available for manual completion.
            pass
        await stop.wait()
        if browser.is_connected():
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
