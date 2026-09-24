"""Site Strategy registry validation and bounded access orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import os
import signal
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

logger = logging.getLogger(__name__)


class ProfileUnavailableError(RuntimeError):
    pass


class ProfileUseCoordinator:
    """Atomic in-process admission paired with persisted operational state."""

    def __init__(self, repository: SiteStrategyRepository) -> None:
        self.repository = repository
        self._condition = asyncio.Condition()
        self._active: dict[str, int] = {}
        self._maintenance_profile: str | None = None
        self._accepting = True

    @property
    def accepting(self) -> bool:
        return self._accepting

    async def recover(self) -> None:
        async with self._condition:
            self._accepting = True
            self._maintenance_profile = None
            external_profiles = {
                identity.profile_id
                for _, identity in self.repository.list_active_identities()
                if identity.runtime_kind is BrowserRuntimeKind.EXTERNAL_CHROME
            }
            for profile in self.repository.list_profiles():
                if (
                    profile.profile_id in external_profiles
                    and profile.operational_state is ProfileOperationalState.MAINTENANCE
                    and profile.maintenance_session_id
                    and self._maintenance_profile is None
                ):
                    self._maintenance_profile = profile.profile_id
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
    async def business(self, profile_id: str) -> AsyncIterator[BrowserProfile]:
        async with self._condition:
            profile = self.repository.get_profile(profile_id)
            if profile is None:
                raise ProfileUnavailableError("profile_not_found")
            if not self._accepting:
                raise ProfileUnavailableError("service_draining")
            if profile.operational_state is not ProfileOperationalState.AVAILABLE:
                raise ProfileUnavailableError("profile_in_maintenance")
            self._active[profile_id] = self._active.get(profile_id, 0) + 1
        try:
            yield profile
        finally:
            async with self._condition:
                remaining = self._active.get(profile_id, 1) - 1
                if remaining <= 0:
                    self._active.pop(profile_id, None)
                else:
                    self._active[profile_id] = remaining
                self._condition.notify_all()

    async def begin_maintenance(self, profile_id: str, *, timeout_seconds: float = 30) -> str:
        session_id = uuid4().hex
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

    async def recover_maintenance(self, profile_id: str, session_id: str) -> BrowserProfile:
        async with self._condition:
            if self._maintenance_profile not in {None, profile_id}:
                raise ProfileUnavailableError("another_profile_is_in_maintenance")
            if self._active.get(profile_id, 0):
                raise ProfileUnavailableError("profile_is_busy")
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
                or self._maintenance_profile != profile_id
            ):
                raise ProfileUnavailableError("invalid_maintenance_session")
            return profile

    async def end_maintenance(self, profile_id: str, session_id: str) -> None:
        async with self._condition:
            await self._release_locked(profile_id, session_id)

    async def _release_locked(self, profile_id: str, session_id: str) -> None:
        profile = self.repository.get_profile(profile_id)
        if profile is None or profile.maintenance_session_id != session_id:
            raise ProfileUnavailableError("invalid_maintenance_session")
        self.repository.save_profile(
            profile.model_copy(
                update={
                    "operational_state": ProfileOperationalState.AVAILABLE,
                    "operational_revision": profile.operational_revision + 1,
                    "maintenance_session_id": None,
                    "updated_at": utc_now(),
                }
            )
        )
        if self._maintenance_profile == profile_id:
            self._maintenance_profile = None
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
        browser_idle_seconds: float = 43_200,
        safety_path: str | Path | None = None,
        supervisor_socket: str | Path | None = None,
        controller_id: str = "site-access",
    ) -> None:
        self.repository = repository
        self.resolver = SiteResolver(repository)
        self.budgets = SiteBudgetManager()
        self.health = CombinationHealthManager(repository)
        self.profile_use = ProfileUseCoordinator(repository)
        self.profile_storage = ProfileSnapshotManager(profile_root, repository)
        self.runtime = SiteAccessRuntime(
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
        self._cache: OrderedDict[str, tuple[float, int, AccessResult]] = OrderedDict()
        self._cache_bytes = 0
        self._cache_lock = asyncio.Lock()
        self._maintenance_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Acquire the single-owner profile lock and start the browser driver."""
        await self.runtime.browser_runtimes.start()
        await self.profile_use.recover()
        await self._prewarm_identities()
        if self._maintenance_task is None:
            self._maintenance_task = asyncio.create_task(self._maintenance_loop())

    async def _prewarm_identities(self) -> None:
        for head, identity in self.repository.list_active_identities():
            if (
                not head.enabled
                or not identity.enabled
                or identity.runtime_kind is not BrowserRuntimeKind.EXTERNAL_CHROME
                or identity.lifecycle.residency is not BrowserResidency.ALWAYS_ON
            ):
                continue
            egress = self.repository.get_egress(identity.egress_id)
            if egress is None or not egress.enabled:
                continue
            try:
                provenance = await self.runtime.browser_runtimes.prewarm(identity, egress)
                if provenance is not None:
                    current = self.repository.get_identity_runtime(identity.identity_id)
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
        return self.runtime.browser_pool.driver_ready

    @property
    def accepting(self) -> bool:
        return self.profile_use.accepting

    async def close(self) -> None:
        await self.profile_use.begin_shutdown()
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
            try:
                await self._prewarm_identities()
                await self.runtime.browser_runtimes.close_idle()
                await self._run_due_probes()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("site access maintenance probe failed")
            await asyncio.sleep(60)

    async def _run_due_probes(self) -> None:
        now = utc_now()
        for egress in self.repository.list_egresses():
            if not egress.enabled or (
                egress.observed_at is not None and now - egress.observed_at < timedelta(minutes=30)
            ):
                continue
            await self.probe_egress(egress.egress_id)
        for head, spec in self.repository.list_active_strategies():
            if not head.enabled or spec.site_id == "generic" or not spec.access.probe_url:
                continue
            claimed = await self.health.claim_due_probe(spec.site_id, spec.access.combinations)
            if claimed is None:
                continue
            combination, assigned_generation = claimed
            await self._probe_combination(spec, combination, assigned_generation)

    async def probe_egress(self, egress_id: str) -> ProxyEgress:
        prior = self.repository.get_egress(egress_id)
        if prior is None:
            raise KeyError(egress_id)
        updated = await probe_egress(self.repository, egress_id)
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
        assert spec.access.probe_url is not None
        resolved = self.resolve(spec.access.probe_url, revision=spec.revision)
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
            url=spec.access.probe_url,
            mode=AccessMode.BROWSER,
            strategy_revision=spec.revision,
            remaining_budget_ms=15_000,
            max_response_bytes=2_000_000,
        )
        budget = self.budgets.get(
            resolved.runtime_key,
            max_concurrency=resolved.access.max_concurrency,
            min_interval_ms=resolved.access.min_interval_ms,
        )
        try:
            async with budget.permit(SitePurpose.PROBE, timeout_seconds=10):
                async with self.profile_use.business(profile.profile_id) as current_profile:
                    response, category, reason, _ = await self._attempt(
                        request, resolved, combination, current_profile, egress
                    )
        except (TimeoutError, ProfileUnavailableError):
            await self.health.mark_probe_uncertain(spec.site_id, combination.combination_id)
            return
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

    async def execute(self, request: AccessRequest) -> AccessResult:
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
                task = asyncio.create_task(self._execute_uncached(request))
                self._inflight[cache_key] = task
                created = True
        try:
            result = await asyncio.shield(task)
        finally:
            if task.done():
                async with self._cache_lock:
                    self._inflight.pop(cache_key, None)
        if task.done() and not task.cancelled() and task.exception() is None:
            await self._put_cache(result, cache_key=cache_key)
            if created:
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
                        },
                    )
                )
        return result

    def _cache_key(self, request: AccessRequest) -> str:
        resolved = self.resolve(request.url, revision=request.strategy_revision)
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
            }
        )
        return f"{request.request_id}:{fingerprint}"

    async def _execute_uncached(self, request: AccessRequest) -> AccessResult:
        started = time.monotonic()
        resolved = self.resolve(request.url, revision=request.strategy_revision)
        if not resolved.enabled:
            return self._result(
                request,
                resolved,
                AccessDisposition.SITE_DISABLED,
                reason="site_disabled",
                started=started,
            )
        combinations = self._purpose_combinations(resolved, request.purpose)
        _, candidates, next_retry = await self.health.candidates(
            resolved.runtime_key,
            combinations,
            excluded=set(request.excluded_combinations),
            transport=request.mode,
        )
        if not candidates:
            return self._result(
                request,
                resolved,
                AccessDisposition.ACCESS_EXHAUSTED,
                category=FailureCategory.ACCESS_RATE_LIMIT,
                reason="access_combinations_unavailable",
                retry_not_before=next_retry,
                started=started,
            )
        attempts: list[AccessAttempt] = []
        risk_failures = 0
        unavailable = 0
        auth_missing = 0
        for combination in candidates:
            materialized = self._materialize_combination(combination)
            if materialized is None:
                unavailable += 1
                continue
            combination = materialized
            remaining = request.remaining_budget_ms / 1000 - (time.monotonic() - started)
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
            assert combination.egress_id is not None
            egress = self.repository.get_egress(combination.egress_id)
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
                attempts.append(
                    AccessAttempt(
                        combination_id=combination.combination_id,
                        generation=self.repository.get_runtime(resolved.runtime_key).generation,
                        transport=request.mode,
                        failure_category=FailureCategory.EGRESS_UNAVAILABLE,
                        reason_code="egress_or_profile_unavailable",
                    )
                )
                continue
            if (
                self._auth_required(resolved, request.purpose)
                and self._auth_state(resolved, combination, profile) is not AuthState.VALID
            ):
                auth_missing += 1
                continue
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
                )
            )
            try:
                async with permit as queue_wait_ms:
                    current = self.repository.get_runtime(resolved.runtime_key)
                    async with self.profile_use.business(profile.profile_id) as current_profile:
                        if (
                            self._auth_required(resolved, request.purpose)
                            and self._auth_state(resolved, combination, current_profile)
                            is not AuthState.VALID
                        ):
                            auth_missing += 1
                            continue
                        profile = current_profile
                        async with asyncio.timeout(remaining):
                            response, category, reason, network_ms = await self._attempt(
                                request, resolved, combination, profile, egress
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
            except ProfileUnavailableError:
                unavailable += 1
                continue
            except TimeoutError:
                return self._result(
                    request,
                    resolved,
                    AccessDisposition.BUDGET_DEFERRED,
                    category=FailureCategory.BUDGET_DEFERRED,
                    reason="site_queue_timeout",
                    attempts=attempts,
                    started=started,
                )
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
                )
                self._event(request, resolved, combination, "COMBINATION_RISK_FAILURE", attempt)
                continue
            if category is FailureCategory.EGRESS_UNAVAILABLE:
                unavailable += 1
                continue
            if category in {
                FailureCategory.AUTH_REQUIRED,
                FailureCategory.ENTITLEMENT_MISSING,
            }:
                self._mark_identity_auth(resolved, combination, profile, category)
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
                FailureCategory.UNKNOWN,
            }:
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
                final_resolved.runtime_key != resolved.runtime_key
                and not self.resolver.supports_host(resolved.site_id, final_host)
            ):
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
            self._event(request, resolved, combination, "ACCESS_SUCCEEDED", attempt)
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
        refreshed, _, next_retry = await self.health.candidates(
            resolved.runtime_key,
            combinations,
            excluded=set(request.excluded_combinations),
            transport=request.mode,
        )
        if auth_missing and auth_missing == len(candidates):
            disposition = AccessDisposition.AUTH_REQUIRED
            category = FailureCategory.AUTH_REQUIRED
            reason = "no_authenticated_profile_available"
        elif risk_failures:
            disposition = AccessDisposition.ACCESS_EXHAUSTED
            category = FailureCategory.ACCESS_RATE_LIMIT
            reason = "risk_combinations_exhausted"
        else:
            disposition = AccessDisposition.SERVICE_UNAVAILABLE
            category = FailureCategory.EGRESS_UNAVAILABLE
            reason = "access_resources_unavailable"
        return self._result(
            request,
            resolved,
            disposition,
            category=category,
            reason=reason,
            attempts=attempts,
            retry_not_before=next_retry,
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
        for retry in range(2):
            started = time.monotonic()
            try:
                response = await self.runtime.execute(
                    request, resolved, combination, profile, egress
                )
                category, reason = classify_response(response)
            except Exception as exc:
                status = int(getattr(exc, "status_code", 0) or 0)
                response = RuntimeResponse(
                    status,
                    request.url,
                    {},
                    reason=type(exc).__name__,
                    retry_after_seconds=float(getattr(exc, "retry_after_seconds", 0) or 0),
                )
                category, reason = classify_exception(exc)
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
            elapsed = int((time.monotonic() - started) * 1000)
            last = response, category, reason, elapsed
            if category is not FailureCategory.TRANSIENT_TRANSPORT or retry == 1:
                break
        assert last is not None
        return last

    def _profile(
        self, resolved: ResolvedSite, combination: AccessCombination
    ) -> BrowserProfile | None:
        combination = self._materialize_combination(combination) or combination
        profile = self.repository.get_profile(combination.profile_id or "")
        if resolved.site_id != "generic":
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
        auth = self.repository.get_site_identity_auth(resolved.runtime_key, identity.identity_id)
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
        category: FailureCategory,
    ) -> None:
        state = (
            AuthState.ENTITLEMENT_MISSING
            if category is FailureCategory.ENTITLEMENT_MISSING
            else AuthState.REAUTH_REQUIRED
        )
        self.repository.save_profile(
            profile.model_copy(update={"auth_state": state, "updated_at": utc_now()})
        )
        identity = self._identity_for(combination, profile)
        runtime = self.repository.get_identity_runtime(identity.identity_id)
        self.repository.save_site_identity_auth(
            SiteIdentityAuth(
                site_id=resolved.runtime_key,
                identity_id=identity.identity_id,
                auth_state=state,
                reason_code=category.value,
                observed_session_revision=runtime.session_revision,
            )
        )

    def save_outcomes(self, values: list[BodyOutcome]) -> int:
        for value in values:
            self.repository.save_body_outcome(value)
        self.repository.prune_diagnostics()
        return len(values)

    async def probe_profile(self, profile_id: str, url: str) -> AccessResult:
        """Exercise an exact Profile+egress for rollout diagnosis, without auth mutation."""
        started = time.monotonic()
        profile = self.repository.get_profile(profile_id)
        if profile is None:
            raise KeyError(profile_id)
        resolved = self.resolve(url)
        if resolved.site_id != profile.site_id:
            raise ValueError("probe URL does not belong to the profile site")
        combination = next(
            (
                item
                for item in resolved.access.combinations
                if item.profile_id == profile_id and item.egress_id == profile.bound_egress_id
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
        budget = self.budgets.get(
            resolved.runtime_key,
            max_concurrency=resolved.access.max_concurrency,
            min_interval_ms=resolved.access.min_interval_ms,
        )
        async with budget.permit(SitePurpose.PROBE, timeout_seconds=30) as queue_wait_ms:
            async with self.profile_use.business(profile_id) as current_profile:
                response, category, reason, network_ms = await self._attempt(
                    request, resolved, combination, current_profile, egress
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

    async def snapshot_profile(self, profile_id: str, *, browser_version: str) -> dict[str, str]:
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
                self.profile_storage.create, profile, browser_version=browser_version
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

    async def open_identity_login(self, site_id: str, identity_id: str) -> dict[str, str]:
        identity = self.repository.get_identity(identity_id)
        if identity is None or not identity.enabled:
            raise KeyError(identity_id)
        profile = self.repository.get_profile(identity.profile_id)
        egress = self.repository.get_egress(identity.egress_id)
        if profile is None or egress is None or not egress.enabled:
            raise RuntimeError("bound identity resources are unavailable")
        spec = self.repository.get_strategy(site_id)
        if spec is None or not any(
            item.identity_id == identity_id
            or (
                item.identity_id is None
                and item.profile_id == identity.profile_id
                and item.egress_id == identity.egress_id
            )
            for item in spec.access.combinations
        ):
            raise ValueError("identity is not referenced by the selected site")
        url = (spec.auth.maintenance_url or spec.auth.login_url) or profile.login_url
        if not url:
            raise ValueError("profile has no maintenance URL")
        session_id = await self.profile_use.begin_maintenance(profile.profile_id)
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
        except Exception:
            await self.profile_use.end_maintenance(profile.profile_id, session_id)
            raise

    async def inspect_profile_login(self, token: str) -> dict[str, str]:
        return await self.runtime.inspect_login(token)

    async def recover_identity_login(
        self, site_id: str, identity_id: str, login_token: str
    ) -> dict[str, str]:
        identity = self.repository.get_identity(identity_id)
        if identity is None or identity.runtime_kind is not BrowserRuntimeKind.EXTERNAL_CHROME:
            raise KeyError(identity_id)
        egress = self.repository.get_egress(identity.egress_id)
        if egress is None or not egress.enabled:
            raise RuntimeError("bound egress is unavailable")
        profile = await self.profile_use.recover_maintenance(identity.profile_id, login_token)
        try:
            return await self.runtime.recover_login(
                profile,
                egress,
                identity=identity,
                site_id=site_id,
                token=login_token,
            )
        except Exception:
            await self.profile_use.end_maintenance(identity.profile_id, login_token)
            raise

    async def close_profile_login(self, token: str) -> None:
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
                ),
                None,
            )
            if identity is not None:
                egress = self.repository.get_egress(identity.egress_id)
                if egress is not None:
                    await self.runtime.browser_runtimes.close_stale_maintenance_page(
                        identity, egress
                    )
            profile_id = profile.profile_id
        await self.profile_use.end_maintenance(profile_id, token)

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
            raise ValueError("profile is not referenced by the active site strategy")
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
                "session_revision": profile.session_revision + 1,
                "updated_at": utc_now(),
            }
        )
        self.repository.save_profile(updated)
        identity_runtime = self.repository.get_identity_runtime(identity_id)
        next_session_revision = identity_runtime.session_revision + 1
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
                site_id=resolved.runtime_key,
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

    async def _put_cache(self, result: AccessResult, *, cache_key: str) -> None:
        size = len(result.body.encode("utf-8")) + len(str(result.recipe_result).encode("utf-8"))
        if size > 32 * 1024 * 1024:
            return
        async with self._cache_lock:
            prior = self._cache.pop(cache_key, None)
            if prior:
                self._cache_bytes -= prior[1]
            self._cache[cache_key] = (time.monotonic() + 60, size, result)
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
            site_id=resolved.site_id,
            runtime_key=resolved.runtime_key,
            strategy_revision=resolved.strategy_revision,
            body_strategy_ref=resolved.body.ref,
            generation=generation,
            attempts=attempts or [],
            network_ms=max(0, int((time.monotonic() - started) * 1000)),
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
            network_ms=max(0, int((time.monotonic() - started) * 1000)),
        )


def classify_response(response: RuntimeResponse) -> tuple[FailureCategory | None, str]:
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
    status = int(getattr(exc, "status_code", 0) or 0)
    if status:
        return classify_response(RuntimeResponse(status, "https://invalid.example/", {}, str(exc)))[
            0
        ] or FailureCategory.UNKNOWN, f"http_{status}"
    name = type(exc).__name__.casefold()
    message = str(exc).casefold()
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
