"""Small persistent publisher contexts; authentication never enters public readers."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from doxagent.content_enrichment.identity import recover_seeking_alpha
from doxagent.content_enrichment.quality import CHALLENGE, choose_candidate, inspect_html
from doxagent.content_enrichment.transport import Observation, public_url, remaining


class PublisherBrowser:
    def __init__(
        self,
        *,
        identity_dir: Path | None = None,
        authenticated_hosts: set[str] | None = None,
        max_pages: int = 2,
        headless: bool = True,
        channel: str | None = None,
        cdp_url: str | None = None,
        trusted_proxy_dns: bool = False,
    ) -> None:
        self.identity_dir = identity_dir
        self.authenticated_hosts = authenticated_hosts or set()
        self.headless = headless
        self.channel = channel
        self.cdp_url = cdp_url
        self.trusted_proxy_dns = trusted_proxy_dns
        self._slots = asyncio.Semaphore(max_pages)
        self._locks: dict[str, asyncio.Lock] = {}
        self._contexts: dict[str, Any] = {}
        self._playwright: Any = None
        self._browser: Any = None
        self._start_lock = asyncio.Lock()
        self._states: dict[str, dict[str, Any]] = {}

    async def _context(self, host: str) -> Any:
        async with self._start_lock:
            if self._playwright is None:
                from playwright.async_api import async_playwright

                self._playwright = await async_playwright().start()
            if self.cdp_url and self._browser and not self._browser.is_connected():
                self._contexts.clear()
                self._browser = None
            if host in self._contexts:
                return self._contexts[host]
            if self.cdp_url:
                # Operator-managed Chrome owns its profile and lifetime. Only our pages close.
                if self._browser is None:
                    self._browser = await self._playwright.chromium.connect_over_cdp(self.cdp_url)
                if not self._browser.contexts:
                    raise RuntimeError("CDP browser has no persistent context")
                context = self._browser.contexts[0]
            elif host in self.authenticated_hosts and self.identity_dir:
                directory = self.identity_dir / host
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                context = await self._playwright.chromium.launch_persistent_context(
                    str(directory / "profile"),
                    headless=self.headless,
                    channel=self.channel,
                    accept_downloads=False,
                )
            else:
                if self._browser is None:
                    self._browser = await self._playwright.chromium.launch(
                        headless=self.headless, channel=self.channel
                    )
                context = await self._browser.new_context(accept_downloads=False)
            if host in self.authenticated_hosts and self.identity_dir:
                directory = self.identity_dir / host
                # Both managed profiles and operator CDP retain identity revisions.
                state_path = directory / "status.json"
                if state_path.exists():
                    try:
                        saved = json.loads(state_path.read_text(encoding="utf-8"))
                    except (ValueError, OSError):
                        saved = {}
                    self._states[host] = saved if isinstance(saved, dict) else {}
                    self._states[host]["auth_state"] = "UNVERIFIED"
            self._contexts[host] = context
            return context

    def _state(self, host: str, state: str) -> dict[str, Any]:
        previous = self._states.get(host, {})
        revision = previous.get("session_revision", 0)
        value: dict[str, Any] = {
            "auth_state": state,
            "session_revision": revision if isinstance(revision, int) else 0,
            "checked_at": datetime.now(UTC).isoformat(),
        }
        if host in self.authenticated_hosts:
            value["credential_ref"] = host
            if state == "VALID" and previous.get("auth_state") != "VALID":
                value["session_revision"] += 1
            self._states[host] = value
            if self.identity_dir:
                directory = self.identity_dir / host
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                tmp = directory / "status.tmp"
                tmp.write_text(json.dumps(value), encoding="utf-8")
                tmp.replace(directory / "status.json")
        return value

    async def read(self, url: str, *, expand: bool = False) -> tuple[Observation, dict[str, Any]]:
        host = urlparse(url).hostname or ""
        if not host or not re.fullmatch(r"[a-zA-Z0-9.-]+", host):
            return Observation(url, "", 0, "invalid_url"), {}
        try:
            async with asyncio.timeout(remaining(35)):
                await public_url(url, trusted_proxy_dns=self.trusted_proxy_dns)
                async with self._locks.setdefault(host, asyncio.Lock()), self._slots:
                    state = self._states.get(host, {})
                    if state.get("auth_state") == "REAUTH_REQUIRED" and not self.cdp_url:
                        return Observation(url, "", 0, "reauth_required"), state
                    context = await self._context(host)
                    if host in self.authenticated_hosts and not self.cdp_url:
                        response = await context.request.get(
                            url, max_redirects=0, timeout=remaining() * 1000
                        )
                        try:
                            text = await response.text()
                            candidate, _, _ = choose_candidate(inspect_html(text, url, None), None)
                            if response.status == 200 and candidate and not expand:
                                # The caller also checks the expected article identity.
                                return Observation(url, text, response.status), self._state(
                                    host, "UNVERIFIED"
                                )
                        finally:
                            await response.dispose()
                    page = await context.new_page()
                    try:
                        # Validate navigations/subrequests as well as the initial address.
                        async def route_request(route: Any) -> None:
                            request_url = route.request.url
                            if request_url.startswith(("data:", "blob:")):
                                await route.continue_()
                                return
                            try:
                                await public_url(
                                    request_url, trusted_proxy_dns=self.trusted_proxy_dns
                                )
                            except Exception:
                                await route.abort()
                                return
                            if not self.cdp_url and route.request.resource_type in {
                                "image", "media", "font"
                            }:
                                await route.abort()
                            else:
                                await route.continue_()

                        await page.route("**/*", route_request)
                        response = await page.goto(
                            url, wait_until="domcontentloaded", timeout=remaining(25) * 1000
                        )
                        status = response.status if response else 0
                        await page.locator("body").wait_for(timeout=remaining(5) * 1000)
                        text = await page.locator("body").inner_text()
                        initial = inspect_html(await page.content(), page.url, None)
                        passive_check = initial.access_reason == "challenge_required" and not (
                            CHALLENGE.search(text)
                        )
                        if (
                            initial.access_reason == "render_required"
                            or passive_check
                            or len(text.strip()) < 120
                            or (not initial.candidates and initial.access_reason is None)
                        ):
                            try:
                                selector = (
                                    '[data-test-id="content-container"]'
                                    if host.removeprefix("www.") == "seekingalpha.com"
                                    else '[data-id="LiveCoverageCard_index_CardBlock"]'
                                    if host.removeprefix("www.") == "barrons.com"
                                    and "/card/" in urlparse(url).path
                                    else 'article, [itemprop="articleBody"], .article-content, '
                                    '.article-body, [data-testid="article-body"]'
                                )
                                await page.wait_for_function(
                                    """selector => Array.from(document.querySelectorAll(selector))
                                        .some(n => n.innerText.trim().length > 120)""",
                                    arg=selector,
                                    timeout=remaining(8) * 1000,
                                )
                            except Exception:
                                pass
                            text = await page.locator("body").inner_text()
                        inspection = inspect_html(await page.content(), page.url, None)
                        if (
                            inspection.access_reason == "challenge_required"
                            or CHALLENGE.search(text)
                        ):
                            state = self._state(
                                host,
                                "REAUTH_REQUIRED"
                                if host in self.authenticated_hosts
                                else "UNCONFIGURED",
                            )
                            return Observation(page.url, "", status, "challenge_required"), state
                        if expand:
                            # Same-page buttons only; no arbitrary navigation / consent clicks.
                            buttons = page.get_by_role(
                                "button",
                                name=re.compile(r"^(Continue Reading|Read More|Show More)$", re.I),
                            )
                            if await buttons.count() == 1 and await buttons.is_visible():
                                before = len(text)
                                await buttons.click(timeout=remaining(5) * 1000)
                                await page.wait_for_function(
                                    "(n) => document.body.innerText.length > n",
                                    arg=before,
                                    timeout=remaining(5) * 1000,
                                )
                        text = await page.locator("body").inner_text()
                        inspection = inspect_html(await page.content(), page.url, None)
                        login_wall = inspection.access_reason == "login_required"
                        if (
                            login_wall
                            and host in self.authenticated_hosts
                            and host == "seekingalpha.com"
                        ):
                            self._state(host, "RECOVERING")
                            recovered = await recover_seeking_alpha(page, context)
                            if recovered == "unverified":
                                await page.goto(
                                    url, wait_until="domcontentloaded", timeout=remaining(10) * 1000
                                )
                                text = await page.locator("body").inner_text()
                                inspection = inspect_html(await page.content(), page.url, None)
                                login_wall = inspection.access_reason == "login_required"
                            else:
                                return Observation(url, "", status, recovered), self._state(
                                    host, "REAUTH_REQUIRED"
                                )
                        if inspection.access_reason in {"login_required", "subscription_required"}:
                            state = self._state(
                                host,
                                "REAUTH_REQUIRED"
                                if login_wall and host in self.authenticated_hosts
                                else (
                                    "UNVERIFIED"
                                    if host in self.authenticated_hosts
                                    else "UNCONFIGURED"
                                ),
                            )
                            return Observation(
                                page.url,
                                "",
                                status,
                                "login_required" if login_wall else "subscription_required",
                            ), state
                        if host.removeprefix("www.") == "seekingalpha.com":
                            hidden = await page.locator(
                                '[data-test-id="content-container"]'
                            ).evaluate_all("""nodes => nodes.length > 0 && Array.from(
                                nodes[0].querySelectorAll('p,h2,h3,table')
                            ).some(n => n.textContent.trim().length > 120 && (
                                getComputedStyle(n).visibility === 'hidden'
                                || getComputedStyle(n).display === 'none'
                                || n.getClientRects().length === 0
                            ))""")
                            if hidden:
                                return Observation(
                                    page.url, "", status, "subscription_required"
                                ), self._state(host, "UNVERIFIED")
                        html = await page.content()
                        return Observation(page.url, html, status), self._state(host, "UNVERIFIED")
                    finally:
                        await page.close()
        except TimeoutError:
            return Observation(url, "", 0, "render_timeout"), self._states.get(host, {})
        except Exception as exc:
            # Error strings can include cookies/URLs from browser libraries: never persist them.
            reason = (
                "render_timeout" if type(exc).__name__ == "TimeoutError" else "browser_unavailable"
            )
            if "Executable doesn't exist" in str(exc):
                reason = "browser_runtime_missing"
            return Observation(url, "", 0, reason), self._states.get(host, {})

    def verified(self, host: str) -> dict[str, Any]:
        if host in self.authenticated_hosts:
            return self._state(host, "VALID")
        return {}

    async def close(self) -> None:
        if not self.cdp_url:
            for context in self._contexts.values():
                await context.close()
        self._contexts.clear()
        if self._browser and not self.cdp_url:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._browser = self._playwright = None

    async def login(self, host: str, article_url: str) -> None:
        """Operator command for normal login in one dedicated profile."""
        if host not in self.authenticated_hosts:
            raise ValueError("publisher_not_configured")
        if urlparse(article_url).hostname != host:
            raise ValueError("publisher_host_mismatch")
        await public_url(article_url, trusted_proxy_dns=self.trusted_proxy_dns)
        context = await self._context(host)
        page = await context.new_page()
        await page.goto(article_url, wait_until="domcontentloaded")
        await asyncio.to_thread(
            input, "Complete normal login in the publisher browser, then press Enter: "
        )
        self._state(host, "UNVERIFIED")
        await page.close()
