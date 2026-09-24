"""Managed Playwright adapter over the established persistent browser pool."""

from __future__ import annotations

from typing import Any

from .browser_runtime import RuntimeProvenance
from .runtime import PersistentBrowserPool
from .schema import BrowserIdentitySpec, BrowserRuntimeKind, ProxyEgress


class _ManagedLease:
    def __init__(self, inner: Any, identity: BrowserIdentitySpec) -> None:
        self.inner = inner
        self.page = inner.page
        self.browser_cdp = inner.entry.cdp
        self.provenance = RuntimeProvenance(
            identity_id=identity.identity_id,
            identity_revision=identity.revision,
            runtime_kind=BrowserRuntimeKind.MANAGED_PLAYWRIGHT,
            instance_id=f"managed:{identity.identity_id}:{id(inner.entry)}",
            generation=inner.entry.egress.generation,
        )

    async def __aenter__(self) -> Any:
        return await self.inner.__aenter__()

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.inner.__aexit__(exc_type, exc, traceback)


class ManagedPlaywrightRuntime:
    def __init__(self, pool: PersistentBrowserPool, repository: Any) -> None:
        self.pool = pool
        self.repository = repository

    async def start(self) -> None:
        await self.pool.start()

    async def close(self) -> None:
        await self.pool.close()

    async def close_idle(self) -> int:
        return await self.pool.close_idle()

    async def page(
        self, identity: BrowserIdentitySpec, egress: ProxyEgress, *, deadline: float | None = None
    ) -> _ManagedLease:
        del deadline
        profile = self.repository.get_profile(identity.profile_id)
        if profile is None:
            raise RuntimeError(f"browser_profile_missing:{identity.profile_id}")
        effective = profile.model_copy(update={"environment": identity.environment})
        inner = await self.pool.page(
            effective, egress, max_context_pages=identity.lifecycle.max_context_pages
        )
        return _ManagedLease(inner, identity)

    async def stop_identity(self, identity_id: str, *, reason: str) -> bool:
        identity = self.repository.get_identity(identity_id)
        profile_id = identity.profile_id if identity is not None else identity_id
        return await self.pool.close_profile_if_idle(profile_id, reason=reason)

    async def status(self, identity_id: str) -> dict[str, object]:
        identity = self.repository.get_identity(identity_id)
        profile_id = identity.profile_id if identity is not None else identity_id
        async with self.pool._lock:
            entry = self.pool._entries.get(profile_id)
            return {
                "identity_id": identity_id,
                "runtime_kind": BrowserRuntimeKind.MANAGED_PLAYWRIGHT.value,
                "running": entry is not None,
                "active_pages": entry.active_pages if entry else 0,
                "pages_opened": entry.pages_opened if entry else 0,
            }


__all__ = ["ManagedPlaywrightRuntime"]
