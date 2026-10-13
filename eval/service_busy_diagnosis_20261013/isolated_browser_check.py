"""Real CDP regression using a disposable local Chrome profile, no network pages."""

import asyncio
import json
import subprocess
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from playwright.async_api import async_playwright

from doxagent.site_strategy.external_runtime import ExternalChromeRuntime
from doxagent.site_strategy.runtime import _close_owned_page, _RawBrowserCDP


async def check(directory):
    active_port = Path(directory) / "DevToolsActivePort"
    for _ in range(200):
        if active_port.exists():
            break
        await asyncio.sleep(0.05)
    port, endpoint = active_port.read_text().splitlines()[:2]
    async with async_playwright() as driver:
        browser = await driver.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        cdp = _RawBrowserCDP(f"ws://127.0.0.1:{port}{endpoint}")
        await cdp.start()
        runtime = ExternalChromeRuntime(None)
        entry = SimpleNamespace(cdp=cdp, context=browser.contexts[0])
        before = await cdp.send("Target.getTargets")
        baseline = {t["targetId"] for t in before["targetInfos"] if t["type"] == "page"}
        try:
            for _ in range(3):
                async with asyncio.timeout(5):
                    pages = await asyncio.gather(
                        *(runtime._create_owned_page(entry) for _ in range(2))
                    )
                    assert pages[0] is not pages[1]
                    for page in pages:
                        await _close_owned_page(page, cdp)
            # Prevent initialization from being observed by the controller:
            # real Chrome target exists, but no Page can be bound.
            invisible = SimpleNamespace(cdp=cdp, context=SimpleNamespace(pages=[]))
            started = time.monotonic()
            try:
                async with asyncio.timeout(0.1):
                    await runtime._create_owned_page(invisible)
            except TimeoutError:
                pass
            else:
                raise AssertionError("missing creation timeout")
            after = await cdp.send("Target.getTargets")
            remaining = {t["targetId"] for t in after["targetInfos"] if t["type"] == "page"}
            assert remaining == baseline, (baseline, remaining)
            print(
                json.dumps(
                    {
                        "normal_pages_closed": 6,
                        "allocation_timeout_seconds": round(time.monotonic() - started, 3),
                        "original_pages_preserved": True,
                    }
                )
            )
        finally:
            await cdp.close()
            await browser.close()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="doxagent-lifecycle-check-") as directory:
        process = subprocess.Popen(
            [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                "--headless=new",
                "--remote-debugging-port=0",
                "--no-first-run",
                "--no-default-browser-check",
                f"--user-data-dir={directory}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            asyncio.run(check(directory))
        finally:
            process.terminate()
            process.wait(timeout=15)
