"""One cold successor per scope, actual canaries, sticky CAS activation."""

import asyncio
import time
from datetime import datetime, timedelta
from uuid import uuid4

from .governance_models import MaintenanceOperation
from .governance_repository import GovernanceRepository, capability_fingerprint
from .schema import AccessCombination, AccessMode, AccessRequest, SitePurpose, utc_now
from .verification import validate_capture, validate_crawler_capture


class FailoverCoordinator:
    def __init__(self, service):
        self.service = service
        self.store = GovernanceRepository(service.repository)
        self.tasks = {}
        self._locks = {}

    def usable(self, scope, identity_id, purpose):
        state = self.service.repository.get_runtime(f"scope:{scope.scope_id}")
        member = state.combinations.get(identity_id)
        capability = member.capabilities.get(purpose.value, {}) if member else {}
        identity = self.service.repository.get_identity(identity_id)
        egress = self.service.repository.get_egress(identity.egress_id)
        expected = capability_fingerprint(self.service.repository, scope, identity, egress)
        checked = capability.get("checked_at")
        return (
            capability.get("state") == "VERIFIED_COLD"
            and capability.get("result") in {"READY", "FULL", "SHORT_FULL"}
            and capability.get("fingerprint") == expected
            and checked is not None
            and utc_now() - datetime.fromisoformat(checked) <= timedelta(hours=24)
        )

    async def close(self):
        for task in self.tasks.values():
            task.cancel()
        if self.tasks:
            await asyncio.wait(tuple(self.tasks.values()), timeout=3)

    def schedule(self, scope, purpose):
        if scope is None:
            return None
        key = (scope.scope_id, purpose)
        if not scope.fallback_identity_id:
            return None
        if self.store.current_challenge(scope.fallback_identity_id):
            return None
        repo = self.service.repository
        identity = repo.get_identity(scope.fallback_identity_id)
        profile = repo.get_profile(identity.profile_id) if identity else None
        if not profile or profile.operational_state.value != "AVAILABLE":
            return None
        state = repo.get_runtime(f"scope:{scope.scope_id}")
        if state.active_combination_id == scope.fallback_identity_id:
            return None  # Sticky successor failure never means silently retrying primary.
        main = repo.get_identity(scope.primary_identity_id)
        main_egress = repo.get_egress(main.egress_id)
        egress = repo.get_egress(identity.egress_id)
        if main_egress.node_ref != "DIRECT" and (
            not self.store.egress_region(main_egress)
            or self.store.egress_region(main_egress) != self.store.egress_region(egress)
            or not main_egress.observed_ip
            or not egress.observed_ip
            or main_egress.observed_ip == egress.observed_ip
        ):
            return None
        spec = repo.get_strategy(scope.owner_site_id)
        required = (
            spec.auth.crawler_requirement
            if purpose is SitePurpose.CRAWLER
            else spec.auth.body_requirement
        )
        if required == "inherit":
            required = spec.auth.requirement
        member = state.combinations.get(identity.identity_id)
        if member and member.cooldown_until and member.cooldown_until > utc_now():
            return None
        if required == "required":
            auth = repo.get_site_identity_auth(scope.owner_site_id, identity.identity_id)
            runtime = repo.get_identity_runtime(identity.identity_id)
            if (
                auth.auth_state.value != "VALID"
                or auth.observed_session_revision != runtime.session_revision
            ):
                return None
            member = state.combinations.get(identity.identity_id)
            if purpose is SitePurpose.BODY and (
                not member
                or len(member.capabilities.get("BODY", {}).get("subscription_samples", [])) < 2
            ):
                return None
        existing = self.tasks.get(key)
        if existing and not existing.done():
            return existing
        task = asyncio.create_task(
            self.activate(scope, purpose), name=f"cold-fallback:{scope.scope_id}"
        )
        self.tasks[key] = task
        return task

    async def activate(self, scope, purpose, *, primary=False):
        identity_id = scope.primary_identity_id if primary else scope.fallback_identity_id
        if not identity_id:
            return {"ready": False, "reason": "fallback_unprovisioned"}
        lock = self._locks.setdefault(scope.scope_id, asyncio.Lock())
        async with lock:
            operation = self.store.save_operation(
                MaintenanceOperation(
                    action="ACTIVATE_PRIMARY" if primary else "ACTIVATE_FALLBACK",
                    idempotency_key=f"cold-activation:{scope.scope_id}:{uuid4().hex}",
                    scope_id=scope.scope_id,
                    identity_id=identity_id,
                    deadline_at=utc_now() + timedelta(seconds=45),
                    state="RUNNING",
                    stage="STARTING_BROWSER",
                )
            )
            attempt = asyncio.create_task(
                self._canary(scope, identity_id, purpose, primary=primary)
            )
            try:
                done, _ = await asyncio.wait({attempt}, timeout=45)
                if not done:
                    attempt.cancel()
                    await asyncio.wait({attempt}, timeout=3)
                    result = {"ready": False, "reason": "cold_activation_timeout"}
                else:
                    result = attempt.result()
            except asyncio.CancelledError:
                attempt.cancel()
                self.store.save_operation(
                    operation.model_copy(update={"state": "CANCELLED", "stage": "CANCELLED"})
                )
                raise
            except Exception:
                result = {"ready": False, "reason": "cold_activation_failed"}
            self.store.save_operation(
                operation.model_copy(
                    update={
                        "state": "DONE" if result.get("ready") else "FAILED",
                        "stage": "DONE",
                        "result": result,
                        "updated_at": utc_now(),
                    }
                )
            )
            return result

    async def _canary(self, scope, identity_id, purpose, *, primary):
        deadline = time.monotonic() + 45
        service = self.service
        if self.store.current_challenge(identity_id):
            return {"ready": False, "reason": "human_challenge_required"}
        identity = service.repository.get_identity(identity_id)
        profile = service.repository.get_profile(identity.profile_id)
        egress = service.repository.get_egress(identity.egress_id)
        if profile.operational_state.value != "AVAILABLE":
            return {"ready": False, "reason": "identity_in_maintenance"}
        spec = service.repository.get_strategy(scope.owner_site_id)
        before = service.repository.get_runtime(f"scope:{scope.scope_id}")
        member = before.combinations.get(identity_id)
        last_url = member.capabilities.get("BODY", {}).get("last_url") if member else None
        url = spec.auth.verification_url or scope.verification_plan.get("body_url") or last_url
        parameters = (
            scope.verification_plan.get("crawler")
            if purpose is SitePurpose.CRAWLER
            else ({"url": url} if url else None)
        )
        if not parameters:
            return {"ready": False, "reason": "canary_not_configured"}
        request = AccessRequest(
            **parameters,
            operation_id=f"cold-canary:{uuid4().hex}",
            purpose=purpose,
            mode=AccessMode.BROWSER,
            remaining_budget_ms=35_000,
            origin_scope_id=scope.scope_id,
            scope_revision=scope.revision,
        )
        resolved = service._resolve_request(request)
        combination = service._materialize_combination(
            AccessCombination(id=identity_id, identity_id=identity_id)
        )
        if (
            service._auth_required(resolved, purpose)
            and service._auth_state(
                resolved,
                combination,
                profile,
            ).value
            != "VALID"
        ):
            return {"ready": False, "reason": "human_login_required"}
        if (
            not primary
            and purpose is SitePurpose.BODY
            and service._auth_required(resolved, purpose)
        ):
            capability = member.capabilities.get("BODY", {}) if member else {}
            if len(capability.get("subscription_samples", [])) < 2:
                return {"ready": False, "reason": "two_subscription_article_checks_required"}
        if not primary and before.active_combination_id != identity_id:
            main = service.repository.get_identity(scope.primary_identity_id)
            main_egress = service.repository.get_egress(main.egress_id)
            if main_egress.node_ref != "DIRECT" and (
                not egress.observed_ip
                or not main_egress.observed_ip
                or egress.observed_ip == main_egress.observed_ip
            ):
                return {"ready": False, "reason": "fallback_egress_not_independent"}
            if main_egress.node_ref != "DIRECT" and (
                not self.store.egress_region(main_egress)
                or self.store.egress_region(main_egress) != self.store.egress_region(egress)
            ):
                return {"ready": False, "reason": "fallback_same_region_unverified"}
        cold = not primary and before.active_combination_id != identity_id
        if cold:
            stopped = await service.runtime.browser_runtimes.stop_identity(
                identity, reason="cold_fallback_canary"
            )
            if not stopped:
                return {"ready": False, "reason": "cold_shutdown_unconfirmed"}
        remaining = min(35, deadline - time.monotonic())
        if remaining <= 0:
            return {"ready": False, "reason": "cold_start_budget_exhausted"}
        request = request.model_copy(update={"remaining_budget_ms": max(1, int(remaining * 1000))})
        async with service.profile_use.business(profile.profile_id):
            response, category, reason, _ = await service._attempt(
                request, resolved, combination, profile, egress
            )
            if response.challenge_token:
                await service.profile_use.begin_challenge(
                    profile.profile_id,
                    token=response.challenge_token,
                    site_id=scope.owner_site_id,
                    identity_id=identity_id,
                    combination_id=identity_id,
                    article_url=request.url,
                    target_id=response.challenge_target_id,
                )
        if response.challenge_token:
            await service.profile_use.finish_challenge(profile.profile_id, response.challenge_token)
        if category is not None:
            await service.health.mark_failure(
                resolved.runtime_key,
                identity_id,
                category,
                reason,
                assigned_generation=before.generation,
                transport=AccessMode.BROWSER,
                retry_after_seconds=response.retry_after_seconds,
            )
            return {"ready": False, "reason": reason}
        proof = (
            validate_crawler_capture(scope.source_id, response, request)
            if purpose is SitePurpose.CRAWLER
            else (validate_capture(response, resolved).body in {"FULL", "SHORT_FULL"})
        )
        if not proof:
            return {"ready": False, "reason": "canary_contract_failed"}
        current_scope = self.store.get_scope(scope.scope_id)
        if current_scope.revision != scope.revision:
            return {"ready": False, "reason": "scope_changed_during_activation"}
        self.store.record_capability(
            scope,
            identity_id,
            purpose.value,
            "READY" if purpose is SitePurpose.CRAWLER else response.body_quality or "FULL",
            url=response.final_url,
            instance_id=response.provenance.instance_id if response.provenance else None,
            cold_verified=cold,
            subscription_verified=response.subscription_verified,
        )
        await service.health.mark_success(
            resolved.runtime_key,
            identity_id,
            assigned_generation=before.generation,
            transport=AccessMode.BROWSER,
        )
        await service.health.clear_purpose_denial(resolved.runtime_key, identity_id, purpose)
        try:
            await service.health.activate(
                resolved.runtime_key, identity_id, expected_generation=before.generation
            )
        except RuntimeError:
            current = service.repository.get_runtime(resolved.runtime_key)
            if current.active_combination_id != identity_id:
                return {"ready": False, "reason": "route_changed_during_activation"}
        return {
            "ready": True,
            "identity_id": identity_id,
            "active_role": "primary" if primary else "fallback",
            "canary": purpose.value,
        }
