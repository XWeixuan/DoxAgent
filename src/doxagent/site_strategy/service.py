"""Site Strategy registry validation and bounded access orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from doxagent.content_enrichment.quality import inspect_html
from doxagent.content_enrichment.strategies import validate_body_strategy

from .budget import SiteBudgetManager
from .egress import probe_egress
from .health import RISK_FAILURES, CombinationHealthManager
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
    BrowserProfile,
    FailureCategory,
    ProxyEgress,
    ResolvedSite,
    SitePurpose,
    SiteStrategySpec,
    utc_now,
)

logger = logging.getLogger(__name__)


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
        browser_idle_seconds: float = 300,
        safety_path: str | Path | None = None,
    ) -> None:
        self.repository = repository
        self.resolver = SiteResolver(repository)
        self.budgets = SiteBudgetManager()
        self.health = CombinationHealthManager(repository)
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
        )
        self._inflight: dict[str, asyncio.Task[AccessResult]] = {}
        self._cache: OrderedDict[str, tuple[float, int, AccessResult]] = OrderedDict()
        self._cache_bytes = 0
        self._cache_lock = asyncio.Lock()
        self._maintenance_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Acquire the single-owner profile lock and start the browser driver."""
        await self.runtime.browser_pool.start()
        for profile in self.repository.list_profiles():
            if profile.auth_state is AuthState.MAINTENANCE:
                self.repository.save_profile(
                    profile.model_copy(
                        update={"auth_state": AuthState.UNKNOWN, "updated_at": utc_now()}
                    )
                )
        if self._maintenance_task is None:
            self._maintenance_task = asyncio.create_task(self._maintenance_loop())

    @property
    def browser_driver_ready(self) -> bool:
        return self.runtime.browser_pool.driver_ready

    async def close(self) -> None:
        if self._maintenance_task is not None:
            self._maintenance_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._maintenance_task
            self._maintenance_task = None
        try:
            await self.runtime.close()
        finally:
            self.repository.close()

    async def _maintenance_loop(self) -> None:
        while True:
            try:
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
        profile = self._profile(resolved, combination)
        egress = self.repository.get_egress(combination.egress_id)
        if profile is None or egress is None or not egress.enabled:
            await self.health.mark_probe_uncertain(spec.site_id, combination.combination_id)
            return
        request = AccessRequest(
            operation_id=f"probe:{spec.site_id}:{combination.combination_id}:{assigned_generation}",
            purpose=SitePurpose.PROBE,
            url=spec.access.probe_url,
            mode=AccessMode.HTTP_PUBLIC,
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
                response, category, reason, _ = await self._attempt(
                    request, resolved, combination, profile, egress
                )
        except TimeoutError:
            await self.health.mark_probe_uncertain(spec.site_id, combination.combination_id)
            return
        if category is None or category is FailureCategory.EMPTY_SUCCESS:
            await self.health.mark_success(
                spec.site_id,
                combination.combination_id,
                assigned_generation=assigned_generation,
            )
        elif category in RISK_FAILURES:
            await self.health.mark_failure(
                spec.site_id,
                combination.combination_id,
                category,
                reason,
                assigned_generation=assigned_generation,
                retry_after_seconds=response.retry_after_seconds,
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
            egress = self.repository.get_egress(combination.egress_id)
            if egress is None:
                raise ValueError(f"unknown egress: {combination.egress_id}")
            profile = self.repository.get_profile(combination.profile_id)
            if profile is None:
                raise ValueError(f"unknown browser profile: {combination.profile_id}")
            if spec.site_id != "generic" and profile.site_id != spec.site_id:
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

    async def execute(self, request: AccessRequest) -> AccessResult:
        created = False
        async with self._cache_lock:
            self._expire_cache()
            cached = self._cache.get(request.request_id)
            if cached is not None:
                self._cache.move_to_end(request.request_id)
                return cached[2]
            task = self._inflight.get(request.request_id)
            if task is None:
                task = asyncio.create_task(self._execute_uncached(request))
                self._inflight[request.request_id] = task
                created = True
        try:
            result = await asyncio.shield(task)
        finally:
            if task.done():
                async with self._cache_lock:
                    self._inflight.pop(request.request_id, None)
        if task.done() and not task.cancelled() and task.exception() is None:
            await self._put_cache(result)
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
            if profile.auth_state is AuthState.MAINTENANCE:
                unavailable += 1
                continue
            if (
                self._auth_required(resolved, request.purpose)
                and profile.auth_state is not AuthState.VALID
            ):
                auth_missing += 1
                continue
            budget = self.budgets.get(
                resolved.runtime_key,
                max_concurrency=resolved.access.max_concurrency,
                min_interval_ms=resolved.access.min_interval_ms,
            )
            queue_cap = (
                resolved.access.body_queue_timeout_ms
                if request.purpose is SitePurpose.BODY
                else resolved.access.crawler_queue_timeout_ms
            ) / 1000
            try:
                async with budget.permit(
                    request.purpose, timeout_seconds=min(remaining, queue_cap)
                ) as queue_wait_ms:
                    current = self.repository.get_runtime(resolved.runtime_key)
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
                    )
                    attempts.append(attempt)
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
                )
                self._event(request, resolved, combination, "COMBINATION_RISK_FAILURE", attempt)
                continue
            if category is FailureCategory.EGRESS_UNAVAILABLE:
                unavailable += 1
                continue
            if category in {
                FailureCategory.AUTH_REQUIRED,
                FailureCategory.ENTITLEMENT_MISSING,
                FailureCategory.AUTH_OR_ACCESS_UNKNOWN,
            }:
                self._mark_profile_auth(profile, category)
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
            elapsed = int((time.monotonic() - started) * 1000)
            last = response, category, reason, elapsed
            if category is not FailureCategory.TRANSIENT_TRANSPORT or retry == 1:
                break
        assert last is not None
        return last

    def _profile(
        self, resolved: ResolvedSite, combination: AccessCombination
    ) -> BrowserProfile | None:
        profile = self.repository.get_profile(combination.profile_id)
        if resolved.site_id != "generic":
            return profile
        egress = self.repository.get_egress(combination.egress_id)
        if egress is None:
            return None
        suffix = hashlib.sha256(resolved.runtime_key.encode()).hexdigest()[:12]
        profile_id = f"generic-{suffix}-{combination.egress_id}"[:128]
        derived = self.repository.get_profile(profile_id)
        if derived is not None:
            return derived
        derived = BrowserProfile(
            profile_id=profile_id,
            site_id="generic",
            bound_egress_id=combination.egress_id,
            directory_key=f"generic-{suffix}-{combination.egress_id}"[:128],
        )
        self.repository.save_profile(derived)
        return derived

    @staticmethod
    def _purpose_combinations(
        resolved: ResolvedSite, purpose: SitePurpose
    ) -> list[AccessCombination]:
        override = (
            resolved.access.overrides.get("body")
            if purpose is SitePurpose.BODY
            else resolved.access.overrides.get("crawler")
        )
        if not override:
            return list(resolved.access.combinations)
        allowed = set(override)
        return [item for item in resolved.access.combinations if item.combination_id in allowed]

    @staticmethod
    def _auth_required(resolved: ResolvedSite, purpose: SitePurpose) -> bool:
        override = (
            resolved.auth.body_requirement
            if purpose is SitePurpose.BODY
            else resolved.auth.crawler_requirement
        )
        requirement = resolved.auth.requirement if override == "inherit" else override
        return requirement == "required"

    def _mark_profile_auth(self, profile: BrowserProfile, category: FailureCategory) -> None:
        state = (
            AuthState.ENTITLEMENT_MISSING
            if category is FailureCategory.ENTITLEMENT_MISSING
            else AuthState.REAUTH_REQUIRED
        )
        self.repository.save_profile(
            profile.model_copy(update={"auth_state": state, "updated_at": utc_now()})
        )

    def save_outcomes(self, values: list[BodyOutcome]) -> int:
        for value in values:
            self.repository.save_body_outcome(value)
        self.repository.prune_diagnostics()
        return len(values)

    async def open_profile_login(self, profile_id: str) -> dict[str, str]:
        profile = self.repository.get_profile(profile_id)
        if profile is None:
            raise KeyError(profile_id)
        egress = self.repository.get_egress(profile.bound_egress_id)
        if egress is None or not egress.enabled:
            raise RuntimeError("bound egress is unavailable")
        spec = self.repository.get_strategy(profile.site_id)
        url = profile.login_url or (spec.auth.login_url if spec else None)
        if not url:
            raise ValueError("profile has no login URL")
        maintenance = profile.model_copy(
            update={"auth_state": AuthState.MAINTENANCE, "updated_at": utc_now()}
        )
        self.repository.save_profile(maintenance)
        try:
            await self.runtime.browser_pool.wait_profile_idle(profile_id)
            return await self.runtime.open_login(maintenance, egress, url)
        except Exception:
            self.repository.save_profile(profile)
            raise

    async def inspect_profile_login(self, token: str) -> dict[str, str]:
        return await self.runtime.inspect_login(token)

    async def close_profile_login(self, token: str) -> None:
        profile_id = await self.runtime.close_login(token)
        profile = self.repository.get_profile(profile_id)
        if profile is not None and profile.auth_state is AuthState.MAINTENANCE:
            self.repository.save_profile(
                profile.model_copy(
                    update={"auth_state": AuthState.UNKNOWN, "updated_at": utc_now()}
                )
            )

    async def verify_profile(self, profile_id: str, article_url: str) -> tuple[BrowserProfile, str]:
        profile = self.repository.get_profile(profile_id)
        if profile is None:
            raise KeyError(profile_id)
        egress = self.repository.get_egress(profile.bound_egress_id)
        if egress is None or not egress.enabled:
            raise RuntimeError("bound egress is unavailable")
        resolved = self.resolve(article_url)
        if resolved.site_id != profile.site_id:
            raise ValueError("verification article does not belong to the profile site")
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
        request = AccessRequest(
            operation_id=f"profile-verify:{profile_id}:{int(time.time())}",
            purpose=SitePurpose.LOGIN,
            url=article_url,
            mode=AccessMode.BROWSER,
            strategy_revision=resolved.strategy_revision,
            remaining_budget_ms=30_000,
        )
        response = await self.runtime.execute(request, resolved, combination, profile, egress)
        category, reason = classify_response(response)
        inspection = inspect_html(
            response.body,
            response.final_url,
            None,
            strategy_ref=resolved.body.ref,
            strategy_parameters=resolved.body.parameters,
        )
        if (
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
        self.repository.append_event(
            AccessEvent(
                operation_id=request.operation_id,
                site_id=resolved.site_id,
                combination_id=combination.combination_id,
                category="PROFILE_VERIFIED",
                payload={"profile_id": profile_id, "state": state.value, "reason": reason},
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

    async def _put_cache(self, result: AccessResult) -> None:
        size = len(result.body.encode("utf-8")) + len(str(result.recipe_result).encode("utf-8"))
        if size > 32 * 1024 * 1024:
            return
        async with self._cache_lock:
            prior = self._cache.pop(result.request_id, None)
            if prior:
                self._cache_bytes -= prior[1]
            self._cache[result.request_id] = (time.monotonic() + 60, size, result)
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
    if status in {401, 403}:
        inspection = inspect_html(response.body, response.final_url, "")
        if inspection.access_reason == "challenge_required":
            return FailureCategory.ACCESS_CHALLENGE, "challenge_required"
        if inspection.access_reason == "login_required":
            return FailureCategory.AUTH_REQUIRED, "login_required"
        if inspection.access_reason == "subscription_required":
            return FailureCategory.ENTITLEMENT_MISSING, "subscription_required"
        return FailureCategory.AUTH_OR_ACCESS_UNKNOWN, f"http_{status}"
    if status in {408, 425} or status >= 500:
        return FailureCategory.TRANSIENT_TRANSPORT, f"http_{status}"
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
    if status == 429:
        return FailureCategory.ACCESS_RATE_LIMIT, "http_429"
    name = type(exc).__name__.casefold()
    message = str(exc).casefold()
    if any(marker in name or marker in message for marker in ("timeout", "connect", "dns", "tls")):
        return FailureCategory.TRANSIENT_TRANSPORT, type(exc).__name__
    if "capacity" in message or "browser" in name or "playwright" in name:
        return FailureCategory.RUNTIME_UNAVAILABLE, type(exc).__name__
    return FailureCategory.TRANSIENT_TRANSPORT, type(exc).__name__


__all__ = ["SiteStrategyService", "classify_exception", "classify_response"]
