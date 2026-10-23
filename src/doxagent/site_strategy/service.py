"""Site Strategy registry validation and bounded access orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import os
import re
import signal
import sqlite3
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from doxagent.content_enrichment.quality import inspect_html
from doxagent.content_enrichment.strategies import validate_body_strategy

from .budget import SiteBudgetManager
from .egress import probe_egress
from .health import RISK_FAILURES, CombinationHealthManager
from .profile_storage import ProfileSnapshotManager
from .repository import SiteStrategyRepository
from .resolver import SiteResolver
from .runtime import RuntimeResponse, SiteAccessRuntime
from .schema import (
    AccessAttempt,
    AccessCombination,
    AccessDisposition,
    AccessEvent,
    AccessMode,
    AccessRequest,
    AccessResult,
    AuthState,
    BodyOutcome,
    BrowserIdentitySpec,
    BrowserProfile,
    BrowserResidency,
    BrowserRuntimeKind,
    FailureCategory,
    IdentityOperationalState,
    ProfileOperationalState,
    ProxyEgress,
    ResolvedSite,
    SiteIdentityAuth,
    SitePurpose,
    SiteStrategySpec,
    digest_json,
    utc_now,
)
from .storage import storage_failure

logger = logging.getLogger(__name__)


class ProfileUnavailableError(RuntimeError):
    pass


def recovery_pending_reason(value):
    if value.manual_review_reason:
        return value.manual_review_reason
    if value.last_error == "datadome_solver_pending":
        return "datadome_solver_pending"
    if value.handling_mode == "HUMAN" and value.assessment.get("kind") == "INTERACTIVE":
        return "human_challenge_pending"
    return "site_recovery_pending"


class ProfileUseCoordinator:
    """Atomic in-process admission paired with persisted operational state."""

    def __init__(self, repository: SiteStrategyRepository) -> None:
        self.repository = repository
        self._condition = asyncio.Condition()
        self._active: dict[str, int] = {}
        self._browser_active: dict[str, int] = {}
        self._exclusive: set[str] = set()
        self._maintenance_profile: str | None = None
        self._challenge_profiles: set[str] = set()
        self._accepting = True

    @property
    def accepting(self) -> bool:
        return self._accepting

    async def recover(self) -> None:
        async with self._condition:
            self._accepting = True
            self._maintenance_profile = None
            self._challenge_profiles.clear()
            from .governance_repository import GovernanceRepository

            store = GovernanceRepository(self.repository)
            sessions = store.list_sessions()
            session_profiles = {}
            for session in sorted(sessions, key=lambda s: (s.state == "OPEN", s.updated_at)):
                if session.state != "OPEN":
                    continue
                identity = self.repository.get_identity(session.identity_id)
                if session.kind == "CHALLENGE":
                    task = store.current_challenge(session.identity_id)
                    if (
                        not task
                        or session.task_id != task.task_id
                        or task.state == "TARGET_LOST"
                        or session.state != "OPEN"
                    ):
                        continue  # Historical released sessions do not reclaim the Profile.
                if identity and session.control_token:
                    session_profiles[identity.profile_id] = session
            external_profiles = {
                identity.profile_id
                for _, identity in self.repository.list_active_identities()
                if identity.runtime_kind is BrowserRuntimeKind.EXTERNAL_CHROME
            }
            for profile in self.repository.list_profiles():
                session = session_profiles.get(profile.profile_id)
                if session:
                    self.repository.save_profile(
                        profile.model_copy(
                            update={
                                "operational_state": ProfileOperationalState.MAINTENANCE,
                                "maintenance_session_id": session.control_token,
                            }
                        )
                    )
                    if session.state == "OPEN":
                        self._maintenance_profile = profile.profile_id
                    if session.kind == "CHALLENGE":
                        self._challenge_profiles.add(profile.profile_id)
                    continue
                if (
                    profile.profile_id in external_profiles
                    and profile.challenge_url
                    and profile.maintenance_session_id
                    and not any(
                        r.identity_id == profile.challenge_identity_id and r.phase != "RESOLVED"
                        for r in store.list_recoveries()
                    )
                    and profile.operational_state
                    in {
                        ProfileOperationalState.MAINTENANCE,
                        ProfileOperationalState.DRAINING_FOR_MAINTENANCE,
                    }
                ):
                    self._challenge_profiles.add(profile.profile_id)
                    if (
                        profile.operational_state
                        is ProfileOperationalState.DRAINING_FOR_MAINTENANCE
                    ):
                        self.repository.save_profile(
                            profile.model_copy(
                                update={
                                    "operational_state": ProfileOperationalState.MAINTENANCE,
                                    "operational_revision": profile.operational_revision + 1,
                                    "updated_at": utc_now(),
                                }
                            )
                        )
                    continue
                if (
                    profile.profile_id in external_profiles
                    and profile.operational_state
                    in {
                        ProfileOperationalState.MAINTENANCE,
                        ProfileOperationalState.DRAINING_FOR_MAINTENANCE,
                    }
                    and profile.maintenance_session_id
                    and self._maintenance_profile is None
                    and not any(
                        r.identity_id == profile.challenge_identity_id and r.phase != "RESOLVED"
                        for r in store.list_recoveries()
                    )
                ):
                    self._maintenance_profile = profile.profile_id
                    if (
                        profile.operational_state
                        is ProfileOperationalState.DRAINING_FOR_MAINTENANCE
                    ):
                        self.repository.save_profile(
                            profile.model_copy(
                                update={
                                    "operational_state": ProfileOperationalState.MAINTENANCE,
                                    "operational_revision": profile.operational_revision + 1,
                                    "updated_at": utc_now(),
                                }
                            )
                        )
                    continue
                auth_state = (
                    AuthState.UNKNOWN
                    if profile.auth_state is AuthState.MAINTENANCE
                    else profile.auth_state
                )
                if (
                    profile.operational_state is not ProfileOperationalState.AVAILABLE
                    or profile.maintenance_session_id is not None
                    or auth_state is not profile.auth_state
                ):
                    self.repository.save_profile(
                        profile.model_copy(
                            update={
                                "auth_state": auth_state,
                                "operational_state": ProfileOperationalState.AVAILABLE,
                                "operational_revision": profile.operational_revision + 1,
                                "maintenance_session_id": None,
                                "updated_at": utc_now(),
                            }
                        )
                    )

    @asynccontextmanager
    async def recovery(self, identity, recovery_id, expected_generation, *, deadline=None):
        from .governance_repository import GovernanceRepository

        async with self._condition:
            store = GovernanceRepository(self.repository)
            value = store.get_recovery(identity.identity_id)
            if (
                not self._accepting
                or not value
                or (value.recovery_id, value.generation) != (recovery_id, expected_generation)
                or not value.lease_until
                or value.lease_until <= utc_now()
            ):
                raise ProfileUnavailableError("stale_recovery_lease")
        async with self.control(
            identity.profile_id, identity.identity_id, deadline=deadline
        ) as profile:
            value = store.get_recovery(identity.identity_id)
            if (
                value.generation != expected_generation
                or not value.lease_until
                or value.lease_until <= utc_now()
            ):
                raise ProfileUnavailableError("stale_recovery_lease")
            yield profile

    @asynccontextmanager
    async def control(self, profile_id, identity_id, *, deadline=None):
        """Short exclusive browser writer for recovery or cold activation, never Viewer."""
        from .governance_repository import GovernanceRepository

        async with self._condition:
            if (
                not self._accepting
                or self._maintenance_profile == profile_id
                or profile_id in self._exclusive
                or any(
                    s.identity_id == identity_id and s.state == "OPEN"
                    for s in GovernanceRepository(self.repository).list_sessions()
                )
            ):
                raise ProfileUnavailableError("user_maintenance_or_business_active")
            self._exclusive.add(profile_id)
            try:
                async with asyncio.timeout(
                    min(20, max(0, deadline - time.monotonic())) if deadline else 20
                ):
                    while self._browser_active.get(profile_id, 0):
                        await self._condition.wait()
                if (
                    not self._accepting
                    or self._maintenance_profile == profile_id
                    or any(
                        s.identity_id == identity_id and s.state == "OPEN"
                        for s in GovernanceRepository(self.repository).list_sessions()
                    )
                ):
                    raise ProfileUnavailableError("user_maintenance_or_business_active")
            except BaseException:
                self._exclusive.discard(profile_id)
                self._condition.notify_all()
                raise
            self._active[profile_id] = self._active.get(profile_id, 0) + 1
        try:
            yield self.repository.get_profile(profile_id)
        finally:
            async with self._condition:
                self._exclusive.discard(profile_id)
                remaining = self._active.get(profile_id, 1) - 1
                if remaining:
                    self._active[profile_id] = remaining
                else:
                    self._active.pop(profile_id, None)
                self._condition.notify_all()

    async def release_recovered(self, identity):
        async with self._condition:
            self._challenge_profiles.discard(identity.profile_id)
            self._condition.notify_all()

    @asynccontextmanager
    async def business(self, profile_id: str, *, browser=True) -> AsyncIterator[BrowserProfile]:
        async with self._condition:
            if browser and profile_id in self._exclusive:
                raise ProfileUnavailableError("profile_control_in_progress")
            profile = self.repository.get_profile(profile_id)
            if profile is None:
                raise ProfileUnavailableError("profile_not_found")
            if not self._accepting:
                raise ProfileUnavailableError("service_draining")
            if profile.operational_state is not ProfileOperationalState.AVAILABLE:
                raise ProfileUnavailableError("profile_in_maintenance")
            self._active[profile_id] = self._active.get(profile_id, 0) + 1
            if browser:
                self._browser_active[profile_id] = self._browser_active.get(profile_id, 0) + 1
        try:
            yield profile
        finally:
            async with self._condition:
                if browser:
                    browser_remaining = self._browser_active.get(profile_id, 1) - 1
                    if browser_remaining:
                        self._browser_active[profile_id] = browser_remaining
                    else:
                        self._browser_active.pop(profile_id, None)
                remaining = self._active.get(profile_id, 1) - 1
                if remaining <= 0:
                    self._active.pop(profile_id, None)
                else:
                    self._active[profile_id] = remaining
                self._condition.notify_all()

    async def begin_maintenance(
        self,
        profile_id: str,
        *,
        timeout_seconds: float = 30,
        session_id: str | None = None,
    ) -> str:
        session_id = session_id or uuid4().hex
        async with self._condition:
            if not self._accepting:
                raise ProfileUnavailableError("service_draining")
            if self._maintenance_profile is not None:
                raise ProfileUnavailableError("another_profile_is_in_maintenance")
            profile = self.repository.get_profile(profile_id)
            if profile is None:
                raise KeyError(profile_id)
            if profile.operational_state is not ProfileOperationalState.AVAILABLE:
                raise ProfileUnavailableError("profile_in_maintenance")
            self._maintenance_profile = profile_id
            self.repository.save_profile(
                profile.model_copy(
                    update={
                        "operational_state": ProfileOperationalState.DRAINING_FOR_MAINTENANCE,
                        "operational_revision": profile.operational_revision + 1,
                        "maintenance_session_id": session_id,
                        "updated_at": utc_now(),
                    }
                )
            )
            try:
                async with asyncio.timeout(timeout_seconds):
                    while self._active.get(profile_id, 0):
                        await self._condition.wait()
            except TimeoutError:
                await self._release_locked(profile_id, session_id)
                raise
            profile = self.repository.get_profile(profile_id)
            assert profile is not None
            self.repository.save_profile(
                profile.model_copy(
                    update={
                        "operational_state": ProfileOperationalState.MAINTENANCE,
                        "operational_revision": profile.operational_revision + 1,
                        "updated_at": utc_now(),
                    }
                )
            )
            return session_id

    async def prepare_login(self, profile_id: str, identity_id: str) -> None:
        """Allocate an independent LOGIN page; a pending Task never becomes that page."""
        from .governance_repository import GovernanceRepository

        async with self._condition:
            store = GovernanceRepository(self.repository)
            task = store.current_challenge(identity_id)
            for session in store.list_sessions():
                if session.identity_id != identity_id or session.kind != "CHALLENGE":
                    continue
                if (
                    task and task.state == "TARGET_LOST" and session.task_id == task.task_id
                ) or not session.target_id:
                    store.save_session(session.model_copy(update={"state": "CLOSED"}))
            profile = self.repository.get_profile(profile_id)
            if task and profile and profile.maintenance_session_id:
                if self._maintenance_profile not in {None, profile_id}:
                    raise ProfileUnavailableError("another_profile_is_in_maintenance")
                # Release only the compatibility reservation, never the Challenge
                # Task, durable target owner or released human session.
                await self._release_locked(
                    profile_id, profile.maintenance_session_id, preserve_challenge=False
                )

    async def begin_challenge(
        self,
        profile_id: str,
        *,
        token: str,
        site_id: str,
        identity_id: str,
        combination_id: str,
        article_url: str,
        target_id: str | None,
    ) -> None:
        """Block new work while the triggering business lease is still active."""
        async with self._condition:
            if not self._accepting:
                raise ProfileUnavailableError("service_draining")
            profile = self.repository.get_profile(profile_id)
            if (
                profile
                and profile.maintenance_session_id == token
                and profile.operational_state
                in {
                    ProfileOperationalState.DRAINING_FOR_MAINTENANCE,
                    ProfileOperationalState.MAINTENANCE,
                }
            ):
                self._challenge_profiles.add(profile_id)
                return  # The ownership transaction already persisted this handoff.
            if (
                profile is None
                or profile.operational_state is not ProfileOperationalState.AVAILABLE
            ):
                raise ProfileUnavailableError("profile_in_maintenance")
            self._challenge_profiles.add(profile_id)
            self.repository.save_profile(
                profile.model_copy(
                    update={
                        "operational_state": ProfileOperationalState.DRAINING_FOR_MAINTENANCE,
                        "operational_revision": profile.operational_revision + 1,
                        "maintenance_session_id": token,
                        "challenge_url": article_url,
                        "challenge_site_id": site_id,
                        "challenge_identity_id": identity_id,
                        "challenge_combination_id": combination_id,
                        "challenge_target_id": target_id,
                        "challenge_verified": False,
                        "updated_at": utc_now(),
                    }
                )
            )

    async def finish_challenge(self, profile_id: str, token: str) -> None:
        async with self._condition:
            async with asyncio.timeout(30):
                while self._active.get(profile_id, 0):
                    await self._condition.wait()
            profile = self.repository.get_profile(profile_id)
            if profile is None or profile.maintenance_session_id != token:
                raise ProfileUnavailableError("invalid_maintenance_session")
            self.repository.save_profile(
                profile.model_copy(
                    update={
                        "operational_state": ProfileOperationalState.MAINTENANCE,
                        "operational_revision": profile.operational_revision + 1,
                        "updated_at": utc_now(),
                    }
                )
            )

    async def recover_maintenance(
        self, profile_id: str, session_id: str, *, timeout_seconds: float = 20
    ) -> BrowserProfile:
        async with self._condition:
            if self._maintenance_profile not in {None, profile_id}:
                raise ProfileUnavailableError("another_profile_is_in_maintenance")
            profile = self.repository.get_profile(profile_id)
            if profile is None:
                raise KeyError(profile_id)
            if (
                profile.operational_state is ProfileOperationalState.MAINTENANCE
                and profile.maintenance_session_id == session_id
                and self._maintenance_profile == profile_id
            ):
                return profile
            self._maintenance_profile = profile_id
            self.repository.save_profile(
                profile.model_copy(
                    update={
                        "operational_state": ProfileOperationalState.DRAINING_FOR_MAINTENANCE,
                        "maintenance_session_id": session_id,
                        "operational_revision": profile.operational_revision + 1,
                        "updated_at": utc_now(),
                    }
                )
            )
            try:
                async with asyncio.timeout(timeout_seconds):
                    while self._active.get(profile_id, 0):
                        await self._condition.wait()
            except BaseException:
                # A failed Viewer handoff must not become a new permanent gate.
                self.repository.save_profile(
                    profile.model_copy(
                        update={
                            "operational_revision": profile.operational_revision + 2,
                            "updated_at": utc_now(),
                        }
                    )
                )
                self._maintenance_profile = None
                self._condition.notify_all()
                raise
            profile = self.repository.get_profile(profile_id)
            updated = profile.model_copy(
                update={
                    "operational_state": ProfileOperationalState.MAINTENANCE,
                    "operational_revision": profile.operational_revision + 1,
                    "maintenance_session_id": session_id,
                    "updated_at": utc_now(),
                }
            )
            self.repository.save_profile(updated)
            return updated

    async def assert_session(self, profile_id: str, session_id: str) -> BrowserProfile:
        async with self._condition:
            profile = self.repository.get_profile(profile_id)
            if (
                profile is None
                or profile.operational_state is not ProfileOperationalState.MAINTENANCE
                or profile.maintenance_session_id != session_id
            ):
                raise ProfileUnavailableError("invalid_maintenance_session")
            return profile

    async def end_maintenance(self, profile_id: str, session_id: str) -> None:
        async with self._condition:
            await self._release_locked(profile_id, session_id)

    async def release_viewer(self, profile_id: str, session_id: str) -> None:
        async with self._condition:
            profile = self.repository.get_profile(profile_id)
            if profile is None or profile.maintenance_session_id != session_id:
                raise ProfileUnavailableError("invalid_maintenance_session")
            from .governance_repository import GovernanceRepository

            store = GovernanceRepository(self.repository)
            session = next(
                (s for s in store.list_sessions() if s.control_token == session_id), None
            )
            recovery = store.get_recovery(session.identity_id) if session else None
            if recovery and recovery.phase != "RESOLVED":
                # Recovery/Task owns the exact page independently. Releasing its
                # Viewer restores proven capabilities and background probes.
                await self._release_locked(profile_id, session_id)
                return
            if self._maintenance_profile == profile_id:
                self._maintenance_profile = None
            self._condition.notify_all()

    async def _release_locked(
        self, profile_id: str, session_id: str, *, preserve_challenge=True
    ) -> None:
        profile = self.repository.get_profile(profile_id)
        if profile is None or profile.maintenance_session_id != session_id:
            raise ProfileUnavailableError("invalid_maintenance_session")
        self.repository.save_profile(
            profile.model_copy(
                update={
                    "operational_state": ProfileOperationalState.AVAILABLE,
                    "operational_revision": profile.operational_revision + 1,
                    "maintenance_session_id": None,
                    "challenge_url": None,
                    "challenge_site_id": None,
                    "challenge_identity_id": None,
                    "challenge_combination_id": None,
                    "challenge_target_id": None,
                    "challenge_verified": False,
                    "updated_at": utc_now(),
                }
            )
        )
        if self._maintenance_profile == profile_id:
            self._maintenance_profile = None
        self._challenge_profiles.discard(profile_id)
        if preserve_challenge:
            from .governance_repository import GovernanceRepository

            store = GovernanceRepository(self.repository)
            task = next(
                (
                    t
                    for t in store.list_challenges()
                    if (identity := self.repository.get_identity(t.identity_id))
                    and identity.profile_id == profile_id
                    and t.state != "TARGET_LOST"
                ),
                None,
            )
            recovery = store.get_recovery(task.identity_id) if task else None
            if task and not (recovery and recovery.phase != "RESOLVED"):
                retained = next(
                    (s for s in store.list_sessions() if s.task_id == task.task_id), None
                )
                current = self.repository.get_profile(profile_id)
                self.repository.save_profile(
                    current.model_copy(
                        update={
                            "operational_state": ProfileOperationalState.MAINTENANCE,
                            "maintenance_session_id": retained.control_token
                            if retained
                            else task.task_id,
                            "challenge_url": task.original_url,
                            "challenge_site_id": task.site_id,
                            "challenge_identity_id": task.identity_id,
                            "challenge_target_id": task.original_target_id,
                            "challenge_verified": False,
                        }
                    )
                )
                self._challenge_profiles.add(profile_id)
        self._condition.notify_all()

    async def begin_shutdown(self) -> None:
        async with self._condition:
            self._accepting = False
            for profile_id in self._active:
                profile = self.repository.get_profile(profile_id)
                if profile is not None:
                    self.repository.save_profile(
                        profile.model_copy(
                            update={
                                "operational_state": ProfileOperationalState.DRAINING_FOR_SHUTDOWN,
                                "operational_revision": profile.operational_revision + 1,
                                "updated_at": utc_now(),
                            }
                        )
                    )


class SiteStrategyService:
    def __init__(
        self,
        repository: SiteStrategyRepository,
        *,
        profile_root: str | Path,
        browser_headless: bool = True,
        browser_channel: str | None = None,
        browser_max_processes: int = 4,
        browser_max_pages: int = 4,
        browser_body_max_pages: int = 2,
        browser_body_borrow: bool = True,
        browser_idle_seconds: float = 43_200,
        safety_path: str | Path | None = None,
        supervisor_socket: str | Path | None = None,
        controller_id: str = "site-access",
        isolated_browser_workers: bool = False,
    ) -> None:
        self.repository = repository
        self.resolver = SiteResolver(repository)
        self.budgets = SiteBudgetManager()
        self.budgets.joint.max_concurrency = browser_max_pages
        self.budgets.joint.body_max_concurrency = browser_body_max_pages
        self.budgets.joint.body_borrow = browser_body_borrow
        self.health = CombinationHealthManager(repository)
        self.profile_use = ProfileUseCoordinator(repository)
        self.profile_storage = ProfileSnapshotManager(profile_root, repository)
        runtime_type = SiteAccessRuntime
        if isolated_browser_workers:
            from .worker_runtime import WorkerSiteAccessRuntime

            runtime_type = WorkerSiteAccessRuntime
        self.runtime = runtime_type(
            repository,
            self.resolver,
            profile_root=profile_root,
            browser_headless=browser_headless,
            browser_channel=browser_channel,
            browser_max_processes=browser_max_processes,
            browser_max_pages=browser_max_pages,
            browser_idle_seconds=browser_idle_seconds,
            safety_path=safety_path,
            supervisor_socket=supervisor_socket,
            controller_id=controller_id,
        )
        self._inflight: dict[str, asyncio.Task[AccessResult]] = {}
        self._inflight_started: dict[str, tuple[float, float]] = {}
        self._cache: OrderedDict[str, tuple[float, int, AccessResult]] = OrderedDict()
        self._cache_bytes = 0
        self._cache_lock = asyncio.Lock()
        self._shared_inflight = {}
        self.budgets.joint.identity_interval = self._identity_navigation_interval
        self.runtime.navigation_pacer = self.budgets.joint
        self._maintenance_task: asyncio.Task[None] | None = None
        self._retention_task: asyncio.Task[None] | None = None
        self._scope_inflight = {}
        self._switching_scopes = set()
        self._native_drains = {}
        from .failover import FailoverCoordinator

        self.failover = FailoverCoordinator(self)
        from .recovery import BrowserRecoveryCoordinator

        self.recovery = BrowserRecoveryCoordinator(self)

    async def start(self) -> None:
        """Acquire the single-owner profile lock and start the browser driver."""
        self.recovery.datadome_solver.normalize_legacy_jobs()
        await self.runtime.browser_runtimes.start()
        await self.profile_use.recover()
        # A persisted HALF_OPEN claim belonged to the previous owner process.
        # Return it to a bounded cooldown; do not clear genuine manual attention.
        for head, spec in self.repository.list_active_strategies():
            if head.enabled:
                state = self.repository.get_runtime(spec.site_id)
                for combination_id, value in state.combinations.items():
                    if value.probe_in_flight or value.state == "HALF_OPEN":
                        await self.health.mark_probe_uncertain(spec.site_id, combination_id)
        # Source warmup is background work, not a condition for serving the
        # maintenance API or for another healthy identity to make progress.
        if self._maintenance_task is None:
            self._maintenance_task = asyncio.create_task(self._maintenance_loop())
        if self._retention_task is None:
            from .retention import RetentionWorker

            self.retention = RetentionWorker(self.repository)
            self.retention.resource_cleanup = self._maintain_managed_resources
            self._retention_task = asyncio.create_task(self.retention.run(), name="site-retention")

    async def _maintain_managed_resources(self):
        from .resource_gc import maintain_profile

        root = self.runtime.browser_pool.root
        # Preserve the production retention fix: retired identities still own
        # profiles, and the shared owner lock fences both browser runtime types.
        for profile in self.repository.list_profiles():
            if not profile.maintenance_session_id:
                await asyncio.to_thread(
                    maintain_profile, root, profile.directory_key, allow_cache=True
                )
        await asyncio.to_thread(self.profile_storage.maintain)
        await asyncio.to_thread(self.runtime.evidence_store.prune)

    async def _prewarm_identities(self) -> None:
        from .governance_repository import GovernanceRepository

        scopes = GovernanceRepository(self.repository).list_scopes()
        hot = set()
        for scope in scopes:
            primary = self.repository.get_identity(scope.primary_identity_id)
            if (
                scope.enabled
                and scope.source_id
                and primary.lifecycle.residency is BrowserResidency.ALWAYS_ON
            ):
                route = self.repository.get_runtime(f"scope:{scope.scope_id}")
                hot.add(route.active_combination_id or scope.primary_identity_id)
        for head, identity in self.repository.list_active_identities():
            if (
                not head.enabled
                or not identity.enabled
                or (scopes and identity.identity_id not in hot)
                or identity.runtime_kind is not BrowserRuntimeKind.EXTERNAL_CHROME
                or (not scopes and identity.lifecycle.residency is not BrowserResidency.ALWAYS_ON)
            ):
                continue
            profile = self.repository.get_profile(identity.profile_id)
            if (profile and profile.maintenance_session_id) or (
                self.repository.get_identity_runtime(identity.identity_id).operational_state
                is IdentityOperationalState.UNHEALTHY
            ):
                continue
            egress = self.repository.get_egress(identity.egress_id)
            if egress is None or not egress.enabled:
                continue
            try:
                provenance = await self.runtime.browser_runtimes.prewarm(identity, egress)
                if provenance is not None:
                    current = self.repository.get_identity_runtime(identity.identity_id)
                    if (
                        current.instance_id == provenance.instance_id
                        and current.operational_state is IdentityOperationalState.AVAILABLE
                        and current.diagnostic is None
                    ):
                        continue
                    self.repository.save_identity_runtime(
                        current.model_copy(
                            update={
                                "operational_state": IdentityOperationalState.AVAILABLE,
                                "instance_id": provenance.instance_id,
                                "generation": current.generation + 1,
                                "diagnostic": None,
                            }
                        ),
                        expected_generation=current.generation,
                    )
            except Exception as exc:
                logger.error(
                    "external identity prewarm failed identity=%s error=%s",
                    identity.identity_id,
                    type(exc).__name__,
                )
            await asyncio.sleep(0.5)

    @property
    def browser_driver_ready(self) -> bool:
        if hasattr(self.runtime, "workers"):
            return True
        return self.runtime.browser_pool.driver_ready and not self.access_diagnostics()["stalled"]

    def access_diagnostics(self) -> dict[str, object]:
        now = time.monotonic()
        operations = []
        for key, task in tuple(self._inflight.items()):
            started, budget = self._inflight_started.get(key, (now, 0))
            age = now - started
            awaiting = []
            coroutine = task.get_coro()
            while coroutine is not None and len(awaiting) < 12:
                frame = getattr(coroutine, "cr_frame", None)
                if frame is not None:
                    awaiting.append(f"{frame.f_code.co_name}:{frame.f_lineno}")
                coroutine = getattr(coroutine, "cr_await", None)
            operations.append(
                {
                    "task": task.get_name(),
                    "age_ms": int(age * 1000),
                    "budget_ms": int(budget * 1000),
                    "done": task.done(),
                    "stalled": not task.done() and age > budget + 15,
                    "awaiting": awaiting,
                }
            )
        joint = self.budgets.joint
        return {
            "external_driver": (
                self.runtime.browser_runtimes.external.diagnostics()
                if self.runtime.browser_runtimes.external is not None
                and hasattr(self.runtime.browser_runtimes.external, "diagnostics")
                else None
            ),
            "stalled": any(item["stalled"] for item in operations),
            "operations": operations,
            "storage": self.repository.storage.diagnostics(),
            "queue_waiters": len(joint._waiters),
            "queue_locked": joint._condition.locked(),
            "site_active": dict(joint._site_active),
            "identity_active": dict(joint._identity_active),
        }

    @property
    def accepting(self) -> bool:
        return self.profile_use.accepting

    async def close(self) -> None:
        await self.recovery.close()
        await self.failover.close()
        for task in self._native_drains.values():
            task.cancel()
        if self._native_drains:
            await asyncio.wait(tuple(self._native_drains.values()), timeout=3)
        await self.profile_use.begin_shutdown()
        if self._retention_task is not None:
            self._retention_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._retention_task
            self._retention_task = None
        if self._maintenance_task is not None:
            self._maintenance_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._maintenance_task
            self._maintenance_task = None
        inflight = tuple(self._inflight.values())
        if inflight:
            try:
                async with asyncio.timeout(45):
                    await asyncio.gather(*inflight, return_exceptions=True)
            except TimeoutError:
                for task in inflight:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*inflight, return_exceptions=True)
        try:
            async with asyncio.timeout(30):
                await self.runtime.close()
        finally:
            self.repository.close()

    async def _maintenance_loop(self) -> None:
        while True:
            exit_code = self.runtime.browser_pool.driver_exit_code
            if exit_code is not None:
                # A dead Node driver cannot be replaced safely while Chrome
                # contexts and profile writer locks still belong to this process.
                # Let the container restart the entire Site Access owner.
                logger.critical(
                    "Playwright driver exited code=%s; restarting Site Access", exit_code
                )
                os.kill(os.getpid(), signal.SIGTERM)
                return
            if self.repository.storage.diagnostics()["unavailable"]:
                await asyncio.sleep(60)
                continue
            external = self.runtime.browser_runtimes.external
            from doxagent.source_maintenance.settings import enabled as maintenance_enabled

            driver_before = external.diagnostics() if maintenance_enabled() and external else None
            try:
                await self._prewarm_identities()
                external = self.runtime.browser_runtimes.external
                if external is not None:
                    external.rotation_paused = any(
                        profile.maintenance_session_id
                        for profile in self.repository.list_profiles()
                    )
                await self.runtime.browser_runtimes.close_idle()
                from doxagent.source_maintenance.runtime_signals import observe

                observe(self, driver_before, external.diagnostics() if driver_before else None)
                if not self.repository.storage.diagnostics()["unavailable"]:
                    await self._run_due_probes()
            except asyncio.CancelledError:
                raise
            except Exception:
                from doxagent.source_maintenance.runtime_signals import observe

                observe(
                    self,
                    driver_before,
                    external.diagnostics() if driver_before else None,
                    failed=bool(driver_before and not external.driver_ready),
                )
                logger.exception("site access maintenance probe failed")
            await asyncio.sleep(60)

    async def _run_due_probes(self) -> None:
        now = utc_now()
        slots = asyncio.Semaphore(2)

        async def check_egress(egress: ProxyEgress) -> None:
            if not egress.enabled or (
                (egress.next_probe_at is not None and now < egress.next_probe_at)
                or (
                    egress.next_probe_at is None
                    and egress.observed_at is not None
                    and now - egress.observed_at < timedelta(minutes=30)
                )
            ):
                return
            try:
                async with slots:
                    await self.probe_egress(egress.egress_id)
            except Exception:
                logger.exception("egress maintenance probe failed: %s", egress.egress_id)

        await asyncio.gather(*(check_egress(e) for e in self.repository.list_egresses()))
        from .governance_repository import GovernanceRepository

        scopes = GovernanceRepository(self.repository).list_scopes()
        if scopes:
            for scope in scopes:
                if not scope.enabled:
                    continue
                key = f"scope:{scope.scope_id}"
                state = self.repository.get_runtime(key)
                target = state.active_combination_id or scope.primary_identity_id
                if GovernanceRepository(self.repository).current_challenge(target):
                    continue
                claimed = await self.health.claim_due_probe(
                    key,
                    [
                        AccessCombination(id=target, identity_id=target),
                    ],
                )
                if claimed:
                    purpose = (
                        SitePurpose.CRAWLER
                        if scope.verification_plan.get("crawler")
                        else SitePurpose.BODY
                    )
                    try:
                        outcome = await self.failover.activate(
                            scope, purpose, primary=target == scope.primary_identity_id
                        )
                    except Exception:
                        outcome = {"ready": False}
                    if not outcome.get("ready"):
                        await self.health.mark_probe_uncertain(key, target)
            return  # Never probe retired legacy combinations after source routing activates.
        for head, spec in self.repository.list_active_strategies():
            if not head.enabled or spec.site_id == "generic" or not self._recovery_probe_url(spec):
                continue
            claimed = await self.health.claim_due_probe(spec.site_id, spec.access.combinations)
            if claimed is None:
                continue
            combination, assigned_generation = claimed
            try:
                await self._probe_combination(spec, combination, assigned_generation)
            except asyncio.CancelledError:
                await self.health.mark_probe_uncertain(spec.site_id, combination.combination_id)
                raise
            except Exception:
                await self.health.mark_probe_uncertain(spec.site_id, combination.combination_id)
                logger.exception(
                    "combination maintenance probe failed: %s/%s",
                    spec.site_id,
                    combination.combination_id,
                )

    def _recovery_probe_url(self, spec: SiteStrategySpec) -> str | None:
        # Older strategies sometimes omitted probe_url. Reuse only an existing
        # same-publisher public maintenance endpoint, never a login/paywall URL.
        candidates = [spec.access.probe_url]
        if spec.auth.verification_kind == "public_access":
            candidates.extend([spec.auth.verification_url, spec.auth.maintenance_url])
        for url in candidates:
            if not url:
                continue
            try:
                if self.resolve(url).site_id == spec.site_id:
                    return url
            except (ValueError, RuntimeError):
                continue
        return None

    async def probe_egress(self, egress_id: str) -> ProxyEgress:
        prior = self.repository.get_egress(egress_id)
        if prior is None:
            raise KeyError(egress_id)
        updated = await probe_egress(self.repository, egress_id)
        if updated.status != prior.status:
            self.repository.append_event(
                AccessEvent(
                    operation_id=f"egress-state:{egress_id}:{utc_now().isoformat()}",
                    site_id="_egress",
                    category="EGRESS_STATE_CHANGED",
                    payload={
                        "egress_id": egress_id,
                        "before": prior.status,
                        "after": updated.status,
                        "error": updated.last_probe_error,
                        "seconds": updated.last_probe_seconds,
                    },
                )
            )
        if updated.generation != prior.generation:
            await self.runtime.reset_egress(egress_id)
            self.repository.append_event(
                AccessEvent(
                    operation_id=f"egress-probe:{egress_id}:{updated.generation}",
                    site_id="_egress",
                    category="EGRESS_IP_CHANGED",
                    payload={
                        "egress_id": egress_id,
                        "prior_ip": prior.observed_ip,
                        "observed_ip": updated.observed_ip,
                        "probe_endpoint": updated.probe_endpoint,
                    },
                )
            )
        duplicates = [
            item.egress_id
            for item in self.repository.list_egresses()
            if item.egress_id != egress_id
            and item.status == "READY"
            and item.observed_ip == updated.observed_ip
            and updated.observed_ip is not None
        ]
        if duplicates:
            self.repository.append_event(
                AccessEvent(
                    operation_id=f"egress-probe:{egress_id}:{updated.generation}",
                    site_id="_egress",
                    category="DUPLICATE_EXIT",
                    payload={"egress_id": egress_id, "duplicates": duplicates},
                )
            )
        return updated

    async def _probe_combination(
        self,
        spec: SiteStrategySpec,
        combination: AccessCombination,
        assigned_generation: int,
    ) -> None:
        probe_url = self._recovery_probe_url(spec)
        if probe_url is None:
            await self.health.mark_probe_uncertain(spec.site_id, combination.combination_id)
            return
        resolved = self.resolve(probe_url, revision=spec.revision)
        materialized = self._materialize_combination(combination)
        if materialized is None:
            await self.health.mark_probe_uncertain(spec.site_id, combination.combination_id)
            return
        combination = materialized
        profile = self._profile(resolved, combination)
        assert combination.egress_id is not None
        egress = self.repository.get_egress(combination.egress_id)
        if profile is None or egress is None or not egress.enabled:
            await self.health.mark_probe_uncertain(spec.site_id, combination.combination_id)
            return
        request = AccessRequest(
            operation_id=f"probe:{spec.site_id}:{combination.combination_id}:{assigned_generation}",
            purpose=SitePurpose.PROBE,
            url=probe_url,
            mode=AccessMode.BROWSER,
            strategy_revision=spec.revision,
            remaining_budget_ms=15_000,
            max_response_bytes=2_000_000,
        )
        identity = self._identity_for(combination, profile)
        try:
            async with self.budgets.joint.permit(
                SitePurpose.PROBE,
                site_key=resolved.runtime_key,
                identity_id=identity.identity_id,
                site_max_concurrency=resolved.access.max_concurrency,
                site_min_interval_ms=resolved.access.min_interval_ms,
                identity_max_concurrency=identity.access.max_concurrency,
                identity_min_interval_ms=identity.access.min_interval_ms,
                timeout_seconds=10,
            ):
                async with self.profile_use.business(profile.profile_id) as current_profile:
                    response, category, reason, network_ms = await self._attempt(
                        request, resolved, combination, current_profile, egress
                    )
        except (TimeoutError, ProfileUnavailableError):
            await self.health.mark_probe_uncertain(spec.site_id, combination.combination_id)
            return
        self._event(
            request,
            resolved,
            combination,
            "COMBINATION_RECOVERY_PROBE",
            AccessAttempt(
                combination_id=combination.combination_id,
                generation=assigned_generation,
                transport=AccessMode.BROWSER,
                status_code=response.status_code or None,
                failure_category=category,
                reason_code=reason,
                network_ms=network_ms,
                exit_ip=egress.observed_ip,
                identity_id=identity.identity_id,
            ),
        )
        if category is None or category is FailureCategory.EMPTY_SUCCESS:
            await self.health.mark_success(
                spec.site_id,
                combination.combination_id,
                assigned_generation=assigned_generation,
                transport=request.mode,
            )
        elif category in RISK_FAILURES:
            await self.health.mark_failure(
                spec.site_id,
                combination.combination_id,
                category,
                reason,
                assigned_generation=assigned_generation,
                retry_after_seconds=response.retry_after_seconds,
                transport=request.mode,
            )
        else:
            await self.health.mark_probe_uncertain(spec.site_id, combination.combination_id)

    def resolve(self, url: str, *, revision: int | None = None) -> ResolvedSite:
        return self.resolver.resolve(url, revision=revision)

    def _resolve_request(self, request: AccessRequest) -> ResolvedSite:
        from .scope import ScopeResolver

        return ScopeResolver(self.repository).resolve(
            self.resolve(request.url, revision=request.strategy_revision),
            request.origin_scope_id,
            request.scope_revision,
        )

    def _identity_belongs_to_site(self, site_id: str, identity_id: str) -> bool:
        from .governance_repository import GovernanceRepository

        scopes = GovernanceRepository(self.repository).list_scopes()
        owner = next(
            (
                scope
                for scope in scopes
                if identity_id
                in {
                    scope.primary_identity_id,
                    scope.fallback_identity_id,
                }
            ),
            None,
        )
        if owner:
            return owner.owner_site_id == site_id
        spec = self.repository.get_strategy(site_id)
        identity = self.repository.get_identity(identity_id)
        return bool(
            spec
            and identity
            and any(
                item.identity_id == identity_id
                or (
                    item.identity_id is None
                    and item.profile_id == identity.profile_id
                    and item.egress_id == identity.egress_id
                )
                for item in spec.access.combinations
            )
        )

    def validate_strategy(self, spec: SiteStrategySpec) -> None:
        self.resolver.validate_no_conflicts(spec)
        validate_body_strategy(spec.body.ref, spec.body.parameters)
        if spec.crawler and not spec.crawler.ref.startswith(("crawler:", "builtin:")):
            raise ValueError("crawler ref must use crawler:<id> or builtin:<recipe>@<version>")
        if spec.crawler and spec.crawler.ref.startswith("builtin:"):
            schemas: dict[str, set[str]] = {
                "builtin:yahoo_page@1": set(),
                "builtin:reuters_search@1": set(),
                "builtin:barrons_ticker@1": set(),
                "builtin:trendforce_listings@1": set(),
                "builtin:digitimes_semiconductors@1": set(),
                "builtin:investorshub_ticker@1": set(),
                "builtin:investing_ticker@1": set(),
                "builtin:globenewswire_search@1": set(),
            }
            allowed = schemas.get(spec.crawler.ref)
            if allowed is None:
                raise ValueError(f"unknown builtin crawler strategy: {spec.crawler.ref}")
            unknown = set(spec.crawler.parameters) - allowed
            if unknown:
                raise ValueError(f"unsupported crawler parameters: {sorted(unknown)}")
        known_combinations: set[str] = set()
        for combination in spec.access.combinations:
            if combination.combination_id in known_combinations:
                raise ValueError("duplicate access combination")
            known_combinations.add(combination.combination_id)
            materialized = self._materialize_combination(combination)
            if materialized is None:
                raise ValueError(f"unknown browser identity: {combination.identity_id}")
            combination = materialized
            assert combination.profile_id and combination.egress_id
            egress = self.repository.get_egress(combination.egress_id)
            if egress is None:
                raise ValueError(f"unknown egress: {combination.egress_id}")
            profile = self.repository.get_profile(combination.profile_id)
            if profile is None:
                raise ValueError(f"unknown browser profile: {combination.profile_id}")
            if (
                combination.identity_id is None
                and spec.site_id != "generic"
                and profile.site_id != spec.site_id
            ):
                raise ValueError(
                    f"profile {profile.profile_id} belongs to {profile.site_id}, not {spec.site_id}"
                )
            if profile.bound_egress_id != combination.egress_id:
                raise ValueError(
                    f"profile {profile.profile_id} is not bound to {combination.egress_id}"
                )
        if not any(item.enabled for item in spec.access.combinations):
            raise ValueError("site strategy requires at least one enabled access combination")

    def apply_strategy(
        self,
        spec: SiteStrategySpec,
        *,
        expected_revision: int | None,
        actor: str,
        enabled: bool = True,
    ) -> SiteStrategySpec:
        self.validate_strategy(spec)
        stored = self.repository.apply_strategy(
            spec,
            expected_revision=expected_revision,
            actor=actor,
            enabled=enabled,
        )
        self.repository.append_event(
            AccessEvent(
                operation_id=f"strategy:{stored.site_id}:{stored.revision}",
                site_id=stored.site_id,
                category="STRATEGY_APPLIED",
                payload={"revision": stored.revision, "actor": actor},
            )
        )
        return stored

    def validate_identity(self, identity: BrowserIdentitySpec) -> None:
        profile = self.repository.get_profile(identity.profile_id)
        if profile is None:
            raise ValueError(f"unknown browser profile: {identity.profile_id}")
        egress = self.repository.get_egress(identity.egress_id)
        if egress is None:
            raise ValueError(f"unknown egress: {identity.egress_id}")
        if profile.bound_egress_id != identity.egress_id:
            raise ValueError("profile and identity egress binding differ")
        allowed = {
            BrowserRuntimeKind.MANAGED_PLAYWRIGHT: {"playwright-cft-153"},
            BrowserRuntimeKind.EXTERNAL_CHROME: {"google-chrome-153.0.8010.52"},
        }
        if identity.browser_release not in allowed[identity.runtime_kind]:
            raise ValueError("browser release is not approved for runtime kind")

    def apply_identity(
        self,
        identity: BrowserIdentitySpec,
        *,
        expected_revision: int | None,
        actor: str,
        enabled: bool | None = None,
    ) -> BrowserIdentitySpec:
        self.validate_identity(identity)
        return self.repository.apply_identity(
            identity,
            expected_revision=expected_revision,
            actor=actor,
            enabled=enabled,
        )

    def _identity_navigation_interval(self, identity_id, configured):
        state = self.repository.get_identity_runtime(identity_id)
        if state.probation_until and (
            state.probation_until > utc_now() or not state.natural_success_at
        ):
            return max(configured, state.probation_interval_ms)
        return configured

    def _shared_key(self, request):
        if (
            request.purpose is not SitePurpose.CRAWLER
            or request.mode is AccessMode.HTTP_PUBLIC
            or not request.recipe_ref
        ):
            return None
        resolved = self._resolve_request(request)
        binding = []
        for combination in resolved.access.combinations:
            identity = self.repository.get_identity(combination.identity_id or "")
            if not identity:
                continue
            runtime = self.repository.get_identity_runtime(identity.identity_id)
            egress = self.repository.get_egress(identity.egress_id)
            binding.append(
                (
                    identity.model_dump(mode="json"),
                    runtime.instance_id,
                    runtime.session_revision,
                    egress.generation,
                    egress.observed_ip,
                )
            )
        return "capture:" + digest_json(
            {
                "request": request.model_dump(
                    mode="json",
                    exclude={"request_id", "operation_id", "parent_id", "remaining_budget_ms"},
                ),
                "site_revision": resolved.strategy_revision,
                "bindings": binding,
                "route_generation": self.repository.get_runtime(resolved.runtime_key).generation,
                "scope": resolved.match_evidence,
            }
        )

    async def execute(self, request: AccessRequest) -> AccessResult:
        consumed_at = time.monotonic()
        if self.repository.storage.diagnostics()["unavailable"]:
            return self._storage_deferred(request)
        if not self.profile_use.accepting:
            return await self._execute_idempotent(request)
        key = self._shared_key(request)
        if key is None:
            return await self._execute_idempotent(request)
        shared = False
        async with self._cache_lock:
            self._expire_cache()
            cached = self._cache.get(key)
            task = self._shared_inflight.get(key)
            if cached:
                result, shared = cached[2], True
            else:
                if task is None:
                    task = asyncio.create_task(self._execute_idempotent(request))
                    self._shared_inflight[key] = task
                    task.add_done_callback(lambda t, k=key: self._shared_inflight.pop(k, None))
                else:
                    shared = True
                result = None
        if result is None:
            result = await asyncio.shield(task)
            if result.disposition is AccessDisposition.SUCCESS:
                result = result.model_copy(
                    update={"capture_id": result.capture_id or result.request_id,
                            "capture_acquired_at": result.capture_acquired_at or utc_now()}
                )
                await self._put_cache(result, cache_key=self._shared_key(request), ttl=15)
        if shared:
            self.repository.append_event(
                AccessEvent(
                    operation_id=request.operation_id,
                    site_id=result.site_id,
                    category="CAPTURE_CONSUMED",
                    payload={
                        "capture_id": result.capture_id or result.request_id,
                        "identity_id": result.identity_id,
                        "purpose": request.purpose.value,
                        "capture_acquired_at": result.capture_acquired_at.isoformat() if result.capture_acquired_at else None,
                        "consumer_elapsed_ms": int((time.monotonic() - consumed_at)*1000),
                    },
                )
            )
        return result.model_copy(
            update={
                "request_id": request.request_id,
                "operation_id": request.operation_id,
                "served_from_shared_capture": shared,
                "consumer_elapsed_ms": int((time.monotonic() - consumed_at)*1000),
                "capture_age_ms": max(0, int((utc_now() - result.capture_acquired_at).total_seconds()*1000)) if result.capture_acquired_at else None,
            }
        )

    async def _execute_idempotent(self, request: AccessRequest) -> AccessResult:
        if self.repository.storage.diagnostics()["unavailable"]:
            return self._storage_deferred(request)
        if not self.profile_use.accepting:
            resolved = self.resolve(request.url, revision=request.strategy_revision)
            return self._result(
                request,
                resolved,
                AccessDisposition.SERVICE_UNAVAILABLE,
                category=FailureCategory.RUNTIME_UNAVAILABLE,
                reason="service_draining",
                started=time.monotonic(),
            )
        created = False
        cache_key = self._cache_key(request)
        async with self._cache_lock:
            self._expire_cache()
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache.move_to_end(cache_key)
                return cached[2]
            task = self._inflight.get(cache_key)
            if task is None:
                task = asyncio.create_task(
                    self._execute_deadlined(request),
                    name=f"site-access:{self.resolve(request.url).site_id}:{request.request_id}",
                )
                self._inflight[cache_key] = task
                self._inflight_started[cache_key] = (
                    time.monotonic(),
                    request.remaining_budget_ms / 1000,
                )
                task.add_done_callback(
                    lambda finished, key=cache_key: self._forget_access_task(key, finished)
                )
                created = True
        try:
            result = await asyncio.shield(task)
        except sqlite3.Error as exc:
            if not storage_failure(exc):
                raise
            return self._storage_deferred(request)
        finally:
            if task.done():
                async with self._cache_lock:
                    if self._inflight.get(cache_key) is task:
                        self._inflight.pop(cache_key, None)
                        self._inflight_started.pop(cache_key, None)
        if task.done() and not task.cancelled() and task.exception() is None:
            blocked_task = None
            if result.effective_scope_id and result.disposition is not AccessDisposition.SUCCESS:
                from .governance_repository import GovernanceRepository

                store = GovernanceRepository(self.repository)
                scope = store.get_scope(result.effective_scope_id)
                route = self.repository.get_runtime(result.runtime_key)
                blocked_task = (
                    store.current_challenge(
                        route.active_combination_id or scope.primary_identity_id
                    )
                    if scope
                    else None
                )
                if blocked_task:
                    if (
                        not result.attempts
                        and result.disposition is not AccessDisposition.RECOVERY_PENDING
                    ):
                        store.save_challenge(
                            blocked_task.model_copy(
                                update={
                                    "seen_count": blocked_task.seen_count + 1,
                                    "last_check": utc_now(),
                                }
                            )
                        )
                    result = result.model_copy(update={"challenge_task_id": blocked_task.task_id})
                    if (
                        not result.attempts
                        and result.disposition is AccessDisposition.AUTH_REQUIRED
                    ):
                        result = result.model_copy(
                            update={
                                "disposition": AccessDisposition.ACCESS_EXHAUSTED,
                                "failure_category": FailureCategory.ACCESS_CHALLENGE,
                                "reason_code": "challenge_target_lost"
                                if blocked_task.state == "TARGET_LOST"
                                else "human_challenge_required",
                            }
                        )
                    if result.attempts:
                        blocked_task = (
                            None  # Persist the initial real failure, not every later poll.
                        )
            await self._put_cache(result, cache_key=cache_key)
            # This operation itself may activate a new route. Lost-response retries
            # should observe its receipt without performing the website I/O again.
            post_key = self._cache_key(request)
            route = self.repository.get_runtime(result.runtime_key)
            if result.route_generation == route.generation and post_key != cache_key:
                await self._put_cache(result, cache_key=post_key)
            if (
                created
                and blocked_task is None
                and not (
                    result.disposition is AccessDisposition.RECOVERY_PENDING and not result.attempts
                )
            ):
                event_strategy_ref = request.recipe_ref
                if event_strategy_ref is None and request.purpose is SitePurpose.CRAWLER:
                    event_site = self.resolve(request.url, revision=result.strategy_revision)
                    event_strategy_ref = event_site.crawler.ref if event_site.crawler else None
                self.repository.append_event(
                    AccessEvent(
                        operation_id=request.operation_id,
                        site_id=result.site_id,
                        combination_id=result.combination_id,
                        category="ACCESS_RESULT",
                        payload={
                            "purpose": request.purpose.value,
                            "identity_id": result.identity_id,
                            "effective_scope_id": result.effective_scope_id,
                            "disposition": result.disposition.value,
                            "failure_category": (
                                result.failure_category.value
                                if result.failure_category is not None
                                else None
                            ),
                            "reason": result.reason_code,
                            "strategy_revision": result.strategy_revision,
                            "strategy_ref": event_strategy_ref or result.body_strategy_ref,
                            "generation": result.generation,
                            "network_ms": result.network_ms,
                            "queue_wait_ms": result.queue_wait_ms,
                            "stage_timings": result.stage_timings,
                            "work_class": request.work_class,
                        },
                    )
                )
        return result

    def _storage_deferred(self, request: AccessRequest) -> AccessResult:
        return self._result(
            request,
            self.resolve(request.url, revision=request.strategy_revision),
            AccessDisposition.SERVICE_UNAVAILABLE,
            category=FailureCategory.RUNTIME_UNAVAILABLE,
            reason="storage_unavailable",
            retry_not_before=utc_now() + timedelta(seconds=60),
            started=time.monotonic(),
        )

    def _forget_access_task(self, key: str, task: asyncio.Task[AccessResult]) -> None:
        if self._inflight.get(key) is task:
            self._inflight.pop(key, None)
            self._inflight_started.pop(key, None)

    def _cache_key(self, request: AccessRequest) -> str:
        resolved = self._resolve_request(request)
        identities: list[tuple[str, int, int]] = []
        for combination in resolved.access.combinations:
            identity_id = combination.identity_id or combination.profile_id
            if not identity_id:
                continue
            identity = self.repository.get_identity(identity_id)
            runtime = self.repository.get_identity_runtime(identity_id)
            identities.append(
                (
                    identity_id,
                    identity.revision if identity else 0,
                    runtime.session_revision,
                )
            )
        fingerprint = digest_json(
            {
                "request": request.model_dump(mode="json"),
                "site_revision": resolved.strategy_revision,
                "identities": sorted(identities),
                "route_generation": self.repository.get_runtime(resolved.runtime_key).generation,
                "scope_revision": resolved.match_evidence.get("scope_revision"),
            }
        )
        return f"{request.request_id}:{fingerprint}"

    async def _execute_deadlined(self, request: AccessRequest) -> AccessResult:
        started = time.monotonic()
        resolved = self._resolve_request(request)
        key = resolved.runtime_key
        self._scope_inflight[key] = self._scope_inflight.get(key, 0) + 1
        try:
            async with asyncio.timeout_at(started + request.remaining_budget_ms / 1000):
                return await self._execute_uncached(request)
        except TimeoutError:
            if resolved.match_evidence.get("scope_id"):
                from .governance_repository import GovernanceRepository

                scope = GovernanceRepository(self.repository).get_scope(
                    resolved.match_evidence["scope_id"]
                )
                self.failover.schedule(scope, request.purpose)
            return self._result(
                request,
                resolved,
                AccessDisposition.BUDGET_DEFERRED,
                category=FailureCategory.BUDGET_DEFERRED,
                reason="access_budget_exhausted",
                started=started,
            )
        finally:
            self._scope_inflight[key] -= 1
            if not self._scope_inflight[key]:
                self._scope_inflight.pop(key)

    async def effective_candidates(
        self,
        resolved: ResolvedSite,
        purpose: SitePurpose,
        mode: AccessMode = AccessMode.BROWSER,
        excluded: set[str] | None = None,
    ) -> list[dict[str, object]]:
        """Read-only explanation of the real purpose/health/resource selection."""
        combinations = self._purpose_combinations(resolved, purpose)
        state, ready, _ = await self.health.candidates(
            resolved.runtime_key,
            combinations,
            excluded=excluded or set(),
            transport=mode,
            purpose=purpose,
            read_only=True,
        )
        eligible_ids = {c.combination_id for c in ready}
        purpose_ids = {c.combination_id for c in combinations}
        rows = []
        from .governance_repository import GovernanceRepository

        scope = GovernanceRepository(self.repository).get_scope(
            resolved.match_evidence.get("scope_id", "")
        )
        for original in resolved.access.combinations:
            combo = self._materialize_combination(original)
            runtime = state.combinations.get(original.combination_id)
            profile = self.repository.get_profile(combo.profile_id or "") if combo else None
            egress = self.repository.get_egress(combo.egress_id or "") if combo else None
            reasons = []
            if not original.enabled:
                reasons.append("disabled")
            if original.combination_id not in purpose_ids:
                reasons.append("purpose_override")
            if original.combination_id in (excluded or set()):
                reasons.append("request_excluded")
            if combo is None:
                reasons.append("identity_disabled")
            if profile is None:
                reasons.append("profile_missing")
            elif profile.operational_state is not ProfileOperationalState.AVAILABLE:
                reasons.append("identity_in_maintenance")
            if (
                egress is None
                or not egress.enabled
                or egress.status in {"CONFIG_MISSING", "UNAVAILABLE"}
            ):
                reasons.append("egress_unavailable")
            auth_state = (
                self._auth_state(resolved, combo, profile)
                if combo and profile
                else AuthState.UNKNOWN
            )
            if self._auth_required(resolved, purpose) and auth_state is not AuthState.VALID:
                reasons.append("auth_required")
            if runtime and original.combination_id not in eligible_ids:
                if mode is not AccessMode.HTTP_PUBLIC and runtime.manual_attention_required:
                    reasons.append("manual_attention")
                elif runtime.purpose_rejected_until.get(purpose.value, utc_now()) > utc_now():
                    reasons.append("purpose_cooldown")
                elif original.enabled and original.combination_id in purpose_ids:
                    reasons.append("cooldown")
            if scope and mode is not AccessMode.HTTP_PUBLIC:
                status = self.failover.candidate_status(
                    scope, original.identity_id or original.combination_id, purpose
                )
                reasons = list(dict.fromkeys([*reasons, *status["skip_reasons"]]))
            identity = self._identity_for(combo, profile) if combo and profile else None
            external = getattr(getattr(self.runtime, "browser_runtimes", None), "external", None)
            entries = getattr(external, "_entries", {})
            attached = bool(identity and identity.identity_id in entries)
            rows.append(
                {
                    "combination_id": original.combination_id,
                    "enabled": original.enabled,
                    "identity_id": original.identity_id,
                    "profile_id": combo.profile_id if combo else None,
                    "egress_id": combo.egress_id if combo else None,
                    "egress_status": egress.status if egress else None,
                    "auth_state": auth_state.value,
                    "runtime_kind": identity.runtime_kind.value if identity else None,
                    "runtime_attached": attached,
                    "cold_start_capacity": "not_required"
                    if attached
                    else "requires_supervisor_check",
                    "skip_reasons": reasons,
                    "selectable": not reasons,
                }
            )
        return rows

    async def _execute_uncached(self, request: AccessRequest) -> AccessResult:
        started = time.monotonic()
        resolved = self._resolve_request(request)
        if resolved.runtime_key in self._switching_scopes:
            return self._result(
                request,
                resolved,
                AccessDisposition.BUDGET_DEFERRED,
                reason="scope_activation_in_progress",
                category=FailureCategory.BUDGET_DEFERRED,
                retry_not_before=utc_now() + timedelta(seconds=60),
                started=started,
            )
        if not resolved.enabled:
            return self._result(
                request,
                resolved,
                AccessDisposition.SITE_DISABLED,
                reason="site_disabled",
                started=started,
            )
        combinations = self._purpose_combinations(resolved, request.purpose)
        view = await self.effective_candidates(
            resolved, request.purpose, request.mode, set(request.excluded_combinations)
        )
        skips = [row for row in view if row["skip_reasons"]]
        route, candidates, next_retry = await self.health.candidates(
            resolved.runtime_key,
            combinations,
            excluded=set(request.excluded_combinations),
            transport=request.mode,
            purpose=request.purpose,
        )
        scope = None
        if resolved.match_evidence.get("scope_id"):
            from .governance_repository import GovernanceRepository

            scope = GovernanceRepository(self.repository).get_scope(
                resolved.match_evidence["scope_id"]
            )
            if scope and request.mode is not AccessMode.HTTP_PUBLIC:
                candidates = [
                    c
                    for c in candidates
                    if self.failover.candidate_status(
                        scope, c.identity_id or c.combination_id, request.purpose
                    )["selectable"]
                ]
        if not candidates:
            if scope and request.mode is not AccessMode.HTTP_PUBLIC:
                for candidate in (scope.primary_identity_id, scope.fallback_identity_id):
                    if candidate and candidate not in request.excluded_combinations:
                        status = self.failover.candidate_status(scope, candidate, request.purpose)
                        if status.get("eligible") and not status["selectable"]:
                            self.failover.schedule(scope, request.purpose, candidate_id=candidate)
            if request.mode is not AccessMode.HTTP_PUBLIC:
                from .governance_repository import GovernanceRepository

                store = GovernanceRepository(self.repository)
                pending = next(
                    (
                        r
                        for c in combinations
                        if (r := store.get_recovery(c.identity_id or c.profile_id or ""))
                        and r.phase != "RESOLVED"
                        and any(x["purpose"] == request.purpose.value for x in r.affected_contexts)
                    ),
                    None,
                )
                if pending:
                    retry_at = max(pending.next_due_at, utc_now() + timedelta(seconds=60))
                    result = self._result(
                        request,
                        resolved,
                        AccessDisposition.RECOVERY_PENDING,
                        reason=recovery_pending_reason(pending),
                        started=started,
                        retry_not_before=retry_at,
                        combination_skips=skips,
                    )
                    return result.model_copy(
                        update={
                            "recovery_id": pending.recovery_id,
                            "challenge_task_id": pending.challenge_task_id,
                            "handling_mode": pending.handling_mode,
                            "retry_at": retry_at,
                        }
                    )
            manual = any("manual_attention" in row["skip_reasons"] for row in skips)
            if any("auth_required" in row["skip_reasons"] for row in skips):
                return self._result(
                    request,
                    resolved,
                    AccessDisposition.AUTH_REQUIRED,
                    category=FailureCategory.AUTH_REQUIRED,
                    reason="no_authenticated_profile_available",
                    combination_skips=skips,
                    started=started,
                )
            return self._result(
                request,
                resolved,
                AccessDisposition.ACCESS_EXHAUSTED,
                category=FailureCategory.ACCESS_CHALLENGE
                if manual
                else FailureCategory.ACCESS_RATE_LIMIT,
                reason="manual_attention_required" if manual else "access_combinations_unavailable",
                combination_skips=skips,
                retry_not_before=next_retry,
                started=started,
            )
        attempts: list[AccessAttempt] = []
        risk_failures = 0
        unavailable = 0
        auth_missing = 0
        maintenance_blocked = 0
        runtime_failure = None
        pending_result = None
        for combination in candidates:
            materialized = self._materialize_combination(combination)
            if materialized is None:
                unavailable += 1
                continue
            combination = materialized
            remaining = request.remaining_budget_ms / 1000 - (time.monotonic() - started)
            if scope and combination.identity_id == scope.fallback_identity_id and remaining < 5:
                activation = self.failover.schedule(scope, request.purpose)
                return self._result(
                    request,
                    resolved,
                    AccessDisposition.BUDGET_DEFERRED,
                    category=FailureCategory.BUDGET_DEFERRED,
                    reason="fallback_activation_pending" if activation else "fallback_not_ready",
                    attempts=attempts,
                    retry_not_before=utc_now() + timedelta(seconds=60),
                    started=started,
                )
            if remaining <= 0.003:
                return self._result(
                    request,
                    resolved,
                    AccessDisposition.BUDGET_DEFERRED,
                    category=FailureCategory.BUDGET_DEFERRED,
                    reason="access_budget_exhausted",
                    attempts=attempts,
                    started=started,
                )
            profile = self._profile(resolved, combination)
            if profile and profile.operational_state is not ProfileOperationalState.AVAILABLE:
                maintenance_blocked += 1
                continue
            assert combination.egress_id is not None
            egress = self.repository.get_egress(combination.egress_id)
            if (
                scope
                and combination.identity_id == scope.fallback_identity_id
                and egress
                and route.active_combination_id != scope.fallback_identity_id
            ):
                primary = self.repository.get_identity(scope.primary_identity_id)
                main_egress = self.repository.get_egress(primary.egress_id)
                if (
                    main_egress
                    and main_egress.node_ref != "DIRECT"
                    and (
                        not egress.observed_ip
                        or not main_egress.observed_ip
                        or egress.observed_ip == main_egress.observed_ip
                    )
                ):
                    unavailable += 1
                    skips.append(
                        {
                            "combination_id": combination.combination_id,
                            "skip_reasons": ["fallback_egress_not_independent"],
                        }
                    )
                    continue
            if (
                profile is None
                or egress is None
                or not egress.enabled
                or egress.status
                in {
                    "CONFIG_MISSING",
                    "UNAVAILABLE",
                }
            ):
                unavailable += 1
                # Resource admission is not a real browser/network attempt.
                # Its exact blockers are already carried by combination_skips.
                continue
            if (
                self._auth_required(resolved, request.purpose)
                and self._auth_state(resolved, combination, profile) is not AuthState.VALID
            ):
                auth_missing += 1
                continue
            if (
                scope
                and combination.identity_id == scope.fallback_identity_id
                and route.active_combination_id != scope.fallback_identity_id
                and request.mode is not AccessMode.HTTP_PUBLIC
                and not self.failover.usable(scope, combination.identity_id, request.purpose)
            ):
                activation = self.failover.schedule(scope, request.purpose)
                return self._result(
                    request,
                    resolved,
                    AccessDisposition.BUDGET_DEFERRED,
                    category=FailureCategory.BUDGET_DEFERRED,
                    reason="fallback_activation_pending" if activation else "fallback_not_ready",
                    attempts=attempts,
                    retry_not_before=utc_now() + timedelta(seconds=60),
                    started=started,
                )
            budget = self.budgets.get(
                resolved.runtime_key,
                max_concurrency=resolved.access.max_concurrency,
                min_interval_ms=resolved.access.min_interval_ms,
            )
            identity = self._identity_for(combination, profile)
            queue_cap = (
                resolved.access.body_queue_timeout_ms
                if request.purpose is SitePurpose.BODY
                else resolved.access.crawler_queue_timeout_ms
            ) / 1000
            queue_cap = min(queue_cap, 3.0)
            permit = (
                budget.permit(request.purpose, timeout_seconds=min(remaining, queue_cap))
                if request.mode is AccessMode.HTTP_PUBLIC
                else self.budgets.joint.permit(
                    request.purpose,
                    site_key=resolved.runtime_key,
                    identity_id=identity.identity_id,
                    site_max_concurrency=resolved.access.max_concurrency,
                    site_min_interval_ms=resolved.access.min_interval_ms,
                    identity_max_concurrency=identity.access.max_concurrency,
                    identity_min_interval_ms=identity.access.min_interval_ms,
                    timeout_seconds=min(remaining, queue_cap),
                    work_class=request.work_class,
                    work_age_seconds=request.work_age_seconds,
                )
            )
            attempting = False
            queue_started = time.monotonic()
            try:
                async with permit as queue_wait_ms:
                    current = self.repository.get_runtime(resolved.runtime_key)
                    async with self.profile_use.business(
                        profile.profile_id, browser=request.mode is not AccessMode.HTTP_PUBLIC
                    ) as current_profile:
                        if (
                            self._auth_required(resolved, request.purpose)
                            and self._auth_state(resolved, combination, current_profile)
                            is not AuthState.VALID
                        ):
                            auth_missing += 1
                            continue
                        profile = current_profile
                        attempting = True
                        remaining = max(
                            0.001,
                            request.remaining_budget_ms / 1000 - (time.monotonic() - started),
                        )
                        async with asyncio.timeout(remaining):
                            response, category, reason, network_ms = await self._attempt(
                                request.model_copy(
                                    update={"remaining_budget_ms": int(remaining * 1000)}
                                ),
                                resolved,
                                combination,
                                profile,
                                egress,
                            )
                        if response.challenge_token is not None:
                            await self.profile_use.begin_challenge(
                                profile.profile_id,
                                token=response.challenge_token,
                                site_id=resolved.site_id,
                                identity_id=identity.identity_id,
                                combination_id=combination.combination_id,
                                article_url=request.url,
                                target_id=response.challenge_target_id,
                            )
                    attempt = AccessAttempt(
                        combination_id=combination.combination_id,
                        generation=current.generation,
                        transport=request.mode,
                        status_code=response.status_code or None,
                        failure_category=category,
                        reason_code=reason,
                        queue_wait_ms=queue_wait_ms,
                        network_ms=network_ms,
                        stage_timings=response.stage_timings,
                        exit_ip=egress.observed_ip,
                        identity_id=(
                            response.provenance.identity_id
                            if response.provenance
                            else combination.identity_id
                        ),
                        runtime_kind=(
                            response.provenance.runtime_kind if response.provenance else None
                        ),
                        runtime_instance_id=(
                            response.provenance.instance_id if response.provenance else None
                        ),
                        runtime_generation=(
                            response.provenance.generation if response.provenance else None
                        ),
                    )
                    attempts.append(attempt)
            except ProfileUnavailableError as exc:
                if str(exc) == "profile_control_in_progress":
                    from .governance_repository import GovernanceRepository

                    pending = GovernanceRepository(self.repository).get_recovery(
                        identity.identity_id
                    )
                    retry_at = utc_now() + timedelta(seconds=60)
                    result = self._result(
                        request,
                        resolved,
                        AccessDisposition.RECOVERY_PENDING
                        if pending and pending.phase != "RESOLVED"
                        else AccessDisposition.BUDGET_DEFERRED,
                        reason=recovery_pending_reason(pending)
                        if pending and pending.phase != "RESOLVED"
                        else "identity_control_busy",
                        attempts=attempts,
                        retry_not_before=retry_at,
                        started=started,
                    )
                    return result.model_copy(
                        update={
                            "recovery_id": pending.recovery_id if pending else None,
                            "challenge_task_id": pending.challenge_task_id if pending else None,
                            "handling_mode": pending.handling_mode if pending else None,
                            "retry_at": retry_at,
                        }
                    )
                unavailable += 1
                continue
            except TimeoutError:
                waited = max(0,int((time.monotonic()-queue_started)*1000))
                if attempting:
                    attempts.append(AccessAttempt(combination_id=combination.combination_id,
                        generation=current.generation,transport=request.mode,
                        failure_category=FailureCategory.TRANSIENT_TRANSPORT,
                        reason_code="transport_timeout",queue_wait_ms=queue_wait_ms,
                        network_ms=max(0,waited-queue_wait_ms),identity_id=identity.identity_id))
                deferred_result = self._result(
                    request,
                    resolved,
                    AccessDisposition.BUDGET_DEFERRED,
                    category=FailureCategory.BUDGET_DEFERRED,
                    reason="browser_attempt_timeout" if attempting else "site_queue_timeout",
                    attempts=attempts,
                    retry_not_before=utc_now()+timedelta(seconds=1) if not attempting else None,
                    started=started,
                )
                if not attempting:
                    deferred_result.queue_wait_ms += waited
                return deferred_result
            if category in RISK_FAILURES:
                risk_failures += 1
                await self.health.mark_failure(
                    resolved.runtime_key,
                    combination.combination_id,
                    category,
                    reason,
                    assigned_generation=attempt.generation,
                    retry_after_seconds=response.retry_after_seconds,
                    transport=request.mode,
                    handling_mode=(response.assessment or {}).get("handling_mode"),
                )
                self._event(request, resolved, combination, "COMBINATION_RISK_FAILURE", attempt)
                if response.challenge_token is not None:
                    await self.profile_use.finish_challenge(
                        profile.profile_id, response.challenge_token
                    )
                    # The human task blocks only this identity. The candidate
                    # loop must still consider this source's independent fallback.
                if response.recovery_id:
                    from .governance_repository import GovernanceRepository

                    pending = GovernanceRepository(self.repository).get_recovery(
                        identity.identity_id
                    )
                    retry_at = max(pending.next_due_at, utc_now() + timedelta(seconds=60))
                    self.failover.schedule(
                        scope, request.purpose
                    ) if pending.handling_mode == "HUMAN" else None
                    result = self._result_from_response(
                        request,
                        resolved,
                        combination,
                        profile,
                        egress,
                        response,
                        AccessDisposition.RECOVERY_PENDING,
                        category,
                        recovery_pending_reason(pending),
                        attempts,
                        started,
                    )
                    pending_result = result.model_copy(
                        update={
                            "recovery_id": pending.recovery_id,
                            "challenge_task_id": pending.challenge_task_id,
                            "handling_mode": pending.handling_mode,
                            "retry_at": retry_at,
                            "retry_not_before": retry_at,
                        }
                    )
                    if combination is candidates[-1]:
                        return pending_result
                    continue
                continue
            if category is FailureCategory.EGRESS_UNAVAILABLE:
                unavailable += 1
                continue
            if category in {
                FailureCategory.AUTH_REQUIRED,
                FailureCategory.ENTITLEMENT_MISSING,
            }:
                # Entitlement is article-specific: one premium article must not
                # invalidate a Profile that can still read other articles.
                if category is FailureCategory.AUTH_REQUIRED:
                    self._mark_identity_auth(resolved, combination, profile)
                    if scope:
                        auth_missing += 1
                        continue  # Only the already-authenticated scoped backup may replace it.
                return self._result_from_response(
                    request,
                    resolved,
                    combination,
                    profile,
                    egress,
                    response,
                    AccessDisposition.AUTH_REQUIRED,
                    category,
                    reason,
                    attempts,
                    started,
                )
            if category is FailureCategory.AUTH_OR_ACCESS_UNKNOWN:
                if request.purpose is SitePurpose.CRAWLER and response.status_code == 403:
                    await self.health.mark_unknown_denial(
                        resolved.runtime_key,
                        combination.combination_id,
                        request.purpose,
                        request.operation_id,
                    )
                    unavailable += 1
                    self._event(request, resolved, combination, "PURPOSE_ACCESS_DENIED", attempt)
                    continue
                return self._result_from_response(
                    request,
                    resolved,
                    combination,
                    profile,
                    egress,
                    response,
                    AccessDisposition.SERVICE_UNAVAILABLE,
                    category,
                    reason,
                    attempts,
                    started,
                )
            if category in {
                FailureCategory.CONTENT_ERROR,
                FailureCategory.EXTRACTION_ERROR,
                FailureCategory.REGION_RESTRICTED,
            }:
                return self._result_from_response(
                    request,
                    resolved,
                    combination,
                    profile,
                    egress,
                    response,
                    AccessDisposition.CONTENT_ERROR,
                    category,
                    reason,
                    attempts,
                    started,
                )
            if category in {
                FailureCategory.TRANSIENT_TRANSPORT,
                FailureCategory.RUNTIME_UNAVAILABLE,
            }:
                if self.repository.storage.diagnostics()["unavailable"]:
                    return self._storage_deferred(request)
                runtime_failure = (category, reason)
                continue
            if category is FailureCategory.UNKNOWN:
                return self._result_from_response(
                    request,
                    resolved,
                    combination,
                    profile,
                    egress,
                    response,
                    AccessDisposition.SERVICE_UNAVAILABLE,
                    category,
                    reason,
                    attempts,
                    started,
                )
            if response.redirect_url:
                return self._result_from_response(
                    request,
                    resolved,
                    combination,
                    profile,
                    egress,
                    response,
                    AccessDisposition.REDIRECT_REQUIRED,
                    None,
                    "redirect_requires_resolution",
                    attempts,
                    started,
                )
            final_resolved = self.resolve(response.final_url)
            final_host = urlsplit(response.final_url).hostname or ""
            if (
                final_resolved.site_id != resolved.site_id
                or (
                    resolved.site_id == "generic"
                    and final_resolved.match_evidence.get("host")
                    != resolved.match_evidence.get("host")
                )
            ) and not self.resolver.supports_host(resolved.site_id, final_host):
                response.redirect_url = response.final_url
                return self._result_from_response(
                    request,
                    resolved,
                    combination,
                    profile,
                    egress,
                    response,
                    AccessDisposition.REDIRECT_REQUIRED,
                    None,
                    "cross_site_navigation",
                    attempts,
                    started,
                )
            await self.health.mark_success(
                resolved.runtime_key,
                combination.combination_id,
                assigned_generation=attempt.generation,
                transport=request.mode,
            )
            await self.health.clear_purpose_denial(
                resolved.runtime_key,
                combination.combination_id,
                request.purpose,
            )
            if scope and request.mode is not AccessMode.HTTP_PUBLIC:
                from .governance_repository import GovernanceRepository

                GovernanceRepository(self.repository).record_capability(
                    scope,
                    identity.identity_id,
                    request.purpose.value,
                    "READY"
                    if request.purpose is SitePurpose.CRAWLER
                    else response.body_quality or "FULL",
                    url=response.final_url,
                    instance_id=response.provenance.instance_id if response.provenance else None,
                    subscription_verified=response.subscription_verified,
                )
                active = self.repository.get_runtime(resolved.runtime_key)
                if active.active_combination_id != identity.identity_id and (
                    active.active_combination_id is None
                    or not self.failover.candidate_status(
                        scope, active.active_combination_id, SitePurpose.CRAWLER
                    )["selectable"]
                ):
                    await self.health.activate(
                        resolved.runtime_key,
                        identity.identity_id,
                        expected_generation=active.generation,
                    )
                from .datadome_solver import mark_solver_outcome

                mark_solver_outcome(
                    self.repository,
                    identity.identity_id,
                    "natural_success_at",
                    purpose=request.purpose.value,
                )
                identity_state = self.repository.get_identity_runtime(identity.identity_id)
                if identity_state.recovered_at and not identity_state.natural_success_at:
                    self.repository.save_identity_runtime(
                        identity_state.model_copy(
                            update={
                                "natural_success_at": utc_now(),
                            }
                        ),
                        expected_generation=identity_state.generation,
                    )
            current_generation = self.repository.get_runtime(resolved.runtime_key).generation
            return self._result_from_response(
                request,
                resolved,
                combination,
                profile,
                egress,
                response,
                AccessDisposition.SUCCESS,
                category,
                reason,
                attempts,
                started,
                generation=current_generation,
            )
        if pending_result is not None:
            return pending_result
        refreshed, _, next_retry = await self.health.candidates(
            resolved.runtime_key,
            combinations,
            excluded=set(request.excluded_combinations),
            transport=request.mode,
            purpose=request.purpose,
        )
        if auth_missing and auth_missing == len(candidates):
            disposition = AccessDisposition.AUTH_REQUIRED
            category = FailureCategory.AUTH_REQUIRED
            reason = "no_authenticated_profile_available"
        elif risk_failures:
            disposition = AccessDisposition.ACCESS_EXHAUSTED
            category = FailureCategory.ACCESS_RATE_LIMIT
            reason = "risk_combinations_exhausted"
        elif runtime_failure:
            disposition = AccessDisposition.SERVICE_UNAVAILABLE
            category, reason = runtime_failure
        else:
            disposition = AccessDisposition.SERVICE_UNAVAILABLE
            category = FailureCategory.EGRESS_UNAVAILABLE
            reason = "access_resources_unavailable"
        if not attempts and maintenance_blocked:
            category = FailureCategory.RUNTIME_UNAVAILABLE
            reason = "identity_in_maintenance"
            next_retry = utc_now() + timedelta(seconds=60)
        elif not attempts and unavailable and not auth_missing:
            reason = "egress_unavailable"
            next_retry = max(next_retry or utc_now(), utc_now() + timedelta(seconds=120))
        return self._result(
            request,
            resolved,
            disposition,
            category=category,
            reason=reason,
            attempts=attempts,
            retry_not_before=next_retry,
            combination_skips=skips,
            generation=refreshed.generation,
            started=started,
        )

    async def _attempt(
        self,
        request: AccessRequest,
        resolved: ResolvedSite,
        combination: AccessCombination,
        profile: BrowserProfile,
        egress: ProxyEgress,
    ) -> tuple[RuntimeResponse, FailureCategory | None, str, int]:
        last: tuple[RuntimeResponse, FailureCategory | None, str, int] | None = None
        total_started = time.monotonic()
        for retry in range(2):
            started = time.monotonic()
            try:
                response = await self.runtime.execute(
                    request, resolved, combination, profile, egress
                )
                category, reason = classify_response(response)
                scope_id = resolved.match_evidence.get("scope_id")
                if (
                    category is None
                    and scope_id
                    and request.mode is not AccessMode.HTTP_PUBLIC
                    and not response.redirect_url
                    and 200 <= response.status_code < 300
                ):
                    from .governance_repository import GovernanceRepository
                    from .verification import validate_capture, validate_crawler_capture

                    scope = GovernanceRepository(self.repository).get_scope(str(scope_id))
                    if request.purpose is SitePurpose.CRAWLER and scope and scope.source_id:
                        try:
                            ready = validate_crawler_capture(scope.source_id, response, request)
                        except Exception:
                            ready = False
                        if not ready:
                            category, reason = (
                                FailureCategory.CONTENT_ERROR,
                                "crawler_contract_failed",
                            )
                    elif request.purpose is SitePurpose.BODY:
                        proof = validate_capture(response, resolved)
                        response.body_quality = proof.body
                        response.subscription_verified = proof.authentication == "VALID"
                        if proof.body not in {"FULL", "SHORT_FULL"}:
                            category, reason = FailureCategory.EXTRACTION_ERROR, proof.reason
            except Exception as exc:
                if isinstance(exc, sqlite3.Error) and storage_failure(exc):
                    raise
                logger.exception(
                    "runtime attempt failed: operation=%s site=%s purpose=%s "
                    "mode=%s combination=%s identity=%s retry=%s",
                    request.operation_id,
                    resolved.site_id,
                    request.purpose.value,
                    request.mode.value,
                    combination.combination_id,
                    combination.identity_id or profile.profile_id,
                    retry + 1,
                )
                status = int(getattr(exc, "status_code", 0) or 0)
                response = RuntimeResponse(
                    status,
                    getattr(exc, "response_url", request.url),
                    getattr(exc, "response_headers", {}),
                    body=getattr(exc, "response_body", ""),
                    reason=type(exc).__name__,
                    retry_after_seconds=float(getattr(exc, "retry_after_seconds", 0) or 0),
                )
                category, reason = (
                    classify_response(response)
                    if status and not getattr(exc, "diagnostics", None)
                    else classify_exception(exc)
                )
                if category is FailureCategory.RUNTIME_UNAVAILABLE:
                    identity_id = combination.identity_id or profile.profile_id
                    identity_runtime = self.repository.get_identity_runtime(identity_id)
                    try:
                        self.repository.save_identity_runtime(
                            identity_runtime.model_copy(
                                update={
                                    "operational_state": IdentityOperationalState.UNHEALTHY,
                                    "generation": identity_runtime.generation + 1,
                                    "diagnostic": type(exc).__name__,
                                }
                            ),
                            expected_generation=identity_runtime.generation,
                        )
                    except RuntimeError:
                        pass
                    if getattr(exc, "control_recovery_attempted", False) and reason in {
                        "browser_cdp_attach_failed",
                        "browser_driver_unavailable",
                        "identity_worker_disconnected",
                    }:
                        identity = self.repository.get_identity(identity_id)
                        if identity and identity.runtime_kind is BrowserRuntimeKind.EXTERNAL_CHROME:
                            self._schedule_native_drain(identity)
            elapsed = int((time.monotonic() - started) * 1000)
            response.stage_timings["attempt_ms"] = elapsed
            response.stage_timings["prior_retry_ms"] = max(0,int((time.monotonic()-total_started)*1000)-elapsed)
            if category in {
                FailureCategory.CONTENT_ERROR,
                FailureCategory.EXTRACTION_ERROR,
                FailureCategory.AUTH_OR_ACCESS_UNKNOWN,
            }:
                from .failure_shapes import record_failure_shape

                try:
                    record_failure_shape(
                        self.repository,
                        request,
                        resolved,
                        combination.identity_id or profile.profile_id,
                        response,
                        category,
                        reason,
                    )
                except (ValueError, sqlite3.Error):
                    pass  # Optional evidence cannot override the actual access outcome.
            if category is not None and resolved.match_evidence.get("scope_id"):
                from .governance_repository import GovernanceRepository

                store = GovernanceRepository(self.repository)
                scope = store.get_scope(resolved.match_evidence["scope_id"])
                store.record_capability(
                    scope,
                    combination.identity_id or profile.profile_id,
                    request.purpose.value,
                    category.value,
                    url=request.url,
                )
            last = response, category, reason, int((time.monotonic() - total_started)*1000)
            if category is not FailureCategory.TRANSIENT_TRANSPORT or retry == 1:
                break
        assert last is not None
        return last

    def _schedule_native_drain(self, identity):
        previous = self._native_drains.get(identity.identity_id)
        if previous and not previous.done():
            return

        async def drain():
            reserved_revision = None
            try:
                async with asyncio.timeout(5), self.profile_use._condition:
                    while self.profile_use._active.get(identity.profile_id, 0):
                        await self.profile_use._condition.wait()
                    profile = self.repository.get_profile(identity.profile_id)
                    if not profile or profile.maintenance_session_id:
                        return  # Native human scenes are never automatically drained.
                    reserved_revision = profile.operational_revision + 1
                    self.repository.save_profile(
                        profile.model_copy(
                            update={
                                "operational_state": (
                                    ProfileOperationalState.DRAINING_FOR_MAINTENANCE
                                ),
                                "operational_revision": reserved_revision,
                            }
                        )
                    )
                async with asyncio.timeout(30):
                    stopped = await self.runtime.browser_runtimes.stop_identity(
                        identity,
                        reason="unhealthy_native_graceful_recovery",
                    )
                self.repository.append_event(
                    AccessEvent(
                        operation_id=f"native-drain:{identity.identity_id}:{reserved_revision}",
                        category="NATIVE_GRACEFUL_DRAIN",
                        site_id=profile.site_id,
                        payload={"identity_id": identity.identity_id, "stopped": stopped},
                    )
                )
            except Exception:
                logger.warning(
                    "native graceful recovery unconfirmed identity=%s", identity.identity_id
                )
            finally:
                async with self.profile_use._condition:
                    profile = self.repository.get_profile(identity.profile_id)
                    if (
                        profile
                        and profile.operational_revision == reserved_revision
                        and not profile.maintenance_session_id
                    ):
                        self.repository.save_profile(
                            profile.model_copy(
                                update={
                                    "operational_state": ProfileOperationalState.AVAILABLE,
                                    "operational_revision": profile.operational_revision + 1,
                                }
                            )
                        )
                    self.profile_use._condition.notify_all()

        self._native_drains[identity.identity_id] = asyncio.create_task(
            drain(),
            name=f"native-graceful-recovery:{identity.identity_id}",
        )

    def _profile(
        self, resolved: ResolvedSite, combination: AccessCombination
    ) -> BrowserProfile | None:
        combination = self._materialize_combination(combination) or combination
        profile = self.repository.get_profile(combination.profile_id or "")
        if resolved.site_id != "generic" or resolved.runtime_key.startswith("scope:"):
            return profile
        egress = self.repository.get_egress(combination.egress_id or "")
        if egress is None:
            return None
        suffix = hashlib.sha256(resolved.runtime_key.encode()).hexdigest()[:12]
        assert combination.egress_id is not None
        egress_id = combination.egress_id
        profile_id = f"generic-{suffix}-{egress_id}"[:128]
        derived = self.repository.get_profile(profile_id)
        if derived is not None:
            return derived
        derived = BrowserProfile(
            profile_id=profile_id,
            site_id="generic",
            bound_egress_id=egress_id,
            directory_key=f"generic-{suffix}-{egress_id}"[:128],
        )
        self.repository.save_profile(derived)
        return derived

    def _materialize_combination(self, combination: AccessCombination) -> AccessCombination | None:
        if combination.identity_id is None:
            return combination
        identity = self.repository.get_identity(combination.identity_id)
        head = self.repository.get_identity_head(combination.identity_id)
        if identity is None or head is None or not head.enabled or not identity.enabled:
            return None
        if combination.profile_id and combination.profile_id != identity.profile_id:
            raise ValueError("combination profile conflicts with Browser Identity")
        if combination.egress_id and combination.egress_id != identity.egress_id:
            raise ValueError("combination egress conflicts with Browser Identity")
        return combination.model_copy(
            update={"profile_id": identity.profile_id, "egress_id": identity.egress_id}
        )

    def _identity_for(
        self, combination: AccessCombination, profile: BrowserProfile
    ) -> BrowserIdentitySpec:
        identity_id = combination.identity_id or profile.profile_id
        identity = self.repository.get_identity(identity_id)
        if identity is not None:
            return identity
        return BrowserIdentitySpec(
            identity_id=identity_id,
            revision=1,
            runtime_kind=BrowserRuntimeKind.MANAGED_PLAYWRIGHT,
            profile_id=profile.profile_id,
            egress_id=profile.bound_egress_id,
            environment=profile.environment,
        )

    def _auth_state(
        self,
        resolved: ResolvedSite,
        combination: AccessCombination,
        profile: BrowserProfile,
    ) -> AuthState:
        identity = self._identity_for(combination, profile)
        auth = self.repository.get_site_identity_auth(resolved.site_id, identity.identity_id)
        if auth.auth_state is AuthState.UNKNOWN and combination.identity_id is None:
            return profile.auth_state
        runtime = self.repository.get_identity_runtime(identity.identity_id)
        if auth.observed_session_revision < runtime.session_revision:
            return AuthState.UNKNOWN
        return auth.auth_state

    def _purpose_combinations(
        self, resolved: ResolvedSite, purpose: SitePurpose
    ) -> list[AccessCombination]:
        override = (
            resolved.access.overrides.get("body")
            if purpose is SitePurpose.BODY
            else resolved.access.overrides.get("crawler")
        )
        if not override:
            values = list(resolved.access.combinations)
        else:
            allowed = set(override)
            values = [
                item for item in resolved.access.combinations if item.combination_id in allowed
            ]
        return [self._materialize_combination(item) or item for item in values]

    @staticmethod
    def _auth_required(resolved: ResolvedSite, purpose: SitePurpose) -> bool:
        override = (
            resolved.auth.body_requirement
            if purpose is SitePurpose.BODY
            else resolved.auth.crawler_requirement
        )
        requirement = resolved.auth.requirement if override == "inherit" else override
        return requirement == "required"

    def _mark_identity_auth(
        self,
        resolved: ResolvedSite,
        combination: AccessCombination,
        profile: BrowserProfile,
    ) -> None:
        state = AuthState.REAUTH_REQUIRED
        self.repository.save_profile(
            profile.model_copy(update={"auth_state": state, "updated_at": utc_now()})
        )
        identity = self._identity_for(combination, profile)
        runtime = self.repository.get_identity_runtime(identity.identity_id)
        self.repository.save_site_identity_auth(
            SiteIdentityAuth(
                site_id=resolved.site_id,
                identity_id=identity.identity_id,
                auth_state=state,
                reason_code=FailureCategory.AUTH_REQUIRED.value,
                observed_session_revision=runtime.session_revision,
            )
        )

    def save_outcomes(self, values: list[BodyOutcome]) -> int:
        for value in values:
            self.repository.save_body_outcome(value)
        return len(values)

    async def probe_profile(self, profile_id: str, url: str) -> AccessResult:
        """Exercise an exact Profile+egress for rollout diagnosis, without auth mutation."""
        started = time.monotonic()
        profile = self.repository.get_profile(profile_id)
        if profile is None:
            raise KeyError(profile_id)
        resolved = self.resolve(url)
        # A shared Identity can serve several sites. The active combination
        from .governance_repository import GovernanceRepository

        owner = next(
            (
                scope
                for scope in GovernanceRepository(self.repository).list_scopes()
                if any(
                    (identity := self.repository.get_identity(member))
                    and identity.profile_id == profile_id
                    for member in (scope.primary_identity_id, scope.fallback_identity_id)
                    if member
                )
            ),
            None,
        )
        if owner:
            if owner.owner_site_id != resolved.site_id:
                raise ValueError("profile scope does not own the requested publisher")
            resolved = self._resolve_request(
                AccessRequest(
                    operation_id="profile-probe-resolve",
                    purpose=SitePurpose.PROBE,
                    mode=AccessMode.BROWSER,
                    url=url,
                    origin_scope_id=owner.scope_id,
                )
            )
        # below, rather than the legacy Profile owner, establishes ownership.
        combination = next(
            (
                materialized
                for item in resolved.access.combinations
                if (materialized := self._materialize_combination(item)) is not None
                and materialized.profile_id == profile_id
                and materialized.egress_id == profile.bound_egress_id
            ),
            None,
        )
        if combination is None:
            raise ValueError("profile is not referenced by the active site strategy")
        egress = self.repository.get_egress(profile.bound_egress_id)
        if egress is None or not egress.enabled or egress.status == "CONFIG_MISSING":
            raise RuntimeError("bound egress is unavailable")
        request = AccessRequest(
            operation_id=f"profile-probe:{profile_id}:{int(time.time())}",
            purpose=SitePurpose.PROBE,
            url=url,
            mode=AccessMode.BROWSER,
            remaining_budget_ms=30_000,
        )
        identity = self._identity_for(combination, profile)
        async with self.budgets.joint.permit(
            SitePurpose.PROBE,
            site_key=resolved.runtime_key,
            identity_id=identity.identity_id,
            site_max_concurrency=resolved.access.max_concurrency,
            site_min_interval_ms=resolved.access.min_interval_ms,
            identity_max_concurrency=identity.access.max_concurrency,
            identity_min_interval_ms=identity.access.min_interval_ms,
            timeout_seconds=30,
        ) as queue_wait_ms:
            async with self.profile_use.business(profile_id) as current_profile:
                response, category, reason, network_ms = await self._attempt(
                    request, resolved, combination, current_profile, egress
                )
                if response.challenge_token is not None:
                    identity = self._identity_for(combination, current_profile)
                    await self.profile_use.begin_challenge(
                        profile_id,
                        token=response.challenge_token,
                        site_id=resolved.site_id,
                        identity_id=identity.identity_id,
                        combination_id=combination.combination_id,
                        article_url=url,
                        target_id=response.challenge_target_id,
                    )
        runtime = self.repository.get_runtime(resolved.runtime_key)
        attempt = AccessAttempt(
            combination_id=combination.combination_id,
            generation=runtime.generation,
            transport=AccessMode.BROWSER,
            status_code=response.status_code or None,
            failure_category=category,
            reason_code=reason,
            queue_wait_ms=queue_wait_ms,
            network_ms=network_ms,
            exit_ip=egress.observed_ip,
        )
        if category in RISK_FAILURES:
            await self.health.mark_failure(
                resolved.runtime_key,
                combination.combination_id,
                category,
                reason,
                assigned_generation=runtime.generation,
                retry_after_seconds=response.retry_after_seconds,
                transport=AccessMode.BROWSER,
            )
        elif category is None and 200 <= response.status_code < 300:
            await self.health.mark_success(
                resolved.runtime_key,
                combination.combination_id,
                assigned_generation=runtime.generation,
                transport=AccessMode.BROWSER,
            )
        self._event(request, resolved, combination, "PROFILE_PROBE", attempt)
        if response.challenge_token is not None:
            await self.profile_use.finish_challenge(profile_id, response.challenge_token)
        if category is None and 200 <= response.status_code < 300:
            disposition = AccessDisposition.SUCCESS
        elif category in {FailureCategory.AUTH_REQUIRED, FailureCategory.ENTITLEMENT_MISSING}:
            disposition = AccessDisposition.AUTH_REQUIRED
        elif category in RISK_FAILURES:
            disposition = AccessDisposition.ACCESS_EXHAUSTED
        elif category in {
            FailureCategory.CONTENT_ERROR,
            FailureCategory.EXTRACTION_ERROR,
            FailureCategory.REGION_RESTRICTED,
        }:
            disposition = AccessDisposition.CONTENT_ERROR
        else:
            disposition = AccessDisposition.SERVICE_UNAVAILABLE
        return self._result_from_response(
            request,
            resolved,
            combination,
            profile,
            egress,
            response,
            disposition,
            category,
            reason,
            [attempt],
            started,
            generation=self.repository.get_runtime(resolved.runtime_key).generation,
        )

    async def certify_profile_probe(
        self, profile_id: str, article_url: str, result: AccessResult
    ) -> None:
        """Record site auth after the admin probe proves a full titled article."""
        resolved = self.resolve(article_url)
        if (
            result.disposition is not AccessDisposition.SUCCESS
            or result.site_id != resolved.site_id
            or result.profile_id != profile_id
            or not result.identity_id
        ):
            raise ValueError("probe result does not match the requested site profile")
        identity = self.repository.get_identity(result.identity_id)
        if identity is None or identity.profile_id != profile_id:
            raise ValueError("probe identity binding is unavailable")
        async with self.profile_use.business(profile_id):
            runtime = self.repository.get_identity_runtime(identity.identity_id)
            if (
                result.runtime_instance_id
                and runtime.instance_id
                and result.runtime_instance_id != runtime.instance_id
            ):
                raise RuntimeError("browser instance changed after article probe")
            self.repository.save_site_identity_auth(
                SiteIdentityAuth(
                    site_id=resolved.site_id,
                    identity_id=identity.identity_id,
                    auth_state=AuthState.VALID,
                    verified_at=utc_now(),
                    verification_url=article_url,
                    reason_code="full_article_probe",
                    observed_session_revision=runtime.session_revision,
                )
            )
            self.repository.append_event(
                AccessEvent(
                    operation_id=result.operation_id,
                    site_id=resolved.site_id,
                    combination_id=result.combination_id,
                    category="PROFILE_VERIFIED",
                    payload={
                        "profile_id": profile_id,
                        "identity_id": identity.identity_id,
                        "state": AuthState.VALID.value,
                        "reason": "full_article_probe",
                    },
                )
            )

    async def snapshot_profile(
        self, profile_id: str, *, browser_version: str, batch_id: str | None = None
    ) -> dict[str, str]:
        session_id = await self.profile_use.begin_maintenance(profile_id)
        try:
            profile = await self.profile_use.assert_session(profile_id, session_id)
            identity = next(
                (
                    value
                    for _, value in self.repository.list_active_identities()
                    if value.profile_id == profile_id
                ),
                None,
            )
            stopped = (
                await self.runtime.browser_runtimes.stop_identity(
                    identity, reason="profile_snapshot"
                )
                if identity
                else await self.runtime.browser_pool.close_profile_if_idle(
                    profile_id, reason="profile_snapshot"
                )
            )
            if not stopped:
                raise ProfileUnavailableError("profile_is_busy")
            return await asyncio.to_thread(
                self.profile_storage.create,
                profile,
                browser_version=browser_version,
                batch_id=batch_id,
            )
        finally:
            await self.profile_use.end_maintenance(profile_id, session_id)

    async def restore_profile(self, profile_id: str, snapshot_id: str) -> dict[str, str]:
        session_id = await self.profile_use.begin_maintenance(profile_id)
        try:
            profile = await self.profile_use.assert_session(profile_id, session_id)
            identity = next(
                (
                    value
                    for _, value in self.repository.list_active_identities()
                    if value.profile_id == profile_id
                ),
                None,
            )
            stopped = (
                await self.runtime.browser_runtimes.stop_identity(
                    identity, reason="profile_restore"
                )
                if identity
                else await self.runtime.browser_pool.close_profile_if_idle(
                    profile_id, reason="profile_restore"
                )
            )
            if not stopped:
                raise ProfileUnavailableError("profile_is_busy")
            return await asyncio.to_thread(self.profile_storage.restore, profile, snapshot_id)
        finally:
            await self.profile_use.end_maintenance(profile_id, session_id)

    async def open_profile_login(self, profile_id: str) -> dict[str, str]:
        profile = self.repository.get_profile(profile_id)
        if profile is None:
            raise KeyError(profile_id)
        identities = [
            value
            for _, value in self.repository.list_active_identities()
            if value.profile_id == profile_id
        ]
        if len(identities) > 1:
            raise ValueError("shared profile requires explicit site and identity")
        identity = (
            identities[0]
            if identities
            else self._identity_for(
                AccessCombination(
                    id=profile_id,
                    profile_id=profile.profile_id,
                    egress_id=profile.bound_egress_id,
                ),
                profile,
            )
        )
        return await self.open_identity_login(profile.site_id, identity.identity_id)

    async def open_identity_login(
        self,
        site_id: str,
        identity_id: str,
        *,
        control_token: str | None = None,
    ) -> dict[str, str]:
        identity = self.repository.get_identity(identity_id)
        if identity is None or not identity.enabled:
            raise KeyError(identity_id)
        profile = self.repository.get_profile(identity.profile_id)
        egress = self.repository.get_egress(identity.egress_id)
        if profile is None or egress is None or not egress.enabled:
            raise RuntimeError("bound identity resources are unavailable")
        spec = self.repository.get_strategy(site_id)
        if spec is None or not self._identity_belongs_to_site(site_id, identity_id):
            raise ValueError("identity is not referenced by the selected site")
        url = (spec.auth.maintenance_url or spec.auth.login_url) or profile.login_url
        if not url:
            from .governance_repository import GovernanceRepository

            scope = next(
                (
                    s
                    for s in GovernanceRepository(self.repository).list_scopes()
                    if identity_id in (s.primary_identity_id, s.fallback_identity_id)
                ),
                None,
            )
            url = scope.verification_plan.get("crawler", {}).get("url") if scope else None
        if not url:
            raise ValueError("profile has no maintenance URL")
        await self.profile_use.prepare_login(profile.profile_id, identity_id)
        session_id = await self.profile_use.begin_maintenance(
            profile.profile_id, session_id=control_token
        )
        external = self.runtime.browser_runtimes.external
        if external is not None:
            external.rotation_paused = True
        try:
            current = await self.profile_use.assert_session(profile.profile_id, session_id)
            if "identity" not in inspect.signature(self.runtime.open_login).parameters:
                return await self.runtime.open_login(current, egress, url, token=session_id)
            return await self.runtime.open_login(
                current,
                egress,
                url,
                token=session_id,
                identity=identity,
                site_id=site_id,
            )
        except BaseException:
            from .governance_repository import GovernanceRepository

            retained = any(
                s.control_token == session_id and s.target_id
                for s in GovernanceRepository(self.repository).list_sessions()
            )
            if retained:
                await self.profile_use.release_viewer(profile.profile_id, session_id)
            else:
                await self.profile_use.end_maintenance(profile.profile_id, session_id)
            raise

    async def inspect_profile_login(self, token: str) -> dict[str, str]:
        return await self.runtime.inspect_login(token)

    async def verify_scope_session(self, session, *, article_url=None, stage=None):
        from .governance_repository import GovernanceRepository
        from .verification import (
            VerificationReport,
            body_verification_url,
            validate_capture,
            validate_crawler_capture,
            verification_failure_reason,
        )

        store = GovernanceRepository(self.repository)
        scope = store.maintenance_scope(session.scope_id)
        if scope is None:
            raise ValueError("maintenance scope is no longer configured")
        # Independent stages leave a cleanup reserve; losing the historical page
        # must not prevent an explicit same-identity business verification.
        deadline = time.monotonic() + 90
        if stage:
            stage("CHECKING_CHALLENGE")
        report = VerificationReport()
        try:
            async with asyncio.timeout(10):
                original = await self.runtime.inspect_challenge(session.control_token)
        except Exception as exc:
            original = RuntimeResponse(
                0,
                scope.verification_plan.get("crawler", {}).get("url")
                or self.repository.get_strategy(scope.owner_site_id).auth.login_url
                or "https://" + self.repository.get_strategy(scope.owner_site_id).domains[0].host,
                {},
                reason=verification_failure_reason(exc),
            )
            report.reason = "original_page_unavailable"
            report.original_page_kind = "unknown"
        original_inspection = inspect_html(
            original.body, original.final_url, "", generic_extraction=False
        )
        report.presence_state = (
            "PRESENT"
            if original.body
            else (
                "LOST"
                if original.reason == "original_challenge_target_lost"
                else "PRESENT_BUT_UNREADABLE"
            )
        )
        report.presence_checked_at = utc_now().isoformat()
        report.original_page_kind = original_inspection.page_kind
        report.verification_kind = (original.assessment or {}).get("kind", "UNKNOWN")
        if (original.assessment or {}).get("kind") in {
            "AUTO_CHECK",
            "INTERACTIVE",
            "ACCESS_BLOCK",
            "UNKNOWN",
        } or (
            not original.assessment and original_inspection.access_reason == "challenge_required"
        ):
            report.challenge = "BLOCKED"
            report.reason = "challenge_required"
            from .challenge_detection import VerificationAssessment, assess

            assessment = (
                VerificationAssessment.model_validate(original.assessment)
                if original.assessment
                else assess(
                    original.body,
                    status=original.status_code,
                    headers=original.headers,
                    policy=self.repository.get_strategy(scope.owner_site_id).challenge,
                    blocked=True,
                )
            )
            report.verification_kind = assessment.kind
            if assessment.handling_mode == "AUTO":
                report.challenge, report.reason = "UNKNOWN", "automatic_verification_pending"
            if (
                session.kind == "LOGIN"
                and assessment.handling_mode == "HUMAN"
                and session.allocation_id
                and original.provenance
            ):
                from .governance_models import ChallengeTask

                task = store.handoff_challenge(
                    ChallengeTask(
                        scope_id=scope.scope_id,
                        identity_id=session.identity_id,
                        site_id=session.site_id,
                        purpose="LOGIN",
                        operation_id=f"login-challenge:{session.session_id}",
                        original_url=original.final_url,
                        original_target_id=session.target_id,
                        browser_instance_id=original.provenance.instance_id,
                        allocation_id=session.allocation_id,
                        reason="login_challenge_required",
                    ),
                    session=session,
                )
                report.challenge_task_id = task.task_id
                session = store.get_session(session.session_id)
                identity = self.repository.get_identity(session.identity_id)
                profile = self.repository.get_profile(identity.profile_id)
                await self.runtime.adopt_challenge(
                    profile,
                    self.repository.get_egress(identity.egress_id),
                    identity=identity,
                    site_id=session.site_id,
                    token=session.control_token,
                )
            if session.kind == "LOGIN":
                from .recovery import register_login_recovery

                register_login_recovery(self, scope, session, assessment)
                return store.commit_verification(
                    scope, session.identity_id, report, session=session
                )
        if original.challenge_cleared:
            report.challenge = "CLEARED"
        spec = self.repository.get_strategy(scope.owner_site_id)
        crawler = scope.verification_plan.get("crawler")
        triggering_task = store.current_challenge(session.identity_id)
        if (
            scope.source_id == "reuters_site_search"
            and session.kind == "CHALLENGE"
            and triggering_task
            and triggering_task.purpose == "CRAWLER"
        ):
            from urllib.parse import parse_qs

            parts = urlsplit(triggering_task.original_url)
            values = parse_qs(parts.query)
            query, offset = values.get("query", [None])[0], values.get("offset", ["0"])[0]
            if (
                parts.scheme == "https"
                and parts.hostname == "www.reuters.com"
                and parts.path == "/site-search/"
                and query
                and offset.isdigit()
            ):
                crawler = {
                    "url": triggering_task.original_url,
                    "recipe_ref": "builtin:reuters_search@1",
                    "recipe_parameters": {"query": query, "offset": int(offset)},
                }
        if (
            session.kind == "CHALLENGE"
            and triggering_task
            and triggering_task.purpose == "BODY"
            and not article_url
        ):
            crawler = None
        if (
            crawler
            and scope.source_id == "investing_ticker_news"
            and session.kind == "CHALLENGE"
            and triggering_task
            and session.task_id == triggering_task.task_id
            and triggering_task.purpose == "CRAWLER"
            and original.final_url == triggering_task.original_url
        ):
            from .investing_document import listing_ticker

            ticker = listing_ticker(original.body, original.final_url)
            if ticker:
                # Prove the affected ticker (e.g. RKLB/ONDS), not always INTC.
                crawler = {
                    **crawler,
                    "url": original.final_url,
                    "recipe_parameters": {**crawler.get("recipe_parameters", {}), "ticker": ticker},
                }
        article_candidates = []
        if crawler:
            if stage:
                stage("CHECKING_CRAWLER")
            request = AccessRequest(
                **crawler,
                operation_id=f"verify-crawler:{uuid4().hex}",
                purpose=SitePurpose.CRAWLER,
                mode=AccessMode.BROWSER,
                remaining_budget_ms=max(1, min(75000, int((deadline - time.monotonic()) * 1000))),
            )
            try:
                from .recovery import recovery_listing_url_matches

                if (
                    (
                        original.final_url == request.url
                        or (
                            scope.source_id == "reuters_site_search"
                            and recovery_listing_url_matches(scope, request, original.final_url)
                        )
                    )
                    and original.provenance
                    and report.challenge != "BLOCKED"
                ):
                    # The live, exact human-owned page can itself prove the
                    # configured collector contract. Do not open its duplicate
                    # just to read the same listing again under a short budget.
                    capture = original
                else:
                    identity = self.repository.get_identity(session.identity_id)
                    capture = await self.runtime.probe_recovery(
                        request, identity, self.repository.get_egress(identity.egress_id)
                    )
                ready = validate_crawler_capture(scope.source_id, capture, request)
            except Exception as exc:
                ready = False
                report.crawler_reason = verification_failure_reason(exc)
            else:
                report.crawler_reason = (
                    "listing_contract_verified"
                    if ready
                    else inspect_html(
                        capture.body, capture.final_url, "", generic_extraction=False
                    ).access_reason
                    or capture.reason
                    or "listing_contract_not_satisfied"
                )
            report.crawler = "READY" if ready else "CONTENT_ERROR"
            if ready:
                report.runtime_instance_id = (
                    capture.provenance.instance_id if capture.provenance else None
                )
                report.challenge = "CLEARED"
                from urllib.parse import urljoin

                from .verification import crawler_capture_items

                for row in crawler_capture_items(scope.source_id, capture, request) or []:
                    raw = row.get("url") if isinstance(row, dict) else row.url
                    if raw:
                        candidate = urljoin(request.url, str(raw))
                        if self.resolve(candidate).site_id == scope.owner_site_id:
                            article_candidates.append(candidate)
        triggering = store.current_challenge(session.identity_id)
        trigger_url = (
            triggering.original_url
            if triggering
            and triggering.purpose == "BODY"
            and original_inspection.page_kind not in {"listing", "quote", "login"}
            else None
        )
        if spec.auth.requirement != "required":
            # Prefer ordinary public news for a public-access maintenance check;
            # a recommendation for Investing Pro must not certify/poison this path.
            article_candidates.sort(key=lambda candidate: "/news/pro/" in candidate)
        url = article_url or body_verification_url(
            scope,
            trigger_url,
            spec.auth.verification_url,
            scope.verification_plan.get("body_url"),
            *article_candidates,
            excluded_urls=(spec.auth.login_url,),
        )
        check_body = not (
            session.kind == "CHALLENGE"
            and triggering_task
            and triggering_task.purpose == "CRAWLER"
            and not article_url
        )
        if url and check_body and time.monotonic() < deadline:
            if self.resolve(url).site_id != scope.owner_site_id:
                raise ValueError("verification article belongs to another publisher")
            if stage:
                stage("CHECKING_BODY")
            from .governance_repository import safe_display_url

            report.verification_url = safe_display_url(url)
            try:
                identity = self.repository.get_identity(session.identity_id)
                capture = await self.runtime.probe_recovery(
                    AccessRequest(
                        operation_id=f"verify-body:{uuid4().hex}",
                        url=url,
                        purpose=SitePurpose.BODY,
                        mode=AccessMode.BROWSER,
                        remaining_budget_ms=max(
                            1, min(75000, int((deadline - time.monotonic()) * 1000))
                        ),
                    ),
                    identity,
                    self.repository.get_egress(identity.egress_id),
                )
                if capture.status_code in {404, 410} and not article_url:
                    replacement = next((u for u in article_candidates if u != url), None)
                    if replacement and time.monotonic() < deadline:
                        url = replacement
                        capture = await self.runtime.probe_recovery(
                            AccessRequest(
                                operation_id=f"verify-body:{uuid4().hex}",
                                url=url,
                                purpose=SitePurpose.BODY,
                                mode=AccessMode.BROWSER,
                                remaining_budget_ms=max(
                                    1, min(75000, int((deadline - time.monotonic()) * 1000))
                                ),
                            ),
                            identity,
                            self.repository.get_egress(identity.egress_id),
                        )
                body = validate_capture(
                    capture,
                    self.resolve(url),
                    observed_document=capture.reason == "observed_document_after_verification",
                    expected_subscription=(
                        spec.auth.verification_kind == "subscription_article"
                        and url == spec.auth.verification_url
                    ),
                )
                report.body, report.authentication, report.reason = (
                    body.body,
                    body.authentication,
                    body.reason,
                )
                report.body_reason = body.reason
                report.verification_url = safe_display_url(capture.final_url)
                report.runtime_instance_id = (
                    capture.provenance.instance_id if capture.provenance else None
                )
                if body.challenge == "CLEARED" or capture.challenge_cleared:
                    report.challenge = "CLEARED"
            except Exception as exc:
                report.body = "CONTENT_ERROR"
                report.body_reason = verification_failure_reason(exc)
                report.reason = "body_check_failed"
        elif spec.auth.requirement == "none":
            report.authentication = "NOT_REQUIRED"
        if report.challenge == "CLEARED" and report.reason == "not_tested":
            report.reason = "challenge_cleared"
        if stage:
            stage("APPLYING_HEALTH")
        report = store.commit_verification(
            scope,
            session.identity_id,
            report,
            session=session,
            credential_change=(
                session.kind == "LOGIN" or bool(triggering and triggering.purpose == "LOGIN")
            )
            and report.authentication == "VALID",
        )
        current = store.get_recovery(session.identity_id)
        if current:
            from .datadome_solver import mark_solver_outcome

            if report.crawler == "READY" or report.body in {"FULL", "SHORT_FULL"}:
                mark_solver_outcome(
                    self.repository,
                    session.identity_id,
                    "business_verified_at",
                    recovery_id=current.recovery_id,
                    purpose="CRAWLER" if report.crawler == "READY" else "BODY",
                )
            jobs = self.recovery.datadome_solver.jobs(current.recovery_id)
            if jobs:
                report.solver_state = jobs[-1].get("verification_state", "INDETERMINATE")
                cost = jobs[-1].get("provider_cost", jobs[-1].get("cost"))
                report.solver_cost = str(cost) if cost is not None else None
        return report

    async def recover_identity_login(
        self,
        site_id: str,
        identity_id: str,
        login_token: str,
        *,
        target_id: str | None = None,
    ) -> dict[str, str]:
        identity = self.repository.get_identity(identity_id)
        if identity is None:
            raise KeyError(identity_id)
        egress = self.repository.get_egress(identity.egress_id)
        if egress is None or not egress.enabled:
            raise RuntimeError("bound egress is unavailable")
        stored_profile = self.repository.get_profile(identity.profile_id)
        from .governance_repository import GovernanceRepository

        store = GovernanceRepository(self.repository)
        session = next((s for s in store.list_sessions() if s.control_token == login_token), None)
        if session:
            if session.identity_id != identity_id or session.site_id != site_id:
                raise ValueError("maintenance session binding mismatch")
            if target_id and target_id != session.target_id:
                raise ValueError("maintenance target binding mismatch")
            target_id = session.target_id
        elif stored_profile is not None and stored_profile.challenge_url is not None:
            if stored_profile.maintenance_session_id != login_token:
                raise ValueError("invalid challenge session")
            if (
                stored_profile.challenge_site_id != site_id
                or stored_profile.challenge_identity_id != identity_id
            ):
                raise ValueError("challenge session binding mismatch")
        task = (
            store.current_challenge(identity_id)
            if session and session.kind == "CHALLENGE"
            else None
        )
        if (
            session
            and session.kind == "CHALLENGE"
            and (not task or task.task_id != session.task_id)
        ):
            raise ValueError("Challenge task is no longer current; refresh maintenance")
        profile = await self.profile_use.recover_maintenance(identity.profile_id, login_token)
        if session:
            profile = profile.model_copy(
                update={
                    "challenge_url": task.original_url if task else None,
                    "challenge_site_id": task.site_id if task else None,
                    "challenge_identity_id": task.identity_id if task else None,
                    "challenge_target_id": session.target_id,
                }
            )
            self.repository.save_profile(profile)
        if target_id:
            profile = profile.model_copy(update={"challenge_target_id": target_id})
        external = self.runtime.browser_runtimes.external
        if external is not None:
            external.rotation_paused = True
        try:
            return await self.runtime.recover_login(
                profile,
                egress,
                identity=identity,
                site_id=site_id,
                token=login_token,
            )
        except Exception:
            # A failed controller reconnect must not release the human-owned
            # challenge page or reopen this identity for business requests.
            await self.profile_use.release_viewer(identity.profile_id, login_token)
            raise

    async def close_profile_login(self, token: str) -> None:
        challenge_profile = next(
            (
                profile
                for profile in self.repository.list_profiles()
                if profile.maintenance_session_id == token and profile.challenge_url
            ),
            None,
        )
        if challenge_profile is not None and not challenge_profile.challenge_verified:
            raise RuntimeError("challenge article has not been verified")
        try:
            profile_id = await self.runtime.close_login(token)
        except KeyError:
            profile = next(
                (
                    item
                    for item in self.repository.list_profiles()
                    if item.maintenance_session_id == token
                ),
                None,
            )
            if profile is None:
                raise
            identity = next(
                (
                    value
                    for _, value in self.repository.list_active_identities()
                    if value.profile_id == profile.profile_id
                    and (
                        challenge_profile is None
                        or value.identity_id == challenge_profile.challenge_identity_id
                    )
                ),
                None,
            )
            if identity is not None:
                egress = self.repository.get_egress(identity.egress_id)
                if egress is not None:
                    if challenge_profile is not None:
                        await self.runtime.recover_login(
                            profile,
                            egress,
                            identity=identity,
                            site_id=challenge_profile.challenge_site_id or profile.site_id,
                            token=token,
                        )
                        await self.runtime.close_login(token)
                    else:
                        raise RuntimeError("exact_maintenance_target_reference_required") from None
            profile_id = profile.profile_id
        await self.profile_use.end_maintenance(profile_id, token)
        if challenge_profile is not None and challenge_profile.challenge_combination_id:
            resolved = self.resolve(challenge_profile.challenge_url)
            await self.health.clear_manual_attention(
                resolved.runtime_key, challenge_profile.challenge_combination_id
            )

    async def verify_profile(
        self, profile_id: str, article_url: str, login_token: str
    ) -> tuple[BrowserProfile, str]:
        await self.profile_use.assert_session(profile_id, login_token)
        resolved = self.resolve(article_url)
        identities = [
            value
            for _, value in self.repository.list_active_identities()
            if value.profile_id == profile_id
            and any(
                item.identity_id == value.identity_id
                or (
                    item.identity_id is None
                    and item.profile_id == profile_id
                    and item.egress_id == value.egress_id
                )
                for item in resolved.access.combinations
            )
        ]
        if len(identities) != 1:
            raise ValueError("verification requires an unambiguous site identity")
        return await self.verify_identity(
            resolved.site_id, identities[0].identity_id, article_url, login_token
        )

    async def verify_identity(
        self, site_id: str, identity_id: str, article_url: str, login_token: str
    ) -> tuple[BrowserProfile, str]:
        identity = self.repository.get_identity(identity_id)
        if identity is None:
            raise KeyError(identity_id)
        profile = await self.profile_use.assert_session(identity.profile_id, login_token)
        if profile.challenge_url is not None:
            if (
                profile.challenge_site_id != site_id
                or profile.challenge_identity_id != identity_id
                or profile.challenge_url != article_url
            ):
                raise ValueError("challenge verification must use the triggering article")
        egress = self.repository.get_egress(identity.egress_id)
        if egress is None or not egress.enabled:
            raise RuntimeError("bound egress is unavailable")
        resolved = self.resolve(article_url)
        if resolved.site_id != site_id:
            raise ValueError("verification article does not belong to the selected site")
        combination = next(
            (
                self._materialize_combination(item)
                for item in resolved.access.combinations
                if item.identity_id == identity_id
                or (
                    item.identity_id is None
                    and item.profile_id == identity.profile_id
                    and item.egress_id == identity.egress_id
                )
            ),
            None,
        )
        if combination is None:
            if not self._identity_belongs_to_site(site_id, identity_id):
                raise ValueError("profile is not referenced by the active site strategy")
            combination = self._materialize_combination(
                AccessCombination(id=identity_id, identity_id=identity_id)
            )
        operation_id = f"identity-verify:{site_id}:{identity_id}:{int(time.time())}"
        response = await self.runtime.verify_login(login_token, article_url)
        category, reason = classify_response(response)
        inspection = inspect_html(
            response.body,
            response.final_url,
            None,
            strategy_ref=resolved.body.ref,
            strategy_parameters=resolved.body.parameters,
        )
        verification_kind = resolved.auth.verification_kind
        if (
            verification_kind == "public_access"
            and category is None
            and 200 <= response.status_code < 300
            and (
                inspection.page_kind in {"article", "listing", "quote"}
                or bool(response.recipe_result)
            )
        ):
            state = profile.auth_state
            reason = "public_access_ready"
        elif (
            category is FailureCategory.ENTITLEMENT_MISSING
            or inspection.access_reason == "subscription_required"
        ):
            state = AuthState.ENTITLEMENT_MISSING
        elif (
            category is FailureCategory.AUTH_REQUIRED
            or inspection.access_reason == "login_required"
        ):
            state = AuthState.REAUTH_REQUIRED
        elif (
            category is None
            and 200 <= response.status_code < 300
            and inspection.page_kind == "article"
        ):
            state = AuthState.VALID
        else:
            state = AuthState.UNKNOWN
        updated = profile.model_copy(
            update={
                "auth_state": state,
                "session_revision": profile.session_revision,
                "updated_at": utc_now(),
            }
        )
        self.repository.save_profile(updated)
        if profile.challenge_url is not None and (
            state is AuthState.VALID or reason == "public_access_ready"
        ):
            updated = updated.model_copy(update={"challenge_verified": True})
            self.repository.save_profile(updated)
        identity_runtime = self.repository.get_identity_runtime(identity_id)
        next_session_revision = identity_runtime.session_revision
        self.repository.save_identity_runtime(
            identity_runtime.model_copy(
                update={
                    "session_revision": next_session_revision,
                    "generation": identity_runtime.generation + 1,
                    "updated_at": utc_now(),
                }
            ),
            expected_generation=identity_runtime.generation,
        )
        self.repository.save_site_identity_auth(
            SiteIdentityAuth(
                site_id=resolved.site_id,
                identity_id=identity_id,
                auth_state=state,
                verified_at=utc_now(),
                verification_url=article_url,
                reason_code=reason,
                observed_session_revision=next_session_revision,
            )
        )
        self.repository.append_event(
            AccessEvent(
                operation_id=operation_id,
                site_id=resolved.site_id,
                combination_id=combination.combination_id,
                category="PROFILE_VERIFIED",
                payload={
                    "profile_id": profile.profile_id,
                    "identity_id": identity_id,
                    "state": state.value,
                    "reason": reason,
                    "verification_kind": verification_kind,
                },
            )
        )
        if reason == "public_access_ready" or state is AuthState.VALID:
            # Authentication and access-health are separate records. A real
            # successful human verification must release the browser circuit,
            # otherwise manual_attention would suppress all subsequent traffic.
            access_state = self.repository.get_runtime(resolved.runtime_key)
            await self.health.mark_success(
                resolved.runtime_key,
                combination.combination_id,
                assigned_generation=access_state.generation,
                transport=AccessMode.BROWSER,
            )
            if reason == "public_access_ready" and article_url == (
                self.repository.get_strategy(resolved.site_id).auth.maintenance_url
            ):
                await self.health.clear_purpose_denial(
                    resolved.runtime_key, combination.combination_id, SitePurpose.CRAWLER
                )
        return updated, reason

    def _event(
        self,
        request: AccessRequest,
        resolved: ResolvedSite,
        combination: AccessCombination,
        category: str,
        attempt: AccessAttempt,
    ) -> None:
        self.repository.append_event(
            AccessEvent(
                operation_id=request.operation_id,
                site_id=resolved.site_id,
                combination_id=combination.combination_id,
                category=category,
                payload=attempt.model_dump(mode="json"),
            )
        )

    async def _put_cache(self, result: AccessResult, *, cache_key: str, ttl=60) -> None:
        size = len(result.body.encode("utf-8")) + len(str(result.recipe_result).encode("utf-8"))
        if size > 32 * 1024 * 1024:
            return
        async with self._cache_lock:
            prior = self._cache.pop(cache_key, None)
            if prior:
                self._cache_bytes -= prior[1]
            self._cache[cache_key] = (time.monotonic() + ttl, size, result)
            self._cache_bytes += size
            while self._cache_bytes > 32 * 1024 * 1024 and self._cache:
                _, (_, removed_size, _) = self._cache.popitem(last=False)
                self._cache_bytes -= removed_size

    def _expire_cache(self) -> None:
        now = time.monotonic()
        for key in [key for key, value in self._cache.items() if value[0] <= now]:
            _, size, _ = self._cache.pop(key)
            self._cache_bytes -= size

    @staticmethod
    def _result(
        request: AccessRequest,
        resolved: ResolvedSite,
        disposition: AccessDisposition,
        *,
        category: FailureCategory | None = None,
        reason: str | None = None,
        attempts: list[AccessAttempt] | None = None,
        retry_not_before: datetime | None = None,
        combination_skips: list[dict[str, object]] | None = None,
        generation: int = 0,
        started: float,
    ) -> AccessResult:
        return AccessResult(
            request_id=request.request_id,
            operation_id=request.operation_id,
            disposition=disposition,
            failure_category=category,
            reason_code=reason,
            retry_not_before=retry_not_before,
            combination_skips=combination_skips or [],
            site_id=resolved.site_id,
            runtime_key=resolved.runtime_key,
            strategy_revision=resolved.strategy_revision,
            body_strategy_ref=resolved.body.ref,
            generation=generation,
            effective_scope_id=resolved.match_evidence.get("scope_id"),
            route_generation=generation,
            attempts=attempts or [],
            network_ms=sum(item.network_ms for item in (attempts or [])),
            queue_wait_ms=sum(item.queue_wait_ms for item in (attempts or [])),
        )

    @staticmethod
    def _result_from_response(
        request: AccessRequest,
        resolved: ResolvedSite,
        combination: AccessCombination,
        profile: BrowserProfile,
        egress: ProxyEgress,
        response: RuntimeResponse,
        disposition: AccessDisposition,
        category: FailureCategory | None,
        reason: str | None,
        attempts: list[AccessAttempt],
        started: float,
        generation: int | None = None,
    ) -> AccessResult:
        result_generation = (
            generation if generation is not None else (attempts[-1].generation if attempts else 0)
        )
        return AccessResult(
            request_id=request.request_id,
            operation_id=request.operation_id,
            disposition=disposition,
            status_code=response.status_code or None,
            final_url=response.final_url,
            headers=response.headers,
            body=response.body,
            recipe_result=response.recipe_result,
            failure_category=category,
            reason_code=reason,
            redirect_url=response.redirect_url,
            site_id=resolved.site_id,
            runtime_key=resolved.runtime_key,
            strategy_revision=resolved.strategy_revision,
            body_strategy_ref=resolved.body.ref,
            combination_id=combination.combination_id,
            generation=result_generation,
            effective_scope_id=resolved.match_evidence.get("scope_id"),
            route_generation=result_generation,
            challenge_task_id=response.challenge_token,
            profile_id=profile.profile_id,
            egress_id=egress.egress_id,
            identity_id=(
                response.provenance.identity_id
                if response.provenance
                else combination.identity_id or profile.profile_id
            ),
            runtime_kind=(response.provenance.runtime_kind if response.provenance else None),
            identity_revision=(
                response.provenance.identity_revision if response.provenance else None
            ),
            runtime_instance_id=(response.provenance.instance_id if response.provenance else None),
            runtime_generation=(response.provenance.generation if response.provenance else None),
            exit_ip_observation=egress.observed_ip,
            attempts=attempts,
            queue_wait_ms=sum(item.queue_wait_ms for item in attempts),
            network_ms=sum(item.network_ms for item in attempts),
            stage_timings={key: sum(a.stage_timings.get(key, 0) for a in attempts) for key in {k for a in attempts for k in a.stage_timings}},
        )


def classify_response(response: RuntimeResponse) -> tuple[FailureCategory | None, str]:
    if response.recovery_id:
        return (
            FailureCategory.ACCESS_BLOCK
            if (response.assessment or {}).get("kind") == "ACCESS_BLOCK"
            else FailureCategory.ACCESS_CHALLENGE
        ), "verification_document_retained"
    if response.reason == "browser_content_not_ready":
        return FailureCategory.EXTRACTION_ERROR, response.reason
    if response.reason == "response_too_large":
        return FailureCategory.CONTENT_ERROR, "response_too_large"
    status = response.status_code
    if status == 429:
        return FailureCategory.ACCESS_RATE_LIMIT, "http_429"
    if status in {404, 410}:
        return FailureCategory.CONTENT_ERROR, f"http_{status}"
    if status == 451:
        return FailureCategory.REGION_RESTRICTED, "http_451"
    if status in {401, 403, 412}:
        inspection = inspect_html(response.body, response.final_url, "")
        if inspection.access_reason == "challenge_required":
            return FailureCategory.ACCESS_CHALLENGE, "challenge_required"
        if inspection.access_reason == "login_required":
            return FailureCategory.AUTH_REQUIRED, "login_required"
        if inspection.access_reason == "subscription_required":
            return FailureCategory.ENTITLEMENT_MISSING, "subscription_required"
        if status == 412:
            return FailureCategory.UNKNOWN, "http_412_unclassified"
        return FailureCategory.AUTH_OR_ACCESS_UNKNOWN, f"http_{status}"
    if status in {408, 425} or status >= 500:
        return FailureCategory.TRANSIENT_TRANSPORT, f"http_{status}"
    if status and not (200 <= status < 300) and not (300 <= status < 400):
        return FailureCategory.UNKNOWN, f"http_{status}"
    body_inspection = inspect_html(response.body, response.final_url, "") if response.body else None
    if body_inspection is not None:
        if body_inspection.access_reason == "challenge_required":
            return FailureCategory.ACCESS_CHALLENGE, "challenge_required"
        if body_inspection.access_reason == "login_required":
            return FailureCategory.AUTH_REQUIRED, "login_required"
        if body_inspection.access_reason == "subscription_required":
            return FailureCategory.ENTITLEMENT_MISSING, "subscription_required"
    return None, "ok"


def classify_exception(exc: Exception) -> tuple[FailureCategory, str]:
    if getattr(exc, "failure_category", None):
        return FailureCategory(exc.failure_category), exc.reason_code
    status = int(getattr(exc, "status_code", 0) or 0)
    if status:
        return classify_response(RuntimeResponse(status, "https://invalid.example/", {}, str(exc)))[
            0
        ] or FailureCategory.UNKNOWN, (
            f"http_{status}:phase={exc.diagnostics.get('phase')}:url={exc.diagnostics.get('url')}"
            if getattr(exc, "diagnostics", None)
            else f"http_{status}"
        )
    name = type(exc).__name__.casefold()
    message = str(exc).casefold()
    if re.fullmatch(r"[a-z][a-z0-9_]{0,127}", message) and message.startswith(
        ("original_", "exact_", "maintenance_", "controller_", "viewer_", "invalid_")
    ):
        return FailureCategory.RUNTIME_UNAVAILABLE, message
    if "exceeds frame" in message or "response exceeds frame budget" in message:
        return FailureCategory.CONTENT_ERROR, "ipc_response_too_large"
    if "no object with guid" in message:
        return FailureCategory.RUNTIME_UNAVAILABLE, "browser_handle_disposed"
    if message.startswith("identity_"):
        return FailureCategory.RUNTIME_UNAVAILABLE, message[:128]
    if "browsertype.connect_over_cdp" in message:
        return FailureCategory.RUNTIME_UNAVAILABLE, "browser_cdp_attach_failed"
    if any(
        marker in message
        for marker in (
            "profile_busy_unknown_singleton",
            "profile_busy_live_chrome_writer",
            "processsingleton",
            "profile appears to be in use",
            "writer lock is already held",
        )
    ):
        return FailureCategory.RUNTIME_UNAVAILABLE, "browser_profile_busy"
    net_error = re.search(r"(?:net::)?ERR_[A-Z_]+", str(exc))
    if net_error:
        return FailureCategory.TRANSIENT_TRANSPORT, net_error.group(0)
    if any(
        marker in message
        for marker in (
            "connection closed while reading from the driver",
            "target page, context or browser has been closed",
            "external_",
            "browser has been closed",
            "playwright connection closed",
        )
    ):
        return FailureCategory.RUNTIME_UNAVAILABLE, "browser_driver_unavailable"
    if any(marker in name or marker in message for marker in ("timeout", "connect", "dns", "tls")):
        return FailureCategory.TRANSIENT_TRANSPORT, type(exc).__name__
    if (
        "capacity" in message
        or "browser" in name
        or "playwright" in name
        or message.startswith(("external_", "chrome_", "supervisor_", "profile_busy"))
    ):
        return FailureCategory.RUNTIME_UNAVAILABLE, type(exc).__name__
    return FailureCategory.TRANSIENT_TRANSPORT, type(exc).__name__


__all__ = ["SiteStrategyService", "classify_exception", "classify_response"]
