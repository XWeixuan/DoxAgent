"""Durable maintenance operations with non-blocking, cached status reads."""

import asyncio
from contextlib import suppress
from datetime import timedelta
from uuid import uuid4

from .governance_models import (
    ChallengeTask,
    MaintenanceCommand,
    MaintenanceOperation,
    MaintenanceSession,
)
from .governance_repository import GovernanceRepository, safe_display_url
from .schema import SitePurpose, utc_now


class MaintenanceController:
    def __init__(self, service):
        self.service = service
        self.store = GovernanceRepository(service.repository)
        self.operations = {}
        self.tasks = {}
        self.snapshot = {"status": "INITIALIZING", "profiles": [], "challenges": []}
        self._refresh_task = None
        self._inspector_task = None

    async def start(self):
        if not self.store.list_scopes():
            from .isolation_migration import import_legacy_state, inspect_legacy_targets

            socket = getattr(self.service.runtime, "supervisor_socket", None)
            observations = (
                await inspect_legacy_targets(self.service.repository, socket) if socket else {}
            )
            for profile in self.service.repository.list_profiles():
                if not profile.challenge_url or not profile.challenge_identity_id:
                    continue
                scope = self.store.maintenance_scope(
                    f"publisher:{profile.challenge_site_id or profile.site_id}"
                )
                if scope:
                    import_legacy_state(
                        self.service.repository,
                        scope.model_copy(
                            update={
                                "primary_identity_id": profile.challenge_identity_id,
                                "fallback_identity_id": None,
                            }
                        ),
                        observations,
                    )
        with self.service.repository._lock:
            rows = self.service.repository._connection.execute(
                "select data_json from maintenance_operations where state in ('QUEUED','RUNNING')"
            ).fetchall()
        for row in rows:
            operation = MaintenanceOperation.model_validate_json(row[0])
            interrupted = operation.model_copy(
                update={
                    "state": "INTERRUPTED",
                    "stage": "INTERRUPTED",
                    "error": "Controller restarted; inspect the preserved session before retrying.",
                    "updated_at": utc_now(),
                }
            )
            self.store.save_operation(interrupted)
            self.operations[interrupted.operation_id] = interrupted
        self._refresh_task = asyncio.create_task(self._refresh_loop(), name="maintenance-inventory")
        from .challenge_inspector import ChallengeInspector

        self._inspector_task = asyncio.create_task(
            ChallengeInspector(self.service).run(), name="challenge-target-inspector"
        )

    async def close(self):
        if self._inspector_task:
            self._inspector_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._inspector_task
        if self._refresh_task:
            self._refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._refresh_task
        for task in self.tasks.values():
            task.cancel()
        if self.tasks:
            await asyncio.wait(tuple(self.tasks.values()), timeout=3)

    async def _refresh_loop(self):
        while True:
            try:
                self.snapshot = await asyncio.to_thread(self._inventory)
                # Terminal receipts remain durable in SQLite, not forever in RAM.
                terminal = [
                    key
                    for key, value in self.operations.items()
                    if value.state not in {"QUEUED", "RUNNING"}
                ]
                for key in terminal[:-128]:
                    self.operations.pop(key, None)
                    self.tasks.pop(key, None)
            except Exception:
                self.snapshot = {
                    **self.snapshot,
                    "stale": True,
                    "reason": "Registry busy; showing the last snapshot.",
                }
            await asyncio.sleep(2)

    def _inventory(self):
        repo = self.service.repository
        profiles = {p.profile_id: p for p in repo.list_profiles()}
        egresses = {e.egress_id: e for e in repo.list_egresses()}
        identities = {i.identity_id: i for _, i in repo.list_active_identities()}
        strategies = {s.site_id: (h, s) for h, s in repo.list_active_strategies()}
        scopes = self.store.list_scopes()
        rows = []
        entries = [
            (
                scope.scope_id,
                scope.owner_site_id,
                [scope.primary_identity_id, scope.fallback_identity_id],
                scope.enabled,
            )
            for scope in scopes
        ]
        if not scopes:
            entries = [
                (
                    f"publisher:{site}",
                    site,
                    [
                        c.identity_id or c.profile_id
                        for c in sorted(spec.access.combinations, key=lambda c: c.priority)
                        if c.enabled
                    ],
                    head.enabled,
                )
                for site, (head, spec) in strategies.items()
                if spec.auth.maintenance_url or spec.auth.login_url
            ]
        for scope_id, site_id, members, enabled in entries:
            entry = strategies.get(site_id)
            if not entry:
                continue
            spec = entry[1]
            runtime = repo.get_runtime(f"scope:{scope_id}" if scopes else site_id)
            for index, member in enumerate(members):
                identity = identities.get(member)
                if not identity:
                    continue
                profile = profiles.get(identity.profile_id)
                egress = egresses.get(identity.egress_id)
                if not profile or not egress:
                    continue
                auth = repo.get_site_identity_auth(site_id, member)
                epoch = repo.get_identity_runtime(member).session_revision
                auth_display = (
                    "REVERIFY_REQUIRED"
                    if auth.auth_state.value == "VALID" and auth.observed_session_revision != epoch
                    else auth.auth_state.value
                )
                health = runtime.combinations.get(member)
                capabilities = health.capabilities if health else {}
                cold_scope = next((s for s in scopes if s.scope_id == scope_id), None)

                displays = {}
                for purpose in (SitePurpose.CRAWLER, SitePurpose.BODY):
                    evidence = capabilities.get(purpose.value, {})
                    value = evidence.get("result", "NOT_TESTED")
                    if (
                        index
                        and evidence
                        and cold_scope
                        and not self.service.failover.usable(cold_scope, member, purpose)
                    ):
                        value = f"{value} (cold-unverified)"
                    displays[purpose] = value

                rows.append(
                    {
                        "row_id": f"{scope_id}:{member}",
                        "scope_id": scope_id,
                        "site_id": site_id,
                        "site_name": spec.display_name or site_id,
                        "site_enabled": enabled,
                        "profile_id": profile.profile_id,
                        "identity_id": member,
                        "runtime_kind": identity.runtime_kind.value,
                        "profile_role": "primary" if index == 0 else "backup",
                        "priority": index,
                        "active": runtime.active_combination_id == member,
                        "crawler_ready": displays[SitePurpose.CRAWLER],
                        "body_ready": displays[SitePurpose.BODY],
                        "combination_id": member,
                        "egress_id": egress.egress_id,
                        "egress_node": egress.node_ref,
                        "egress_country": self.store.egress_region(egress),
                        "egress_enabled": egress.enabled,
                        "egress_status": egress.status,
                        "observed_ip": egress.observed_ip,
                        "observed_at": egress.observed_at,
                        "auth_state": auth_display,
                        "stored_auth_state": auth.auth_state.value,
                        "credential_epoch": epoch,
                        "operational_state": profile.operational_state.value,
                        "verification_configured": bool(spec.auth.verification_url),
                        "verification_kind": spec.auth.verification_kind,
                        "verification_url": spec.auth.verification_url,
                        "challenge_url": safe_display_url(profile.challenge_url)
                        if profile.challenge_url
                        else None,
                        "session_revision": auth.observed_session_revision,
                    }
                )
        return {
            "status": "READY",
            "snapshot_at": utc_now().isoformat(),
            "stale": False,
            "profiles": rows,
            "challenges": [{**t.model_dump(mode="json"),
                            "challenge_type": self.service.runtime.evidence_store.summary(t)}
                           for t in self.store.list_challenges()],
            "vnc_ready": True,
            "sessions": [
                s.model_dump(mode="json", exclude={"control_token"})
                for s in self.store.list_sessions()
            ],
            "active_operations": [
                op.model_dump(mode="json")
                for op in list(self.operations.values())
                if op.state in {"QUEUED", "RUNNING"}
            ],
        }

    def get_operation(self, operation_id):
        return self.operations.get(operation_id) or self.store.get_operation(operation_id)

    def submit(self, command: MaintenanceCommand):
        seconds = (
            45
            if command.action.startswith("VERIFY")
            else (10 if command.action in {"CLOSE_LOGIN", "RELEASE_VIEWER"} else 40)
        )
        operation = self.store.save_operation(
            MaintenanceOperation(
                **command.model_dump(),
                deadline_at=utc_now() + timedelta(seconds=seconds),
            )
        )
        self.operations[operation.operation_id] = operation
        if operation.state == "QUEUED" and operation.operation_id not in self.tasks:
            self.tasks[operation.operation_id] = asyncio.create_task(
                self._run(operation),
                name=f"maintenance:{operation.operation_id}",
            )
        return operation

    def _update(self, operation, **updates):
        operation = operation.model_copy(update={**updates, "updated_at": utc_now()})
        self.store.save_operation(operation)
        self.operations[operation.operation_id] = operation
        return operation

    async def _run(self, operation):
        operation = self._update(operation, state="RUNNING", stage="STARTING_BROWSER")
        action = asyncio.create_task(self._dispatch(operation))
        try:
            remaining = max(0, (operation.deadline_at - utc_now()).total_seconds())
            done, _ = await asyncio.wait({action}, timeout=remaining)
            if not done:
                latest = self.operations.get(operation.operation_id, operation)
                self._update(
                    latest,
                    state="FAILED",
                    stage="FAILED",
                    error=f"Timed out during {latest.stage}; inspect the preserved page.",
                )
                action.cancel()
                # Do not wait indefinitely for Playwright's cancellation acknowledgement.
                await asyncio.wait({action}, timeout=3)
                return
            result = action.result()
            self._update(operation, state="DONE", stage="DONE", result=result)
        except asyncio.CancelledError:
            action.cancel()
            self._update(operation, state="CANCELLED", stage="CANCELLED")
        except Exception as exc:
            lost = "target_lost" in str(exc) or "page is lost" in str(exc)
            from .service import classify_exception

            _, reason = classify_exception(exc)
            latest = self.operations.get(operation.operation_id, operation)
            self._update(
                operation,
                state="FAILED",
                stage="FAILED",
                error=(
                    "Original page is lost. Use Reopen Lost Page."
                    if lost
                    else (
                        str(exc)
                        if str(exc).startswith("Viewer is in use by ")
                        else "An active Challenge page is preserved for this identity. "
                        "Use Open Original / Verify Challenge first."
                        if str(exc) == "active_challenge_use_challenge_maintenance"
                        else "This is a Challenge session. Choose Later to release the Viewer "
                        "without discarding its page."
                        if str(exc) == "Close Login cannot discard a Challenge page"
                        else f"{reason} during {latest.stage}; inspect the preserved session."
                    )
                ),
            )

    async def _login_session_reusable(self, session):
        if session.state not in {"OPEN", "RELEASED"}:
            return False
        target = self.store.get_target(session.allocation_id) if session.allocation_id else None
        if target and target.state in {"LOST", "CLOSED"}:
            return False
        socket = getattr(self.service.runtime, "supervisor_socket", None)
        identity = self.service.repository.get_identity(session.identity_id)
        if socket and identity.runtime_kind.value == "external_chrome" and target:
            from .maintenance_document import original_target_exists
            from .supervisor_client import ChromeSupervisorClient

            return await original_target_exists(
                ChromeSupervisorClient(socket, owner_id="maintenance-presence"),
                identity,
                session,
                target,
            )
        # An incomplete create reply is not proof that its page is gone. Leave
        # its existing ownership/recovery path in charge rather than duplicate it.
        return True

    async def _finish_lost_login(self, session):
        identity = self.service.repository.get_identity(session.identity_id)
        profile = self.service.repository.get_profile(identity.profile_id)
        if profile.maintenance_session_id == session.control_token:
            await self.service.profile_use.end_maintenance(
                profile.profile_id, session.control_token
            )
        current = self.store.get_session(session.session_id)
        self.store.save_session(current.model_copy(update={"state": "CLOSED"}))

    async def _dispatch(self, operation):
        scope = self.store.maintenance_scope(operation.scope_id) if operation.scope_id else None
        identity_id = operation.identity_id or (scope.primary_identity_id if scope else None)
        session = self.store.get_session(operation.session_id) if operation.session_id else None
        if operation.action == "OPEN_LOGIN":
            if (
                session
                and session.kind == "LOGIN"
                and not await self._login_session_reusable(session)
            ):
                scope = scope or self.store.maintenance_scope(session.scope_id)
                identity_id = session.identity_id
                await self._finish_lost_login(session)
                session = None
            if not session and identity_id:
                # Repeated Open Login is a reconnect, not a second Viewer seat.
                for candidate in self.store.list_sessions():
                    if (
                        candidate.kind != "LOGIN"
                        or candidate.state not in {"OPEN", "RELEASED"}
                        or candidate.identity_id != identity_id
                        or candidate.scope_id != operation.scope_id
                    ):
                        continue
                    if await self._login_session_reusable(candidate):
                        session = candidate
                        break
                    await self._finish_lost_login(candidate)
            if session:
                if session.kind != "LOGIN":
                    raise ValueError("Use Open Challenge for a Challenge session")
                session = self.store.save_session(session.model_copy(update={"state": "OPEN"}))
                try:
                    result = await self.service.recover_identity_login(
                        session.site_id,
                        session.identity_id,
                        session.control_token,
                        target_id=session.target_id,
                    )
                except BaseException:
                    current = self.store.get_session(session.session_id)
                    if current.generation == session.generation:
                        self.store.save_session(current.model_copy(update={"state": "RELEASED"}))
                    raise
                return {
                    "session_id": session.session_id,
                    "identity_id": session.identity_id,
                    "generation": session.generation,
                    "stage": "VIEWER_READY",
                    "kind": session.kind,
                    "task_id": session.task_id,
                    "site_id": session.site_id,
                }
            if not scope or identity_id not in (
                scope.primary_identity_id,
                scope.fallback_identity_id,
            ):
                raise ValueError("Select a registered source identity")
            session = self.store.save_session(
                MaintenanceSession(
                    kind="LOGIN",
                    scope_id=scope.scope_id,
                    identity_id=identity_id,
                    site_id=scope.owner_site_id,
                    control_token=uuid4().hex,
                )
            )
            try:
                result = await self.service.open_identity_login(
                    scope.owner_site_id,
                    identity_id,
                    control_token=session.control_token,
                )
            except BaseException:
                identity = self.service.repository.get_identity(identity_id)
                profile = self.service.repository.get_profile(identity.profile_id)
                keep = profile and profile.maintenance_session_id == session.control_token
                session = self.store.get_session(session.session_id)
                self.store.save_session(
                    session.model_copy(
                        update={
                            "state": "RELEASED" if keep else "CLOSED",
                        }
                    )
                )
                raise
            session = self.store.get_session(session.session_id)
            session = self.store.save_session(
                session.model_copy(
                    update={
                        "control_token": result["login_token"],
                        "target_id": result.get("target_id"),
                        "allocation_id": result.get("allocation_id"),
                        "browser_instance_id": result.get("browser_instance_id"),
                        "state": "OPEN" if result.get("viewer_ready", True) else "RELEASED",
                    }
                )
            )
            if not result.get("viewer_ready", True):
                identity = self.service.repository.get_identity(identity_id)
                await self.service.profile_use.release_viewer(
                    identity.profile_id, session.control_token
                )
            return {
                "session_id": session.session_id,
                "identity_id": identity_id,
                "stage": "VIEWER_READY"
                if result.get("viewer_ready", True)
                else "VIEWER_UNAVAILABLE",
                "generation": session.generation,
                "kind": session.kind,
                "task_id": session.task_id,
                "site_id": session.site_id,
            }
        if operation.action == "RELEASE_VIEWER":
            if not session:
                raise KeyError("session")
            self.store.save_session(session.model_copy(update={"state": "RELEASED"}))
            identity = self.service.repository.get_identity(session.identity_id)
            await self.service.profile_use.release_viewer(
                identity.profile_id, session.control_token
            )
            return {"released": True, "page_preserved": True}
        if operation.action == "CLOSE_LOGIN":
            if operation.confirm_discard:
                task = next(
                    (t for t in self.store.list_challenges() if t.task_id == operation.task_id),
                    None,
                )
                if not task:
                    raise ValueError("Select a current Challenge task to discard")
                identity = self.service.repository.get_identity(task.identity_id)
                profile = self.service.repository.get_profile(identity.profile_id)
                session = next(
                    (s for s in self.store.list_sessions() if s.task_id == task.task_id), None
                )
                if not session:
                    session = self.store.save_session(
                        MaintenanceSession(
                            kind="CHALLENGE",
                            scope_id=task.scope_id,
                            site_id=task.site_id,
                            identity_id=task.identity_id,
                            task_id=task.task_id,
                            allocation_id=task.allocation_id,
                            target_id=task.original_target_id,
                            browser_instance_id=task.browser_instance_id,
                            control_token=profile.maintenance_session_id,
                            state="RELEASED",
                        )
                    )
                await self.service.runtime.close_login(session.control_token)
                self.store.save_challenge(
                    task.model_copy(
                        update={
                            "state": "TARGET_LOST",
                            "reason": "explicit_human_page_discard",
                        }
                    )
                )
                self.store.save_session(session.model_copy(update={"state": "CLOSED"}))
                await self.service.profile_use.release_viewer(
                    identity.profile_id, session.control_token
                )
                return {"page_discarded": True, "challenge_still_blocked": True, "released": True}
            task = next(
                (
                    t
                    for t in self.store.list_challenges(include_resolved=True)
                    if session and t.task_id == session.task_id
                ),
                None,
            )
            if not session or (
                session.kind != "LOGIN" and (task is None or task.state != "RESOLVED")
            ):
                raise ValueError("Close Login cannot discard a Challenge page")
            await self.service.runtime.close_login(session.control_token)
            identity = self.service.repository.get_identity(session.identity_id)
            await self.service.profile_use.end_maintenance(
                identity.profile_id, session.control_token
            )
            self.store.save_session(session.model_copy(update={"state": "CLOSED"}))
            return {"session_closed": True}
        if operation.action in {"VERIFY_LOGIN", "VERIFY_CHALLENGE"}:
            if not session:
                raise KeyError("session")
            scope = self.store.maintenance_scope(session.scope_id)

            def stage(value):
                latest = self.operations.get(operation.operation_id, operation)
                self._update(latest, stage=value, stage_started_at=utc_now())

            report = await self.service.verify_scope_session(
                session,
                article_url=operation.verification_url,
                stage=stage,
            )
            spec = self.service.repository.get_strategy(scope.owner_site_id)
            required = spec.auth.body_requirement
            if required == "inherit":
                required = spec.auth.requirement
            if (
                session.kind == "LOGIN"
                and session.identity_id == scope.fallback_identity_id
                and required == "required"
                and report.authentication == "VALID"
                and report.body in {"FULL", "SHORT_FULL"}
            ):
                member = self.service.repository.get_runtime(
                    f"scope:{scope.scope_id}"
                ).combinations.get(session.identity_id)
                samples = (
                    member.capabilities.get("BODY", {}).get("subscription_samples", [])
                    if member
                    else []
                )
                if len(samples) < 2:
                    # A second LOGIN session would advance the credential epoch
                    # and invalidate the first proof. Keep this same session for
                    # the advanced second-article check; never weaken paid proof.
                    report.reason = "second_subscription_article_required"
                    return report.as_dict()
            # A cleared Challenge ends independently of entitlement/parser health.
            completed = report.challenge == "CLEARED" and not report.challenge_task_id and (
                session.kind == "CHALLENGE"
                or report.authentication in {"VALID", "NOT_REQUIRED"}
                or (
                    required != "required"
                    and (report.crawler == "READY" or report.body in {"FULL", "SHORT_FULL"})
                )
            )
            if completed:
                try:
                    stage("CLEANUP")
                    async with asyncio.timeout(10):
                        await self.service.runtime.close_login(session.control_token)
                        identity = self.service.repository.get_identity(session.identity_id)
                        await self.service.profile_use.end_maintenance(
                            identity.profile_id, session.control_token
                        )
                except Exception:
                    report.cleanup_pending = True
                else:
                    current_session = self.store.get_session(session.session_id)
                    self.store.save_session(current_session.model_copy(update={"state": "CLOSED"}))
                    report.session_closed = True
            return report.as_dict()
        if operation.action in {"OPEN_CHALLENGE", "REOPEN_LOST_CHALLENGE"}:
            task = next(
                (t for t in self.store.list_challenges() if t.task_id == operation.task_id), None
            )
            if not task:
                raise KeyError("challenge")
            if operation.action == "REOPEN_LOST_CHALLENGE":
                if task.state != "TARGET_LOST":
                    raise ValueError("Reopen is only available for a confirmed lost target")
                identity = self.service.repository.get_identity(task.identity_id)
                profile = self.service.repository.get_profile(identity.profile_id)
                egress = self.service.repository.get_egress(identity.egress_id)
                spec = self.service.repository.get_strategy(task.site_id)
                replacement_id = "challenge_" + uuid4().hex
                # A replacement re-exercises the failed URL, not an unrelated login
                # landing page. The predecessor records the lost original separately.
                url = task.original_url or spec.auth.maintenance_url or spec.auth.login_url
                from urllib.parse import urlsplit

                task_scope = self.store.maintenance_scope(task.scope_id)
                crawler_url = task_scope.verification_plan.get("crawler", {}).get("url")
                if crawler_url and urlsplit(url).path.rstrip("/") == urlsplit(
                    crawler_url
                ).path.rstrip("/"):
                    # Task display URLs intentionally exclude query strings.
                    # Restore the registered public search query, not a blank search.
                    url = crawler_url
                reserved = self.store.save_session(
                    MaintenanceSession(
                        kind="CHALLENGE",
                        scope_id=task.scope_id,
                        identity_id=task.identity_id,
                        site_id=task.site_id,
                        control_token=replacement_id,
                        task_id=replacement_id,
                    )
                )
                try:
                    result = await self.service.runtime.open_login(
                        profile,
                        egress,
                        url,
                        token=replacement_id,
                        identity=identity,
                        site_id=task.site_id,
                    )
                    reserved = self.store.get_session(reserved.session_id)
                    replacement = self.store.replace_lost_challenge(
                        task,
                        ChallengeTask(
                            task_id=replacement_id,
                            scope_id=task.scope_id,
                            identity_id=task.identity_id,
                            site_id=task.site_id,
                            purpose=task.purpose,
                            operation_id=operation.operation_id,
                            original_url=url,
                            original_target_id=result["target_id"],
                            browser_instance_id=result["browser_instance_id"],
                            allocation_id=result["allocation_id"],
                            reason="explicit_lost_target_reopen",
                        ),
                    )
                    session = self.store.save_session(
                        reserved.model_copy(
                            update={
                                "allocation_id": replacement.allocation_id,
                                "target_id": replacement.original_target_id,
                                "browser_instance_id": replacement.browser_instance_id,
                                "state": "OPEN" if result.get("viewer_ready", True) else "RELEASED",
                            }
                        )
                    )
                    if result.get("viewer_ready", True):
                        await self.service.profile_use.recover_maintenance(
                            identity.profile_id, replacement_id
                        )
                except BaseException:
                    reserved = self.store.get_session(reserved.session_id)
                    if reserved.target_id and reserved.browser_instance_id:
                        current_task = self.store.current_challenge(task.identity_id)
                        if current_task and current_task.task_id == task.task_id:
                            self.store.replace_lost_challenge(
                                task,
                                ChallengeTask(
                                    task_id=replacement_id,
                                    scope_id=task.scope_id,
                                    identity_id=task.identity_id,
                                    site_id=task.site_id,
                                    purpose=task.purpose,
                                    operation_id=operation.operation_id,
                                    original_url=url,
                                    original_target_id=reserved.target_id,
                                    browser_instance_id=reserved.browser_instance_id,
                                    allocation_id=reserved.allocation_id,
                                    reason="interrupted_explicit_reopen",
                                ),
                            )
                    self.store.save_session(
                        reserved.model_copy(
                            update={"state": "RELEASED" if reserved.target_id else "CLOSED"}
                        )
                    )
                    raise
                return {
                    "session_id": session.session_id,
                    "stage": "VIEWER_READY"
                    if result.get("viewer_ready", True)
                    else "VIEWER_UNAVAILABLE",
                    "generation": session.generation,
                    "original_page_restored": False,
                    "replacement_task_id": replacement.task_id,
                    "kind": session.kind,
                    "task_id": replacement.task_id,
                    "identity_id": session.identity_id,
                }
            if task.state == "TARGET_LOST":
                raise ValueError(
                    "Original page is lost. Use Reopen Lost Page to create a linked replacement."
                )
            existing = next(
                (s for s in self.store.list_sessions() if s.task_id == task.task_id), None
            )
            if existing:
                existing = self.store.save_session(existing.model_copy(update={"state": "OPEN"}))
                try:
                    await self.service.recover_identity_login(
                        existing.site_id,
                        existing.identity_id,
                        existing.control_token,
                        target_id=existing.target_id,
                    )
                except BaseException:
                    current = self.store.get_session(existing.session_id)
                    if current.generation == existing.generation:
                        self.store.save_session(current.model_copy(update={"state": "RELEASED"}))
                    raise
                return {
                    "session_id": existing.session_id,
                    "stage": "VIEWER_READY",
                    "generation": existing.generation,
                    "page_preserved": True,
                    "kind": existing.kind,
                    "task_id": existing.task_id,
                    "identity_id": existing.identity_id,
                }
            reserved = self.store.save_session(
                MaintenanceSession(
                    kind="CHALLENGE",
                    scope_id=task.scope_id,
                    site_id=task.site_id,
                    identity_id=task.identity_id,
                    task_id=task.task_id,
                    allocation_id=task.allocation_id,
                    target_id=task.original_target_id,
                    browser_instance_id=task.browser_instance_id,
                    control_token=task.task_id,
                )
            )
            try:
                result = await self.service.recover_identity_login(
                    task.site_id,
                    task.identity_id,
                    task.task_id,
                    target_id=task.original_target_id,
                )
            except BaseException:
                self.store.save_session(reserved.model_copy(update={"state": "RELEASED"}))
                raise
            session = self.store.save_session(
                reserved.model_copy(update={"control_token": result["login_token"]})
            )
            return {
                "session_id": session.session_id,
                "generation": session.generation,
                "stage": "VIEWER_READY",
                "page_preserved": True,
                "kind": session.kind,
                "task_id": session.task_id,
                "identity_id": session.identity_id,
            }
        if operation.action == "ACTIVATE_PRIMARY":
            if not scope:
                raise KeyError("scope")
            from .schema import SitePurpose

            key = f"scope:{scope.scope_id}"
            self.service._switching_scopes.add(key)
            try:
                while self.service._scope_inflight.get(key, 0):
                    await asyncio.sleep(0.1)
                purpose = (
                    SitePurpose.CRAWLER
                    if scope.verification_plan.get("crawler")
                    else SitePurpose.BODY
                )
                return await self.service.failover.activate(scope, purpose, primary=True)
            finally:
                self.service._switching_scopes.discard(key)
        raise ValueError("Unsupported maintenance action")

    def cancel(self, operation_id):
        task = self.tasks.get(operation_id)
        if task and not task.done():
            task.cancel()
        return self.get_operation(operation_id)
