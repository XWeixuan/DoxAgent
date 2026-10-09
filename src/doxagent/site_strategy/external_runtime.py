"""Playwright CDP control of ordinary, supervisor-owned Google Chrome."""

from __future__ import annotations

import asyncio
import importlib.metadata
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doxagent.resource_safety import SafetyLevel, SafetyStateReader

from .browser_runtime import RuntimeProvenance
from .runtime import EXPECTED_PLAYWRIGHT_VERSION, _close_owned_page, _RawBrowserCDP
from .schema import BrowserIdentitySpec, BrowserResidency, BrowserRuntimeKind, ProxyEgress
from .supervisor_client import ChromeSupervisorClient, SupervisorInstance

logger = logging.getLogger(__name__)


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
    driver_epoch: int = 0


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
            await _close_owned_page(self.page, self.entry.cdp)
        finally:
            async with self.adapter._lock:
                self.entry.active_pages -= 1
                self.entry.last_used = time.monotonic()
            self.adapter._page_slots.release()
            if self.adapter._rotation_due and not self.adapter.rotation_paused:
                try:
                    await self.adapter.maintain_driver()
                except Exception:
                    logger.exception("deferred external driver rotation failed")

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
        self._driver_lock = asyncio.Lock()
        self.driver_epoch = 0
        self.driver_state = "STARTING"
        self._driver_started = 0.0
        self._high_rss_samples = 0
        self._acquiring = 0
        self._retry_at = 0.0
        self._recovery_failures = 0
        self.rotation_paused = False
        self._rotation_due = False
        self._stopping = False
        self.last_recovery_reason: str | None = None
        self._safety = SafetyStateReader(Path(safety_path) if safety_path else None)

    async def start(self) -> None:
        async with self._driver_lock:
            if self._stopping:
                raise RuntimeError("external_driver_stopping")
            if self.driver_ready:
                return
            if time.monotonic() < self._retry_at:
                raise RuntimeError("external_driver_recovery_backoff")
            self.driver_state = "RECOVERING" if self.driver_epoch else "STARTING"
            if self.driver_epoch:
                self.last_recovery_reason = "driver_unavailable"
            await self._detach_driver()
            try:
                await self._start_driver()
            except BaseException:
                self.driver_state = "FAILED"
                self._recovery_failures += 1
                self._retry_at = time.monotonic() + min(60, 5 * self._recovery_failures)
                raise

    def _driver_process(self) -> Any:
        # Pinned Playwright 1.63 transport; never confuse the driver with Chrome.
        impl = getattr(self._playwright, "_impl_obj", None)
        connection = getattr(impl, "_connection", None)
        return getattr(getattr(connection, "_transport", None), "_proc", None)

    @property
    def driver_ready(self) -> bool:
        process = self._driver_process()
        return (
            self._playwright is not None
            and (process is None or process.returncode is None)
            and self.driver_state == "READY"
        )

    def diagnostics(self) -> dict[str, object]:
        process = self._driver_process()
        pid = getattr(process, "pid", None)
        rss = 0
        if pid:
            try:
                for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                    if line.startswith("VmRSS:"):
                        rss = int(line.split()[1]) * 1024
            except (OSError, ValueError):
                pass
        return {
            "state": self.driver_state,
            "ready": self.driver_ready,
            "epoch": self.driver_epoch,
            "pid": pid,
            "rss_bytes": rss,
            "age_seconds": max(0, time.monotonic() - self._driver_started),
            "attachments": len(self._entries),
            "active_pages": sum(e.active_pages for e in self._entries.values()),
            "last_recovery_reason": self.last_recovery_reason,
        }

    async def _start_driver(self) -> None:
        installed = importlib.metadata.version("playwright")
        if installed != EXPECTED_PLAYWRIGHT_VERSION:
            raise RuntimeError(
                f"playwright version mismatch: {installed} != {EXPECTED_PLAYWRIGHT_VERSION}"
            )
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self.driver_epoch += 1
        self._driver_started = time.monotonic()
        self._high_rss_samples = 0
        self.driver_state = "READY"
        self._rotation_due = False
        self._recovery_failures = 0
        self._retry_at = 0.0
        logger.info(
            "external driver ready epoch=%s pid=%s",
            self.driver_epoch,
            getattr(self._driver_process(), "pid", None),
        )

    async def _detach_driver(self) -> None:
        # Old leases retain their own entry and release their own permits exactly
        # once. They cannot decrement a replacement attachment's counters.
        entries = list(self._entries.values())
        self._entries.clear()
        for entry in entries:
            try:
                async with asyncio.timeout(6):
                    if not self.driver_ready and not self.rotation_paused:
                        for target_id in tuple(getattr(entry.cdp, "_policies", {})):
                            await entry.cdp.send("Target.closeTarget", {"targetId": target_id})
                    await entry.cdp.close()
            except Exception:
                logger.warning(
                    "external raw CDP detach failed identity=%s", entry.identity.identity_id
                )
        playwright, self._playwright = self._playwright, None
        if playwright is not None:
            try:
                async with asyncio.timeout(8):
                    await playwright.stop()
            except Exception:
                logger.warning("external driver stop failed epoch=%s", self.driver_epoch)

    async def maintain_driver(self) -> None:
        if self._stopping:
            return
        if not self.driver_ready:
            await self.start()
            return
        # Probe the Playwright transport, not just the Chrome HTTP endpoint or
        # a cached Python object. Detached sessions never accumulate.
        entry = next(iter(self._entries.values()), None)
        if entry is not None:
            session = None
            try:
                async with asyncio.timeout(5):
                    session = await entry.browser.new_browser_cdp_session()
                    await session.send("Browser.getVersion")
            except Exception:
                self.driver_state = "FAILED"
            finally:
                if session is not None:
                    try:
                        async with asyncio.timeout(2):
                            await session.detach()
                    except Exception:
                        pass
            if self.driver_state == "FAILED":
                await self.start()
                return
        diagnostic = self.diagnostics()
        self._high_rss_samples = (
            self._high_rss_samples + 1 if diagnostic["rss_bytes"] > 1_200 * 1024**2 else 0
        )
        rotate = diagnostic["age_seconds"] >= 4 * 3600 or self._high_rss_samples >= 3
        self._rotation_due = rotate
        # Rotation is idle-only, including interactive leases. A dead driver can
        # be replaced immediately: stopping its transport fails pending calls.
        if (
            rotate
            and not diagnostic["active_pages"]
            and not self._acquiring
            and not self.rotation_paused
        ):
            async with self._driver_lock:
                if self._acquiring or any(e.active_pages for e in self._entries.values()):
                    return
                self.driver_state = "DRAINING"
                self.last_recovery_reason = (
                    "rss_high_watermark" if self._high_rss_samples >= 3 else "max_driver_age"
                )
                await self._detach_driver()
                try:
                    await self._start_driver()
                except BaseException:
                    self.driver_state = "FAILED"
                    raise

    async def close(self) -> None:
        self._stopping = True
        # Do not call browser/context.close(): an attached client must not own Chrome.
        entries = list(self._entries.values())
        await self._detach_driver()
        self.driver_state = "FAILED"
        for entry in entries:
            try:
                await self.client.release_controller(entry.identity.identity_id)
            except Exception:
                pass

    async def close_idle(self) -> int:
        await self.maintain_driver()
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
        epoch = self.driver_epoch
        start_lock = self._start_locks.setdefault(identity.identity_id, asyncio.Lock())
        async with start_lock:
            entry = self._entries.get(identity.identity_id)
            if entry is not None:
                if (
                    entry.identity.revision == identity.revision
                    and entry.driver_epoch == epoch
                    and entry.egress.generation == egress.generation
                    and entry.browser.is_connected()
                    and getattr(entry.cdp, "connected", True)
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
            if epoch != self.driver_epoch or not self.driver_ready:
                await cdp.close()
                raise RuntimeError("external_driver_epoch_changed")
            entry = _ExternalEntry(
                identity=identity,
                egress=egress,
                instance=instance,
                browser=browser,
                context=browser.contexts[0],
                cdp=cdp,
                last_used=time.monotonic(),
                driver_epoch=epoch,
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
            self._acquiring += 1
            entry = await self._entry(identity, egress)
            page = await entry.context.new_page()
            async with self._lock:
                entry.active_pages += 1
                entry.last_used = time.monotonic()
            return _ExternalLease(self, entry, page)
        except BaseException:
            self._page_slots.release()
            raise
        finally:
            self._acquiring -= 1

    async def prewarm(
        self, identity: BrowserIdentitySpec, egress: ProxyEgress
    ) -> RuntimeProvenance:
        self._acquiring += 1
        try:
            entry = await self._entry(identity, egress)
        finally:
            self._acquiring -= 1
        return RuntimeProvenance(
            identity_id=identity.identity_id,
            identity_revision=identity.revision,
            runtime_kind=BrowserRuntimeKind.EXTERNAL_CHROME,
            instance_id=entry.instance.instance_id,
            generation=entry.instance.generation,
        )

    async def existing_page(
        self, identity: BrowserIdentitySpec, egress: ProxyEgress
    ) -> _ExternalLease:
        await self._page_slots.acquire()
        try:
            self._acquiring += 1
            entry = await self._entry(identity, egress)
            candidates = [page for page in entry.context.pages if page.url != "about:blank"]
            if not candidates:
                raise RuntimeError("external_maintenance_page_not_found")
            async with self._lock:
                entry.active_pages += 1
                entry.last_used = time.monotonic()
            return _ExternalLease(self, entry, candidates[-1])
        except BaseException:
            self._page_slots.release()
            raise
        finally:
            self._acquiring -= 1

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
