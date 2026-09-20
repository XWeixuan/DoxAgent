"""Parent-owned HTTP sessions and persistent browser profiles."""

from __future__ import annotations

import asyncio
import inspect
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

from doxagent.content_enrichment.transport import public_url
from doxagent.message_bus_v2.reuters_sources import capture_reuters_search
from doxagent.message_bus_v2.yahoo_sources import capture_latest_news
from doxagent.resource_safety import SafetyLevel, SafetyStateReader

from .repository import SiteStrategyRepository
from .resolver import SiteResolver
from .schema import (
    AccessCombination,
    AccessMode,
    AccessRequest,
    BrowserProfile,
    ProxyEgress,
    ResolvedSite,
)


@dataclass
class RuntimeResponse:
    status_code: int
    final_url: str
    headers: dict[str, str]
    body: str = ""
    recipe_result: dict[str, object] | list[dict[str, object]] | None = None
    redirect_url: str | None = None
    reason: str | None = None
    retry_after_seconds: float | None = None


class OwnerFileLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._stream: Any | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write("\0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(  # type: ignore[attr-defined]
                    stream.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,  # type: ignore[attr-defined]
                )
        except OSError as exc:
            stream.close()
            raise RuntimeError("another Site Access owner holds the profile root") from exc
        stream.seek(0)
        stream.truncate()
        stream.write(str(os.getpid()))
        stream.flush()
        self._stream = stream

    def release(self) -> None:
        if self._stream is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._stream.seek(0)
                msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(  # type: ignore[attr-defined]
                    self._stream.fileno(),
                    fcntl.LOCK_UN,  # type: ignore[attr-defined]
                )
        finally:
            self._stream.close()
            self._stream = None


@dataclass
class _BrowserEntry:
    profile: BrowserProfile
    egress: ProxyEgress
    context: Any
    last_used: float
    active_pages: int = 0


class PersistentBrowserPool:
    def __init__(
        self,
        root: Path,
        *,
        headless: bool,
        channel: str | None,
        max_processes: int,
        max_pages: int,
        idle_seconds: float,
        safety_path: Path | None = None,
    ) -> None:
        self.root = root
        self.headless = headless
        self.channel = channel
        self.max_processes = max(1, max_processes)
        self.idle_seconds = max(1, idle_seconds)
        self._page_slots = asyncio.Semaphore(max(1, max_pages))
        self._entries: OrderedDict[str, _BrowserEntry] = OrderedDict()
        self._lock = asyncio.Lock()
        self._playwright: Any | None = None
        self._owner_lock = OwnerFileLock(root / ".owner.lock")
        self._safety = SafetyStateReader(safety_path)

    @property
    def driver_ready(self) -> bool:
        return self._playwright is not None

    async def start(self) -> None:
        if self._playwright is not None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self._owner_lock.acquire()
        try:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
        except Exception:
            self._owner_lock.release()
            raise

    async def close(self) -> None:
        async with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            try:
                await entry.context.close()
            except Exception:
                pass
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        self._owner_lock.release()

    async def close_idle(self) -> int:
        cutoff = time.monotonic() - self.idle_seconds
        async with self._lock:
            stale = [
                key
                for key, entry in self._entries.items()
                if entry.active_pages == 0 and entry.last_used < cutoff
            ]
            entries = [self._entries.pop(key) for key in stale]
        for entry in entries:
            await entry.context.close()
        return len(entries)

    async def wait_profile_idle(self, profile_id: str, *, timeout_seconds: float = 30) -> None:
        async with asyncio.timeout(timeout_seconds):
            while True:
                async with self._lock:
                    entry = self._entries.get(profile_id)
                    if entry is None or entry.active_pages == 0:
                        return
                await asyncio.sleep(0.1)

    async def page(self, profile: BrowserProfile, egress: ProxyEgress) -> Any:
        await self.start()
        await self._page_slots.acquire()
        try:
            entry = await self._entry(profile, egress)
            entry.active_pages += 1
            entry.last_used = time.monotonic()
            try:
                page = await entry.context.new_page()
            except Exception:
                entry.active_pages -= 1
                raise
            return _PageLease(page, entry, self._page_slots)
        except Exception:
            self._page_slots.release()
            raise

    async def _entry(self, profile: BrowserProfile, egress: ProxyEgress) -> _BrowserEntry:
        async with self._lock:
            entry = self._entries.get(profile.profile_id)
            if entry is not None:
                if entry.egress.egress_id != egress.egress_id:
                    raise RuntimeError("profile attempted to change bound egress")
                if entry.egress.generation != egress.generation:
                    if entry.active_pages:
                        raise RuntimeError("egress_changed_while_profile_busy")
                    self._entries.pop(profile.profile_id)
                    await entry.context.close()
                    entry = None
            if entry is not None:
                self._entries.move_to_end(profile.profile_id)
                return entry
            while self._safety.read().level is not SafetyLevel.NORMAL:
                await asyncio.sleep(0.25)
            if len(self._entries) >= self.max_processes:
                victim_key = next(
                    (key for key, value in self._entries.items() if value.active_pages == 0), None
                )
                if victim_key is None:
                    raise RuntimeError("browser_process_capacity")
                victim = self._entries.pop(victim_key)
                await victim.context.close()
            assert self._playwright is not None
            directory = self.root / profile.directory_key
            directory.mkdir(parents=True, exist_ok=True)
            context = await self._playwright.chromium.launch_persistent_context(
                str(directory),
                headless=self.headless,
                channel=self.channel or None,
                proxy={"server": egress.endpoint},
                accept_downloads=False,
                args=[
                    "--disable-quic",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
                ],
            )
            entry = _BrowserEntry(
                profile=profile,
                egress=egress,
                context=context,
                last_used=time.monotonic(),
            )
            self._entries[profile.profile_id] = entry
            return entry


class _PageLease:
    def __init__(self, page: Any, entry: _BrowserEntry, slots: asyncio.Semaphore) -> None:
        self.page = page
        self.entry = entry
        self.slots = slots

    async def __aenter__(self) -> Any:
        return self.page

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            await self.page.close()
        finally:
            self.entry.active_pages -= 1
            self.entry.last_used = time.monotonic()
            self.slots.release()


class SiteAccessRuntime:
    def __init__(
        self,
        repository: SiteStrategyRepository,
        resolver: SiteResolver,
        *,
        profile_root: str | Path,
        browser_headless: bool = True,
        browser_channel: str | None = None,
        browser_max_processes: int = 4,
        browser_max_pages: int = 4,
        browser_idle_seconds: float = 300,
        safety_path: str | Path | None = None,
    ) -> None:
        self.repository = repository
        self.resolver = resolver
        self.browser_pool = PersistentBrowserPool(
            Path(profile_root),
            headless=browser_headless,
            channel=browser_channel,
            max_processes=browser_max_processes,
            max_pages=browser_max_pages,
            idle_seconds=browser_idle_seconds,
            safety_path=Path(safety_path) if safety_path else None,
        )
        self._http_sessions: dict[tuple[str, str], Any] = {}
        self._maintenance_pages: dict[str, tuple[_PageLease, Any]] = {}

    async def close(self) -> None:
        for token in list(self._maintenance_pages):
            await self.close_login(token)
        await self.browser_pool.close()
        sessions = list(self._http_sessions.values())
        self._http_sessions.clear()
        for session in sessions:
            closed = session.close()
            if inspect.isawaitable(closed):
                await closed

    async def execute(
        self,
        request: AccessRequest,
        resolved: ResolvedSite,
        combination: AccessCombination,
        profile: BrowserProfile,
        egress: ProxyEgress,
    ) -> RuntimeResponse:
        if request.mode is AccessMode.HTTP_PUBLIC:
            return await self._http(request, resolved, combination, egress)
        return await self._browser(request, resolved, profile, egress)

    async def _http(
        self,
        request: AccessRequest,
        resolved: ResolvedSite,
        combination: AccessCombination,
        egress: ProxyEgress,
    ) -> RuntimeResponse:
        await public_url(request.url, trusted_proxy_dns=True)
        # Sessions are isolated by governed site and physical egress.  Keying on the
        # egress also lets an egress update deterministically discard every cookie
        # jar and connection pool that was bound to the previous listener/node.
        key = (resolved.runtime_key, egress.egress_id)
        session = self._http_sessions.get(key)
        if session is None:
            from curl_cffi.requests import AsyncSession

            session = AsyncSession(impersonate="chrome", timeout=15, proxy=egress.endpoint)
            self._http_sessions[key] = session
        kwargs: Any = {
            "headers": {
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                **request.allowed_headers,
            },
            "allow_redirects": False,
        }
        if "query" in request.parameters:
            kwargs["params"] = request.parameters["query"]
        if "json" in request.parameters:
            kwargs["json"] = request.parameters["json"]
        response = await session.request(request.method, request.url, **kwargs)
        raw = bytes(response.content)
        if len(raw) > request.max_response_bytes:
            return RuntimeResponse(0, request.url, {}, reason="response_too_large")
        headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
        status = int(response.status_code)
        final_url = str(response.url)
        redirect = None
        if 300 <= status < 400 and headers.get("location"):
            redirect = urljoin(final_url, headers["location"])
        delay = _retry_after(headers.get("retry-after"))
        return RuntimeResponse(
            status_code=status,
            final_url=final_url,
            headers=_filtered_headers(headers),
            body=raw.decode(response.encoding or "utf-8", errors="replace"),
            redirect_url=redirect,
            retry_after_seconds=delay,
        )

    async def _browser(
        self,
        request: AccessRequest,
        resolved: ResolvedSite,
        profile: BrowserProfile,
        egress: ProxyEgress,
    ) -> RuntimeResponse:
        await public_url(request.url, trusted_proxy_dns=True)
        lease = await self.browser_pool.page(profile, egress)
        async with lease as page:
            blocked_navigation: list[str] = []
            checked_public_hosts: set[str] = set()

            async def constrain_navigation(route: Any, browser_request: Any) -> None:
                parsed_request = urlsplit(browser_request.url)
                request_host = parsed_request.hostname or ""
                if (
                    parsed_request.scheme in {"http", "https"}
                    and request_host not in checked_public_hosts
                ):
                    try:
                        await public_url(browser_request.url, trusted_proxy_dns=True)
                    except ValueError:
                        await route.abort("blockedbyclient")
                        return
                    checked_public_hosts.add(request_host)
                if (
                    browser_request.is_navigation_request()
                    and browser_request.frame == page.main_frame
                ):
                    target_host = request_host
                    target = self.resolver.resolve(browser_request.url)
                    if (
                        target.runtime_key != resolved.runtime_key
                        and not self.resolver.supports_host(resolved.site_id, target_host)
                    ):
                        blocked_navigation.append(browser_request.url)
                        await route.abort("blockedbyclient")
                        return
                await route.continue_()

            await page.route("**/*", constrain_navigation)
            if request.recipe_ref == "builtin:yahoo_latest_news@1":
                rows, metadata = await capture_latest_news(
                    page,
                    str(request.recipe_parameters["ticker"]),
                    timeout_seconds=int(request.recipe_parameters.get("timeout_seconds", 20)),
                    snippet_count=int(request.recipe_parameters.get("snippet_count", 20)),
                )
                return RuntimeResponse(
                    200,
                    str(metadata.get("page_url") or request.url),
                    {},
                    recipe_result={"rows": rows, "metadata": metadata},
                )
            if request.recipe_ref == "builtin:reuters_search@1":
                rows = await capture_reuters_search(
                    page,
                    str(request.recipe_parameters["query"]),
                    int(request.recipe_parameters.get("offset", 0)),
                )
                return RuntimeResponse(
                    200,
                    page.url,
                    {},
                    recipe_result={"rows": rows},
                )
            response = await page.goto(
                request.url,
                wait_until="domcontentloaded",
                timeout=min(request.remaining_budget_ms, 20_000),
            )
            status = response.status if response is not None else 200
            if blocked_navigation:
                return RuntimeResponse(
                    0,
                    request.url,
                    {},
                    redirect_url=blocked_navigation[0],
                    reason="cross_site_navigation",
                )
            headers = await response.all_headers() if response is not None else {}
            if request.recipe_parameters.get("expand"):
                await _expand_article(page)
            wait_selector = request.recipe_parameters.get("wait_selector")
            if isinstance(wait_selector, str) and wait_selector:
                await page.locator(wait_selector).first.wait_for(
                    state="visible", timeout=min(request.remaining_budget_ms, 10_000)
                )
            html = await page.content()
            if len(html.encode("utf-8")) > request.max_response_bytes:
                return RuntimeResponse(0, request.url, {}, reason="response_too_large")
            return RuntimeResponse(
                int(status),
                page.url,
                _filtered_headers({str(k).lower(): str(v) for k, v in headers.items()}),
                body=html,
            )

    async def reset_egress(self, egress_id: str) -> None:
        stale = [key for key in self._http_sessions if key[1] == egress_id]
        for key in stale:
            session = self._http_sessions.pop(key)
            closed = session.close()
            if inspect.isawaitable(closed):
                await closed
        async with self.browser_pool._lock:
            browser_keys = [
                key
                for key, entry in self.browser_pool._entries.items()
                if entry.egress.egress_id == egress_id and entry.active_pages == 0
            ]
            browser_entries = [self.browser_pool._entries.pop(key) for key in browser_keys]
        for entry in browser_entries:
            await entry.context.close()

    async def open_login(
        self, profile: BrowserProfile, egress: ProxyEgress, url: str
    ) -> dict[str, str]:
        await public_url(url, trusted_proxy_dns=True)
        lease = await self.browser_pool.page(profile, egress)
        page = await lease.__aenter__()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except Exception:
            await lease.__aexit__(None, None, None)
            raise
        token = uuid4().hex
        self._maintenance_pages[token] = (lease, page)
        return {"login_token": token, "url": page.url}

    async def inspect_login(self, token: str) -> dict[str, str]:
        pair = self._maintenance_pages.get(token)
        if pair is None:
            raise KeyError(token)
        _, page = pair
        return {"url": page.url, "title": await page.title()}

    async def close_login(self, token: str) -> str:
        pair = self._maintenance_pages.pop(token, None)
        if pair is None:
            raise KeyError(token)
        lease, _ = pair
        profile_id = lease.entry.profile.profile_id
        await lease.__aexit__(None, None, None)
        return profile_id


async def _expand_article(page: Any) -> None:
    for selector in (
        "button:has-text('Continue Reading')",
        "button:has-text('Read more')",
        "button:has-text('Show more')",
    ):
        try:
            locator = page.locator(selector).first
            if await locator.is_visible(timeout=250):
                await locator.click(timeout=1_000)
                await asyncio.sleep(0.25)
                return
        except Exception:
            continue


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        result = float(value)
        return max(0.0, result) if result < float("inf") else None
    except ValueError:
        return None


def _filtered_headers(headers: dict[str, str]) -> dict[str, str]:
    forbidden = {"set-cookie", "cookie", "authorization", "proxy-authorization"}
    return {key: value for key, value in headers.items() if key.casefold() not in forbidden}


__all__ = ["RuntimeResponse", "SiteAccessRuntime"]
