"""Own only ephemeral public contexts. Never attach to a news identity."""

import asyncio


class PublicBrowserPool:
    def __init__(self):
        self.slots = asyncio.Semaphore(2)
        self.lock = asyncio.Lock()
        self.playwright = None
        self.browser = None
        self.generation = 0
        self.active = 0
        self.idle_task = None
        self.driver_process = None

    async def _ensure(self):
        async with self.lock:
            if self.browser is None or not self.browser.is_connected():
                from playwright.async_api import async_playwright

                if self.playwright is not None:
                    await self.playwright.stop()
                self.playwright = await async_playwright().start()
                self.driver_process = self.playwright._impl_obj._connection._transport._proc
                self.browser = await self.playwright.chromium.launch(headless=True)
                self.generation += 1
            return self.browser

    async def fetch(self, spec):
        async with asyncio.timeout(2):
            await self.slots.acquire()
        context = None
        browser = None
        self.active += 1
        if self.idle_task:
            self.idle_task.cancel()
        try:
            async with asyncio.timeout(20):
                browser = await self._ensure()
                context = await browser.new_context()
                page = await context.new_page()
                from ..poll_trace import request_started
                from ..schema import utc_now
                events = []
                def observe(request):
                    if request.is_navigation_request() and request.frame == page.main_frame:
                        event = request_started(request.method, request.url, "ir_browser")
                        if event is not None:
                            events.append(event)
                page.on("request", observe)
                response = await page.goto(spec.entry_url, wait_until="domcontentloaded")
                selector = spec.parser_parameters.get("ready_selector")
                if selector:
                    await page.locator(selector).first.wait_for()
                if events:
                    events[-1].update(response_received_at=utc_now().isoformat(),
                                      status_code=response.status if response else 0)
                return response.status if response else 0, await page.content(), page.url
        except Exception:
            # A page timeout alone is not evidence that sibling contexts are dead.
            if browser is not None:
                responsive = False
                probe = None
                try:
                    async with asyncio.timeout(1):
                        probe = await browser.new_browser_cdp_session()
                        await probe.send("Browser.getVersion")
                        responsive = True
                except Exception:
                    pass
                finally:
                    if probe:
                        try:
                            async with asyncio.timeout(0.5):
                                await probe.detach()
                        except Exception:
                            pass
                if not responsive:
                    async with self.lock:
                        if self.browser is browser:
                            await self._close()
            raise
        finally:
            try:
                if context:
                    async with asyncio.timeout(2):
                        await context.close()
            finally:
                self.active -= 1
                self.slots.release()
                if not self.active:
                    self.idle_task = asyncio.create_task(self._idle())

    async def _idle(self):
        await asyncio.sleep(60)
        async with self.lock:
            if not self.active:
                await self._close()

    async def _close(self):
        try:
            if self.browser:
                async with asyncio.timeout(3):
                    await self.browser.close()
        finally:
            self.browser = None
            if self.playwright:
                try:
                    async with asyncio.timeout(3):
                        await self.playwright.stop()
                finally:
                    # This is the exact subprocess handle returned by our own
                    # Playwright launch, never a PID/name lookup of other Chrome.
                    process = self.driver_process
                    if process and process.returncode is None:
                        process.kill()
                        async with asyncio.timeout(2):
                            await process.wait()
                    self.playwright = None
                    self.driver_process = None

    async def close(self):
        if self.idle_task:
            self.idle_task.cancel()
            await asyncio.gather(self.idle_task, return_exceptions=True)
        async with self.lock:
            await self._close()
