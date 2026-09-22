"""Uniform browser runtime contracts for managed and external Chrome tracks."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from .schema import BrowserIdentitySpec, BrowserRuntimeKind, ProxyEgress


@dataclass(frozen=True)
class RuntimeProvenance:
    identity_id: str
    identity_revision: int
    runtime_kind: BrowserRuntimeKind
    instance_id: str
    generation: int


class PageLease(Protocol):
    page: Any
    browser_cdp: Any
    provenance: RuntimeProvenance

    async def __aenter__(self) -> Any: ...

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None: ...


class BrowserRuntimeAdapter(Protocol):
    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def close_idle(self) -> int: ...

    async def page(
        self, identity: BrowserIdentitySpec, egress: ProxyEgress, *, deadline: float | None = None
    ) -> PageLease: ...

    async def stop_identity(self, identity_id: str, *, reason: str) -> bool: ...

    async def status(self, identity_id: str) -> dict[str, object]: ...


class BrowserRuntimeManager:
    """Selects one owner per Identity and serializes first acquisition."""

    def __init__(
        self,
        managed: BrowserRuntimeAdapter,
        external: BrowserRuntimeAdapter | None,
        relay_target_path: str | Path | None = None,
        max_pages: int = 4,
    ) -> None:
        self.managed = managed
        self.external = external
        self.relay_target_path = Path(relay_target_path) if relay_target_path else None
        self._page_slots = asyncio.Semaphore(max(1, max_pages))
        self._locks: dict[str, asyncio.Lock] = {}

    def adapter(self, identity: BrowserIdentitySpec) -> BrowserRuntimeAdapter:
        if identity.runtime_kind is BrowserRuntimeKind.EXTERNAL_CHROME:
            if self.external is None:
                raise RuntimeError("external_chrome_runtime_unavailable")
            return self.external
        return self.managed

    async def start(self) -> None:
        await self.managed.start()
        if self.external is not None:
            await self.external.start()

    async def close(self) -> None:
        # External close only detaches Playwright; it does not terminate Chrome.
        if self.external is not None:
            await self.external.close()
        await self.managed.close()

    async def close_idle(self) -> int:
        closed = 0
        for adapter in (self.managed, self.external):
            if adapter is None:
                continue
            closed += int(await adapter.close_idle())
        return closed

    async def page(
        self, identity: BrowserIdentitySpec, egress: ProxyEgress, *, deadline: float | None = None
    ) -> PageLease:
        if deadline is None:
            await self._page_slots.acquire()
        else:
            async with asyncio.timeout(max(0.001, deadline - time.monotonic())):
                await self._page_slots.acquire()
        lock = self._locks.setdefault(identity.identity_id, asyncio.Lock())
        try:
            async with lock:
                inner = await self.adapter(identity).page(identity, egress, deadline=deadline)
            return _ManagerLease(inner, self._page_slots)
        except Exception:
            self._page_slots.release()
            raise

    async def stop_identity(self, identity: BrowserIdentitySpec, *, reason: str) -> bool:
        lock = self._locks.setdefault(identity.identity_id, asyncio.Lock())
        async with lock:
            return await self.adapter(identity).stop_identity(identity.identity_id, reason=reason)

    async def status(self, identity: BrowserIdentitySpec) -> dict[str, object]:
        return await self.adapter(identity).status(identity.identity_id)

    async def prewarm(
        self, identity: BrowserIdentitySpec, egress: ProxyEgress
    ) -> RuntimeProvenance | None:
        adapter = self.adapter(identity)
        prewarm = getattr(adapter, "prewarm", None)
        if prewarm is not None:
            return cast(RuntimeProvenance, await prewarm(identity, egress))
        return None

    async def select_maintenance(self, identity: BrowserIdentitySpec) -> None:
        if identity.runtime_kind is BrowserRuntimeKind.EXTERNAL_CHROME:
            adapter = self.adapter(identity)
            selector = getattr(adapter, "select_maintenance", None)
            if selector is None:
                raise RuntimeError("external_vnc_relay_unavailable")
            await selector(identity.identity_id)
            return
        if self.relay_target_path is not None:
            self.relay_target_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.relay_target_path.with_suffix(".tmp")
            temporary.write_text("5910\n", encoding="ascii")
            os.replace(temporary, self.relay_target_path)

    async def recover_page(self, identity: BrowserIdentitySpec, egress: ProxyEgress) -> PageLease:
        if identity.runtime_kind is not BrowserRuntimeKind.EXTERNAL_CHROME:
            raise RuntimeError("managed_maintenance_session_cannot_be_recovered")
        adapter = self.adapter(identity)
        recover = getattr(adapter, "existing_page", None)
        if recover is None:
            raise RuntimeError("external_maintenance_recovery_unavailable")
        await self._page_slots.acquire()
        try:
            return cast(PageLease, _ManagerLease(await recover(identity, egress), self._page_slots))
        except Exception:
            self._page_slots.release()
            raise

    async def close_stale_maintenance_page(
        self, identity: BrowserIdentitySpec, egress: ProxyEgress
    ) -> bool:
        if identity.runtime_kind is not BrowserRuntimeKind.EXTERNAL_CHROME:
            return False
        adapter = self.adapter(identity)
        close_page = getattr(adapter, "close_existing_maintenance_page", None)
        return bool(await close_page(identity, egress)) if close_page else False


class _ManagerLease:
    def __init__(self, inner: PageLease, semaphore: asyncio.Semaphore) -> None:
        self.inner = inner
        self.page = inner.page
        self.browser_cdp = inner.browser_cdp
        self.provenance = inner.provenance
        self.semaphore = semaphore
        self._released = False

    async def __aenter__(self) -> Any:
        return await self.inner.__aenter__()

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            await self.inner.__aexit__(exc_type, exc, traceback)
        finally:
            self._release()

    async def abandon(self) -> None:
        try:
            inner_any: Any = self.inner
            if not hasattr(inner_any, "abandon"):
                await self.inner.__aexit__(None, None, None)
            else:
                await inner_any.abandon()
        finally:
            self._release()

    def _release(self) -> None:
        if not self._released:
            self._released = True
            self.semaphore.release()


__all__ = [
    "BrowserRuntimeAdapter",
    "BrowserRuntimeManager",
    "PageLease",
    "RuntimeProvenance",
]
