"""Owner-facing Runtime facade. The API process never starts a browser driver."""

import asyncio
import time
from pathlib import Path
from uuid import uuid4

from .browser_operations import decode_response
from .browser_runtime import RuntimeProvenance
from .runtime import SiteAccessRuntime
from .schema import AccessMode, AccessRequest, BrowserRuntimeKind, IdentityOperationalState, utc_now
from .worker_manager import IdentityOperationError, IdentityWorkerManager


class WorkerRuntimeFacade:
    def __init__(self, runtime):
        self.runtime = runtime
        self.external = self
        self.rotation_paused = False
        self._inactive_since = {}

    @property
    def driver_ready(self):
        return True  # The controller is healthy; source readiness is separate.

    async def start(self):
        self.runtime.browser_pool._owner_lock.acquire()

    async def close(self):
        try:
            await self.runtime.workers.close()
        finally:
            self.runtime.browser_pool._owner_lock.release()

    def diagnostics(self):
        return {"ready": True, "mode": "identity_workers", "workers": self.runtime.workers.status()}

    async def close_idle(self):
        from .governance_repository import GovernanceRepository

        closed = 0
        self.runtime.workers.sample_resources()
        store = GovernanceRepository(self.runtime.repository)
        viewer_identities = {
            session.identity_id for session in store.list_sessions() if session.state == "OPEN"
        }
        active = set()
        hot = set()
        for scope in store.list_scopes():
            route = self.runtime.repository.get_runtime(f"scope:{scope.scope_id}")
            selected = route.active_combination_id or scope.primary_identity_id
            active.add(selected)
            primary = self.runtime.repository.get_identity(scope.primary_identity_id)
            if (
                scope.enabled
                and scope.source_id
                and primary.lifecycle.residency.value == "always_on"
            ):
                hot.add(selected)
        now = time.monotonic()
        for identity_id, status in list(self.runtime.workers.status().items()):
            identity = self.runtime.repository.get_identity(identity_id)
            if not identity or status["stage"] != "IDLE" or identity_id in viewer_identities:
                continue
            profile = self.runtime.repository.get_profile(identity.profile_id)
            challenge = store.current_challenge(identity_id)
            lost_scene = challenge is not None and challenge.state == "TARGET_LOST"
            human = bool(profile and profile.maintenance_session_id and not lost_scene)
            if human and identity.runtime_kind is BrowserRuntimeKind.MANAGED_PLAYWRIGHT:
                continue
            timeout = min(identity.lifecycle.idle_seconds, 600 if identity_id in active else 120)
            rotate = status["age_seconds"] >= 4 * 3600 or status["high_rss_samples"] >= 3
            driver_idle = status["idle_seconds"] >= 600
            native_idle = (
                not human
                and (identity_id not in hot or lost_scene)
                and status["idle_seconds"] >= timeout
            )
            if rotate or driver_idle or native_idle:
                await self.runtime.workers.retire(identity_id)
                if identity.runtime_kind is BrowserRuntimeKind.MANAGED_PLAYWRIGHT:
                    if await self.runtime.workers.graceful_drain_managed(identity):
                        old = self.runtime.repository.get_identity_runtime(identity_id)
                        store.mark_instance_lost(identity_id, old.instance_id)
                self._inactive_since.setdefault(identity_id, now - status["idle_seconds"])
                closed += 1
        # A detached controller is not a reason to leave an inactive native Chrome forever.
        live = self.runtime.workers.status()
        for _, identity in self.runtime.repository.list_active_identities():
            challenge = store.current_challenge(identity.identity_id)
            lost_scene = challenge is not None and challenge.state == "TARGET_LOST"
            if (
                identity.runtime_kind is not BrowserRuntimeKind.EXTERNAL_CHROME
                or identity.identity_id in viewer_identities
                or (identity.identity_id in hot and not lost_scene)
            ):
                continue
            profile = self.runtime.repository.get_profile(identity.profile_id)
            if profile and profile.maintenance_session_id and not lost_scene:
                continue
            status = live.get(identity.identity_id)
            timeout = min(
                identity.lifecycle.idle_seconds, 600 if identity.identity_id in active else 120
            )
            if lost_scene:
                timeout = 120
            if status and (status["stage"] != "IDLE" or status["idle_seconds"] < timeout):
                self._inactive_since.pop(identity.identity_id, None)
                continue
            since = self._inactive_since.setdefault(identity.identity_id, now)
            if now - since >= timeout:
                await self.stop_identity(identity, reason="inactive_identity_idle")
                self._inactive_since.pop(identity.identity_id, None)
        return closed

    async def prewarm(self, identity, egress):
        value = await self.runtime.workers.command(
            identity, egress, "allocate", {"prewarm": True}, timeout=30
        )
        return RuntimeProvenance(**value) if value else None

    async def stop_identity(self, identity, *, reason):
        await self.runtime.workers.retire(identity.identity_id)
        if identity.runtime_kind is BrowserRuntimeKind.EXTERNAL_CHROME:
            from .supervisor_client import ChromeSupervisorClient

            client = ChromeSupervisorClient(
                self.runtime.supervisor_socket, owner_id=self.runtime.controller_id
            )
            status = await client.status(identity.identity_id)
            if not status.get("running"):
                return status.get("known_missing") is not False
            stopped = await client.stop(identity.identity_id, reason=reason)
            if stopped:
                from .governance_repository import GovernanceRepository

                GovernanceRepository(self.runtime.repository).mark_instance_lost(
                    identity.identity_id,
                    status.get("instance_id"),
                )
            return stopped
        return await self.runtime.workers.graceful_drain_managed(
            identity, allow_maintenance=reason in {"profile_snapshot", "profile_restore"}
        )

    async def status(self, identity):
        return self.runtime.workers.status().get(identity.identity_id, {"alive": False})


class WorkerSiteAccessRuntime(SiteAccessRuntime):
    def __init__(self, repository, resolver, **options):
        super().__init__(repository, resolver, **options)
        self.supervisor_socket = options.get("supervisor_socket")
        self.controller_id = options.get("controller_id", "site-access")
        safe_options = {
            key: str(value) if isinstance(value, Path) else value for key, value in options.items()
        }
        safe_options["browser_max_processes"] = 1
        self.workers = IdentityWorkerManager(repository, safe_options)
        self.browser_runtimes = WorkerRuntimeFacade(self)
        self._business_slots = asyncio.Semaphore(options.get("browser_max_pages", 4))
        self._tokens = {}

    async def _browser(self, request, resolved, identity, egress):
        deadline = time.monotonic() + request.remaining_budget_ms / 1000
        async with asyncio.timeout_at(deadline):
            await self._business_slots.acquire()
        try:
            for recovery in range(2):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("identity operation budget exhausted")
                try:
                    response = decode_response(
                        await self.workers.command(
                            identity,
                            egress,
                            "execute",
                            {
                                "request": request.model_copy(
                                    update={
                                        "remaining_budget_ms": max(1, int(remaining * 1000)),
                                    }
                                ).model_dump(mode="json"),
                                "resolved": resolved.model_dump(mode="json"),
                            },
                            timeout=remaining,
                        )
                    )
                    break
                except IdentityOperationError as exc:
                    if recovery:
                        exc.control_recovery_attempted = True
                    if (
                        not recovery
                        and identity.runtime_kind is BrowserRuntimeKind.MANAGED_PLAYWRIGHT
                        and exc.reason_code == "browser_profile_busy"
                        and deadline - time.monotonic() >= 12
                    ):
                        await self.workers.retire(identity.identity_id, graceful=False)
                        if await self.workers.graceful_drain_managed(identity):
                            continue
                    if (
                        recovery
                        or exc.failure_category != "RUNTIME_UNAVAILABLE"
                        or exc.reason_code
                        in {"browser_profile_busy", "identity_configuration_changed_requires_drain"}
                        or deadline - time.monotonic() <= 1
                    ):
                        raise
                    await self.workers.retire(identity.identity_id, graceful=False)
            if response.provenance:
                state = self.repository.get_identity_runtime(identity.identity_id)
                if (
                    state.instance_id != response.provenance.instance_id
                    or state.operational_state is not IdentityOperationalState.AVAILABLE
                ):
                    if identity.runtime_kind is BrowserRuntimeKind.MANAGED_PLAYWRIGHT:
                        from .governance_repository import GovernanceRepository

                        GovernanceRepository(self.repository).mark_instance_lost(
                            identity.identity_id,
                            state.instance_id,
                        )
                    self.repository.save_identity_runtime(
                        state.model_copy(
                            update={
                                "instance_id": response.provenance.instance_id,
                                "generation": state.generation + 1,
                                "operational_state": IdentityOperationalState.AVAILABLE,
                                "updated_at": utc_now(),
                            }
                        ),
                        expected_generation=state.generation,
                    )
            if response.challenge_token:
                self._tokens[response.challenge_token] = (resolved.site_id, identity.identity_id)
            return response
        finally:
            self._business_slots.release()

    def _binding(self, token):
        binding = self._tokens.get(token)
        if binding is None:
            from .governance_repository import GovernanceRepository

            session = next(
                (
                    s
                    for s in GovernanceRepository(self.repository).list_sessions()
                    if s.control_token == token
                ),
                None,
            )
            if session:
                binding = (session.site_id, session.identity_id)
        if binding is None:
            profile = next(
                (p for p in self.repository.list_profiles() if p.maintenance_session_id == token),
                None,
            )
            if not profile:
                raise KeyError(token)
            identity = next(
                (
                    i
                    for _, i in self.repository.list_active_identities()
                    if i.profile_id == profile.profile_id
                ),
                None,
            )
            if not identity:
                raise KeyError(token)
            binding = (profile.challenge_site_id or profile.site_id, identity.identity_id)
        identity = self.repository.get_identity(binding[1])
        egress = self.repository.get_egress(identity.egress_id)
        return binding[0], identity, egress

    async def open_login(self, profile, egress, url, *, token=None, identity=None, site_id=None):
        if identity is None or token is None:
            raise ValueError("maintenance requires an explicit identity and session")
        result = await self.workers.command(
            identity,
            egress,
            "allocate",
            {
                "url": url,
                "token": token,
                "site_id": site_id or profile.site_id,
            },
            timeout=40,
        )
        self._tokens[token] = (site_id or profile.site_id, identity.identity_id)
        return result

    async def inspect_login(self, token):
        _, identity, egress = self._binding(token)
        return await self.workers.command(
            identity, egress, "inspect_target", self._maintenance_refs(token), timeout=15
        )

    def _maintenance_refs(self, token):
        from .governance_repository import GovernanceRepository

        session = next(
            (
                s
                for s in GovernanceRepository(self.repository).list_sessions()
                if s.control_token == token
            ),
            None,
        )
        return {
            "token": token,
            "target_id": session.target_id if session else None,
            "allocation_id": session.allocation_id if session else None,
            "expected_instance_id": session.browser_instance_id if session else None,
            "site_id": session.site_id if session else None,
        }

    async def adopt_challenge(self, profile, egress, *, identity, site_id, token):
        from .governance_repository import GovernanceRepository
        from .supervisor_client import ChromeSupervisorClient

        store = GovernanceRepository(self.repository)
        task = store.current_challenge(identity.identity_id)
        if task and identity.runtime_kind is BrowserRuntimeKind.EXTERNAL_CHROME:
            status = await ChromeSupervisorClient(
                self.supervisor_socket, owner_id=self.controller_id
            ).status(identity.identity_id)
            if not status.get("running") and status.get("known_missing") is False:
                raise RuntimeError("original_browser_state_unverified")
            if not status.get("running") or status.get("instance_id") != task.browser_instance_id:
                store.save_challenge(task.model_copy(update={"state": "TARGET_LOST"}))
                raise RuntimeError("original_challenge_target_lost")
        result = await self.workers.command(
            identity,
            egress,
            "adopt_target",
            {
                "site_id": site_id,
                "target_id": profile.challenge_target_id,
                "token": token,
            },
            timeout=30,
        )
        self._tokens[token] = (site_id, identity.identity_id)
        return result

    async def recover_login(self, profile, egress, *, identity, site_id, token):
        return await self.adopt_challenge(
            profile, egress, identity=identity, site_id=site_id, token=token
        )

    async def verify_login(self, token, article_url, *, timeout_ms=30_000):
        site_id, identity, egress = self._binding(token)
        if site_id == "investing" and identity.runtime_kind is BrowserRuntimeKind.EXTERNAL_CHROME:
            # Verification uses a separate business target. Reconstructing a
            # Playwright Page for the old human tab first can stall on recovery,
            # even though bounded native DOM inspection of that exact tab works.
            # Keep its ownership/instance check, not its disposable Python handle.
            deadline = time.monotonic() + timeout_ms / 1000
            original = await self.inspect_challenge(token)
            from doxagent.content_enrichment.quality import inspect_html

            info = inspect_html(original.body, original.final_url, "", generic_extraction=False)
            if info.access_reason == "challenge_required":
                return original
            resolved = self.resolver.resolve(article_url)
            if resolved.site_id != site_id:
                raise ValueError("verification article belongs to another publisher")
            response = await self._browser(
                AccessRequest(
                    operation_id=f"maintenance-verify:{uuid4().hex}",
                    purpose="BODY",
                    url=article_url,
                    mode=AccessMode.BROWSER,
                    recipe_parameters={"_maintenance_canary": True},
                    remaining_budget_ms=max(1, int((deadline - time.monotonic()) * 1000)),
                ),
                resolved,
                identity,
                egress,
            )
            if (
                not response.provenance
                or not original.provenance
                or response.provenance.instance_id != original.provenance.instance_id
            ):
                raise RuntimeError("original_browser_changed_during_verification")
            response.challenge_cleared = original.challenge_cleared
            return response
        return decode_response(
            await self._verify_with_capacity(
                identity,
                egress,
                "verify",
                {
                    **self._maintenance_refs(token),
                    "url": article_url,
                    "timeout_ms": timeout_ms,
                },
                timeout=timeout_ms / 1000,
            )
        )

    async def inspect_challenge(self, token):
        _, identity, egress = self._binding(token)
        if identity.runtime_kind is BrowserRuntimeKind.EXTERNAL_CHROME:
            from doxagent.content_enrichment.quality import inspect_html

            from .governance_repository import GovernanceRepository
            from .maintenance_document import read_original_document
            from .supervisor_client import ChromeSupervisorClient

            store = GovernanceRepository(self.repository)
            session = next((s for s in store.list_sessions() if s.control_token == token), None)
            if session is None:
                raise KeyError("maintenance session")
            capture = await read_original_document(
                ChromeSupervisorClient(self.supervisor_socket, owner_id=self.controller_id),
                identity,
                session,
                store.get_target(session.allocation_id),
                evidence_store=self.evidence_store,
            )
            info = inspect_html(capture.body, capture.final_url, "", generic_extraction=False)
            if (
                identity.identity_id
                and session.site_id == "investing"
                and info.access_reason == "challenge_required"
            ):
                # Cloudflare may finish a native JS check after first commit.
                # Observe this exact owned page once, never reload/click/solve it.
                try:
                    async with asyncio.timeout(8):
                        while info.access_reason == "challenge_required":
                            await asyncio.sleep(0.5)
                            capture = await read_original_document(
                                ChromeSupervisorClient(
                                    self.supervisor_socket, owner_id=self.controller_id
                                ),
                                identity,
                                session,
                                store.get_target(session.allocation_id),
                                  evidence_store=self.evidence_store,
                            )
                            info = inspect_html(
                                capture.body, capture.final_url, "", generic_extraction=False
                            )
                except TimeoutError:
                    pass  # A still-blocked page remains a real human task.
            capture.challenge_cleared = info.page_kind in {"article", "listing", "quote"} or (
                info.access_reason in {"subscription_required", "login_required"}
            )
            capture.reason = info.access_reason or "original_page_inspected"
            return capture
        return decode_response(
            await self.workers.command(
                identity,
                egress,
                "verify",
                {
                    **self._maintenance_refs(token),
                    "inspect_only": True,
                },
                # A recovered worker must attach CDP, read the original page and
                # cold-load the article classifier. The old 5s budget killed a
                # healthy controller during its first extraction (~4.6s alone),
                # making every subsequent Verify cold again. Still bounded by
                # the maintenance operation's overall deadline.
                timeout=15,
            )
        )

    async def verify_crawler(self, token, request):
        _, identity, egress = self._binding(token)
        return decode_response(
            await self._verify_with_capacity(
                identity,
                egress,
                "verify",
                {
                    **self._maintenance_refs(token),
                    "crawler_request": request.model_dump(mode="json"),
                },
                timeout=request.remaining_budget_ms / 1000,
            )
        )

    async def _verify_with_capacity(self, identity, egress, action, payload, *, timeout):
        deadline = time.monotonic() + timeout
        async with asyncio.timeout_at(deadline), self._business_slots:
            remaining = max(0.001, deadline - time.monotonic())
            payload = dict(payload)
            if "timeout_ms" in payload:
                payload["timeout_ms"] = max(1, int(remaining * 1000))
            if "crawler_request" in payload:
                payload["crawler_request"] = {
                    **payload["crawler_request"],
                    "remaining_budget_ms": max(1, int(remaining * 1000)),
                }
            return await self.workers.command(identity, egress, action, payload, timeout=remaining)

    async def close_login(self, token):
        _, identity, egress = self._binding(token)
        from .governance_repository import GovernanceRepository

        session = next(
            (
                s
                for s in GovernanceRepository(self.repository).list_sessions()
                if s.control_token == token
            ),
            None,
        )
        handle = self.workers.workers.get(identity.identity_id)
        if identity.runtime_kind is BrowserRuntimeKind.EXTERNAL_CHROME and (
            handle is None or handle.process.returncode is not None
        ):
            from .maintenance_document import close_original_document
            from .supervisor_client import ChromeSupervisorClient

            store = GovernanceRepository(self.repository)
            if session is None:
                raise KeyError("maintenance session")
            target = store.get_target(session.allocation_id)
            await close_original_document(
                ChromeSupervisorClient(self.supervisor_socket, owner_id=self.controller_id),
                identity,
                session,
                target,
            )
            store.save_target(
                target.model_copy(update={"state": "CLOSED", "updated_at": utc_now()})
            )
            self._tokens.pop(token, None)
            return identity.profile_id
        result = await self.workers.command(
            identity,
            egress,
            "close_owned_target",
            {
                "token": token,
                "target_id": session.target_id if session else None,
                "expected_instance_id": session.browser_instance_id if session else None,
                "site_id": session.site_id if session else None,
                "allocation_id": session.allocation_id if session else None,
            },
            timeout=10,
        )
        self._tokens.pop(token, None)
        return result["profile_id"]
