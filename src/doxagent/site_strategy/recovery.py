"""Bounded identity recovery; optional paid DataDome handling remains separately gated."""

import asyncio
import logging
import time
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from .challenge_detection import assess
from .governance_repository import GovernanceRepository, capability_fingerprint
from .schema import (
    AccessEvent,
    AccessRequest,
    BrowserRuntimeKind,
    ChallengePolicy,
    SitePurpose,
    digest_json,
    utc_now,
)
from .verification import VerificationReport, validate_capture, validate_crawler_capture

logger = logging.getLogger(__name__)


def recovery_context(request):
    """Retain a private, replayable business request, never browser credentials."""

    def clean(value):
        if isinstance(value, dict):
            return {
                k: clean(v)
                for k, v in value.items()
                if not k.startswith("_")
                and not any(
                    part in k.casefold()
                    for part in ("password", "cookie", "secret", "token", "authorization")
                )
            }
        return [clean(v) for v in value] if isinstance(value, list) else value

    return {
        "operation_id": request.operation_id,
        "url": request.url,
        "purpose": request.purpose.value,
        "mode": request.mode.value,
        "method": request.method,
        "parameters": clean(request.parameters),
        "recipe_ref": request.recipe_ref,
        "recipe_parameters": clean(request.recipe_parameters),
        "origin_scope_id": request.origin_scope_id,
    }


def context_key(context):
    return digest_json({k: v for k, v in context.items() if k != "operation_id"})


def retained_token(store, recovery):
    session = next(
        (s for s in store.list_sessions() if s.allocation_id == recovery.original_allocation_id),
        None,
    )
    return (
        session.control_token
        if session and session.control_token
        else recovery.challenge_task_id or recovery.recovery_id
    )


def register_login_recovery(service, scope, session, assessment):
    """Retain recovery intent, without stealing the OPEN user's LOGIN target."""
    from .governance_models import BrowserRecovery

    store = GovernanceRepository(service.repository)
    identity = service.repository.get_identity(session.identity_id)
    crawler = scope.verification_plan.get("crawler")
    spec = service.repository.get_strategy(scope.owner_site_id)
    values = (
        {**crawler, "purpose": "CRAWLER", "mode": "BROWSER"}
        if crawler
        else {
            "url": spec.auth.verification_url or spec.auth.login_url,
            "purpose": "BODY",
            "mode": "BROWSER",
        }
    )
    request = AccessRequest(**values, operation_id="login-recovery:" + session.session_id)
    with service.repository.transaction() as connection:
        previous = store.get_recovery(session.identity_id)
        if previous and previous.phase != "RESOLVED":
            return previous
        task = store.current_challenge(session.identity_id)
        value = BrowserRecovery(
            scope_id=scope.scope_id,
            identity_id=session.identity_id,
            site_id=session.site_id,
            original_allocation_id=session.allocation_id,
            original_target_id=session.target_id,
            original_instance_id=session.browser_instance_id,
            original_url=task.original_url if task else spec.auth.login_url,
            challenge_task_id=task.task_id if task else None,
            page_status="PRESENT",
            assessment=assessment.model_dump(mode="json"),
            handling_mode=assessment.handling_mode,
            affected_contexts=[recovery_context(request)],
            next_probe_at=utc_now() + timedelta(seconds=60),
            fingerprint=capability_fingerprint(
                service.repository,
                scope,
                identity,
                service.repository.get_egress(identity.egress_id),
            ),
        )
        if task:
            store._write_task(
                connection, task.model_copy(update={"recovery_id": value.recovery_id})
            )
        store._write_recovery(connection, value)
        return value


def resource_observation(safety):
    """Small telemetry only; CPU load is never evidence of a website challenge."""
    import os
    from pathlib import Path

    value = {
        "safety": safety.level.value,
        "safety_reasons": list(safety.reasons),
        "safety_stale": safety.stale,
        "cpu_count": os.cpu_count(),
    }
    try:
        value["load_average"] = list(os.getloadavg())
    except (AttributeError, OSError):
        pass
    try:
        lines = Path("/sys/fs/cgroup/cpu.stat").read_text().splitlines()
        value["cpu_throttling"] = {
            k: int(v)
            for k, v in (line.split() for line in lines)
            if k in {"nr_throttled", "throttled_usec", "nr_periods"}
        }
    except (OSError, ValueError):
        pass
    return value


async def reuters_login_form(page, credentials):
    """Ordinary form interaction only; no CAPTCHA/MFA, no raw error messages."""
    import re
    from urllib.parse import urlsplit

    def trusted():
        parsed = urlsplit(page.url)
        return parsed.scheme == "https" and parsed.hostname in {
            "www.reuters.com",
            "reuters.com",
            "auth.reuters.com",
        }

    async def blocked():
        if not trusted():
            return "UNTRUSTED_LOGIN_REDIRECT"
        html = await page.content()
        value = assess(html, policy=ChallengePolicy(unknown_handling="human"))
        if value.kind != "NONE":
            return "CHALLENGE_REQUIRED"
        if await page.locator('input[autocomplete="one-time-code"], input[name="otp"]').count():
            return "MFA_REQUIRED"
        return None

    reason = await blocked()
    if reason:
        return {"status": reason}
    email = page.locator('input[type="email"], input[name="email"], input[name="username"]').first
    if not await email.count():
        sign_in = page.get_by_role("link", name=re.compile(r"^sign in$|^log in$", re.I)).first
        if not await sign_in.count():
            return {"status": "LOGIN_FORM_UNAVAILABLE"}
        await sign_in.click(timeout=5000)
        reason = await blocked()
        if reason:
            return {"status": reason}
        email = page.locator(
            'input[type="email"], input[name="email"], input[name="username"]'
        ).first
    if not await email.count() or not trusted():
        return {"status": "LOGIN_FORM_UNAVAILABLE"}
    # A foreign form action must never receive credentials even on an approved page.
    actions = await page.locator("form").evaluate_all("forms => forms.map(f => f.action)")
    if any(
        urlsplit(url).hostname not in {"www.reuters.com", "reuters.com", "auth.reuters.com"}
        for url in actions
        if url
    ):
        return {"status": "UNTRUSTED_LOGIN_FORM"}
    await email.fill(credentials["username"], timeout=5000)
    password = page.locator('input[type="password"]').first
    if not await password.count():
        await page.get_by_role(
            "button", name=re.compile(r"^continue$|^next$|^sign in$", re.I)
        ).first.click(timeout=5000)
        reason = await blocked()
        if reason:
            return {"status": reason}
        password = page.locator('input[type="password"]').first
    if not await password.count() or not trusted():
        return {"status": "LOGIN_FORM_UNAVAILABLE"}
    actions = await page.locator("form").evaluate_all("forms => forms.map(f => f.action)")
    if any(
        urlsplit(url).hostname not in {"www.reuters.com", "reuters.com", "auth.reuters.com"}
        for url in actions
        if url
    ):
        return {"status": "UNTRUSTED_LOGIN_FORM"}
    await password.fill(credentials["password"], timeout=5000)
    await page.get_by_role(
        "button", name=re.compile(r"^sign in$|^log in$|^continue$", re.I)
    ).first.click(timeout=5000)
    for _ in range(12):
        await asyncio.sleep(0.5)
        reason = await blocked()
        if reason:
            return {"status": reason}
        logout = page.get_by_role("link", name=re.compile(r"^sign out$|^log out$", re.I))
        if await logout.count() and await logout.first.is_visible():
            return {"status": "AUTHENTICATED"}
        if not await page.locator('input[type="password"]').count():
            return {"status": "SUBMITTED"}  # Never equate a disappeared form with authenticated.
    return {"status": "LOGIN_NOT_CONFIRMED"}


def recovery_listing_url_matches(scope, request, actual):
    if actual == request.url:
        return True
    if scope.source_id == 'investorshub_ticker_news':
        from urllib.parse import urlsplit
        import re
        expected, final = urlsplit(request.url), urlsplit(actual)
        pattern = r'/stock-market/(NASDAQ|NYSE|AMEX)/([^/]+)/news/?'
        parts = [re.fullmatch(pattern, p.path) for p in (expected, final)]
        ticker = str(request.recipe_parameters.get('ticker') or '').upper()
        return bool(ticker and all(p.scheme == 'https' and p.hostname == 'investorshub.advfn.com'
            and not p.username and p.port in {None, 443} and not p.query and not p.fragment
            for p in (expected, final)) and all(parts) and parts[0][1] == parts[1][1]
            and all(p[2].rsplit('-', 1)[-1].upper() == ticker for p in parts))
    # The trusted Reuters recipe always adds offset=0; old incidents omitted it.
    # Compare the declared query/offset, not arbitrary same-site navigation.
    if scope.source_id != 'reuters_site_search' or request.recipe_ref != 'builtin:reuters_search@1':
        return False
    from urllib.parse import urlsplit, parse_qs
    expected, final = urlsplit(request.url), urlsplit(actual)
    if any(p.scheme != 'https' or p.hostname != 'www.reuters.com' or p.port not in {None, 443}
           or p.username or p.path != '/site-search/' or p.fragment for p in (expected, final)):
        return False
    query = str(request.recipe_parameters.get('query', ''))
    offset = str(request.recipe_parameters.get('offset', 0))
    if not query:
        return False
    wanted = {'query': [query], 'offset': [offset]}
    def canonical(p):
        values = parse_qs(p.query, keep_blank_values=True)
        if offset == '0':
            values.setdefault('offset', ['0'])
        return values
    return canonical(expected) == canonical(final) == wanted


def verify_capture(service, scope, context, capture, *, observed=False):
    """Same publisher and real parser/body proof; absence of CAPTCHA is insufficient."""
    from doxagent.content_enrichment.quality import inspect_html

    resolved = service.resolve(capture.final_url)
    report = VerificationReport(
        runtime_instance_id=capture.provenance.instance_id if capture.provenance else None,
        verification_url=capture.final_url,
    )
    if resolved.site_id != scope.owner_site_id:
        report.reason = "recovery_cross_publisher_redirect"
        return report
    info = inspect_html(capture.body, capture.final_url, "", generic_extraction=False)
    if capture.recovery_id or info.access_reason == "challenge_required":
        report.challenge, report.reason = "BLOCKED", "challenge_required"
        return report
    if context["purpose"] == "CRAWLER":
        request = AccessRequest(**context)
        try:
            crawler_ready = validate_crawler_capture(scope.source_id, capture, request)
        except (RuntimeError, ValueError, KeyError):
            crawler_ready = False
        # A DOM observation has no HTTP status, but must satisfy the actual listing contract.
        if (
            (capture.status_code == 0 and observed or 200 <= capture.status_code < 300)
            and recovery_listing_url_matches(scope, request, capture.final_url)
            and crawler_ready
        ):
            report.challenge, report.crawler = "CLEARED", "READY"
            report.original_page_kind, report.crawler_reason = (
                "listing",
                "listing_contract_verified",
            )
        elif info.access_reason in {"login_required", "subscription_required"}:
            report.challenge = "CLEARED"
            report.authentication = (
                "REAUTH_REQUIRED"
                if info.access_reason == "login_required"
                else "ENTITLEMENT_MISSING"
            )
            report.crawler = "AUTH_REQUIRED"
        else:
            report.crawler, report.reason = "CONTENT_ERROR", "crawler_contract_not_satisfied"
    else:
        report = validate_capture(capture, resolved, observed_document=observed)
        report.verification_url = capture.final_url
        report.runtime_instance_id = capture.provenance.instance_id if capture.provenance else None
        if (
            report.body not in {"FULL", "SHORT_FULL", "ENTITLEMENT_MISSING"}
            and report.authentication != "REAUTH_REQUIRED"
        ):
            report.challenge = "UNKNOWN"  # An app shell is not a body-recovery proof.
    return report


class BrowserRecoveryCoordinator:
    def __init__(self, service):
        self.service = service
        self.store = GovernanceRepository(service.repository)
        self.owner = "recovery-owner:" + uuid4().hex
        self.slots = asyncio.Semaphore(2)
        self.tasks = {}
        self._last_legacy_scan = 0.0
        self._legacy_migration_enabled = False
        from .datadome_solver import DataDomeSolver
        self.datadome_solver = DataDomeSolver(self)

    @staticmethod
    def now():
        return utc_now()

    def event(self, recovery, category, **payload):
        self.service.repository.append_event(
            AccessEvent(
                operation_id=recovery.recovery_id,
                site_id=recovery.site_id,
                category=category,
                payload={
                    "recovery_id": recovery.recovery_id,
                    "identity_id": recovery.identity_id,
                    "generation": recovery.generation,
                    **payload,
                },
            )
        )

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run_once(self):
        if time.monotonic() - self._last_legacy_scan >= 60:
            # An OPEN user session is deferred by migration, never stolen. Once
            # released, the same bounded scan brings it under autonomous recovery.
            with self.service.repository._lock:
                self._legacy_migration_enabled = bool(
                    self.service.repository._connection.execute(
                        "select 1 from site_recovery_migrations where name='challenge-autorecovery-v1'"
                    ).fetchone()
                )
            if self._legacy_migration_enabled:
                await asyncio.to_thread(
                    migrate_recoveries, self.service, apply=True, update_policies=False
                )
            self._last_legacy_scan = time.monotonic()
        # Claim only once a slot is available; queued leases must not expire in RAM.
        for recovery in self.store.list_recoveries(active_only=False):
            if recovery.phase == "RESOLVED" and not recovery.cleanup_pending:
                continue
            if recovery.identity_id in self.tasks or recovery.next_due_at > utc_now():
                continue
            if len(self.tasks) >= 2:
                break
            work = (
                self._cleanup(recovery)
                if recovery.phase == "RESOLVED"
                else self._run(recovery.identity_id)
            )
            task = asyncio.create_task(work, name="browser-recovery:" + recovery.identity_id)
            self.tasks[recovery.identity_id] = task
            task.add_done_callback(lambda t, key=recovery.identity_id: self._finished(key, t))

    def _finished(self, key, task):
        self.tasks.pop(key, None)
        if not task.cancelled() and task.exception():
            logger.error(
                "recovery iteration failed identity=%s error=%s",
                key,
                type(task.exception()).__name__,
            )

    async def _observe(self, recovery, identity):
        target = self.store.get_target(recovery.original_allocation_id or "")
        if not target or target.state in {"CLOSED", "LOST"}:
            raise RuntimeError("original_challenge_target_lost")
        if identity.runtime_kind is BrowserRuntimeKind.EXTERNAL_CHROME:
            from .maintenance_document import read_original_document
            from .supervisor_client import ChromeSupervisorClient

            client = ChromeSupervisorClient(
                self.service.runtime.supervisor_socket, owner_id=self.service.runtime.controller_id
            )
            ref = SimpleNamespace(
                target_id=target.target_id, browser_instance_id=target.browser_instance_id
            )
            return await read_original_document(client, identity, ref, target)
        return await self.service.runtime.observe_recovery(recovery, identity)

    async def _close_page(self, recovery, identity):
        target = self.store.get_target(recovery.original_allocation_id or "")
        if not target or target.state in {"CLOSED", "LOST"}:
            return
        if any(
            s.identity_id == identity.identity_id and s.state == "OPEN"
            for s in self.store.list_sessions()
        ):
            return
        task = self.store.current_challenge(identity.identity_id)
        if task and task.allocation_id == target.allocation_id:
            return  # A different capability's live human original is still needed.
        await self.service.runtime.close_recovery(recovery, identity)

    async def _cleanup(self, recovery):
        identity = self.service.repository.get_identity(recovery.identity_id)
        try:
            async with self.slots, asyncio.timeout(10):
                await self._close_page(recovery, identity)
            target = self.store.get_target(recovery.original_allocation_id or "")
            done = target is None or target.state in {"CLOSED", "LOST"}
        except Exception:
            done = False
        current = self.store.get_recovery(recovery.identity_id)
        if current and current.recovery_id == recovery.recovery_id and current.phase == "RESOLVED":
            self.store.save_recovery(
                current.model_copy(
                    update={
                        "cleanup_pending": not done,
                        "next_due_at": utc_now() + timedelta(seconds=60),
                    }
                ),
                expected_generation=current.generation,
            )

    async def _run(self, identity_id):
        async with self.slots:
            claimed = self.store.claim_recovery(identity_id, owner=self.owner)
            if claimed is None:
                return
            recovery = claimed
            started = time.monotonic()
            try:
                async with asyncio.timeout(90):
                    scope = self.store.maintenance_scope(recovery.scope_id)
                    identity = self.service.repository.get_identity(identity_id)
                    spec = self.service.repository.get_strategy(recovery.site_id)
                    egress = self.service.repository.get_egress(identity.egress_id)
                    fingerprint = capability_fingerprint(
                        self.service.repository, scope, identity, egress
                    )
                    configuration_changed = fingerprint != recovery.fingerprint
                    if configuration_changed:
                        # New config requires a new probe, not acceptance of an old observation.
                        recovery = recovery.model_copy(
                            update={
                                "fingerprint": fingerprint,
                                "page_status": "UNKNOWN",
                                "next_probe_at": utc_now(),
                            }
                        )
                        self.store.save_recovery(recovery, expected_generation=claimed.generation)
                    context = next((c for c in recovery.affected_contexts if c['purpose'] == 'CRAWLER'),
                        recovery.affected_contexts[0] if recovery.affected_contexts else None)
                    if context is None:
                        await self._defer(recovery, "recovery_request_unavailable", delay=600)
                        return
                    if await self.datadome_solver.tick(recovery, scope, identity, egress):
                        self.service.failover.schedule(scope, SitePurpose(context['purpose']))
                        recovery = recovery.model_copy(update={"next_probe_at": utc_now()})
                        await self._defer(recovery, "datadome_solver_pending", delay=15)
                        return
                    if (
                        scope.source_id == "ctee_semiconductor"
                        and context["purpose"] == "CRAWLER"
                        and "/api/category/semi/" not in context["url"]
                    ):
                        # Old fallback checks used the category HTML rather than
                        # the API actually consumed by the collector.
                        context = {**context, "url": "https://www.ctee.com.tw/api/category/semi/1"}
                        recovery = recovery.model_copy(
                            update={"affected_contexts": [context, *recovery.affected_contexts[1:]]}
                        )
                        self.store.save_recovery(recovery, expected_generation=claimed.generation)
                    capture = None
                    if recovery.page_status != "LOST" and not configuration_changed:
                        try:
                            async with asyncio.timeout(12):
                                capture = await self._observe(recovery, identity)
                        except Exception as exc:
                            reason = str(exc)
                            if reason == "original_challenge_target_lost":
                                target = self.store.get_target(
                                    recovery.original_allocation_id or ""
                                )
                                if target and target.state not in {"LOST", "CLOSED"}:
                                    self.store.save_target(
                                        target.model_copy(
                                            update={"state": "LOST", "updated_at": utc_now()}
                                        )
                                    )
                                task = self.store.current_challenge(identity_id)
                                if (
                                    task
                                    and task.allocation_id == recovery.original_allocation_id
                                    and task.state != "TARGET_LOST"
                                ):
                                    self.store.save_challenge(
                                        task.model_copy(update={"state": "TARGET_LOST"})
                                    )
                                recovery = recovery.model_copy(
                                    update={
                                        "page_status": "LOST",
                                        "next_probe_at": min(
                                            recovery.next_probe_at or utc_now(),
                                            utc_now() + timedelta(seconds=60),
                                        ),
                                    }
                                )
                                self.event(recovery, "TARGET_LOST", reason="exact_original_absent")
                            else:
                                recovery = recovery.model_copy(update={"page_status": "UNKNOWN"})
                    if capture is not None:
                        report = verify_capture(
                            self.service, scope, context, capture, observed=True
                        )
                        if report.challenge == "CLEARED":
                            await self._commit(
                                recovery, report, context, "original_document", identity
                            )
                            return
                        recovery = self._classify(recovery, capture, spec, report)
                    open_writer = any(
                        s.identity_id == identity_id and s.state == "OPEN"
                        for s in self.store.list_sessions()
                    )
                    age = (utc_now() - recovery.opened_at).total_seconds()
                    if (
                        not open_writer
                        and recovery.next_probe_at
                        and recovery.next_probe_at <= utc_now()
                    ):
                        safety = self.service.runtime.browser_pool._safety.read()
                        if safety.level.value == "CRITICAL":
                            await self._defer(recovery, "RESOURCE_DEFERRED", delay=60)
                            return
                        self.event(
                            recovery,
                            "PROBE_STARTED",
                            purpose=context["purpose"],
                            queue_ms=max(
                                0, int((utc_now() - recovery.next_due_at).total_seconds() * 1000)
                            ),
                            resources=resource_observation(safety),
                        )
                        recovery = recovery.model_copy(
                            update={
                                "attempt_count": recovery.attempt_count + 1,
                            }
                        )
                        self.store.save_recovery(recovery, expected_generation=claimed.generation)
                        parameters = {
                            **context.get("recipe_parameters", {}),
                            "_retain_recovery_probe": True,
                        }
                        request = AccessRequest(
                            **{
                                **context,
                                "operation_id": recovery.recovery_id,
                                "origin_scope_id": recovery.scope_id,
                                "scope_revision": scope.revision,
                                "recipe_parameters": parameters,
                            },
                            remaining_budget_ms=75000,
                        )
                        from .verification import body_verification_url, crawler_capture_items

                        discovery = bool(
                            context["purpose"] == "BODY"
                            and scope.verification_plan.get("crawler")
                            and (body_verification_url(
                                scope, context["url"], excluded_urls=(spec.auth.login_url,)
                            )
                            is None or (recovery.manual_review_reason and not context.get('recipe_parameters', {}).get('_recovery_sample_selected')))
                        )
                        if discovery:
                            request = AccessRequest(
                                **scope.verification_plan["crawler"],
                                purpose="CRAWLER",
                                mode="BROWSER",
                                operation_id=recovery.recovery_id,
                                origin_scope_id=recovery.scope_id,
                                scope_revision=scope.revision,
                                remaining_budget_ms=75000,
                            )
                            request = request.model_copy(update={"recipe_parameters": {
                                **request.recipe_parameters, "_retain_recovery_probe": True}})
                        previous_page = recovery
                        async with self.service.profile_use.recovery(
                            identity, recovery.recovery_id, recovery.generation
                        ):
                            capture = await self.service.runtime.probe_recovery(
                                request, identity, egress
                            )
                        latest = self.store.get_recovery(identity_id)
                        if not latest or latest.generation != recovery.generation:
                            return
                        # A lost original may have been replaced atomically by this probe.
                        recovery = latest.model_copy(update={"last_real_probe_at": utc_now()})
                        self.store.save_recovery(recovery, expected_generation=claimed.generation)
                        if (
                            discovery
                            and verify_capture(
                                self.service,
                                scope,
                                recovery_context(request),
                                capture,
                                observed=capture.reason == "observed_document_after_verification",
                            ).crawler
                            == "READY"
                        ):
                            from urllib.parse import urljoin

                            articles = []
                            for row in (
                                crawler_capture_items(scope.source_id, capture, request) or []
                            ):
                                raw = row.get("url") if isinstance(row, dict) else row.url
                                if raw:
                                    url = urljoin(request.url, str(raw))
                                    if self.service.resolve(url).site_id == scope.owner_site_id:
                                        articles.append(url)
                            article = body_verification_url(
                                scope, *articles, excluded_urls=(spec.auth.login_url, context['url'])
                            )
                            if article:
                                context = {
                                    **context,
                                    "url": article,
                                    "recipe_ref": None,
                                    "recipe_parameters": {'_recovery_sample_selected': True},
                                }
                                recovery = recovery.model_copy(
                                    update={
                                        "affected_contexts": [
                                            context,
                                            *recovery.affected_contexts[1:],
                                        ],
                                        "next_probe_at": utc_now() + timedelta(seconds=60),
                                    }
                                )
                                self.event(recovery, "BODY_VERIFICATION_URL_SELECTED")
                                await self._defer(
                                    recovery, "body_article_selected_pending_proof", delay=60
                                )
                            else:
                                recovery = recovery.model_copy(
                                    update={"next_probe_at": utc_now() + timedelta(seconds=600)}
                                )
                                await self._defer(
                                    recovery, "verification_article_unavailable", delay=60
                                )
                            return  # Listing proof must never certify a body.
                        if recovery.original_allocation_id != previous_page.original_allocation_id:
                            try:
                                await self._close_page(previous_page, identity)
                            except Exception:
                                pass  # Exact orphan reconciliation retries on a later attach.
                        # A genuine new runtime can replace a lost one; old generations cannot.
                        report = verify_capture(
                            self.service,
                            scope,
                            context,
                            capture,
                            observed=capture.reason == "observed_document_after_verification",
                        )
                        self.event(
                            recovery,
                            "PROBE_FINISHED",
                            http_status=capture.status_code,
                            capture_reason=capture.reason,
                            html_bytes=len((capture.body or '').encode('utf-8')),
                            retained=bool(capture.recovery_id),
                            recipe_rows=len(capture.recipe_result.get('rows', [])) if capture.recipe_result else None,
                            purpose=context["purpose"],
                            result=report.crawler
                            if context["purpose"] == "CRAWLER"
                            else report.body,
                            elapsed_ms=int((time.monotonic() - started) * 1000),
                        )
                        if report.challenge == "CLEARED":
                            await self._commit(recovery, report, context, "fresh_probe", identity)
                            return
                        recovery = self._classify(recovery, capture, spec, report)
                        delay = (
                            600
                            if recovery.handling_mode == "HUMAN"
                            else (60, 120, 300, 600)[min(recovery.attempt_count - 1, 3)]
                        )
                        recovery = recovery.model_copy(
                            update={"next_probe_at": utc_now() + timedelta(seconds=delay)}
                        )
                    original = self.store.get_target(recovery.original_allocation_id or '')
                    page_age = (utc_now() - original.created_at).total_seconds() if original else age
                    if page_age >= 900 and recovery.handling_mode == "AUTO" and not open_writer:
                        await self._close_page(recovery, identity)
                        recovery = recovery.model_copy(update={"page_status": "LOST"})
                    if age >= spec.challenge.automatic_wait_seconds:
                        self.service.failover.schedule(scope, SitePurpose(context["purpose"]))
                    await self._defer(
                        recovery,
                        "user_maintenance" if open_writer else recovery.manual_review_reason or "verification_pending",
                        delay=2
                        if recovery.handling_mode == "AUTO"
                        and age < spec.challenge.automatic_wait_seconds
                        else 60,
                    )
            except asyncio.CancelledError:
                await self._defer(recovery, "controller_shutdown", delay=5)
                raise
            except Exception as exc:
                # No exception contains credentials/HTML/URLs in persisted diagnostics.
                delay = (
                    600
                    if recovery.handling_mode == "HUMAN"
                    else (60, 120, 300, 600)[min(max(recovery.attempt_count - 1, 0), 3)]
                )
                recovery = recovery.model_copy(
                    update={"next_probe_at": utc_now() + timedelta(seconds=delay)}
                )
                await self._defer(
                    recovery, getattr(exc, "reason_code", None) or type(exc).__name__, delay=60
                )

    def _classify(self, recovery, capture, spec, report):
        from .challenge_detection import VerificationAssessment
        data = capture.assessment or {}
        assessment = (VerificationAssessment.model_validate(data)
            if data.get('kind') in {'AUTO_CHECK', 'INTERACTIVE', 'ACCESS_BLOCK'} else assess(
                capture.body, status=capture.status_code, headers=capture.headers,
                policy=spec.challenge, blocked=report.challenge == 'BLOCKED'))
        reason = recovery.manual_review_reason
        if assessment.kind == 'ACCESS_BLOCK':
            reason = assessment.policy_reason
        elif assessment.handling_mode == 'AUTO' and (
            utc_now() - recovery.opened_at).total_seconds() >= max(180, min(300, spec.challenge.automatic_wait_seconds * 3)):
            reason = reason or ('recovery_contract_not_satisfied' if assessment.kind == 'NONE' else 'automatic_verification_stalled')
        mode = 'HUMAN' if reason else assessment.handling_mode
        value = recovery.model_copy(update={'assessment':assessment.model_dump(mode='json'),
            'handling_mode':mode, 'manual_review_reason':reason,
            'manual_review_at': (recovery.manual_review_at or utc_now()) if reason else None})
        if (recovery.assessment.get('kind'), recovery.handling_mode, recovery.manual_review_reason) != (assessment.kind, mode, reason):
            self.event(value, 'CLASSIFICATION_CHANGED', kind=assessment.kind, handling_mode=mode, reason=reason)
        return self.store.save_recovery(value, expected_generation=recovery.generation)

    async def _defer(self, recovery, reason, *, delay):
        current = self.store.get_recovery(recovery.identity_id)
        if not current or current.generation != recovery.generation or current.phase == "RESOLVED":
            return
        value = recovery.model_copy(
            update={
                "phase": "BACKOFF",
                "next_due_at": utc_now() + timedelta(seconds=delay),
                "last_error": str(reason)[:160],
                "lease_owner": None,
                "lease_until": None,
                "updated_at": utc_now(),
            }
        )
        self.store.save_recovery(value, expected_generation=recovery.generation)

    async def _commit(self, recovery, report, context, resolved_by, identity):
        spec = self.service.repository.get_strategy(recovery.site_id)
        scope = self.store.maintenance_scope(recovery.scope_id)
        if (
            recovery.site_id == "reuters"
            and spec.auth.auto_login
            and report.authentication == "REAUTH_REQUIRED"
        ):
            credential_id = (
                identity.credential_ref
                or self.service.repository.get_profile(identity.profile_id).credential_ref
            )
            credentials = getattr(self.service, "credential_store", None)
            if (
                credential_id
                and credentials
                and credentials.status(credential_id)["available"]
                and self.store.claim_auto_login(credential_id)
            ):
                async with self.service.profile_use.recovery(
                    identity, recovery.recovery_id, recovery.generation
                ):
                    outcome = await self.service.runtime.auto_login_reuters(
                        identity,
                        self.service.repository.get_egress(identity.egress_id),
                        credentials.get(credential_id),
                        AccessRequest(
                            operation_id=recovery.recovery_id,
                            url=spec.auth.login_url or context["url"],
                            origin_scope_id=recovery.scope_id,
                            scope_revision=scope.revision,
                            purpose="CRAWLER",
                            mode="BROWSER",
                        ),
                    )
                status = outcome.get("status", "LOGIN_ATTEMPT_FAILED")
                self.store.finish_auto_login(credential_id, status)
                self.event(recovery, "AUTO_LOGIN_RESULT", result=status)
                # Neither a submitted form nor a login label replaces business verification.
                # Stop on CAPTCHA/MFA; a later due probe provides independent proof.
                latest = self.store.get_recovery(identity.identity_id)
                await self._defer(
                    latest.model_copy(update={"next_probe_at": utc_now() + timedelta(seconds=60)}),
                    status,
                    delay=60,
                )
                return
        value = self.store.finish_recovery(
            recovery, report, context=context, resolved_by=resolved_by
        )
        await self.service.profile_use.release_recovered(identity)
        self.service._cache.clear()
        self.service._cache_bytes = 0
        self.event(
            value,
            "RECOVERY_RESOLVED" if value.phase == "RESOLVED" else "CAPABILITY_RECOVERED",
            purpose=context["purpose"],
            resolved_by=resolved_by,
            crawler=report.crawler,
            body=report.body,
            authentication=report.authentication,
        )
        try:
            await self._close_page(recovery, identity)
            target = self.store.get_target(value.original_allocation_id or "")
            if value.phase == "RESOLVED" and target and target.state not in {"CLOSED", "LOST"}:
                self.store.save_recovery(
                    value.model_copy(update={"cleanup_pending": True}),
                    expected_generation=value.generation,
                )
        except Exception:
            current = self.store.get_recovery(value.identity_id)
            self.store.save_recovery(
                current.model_copy(update={"cleanup_pending": True}),
                expected_generation=current.generation,
            )


def migrate_recoveries(service, *, apply=False, update_policies=True):
    """Explicit, idempotent conversion. No browsing and no changes to identities/cookies."""
    from html import escape
    from .governance_models import BrowserRecovery
    from .schema import CombinationRuntime, ProfileOperationalState

    store = GovernanceRepository(service.repository)
    policy_changes = []
    scope_changes = []
    legacy_auth = (
        [
            value
            for value in service.repository.list_site_identity_auth()
            if value.verified_at
            and value.last_check_at is None
            and value.last_check_reason != "legacy_confirmation_time_uncertain"
        ]
        if update_policies
        else []
    )
    if apply:
        for value in legacy_auth:
            service.repository.save_site_identity_auth(
                value.model_copy(update={"last_check_reason": "legacy_confirmation_time_uncertain"})
            )
    ctee = store.get_scope("source:ctee_semiconductor")
    if (
        update_policies
        and ctee
        and (ctee.verification_plan.get("crawler") or {}).get("url")
        != "https://www.ctee.com.tw/api/category/semi/1"
    ):
        scope_changes.append(ctee.scope_id)
        if apply:
            store.apply_scope(
                ctee.model_copy(
                    update={
                        "verification_plan": {
                            **ctee.verification_plan,
                            "crawler": {"url": "https://www.ctee.com.tw/api/category/semi/1"},
                        }
                    }
                ),
                expected_revision=ctee.revision,
            )
    for head, spec in service.repository.list_active_strategies() if update_policies else []:
        expected = "human" if spec.site_id == "reuters" else "auto"
        if spec.challenge.unknown_handling != expected:
            policy_changes.append(spec.site_id)
            if apply:
                service.apply_strategy(
                    spec.model_copy(
                        update={
                            "challenge": spec.challenge.model_copy(
                                update={"unknown_handling": expected}
                            )
                        }
                    ),
                    expected_revision=spec.revision,
                    actor="challenge-recovery-migration",
                    enabled=head.enabled,
                )
    result = []
    for task in store.list_challenges():
        existing = store.get_recovery(task.identity_id)
        if existing and existing.phase != "RESOLVED":
            continue
        scope = store.maintenance_scope(task.scope_id)
        identity = service.repository.get_identity(task.identity_id)
        if not scope or not identity:
            result.append(
                {"task_id": task.task_id, "action": "SKIP", "reason": "scope_or_identity_missing"}
            )
            continue
        if any(
            s.identity_id == task.identity_id and s.state == "OPEN" for s in store.list_sessions()
        ):
            result.append(
                {"task_id": task.task_id, "action": "DEFER", "reason": "user_maintenance"}
            )
            continue
        spec = service.repository.get_strategy(task.site_id)
        evidence = service.runtime.evidence_store.for_task(task) or {}
        frames = evidence.get("frames", {})
        prior = (frames.get("confirmed") or frames.get("first") or {}).get("classification", {})
        markup = (
            "<title>"
            + escape(prior.get("title", ""))
            + "</title>"
            + escape(prior.get("visible_excerpt", ""))
        )
        markup += "".join(
            '<iframe src="' + escape(url, quote=True) + '"></iframe>'
            for url in prior.get("iframe_urls", [])
        )
        policy = spec.challenge.model_copy(
            update={"unknown_handling": "human" if task.site_id == "reuters" else "auto"}
        )
        assessment = assess(markup, headers=prior.get("headers", {}), blocked=True, policy=policy)
        crawler = scope.verification_plan.get("crawler") or {}
        if task.purpose in {"CRAWLER", "LOGIN"} and crawler:
            values = {**crawler, "url": task.original_url or crawler["url"]}
            # Search query strings were historically redacted. Reconstruct from approved canary.
            if task.site_id == "reuters" or task.purpose == "LOGIN":
                values = crawler
            request = AccessRequest(
                **values, operation_id="migrate:" + task.task_id, purpose="CRAWLER", mode="BROWSER"
            )
        else:
            request = AccessRequest(
                operation_id="migrate:" + task.task_id,
                purpose="BODY" if task.purpose == "BODY" else "CRAWLER",
                mode="BROWSER",
                url=task.original_url,
            )
        row = {
            "task_id": task.task_id,
            "identity_id": task.identity_id,
            "action": assessment.handling_mode,
            "page_status": "LOST" if task.state == "TARGET_LOST" else "UNKNOWN",
            "resources_changed": False,
        }
        result.append(row)
        if not apply:
            continue
        now = utc_now()
        recovery = BrowserRecovery(
            identity_id=task.identity_id,
            scope_id=scope.scope_id,
            site_id=task.site_id,
            handling_mode=assessment.handling_mode,
            assessment=assessment.model_dump(mode="json"),
            original_allocation_id=task.allocation_id,
            original_target_id=task.original_target_id,
            original_instance_id=task.browser_instance_id,
            page_status=row["page_status"],
            challenge_task_id=task.task_id if assessment.handling_mode == "HUMAN" else None,
            affected_contexts=[recovery_context(request)],
            next_probe_at=now + timedelta(seconds=60 if task.state == "TARGET_LOST" else 300),
            fingerprint=capability_fingerprint(
                service.repository,
                scope,
                identity,
                service.repository.get_egress(identity.egress_id),
            ),
        )
        with service.repository.transaction() as connection:
            existing = store.get_recovery(task.identity_id)
            if existing and existing.phase != "RESOLVED":
                continue
            if assessment.handling_mode == "AUTO":
                store._write_task(
                    connection,
                    task.model_copy(
                        update={
                            "state": "RESOLVED",
                            "resolved_at": now,
                            "resolution_reason": "RECLASSIFIED_AUTO_CHECK",
                            "resolved_by": "migration",
                            "recovery_id": recovery.recovery_id,
                        }
                    ),
                )
                target = store.get_target(task.allocation_id)
                if target and target.owner_kind == "CHALLENGE":
                    store._write_target(
                        connection,
                        target.model_copy(
                            update={"owner_kind": "RECOVERY", "owner_ref": recovery.recovery_id}
                        ),
                    )
            else:
                store._write_task(
                    connection, task.model_copy(update={"recovery_id": recovery.recovery_id})
                )
            store._write_recovery(connection, recovery)
            key = scope.verification_plan.get("legacy_runtime_key", f"scope:{scope.scope_id}")
            state = service.repository.get_runtime(key)
            member = state.combinations.setdefault(task.identity_id, CombinationRuntime())
            member.manual_attention_required = assessment.handling_mode == "HUMAN"
            state.generation += 1
            connection.execute(
                "insert into site_runtime values(?,?,?,?) on conflict(runtime_key) do update set "
                "data_json=excluded.data_json,generation=excluded.generation,updated_at=excluded.updated_at",
                (key, state.model_dump_json(), state.generation, now.isoformat()),
            )
            profile = service.repository.get_profile(identity.profile_id)
            if profile and profile.maintenance_session_id and assessment.handling_mode == "AUTO":
                valid_tokens = {
                    task.task_id,
                    *(
                        s.control_token
                        for s in store.list_sessions()
                        if s.task_id == task.task_id and s.state != "OPEN"
                    ),
                }
                if profile.maintenance_session_id in valid_tokens:
                    profile = profile.model_copy(
                        update={
                            "operational_state": ProfileOperationalState.AVAILABLE,
                            "maintenance_session_id": None,
                            "challenge_url": None,
                            "challenge_target_id": None,
                        }
                    )
                    connection.execute(
                        "update browser_profiles set data_json=?,updated_at=? where profile_id=?",
                        (profile.model_dump_json(), now.isoformat(), profile.profile_id),
                    )
    if apply and update_policies:
        with service.repository.transaction() as connection:
            connection.execute(
                "insert or ignore into site_recovery_migrations values(?,?)",
                ("challenge-autorecovery-v1", utc_now().isoformat()),
            )
        service.repository.append_event(
            AccessEvent(
                operation_id="challenge-recovery-migration",
                site_id="registry",
                category="RECOVERY_MIGRATION",
                payload={"records": len(result), "policies": policy_changes},
            )
        )
    return {
        "applied": apply,
        "policies_updated": policy_changes,
        "scope_contracts_updated": scope_changes,
        "legacy_auth_times_marked_uncertain": len(legacy_auth),
        "records": result,
        "profiles_recreated": 0,
        "egresses_changed": 0,
    }


def rollback_export(service):
    """Produce an old-schema projection; never apply it to the live database."""
    from .schema import CombinationRuntime

    store = GovernanceRepository(service.repository)
    if any(r.lease_until and r.lease_until > utc_now() for r in store.list_recoveries()):
        raise RuntimeError("recovery_leases_not_drained")
    tables = {}
    remove = {
        "challenge_tasks": {"recovery_id", "resolution_reason", "resolved_by"},
        "site_identity_auth": {"last_check_at", "last_check_result", "last_check_reason"},
    }
    with service.repository._lock:
        for table, fields in remove.items():
            rows = service.repository._connection.execute(
                f"select rowid,data_json from {table}"
            ).fetchall()
            tables[table] = [
                {
                    "rowid": r[0],
                    "data": {
                        k: v for k, v in __import__("json").loads(r[1]).items() if k not in fields
                    },
                }
                for r in rows
            ]
        rows = service.repository._connection.execute(
            "select site_id,revision,spec_json from site_strategy_revisions"
        ).fetchall()
        tables["site_strategy_revisions"] = [
            {
                "site_id": r[0],
                "revision": r[1],
                "spec": {
                    k: v for k, v in __import__("json").loads(r[2]).items() if k != "challenge"
                },
            }
            for r in rows
        ]
        for row in tables["site_strategy_revisions"]:
            row["spec"].get("auth", {}).pop("auto_login", None)
        rows = service.repository._connection.execute(
            "select data_json from owned_browser_targets"
        ).fetchall()
        targets = [__import__("json").loads(r[0]) for r in rows]
        for target in targets:
            if target["owner_kind"] == "RECOVERY":
                target["owner_kind"] = (
                    "CHALLENGE"  # Never return retained pages to business cleanup.
                )
        tables["owned_browser_targets"] = targets
    runtimes = []
    for recovery in store.list_recoveries():
        scope = store.maintenance_scope(recovery.scope_id)
        key = scope.verification_plan.get("legacy_runtime_key", f"scope:{scope.scope_id}")
        state = service.repository.get_runtime(key)
        member = state.combinations.setdefault(recovery.identity_id, CombinationRuntime())
        member.manual_attention_required = True
        member.state = "COOLDOWN"
        runtimes.append(state.model_dump(mode="json"))
    return {
        "projection_only": True,
        "tables": tables,
        "site_runtime": runtimes,
        "operator_action": "Stop new recovery claims; validate and apply projection to a DB copy before old-image rollback.",
    }
