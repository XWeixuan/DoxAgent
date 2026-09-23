"""Playwright CDP control of ordinary, supervisor-owned Google Chrome."""

from __future__ import annotations

import asyncio
import importlib.metadata
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doxagent.resource_safety import SafetyLevel, SafetyStateReader

from .browser_runtime import RuntimeProvenance
from .runtime import EXPECTED_PLAYWRIGHT_VERSION, _RawBrowserCDP
from .schema import BrowserIdentitySpec, BrowserResidency, BrowserRuntimeKind, ProxyEgress
from .supervisor_client import ChromeSupervisorClient, SupervisorInstance


@dataclass
class _ExternalEntry:
    identity: BrowserIdentitySpec
    egress: ProxyEgress
    instance: SupervisorInstance
    browser: Any
    context: Any
    cdp: _RawBrowserCDP
    active_pages: int = 0
    last_used: float = 0.0


class _ExternalLease:
    def __init__(self, adapter: ExternalChromeRuntime, entry: _ExternalEntry, page: Any) -> None:
        self.adapter = adapter
        self.entry = entry
        self.page = page
        self.browser_cdp = entry.cdp
        self.provenance = RuntimeProvenance(
            identity_id=entry.identity.identity_id,
            identity_revision=entry.identity.revision,
            runtime_kind=BrowserRuntimeKind.EXTERNAL_CHROME,
            instance_id=entry.instance.instance_id,
            generation=entry.instance.generation,
        )
        self._closed = False

    async def __aenter__(self) -> Any:
        return self.page

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.page.close()
        finally:
            async with self.adapter._lock:
                self.entry.active_pages -= 1
                self.entry.last_used = time.monotonic()
            self.adapter._page_slots.release()

    async def abandon(self) -> None:
        """Release the controller lease while leaving the real Chrome page open."""
        if self._closed:
            return
        self._closed = True
        async with self.adapter._lock:
            self.entry.active_pages -= 1
            self.entry.last_used = time.monotonic()
        self.adapter._page_slots.release()


class ExternalChromeRuntime:
    def __init__(
        self,
        client: ChromeSupervisorClient,
        *,
        max_pages: int = 4,
        safety_path: str | None = None,
    ) -> None:
        self.client = client
        self._page_slots = asyncio.Semaphore(max(1, max_pages))
        self._lock = asyncio.Lock()
        self._start_locks: dict[str, asyncio.Lock] = {}
        self._entries: dict[str, _ExternalEntry] = {}
        self._playwright: Any | None = None
        self._safety = SafetyStateReader(Path(safety_path) if safety_path else None)

    async def start(self) -> None:
        if self._playwright is not None:
            return
        installed = importlib.metadata.version("playwright")
        if installed != EXPECTED_PLAYWRIGHT_VERSION:
            raise RuntimeError(
                f"playwright version mismatch: {installed} != {EXPECTED_PLAYWRIGHT_VERSION}"
            )
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()

    async def close(self) -> None:
        # Do not call browser/context.close(): an attached client must not own Chrome.
        entries = list(self._entries.values())
        self._entries.clear()
        for entry in entries:
            await entry.cdp.close()
            try:
                await self.client.release_controller(entry.identity.identity_id)
            except Exception:
                pass
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def close_idle(self) -> int:
        now = time.monotonic()
        async with self._lock:
            safety = self._safety.read().level
            identities = [
                identity_id
                for identity_id, entry in self._entries.items()
                if (
                    entry.identity.lifecycle.residency is BrowserResidency.ON_DEMAND
                    or safety is SafetyLevel.CRITICAL
                )
                and entry.active_pages == 0
                and now - entry.last_used >= entry.identity.lifecycle.idle_seconds
            ]
        closed = 0
        for identity_id in identities:
            if await self.stop_identity(identity_id, reason="idle_timeout"):
                closed += 1
        return closed

    async def _entry(self, identity: BrowserIdentitySpec, egress: ProxyEgress) -> _ExternalEntry:
        await self.start()
        start_lock = self._start_locks.setdefault(identity.identity_id, asyncio.Lock())
        async with start_lock:
            entry = self._entries.get(identity.identity_id)
            if entry is not None:
                if (
                    entry.identity.revision == identity.revision
                    and entry.egress.generation == egress.generation
                    and entry.browser.is_connected()
                ):
                    return entry
                if entry.active_pages:
                    raise RuntimeError("external_identity_changed_while_busy")
                await entry.cdp.close()
                self._entries.pop(identity.identity_id, None)
            assert self._playwright is not None
            if self._safety.read().level is not SafetyLevel.NORMAL:
                raise RuntimeError("external_cold_start_deferred_resource_pressure")
            instance = await self.client.ensure(identity, egress)
            browser = await self._playwright.chromium.connect_over_cdp(
                instance.cdp_http_url, timeout=20_000
            )
            if len(browser.contexts) != 1:
                raise RuntimeError("external_chrome_default_context_missing")
            cdp = _RawBrowserCDP(instance.cdp_websocket_url)
            await cdp.start()
            entry = _ExternalEntry(
                identity=identity,
                egress=egress,
                instance=instance,
                browser=browser,
                context=browser.contexts[0],
                cdp=cdp,
                last_used=time.monotonic(),
            )
            self._entries[identity.identity_id] = entry
            return entry

    async def page(
        self, identity: BrowserIdentitySpec, egress: ProxyEgress, *, deadline: float | None = None
    ) -> _ExternalLease:
        timeout = max(0.001, deadline - time.monotonic()) if deadline else None
        if timeout is None:
            await self._page_slots.acquire()
        else:
            async with asyncio.timeout(timeout):
                await self._page_slots.acquire()
        try:
            entry = await self._entry(identity, egress)
            page = await entry.context.new_page()
            async with self._lock:
                entry.active_pages += 1
                entry.last_used = time.monotonic()
            return _ExternalLease(self, entry, page)
        except Exception:
            self._page_slots.release()
            raise

    async def prewarm(
        self, identity: BrowserIdentitySpec, egress: ProxyEgress
    ) -> RuntimeProvenance:
        entry = await self._entry(identity, egress)
        return RuntimeProvenance(
            identity_id=identity.identity_id,
            identity_revision=identity.revision,
            runtime_kind=BrowserRuntimeKind.EXTERNAL_CHROME,
            instance_id=entry.instance.instance_id,
            generation=entry.instance.generation,
        )

    async def existing_page(
        self,
        identity: BrowserIdentitySpec,
        egress: ProxyEgress,
        *,
        target_id: str | None = None,
    ) -> _ExternalLease:
        await self._page_slots.acquire()
        try:
            entry = await self._entry(identity, egress)
            candidates = [page for page in entry.context.pages if page.url != "about:blank"]
            if target_id is not None:
                matched = []
                for candidate in candidates:
                    session = await entry.context.new_cdp_session(candidate)
                    try:
                        info = await session.send("Target.getTargetInfo")
                        if str(info["targetInfo"]["targetId"]) == target_id:
                            matched.append(candidate)
                    finally:
                        await session.detach()
                candidates = matched
            if not candidates:
                raise RuntimeError("external_maintenance_page_not_found")
            async with self._lock:
                entry.active_pages += 1
                entry.last_used = time.monotonic()
            return _ExternalLease(self, entry, candidates[-1])
        except Exception:
            self._page_slots.release()
            raise

    async def close_existing_maintenance_page(
        self, identity: BrowserIdentitySpec, egress: ProxyEgress
    ) -> bool:
        entry = await self._entry(identity, egress)
        candidates = [page for page in entry.context.pages if page.url != "about:blank"]
        if not candidates:
            return False
        await candidates[-1].close()
        return True

    async def stop_identity(self, identity_id: str, *, reason: str) -> bool:
        async with self._lock:
            entry = self._entries.get(identity_id)
            if entry is not None and entry.active_pages:
                return False
            entry = self._entries.pop(identity_id, None)
        if entry is not None:
            await entry.cdp.close()
        return await self.client.stop(identity_id, reason=reason)

    async def status(self, identity_id: str) -> dict[str, object]:
        return await self.client.status(identity_id)

    async def select_maintenance(self, identity_id: str) -> None:
        await self.client.select_maintenance(identity_id)


__all__ = ["ExternalChromeRuntime"]
