"""Read local DevTools metadata only; no publisher navigation or driver attach."""

import asyncio
import json
import logging
import urllib.request
from types import SimpleNamespace
from urllib.parse import urlsplit

from .governance_repository import GovernanceRepository
from .schema import BrowserRuntimeKind, utc_now
from .supervisor_client import ChromeSupervisorClient


def _target_ids(endpoint):
    parsed = urlsplit(endpoint)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
        raise ValueError("untrusted local browser metadata endpoint")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(endpoint + "/json/list", timeout=1) as response:
        raw = response.read(512 * 1024 + 1)
    if len(raw) > 512 * 1024:
        raise ValueError("browser metadata exceeds limit")
    return {entry["id"] for entry in json.loads(raw) if entry.get("type") == "page"}


class ChallengeInspector:
    def __init__(self, service):
        self.service = service
        self.store = GovernanceRepository(service.repository)

    async def recover_passive_investing(self, task, client):
        """Reconcile only an exact Cloudflare original that now proves its listing.

        No reload, new publisher request, Viewer claim, login, or CAPTCHA action.
        All other publishers/purposes keep the existing human maintenance policy.
        """
        if task.site_id != "investing" or task.purpose != "CRAWLER":
            return False
        if any(
            s.identity_id == task.identity_id and s.state == "OPEN"
            for s in self.store.list_sessions()
        ):
            return False
        evidence = self.service.runtime.evidence_store.for_task(task)
        frames = evidence.get("frames", {}) if evidence else {}
        if not any(
            f.get("classification", {}).get("provider") == "CLOUDFLARE" for f in frames.values()
        ):
            return False
        from .investing_document import listing_ticker
        from .maintenance_document import read_original_document
        from .schema import AccessEvent
        from .verification import VerificationReport

        scope = self.store.get_scope(task.scope_id)
        identity = self.service.repository.get_identity(task.identity_id)
        if not scope or not scope.enabled or not identity:
            return False
        ref = SimpleNamespace(
            target_id=task.original_target_id, browser_instance_id=task.browser_instance_id
        )
        capture = await read_original_document(
            client,
            identity,
            ref,
            self.store.get_target(task.allocation_id),
        )
        if capture.final_url != task.original_url or not listing_ticker(
            capture.body, capture.final_url
        ):
            return False
        report = VerificationReport(
            challenge="CLEARED",
            crawler="READY",
            reason="native_auto_check_completed",
            original_page_kind="listing",
            verification_url=task.original_url,
            runtime_instance_id=capture.provenance.instance_id,
            crawler_reason="exact_original_listing_verified",
        )
        self.store.commit_verification(scope, task.identity_id, report, passive_task=task)
        # The durable proof is committed before releasing the old reservation.
        # Use the existing exact-target close path, not a browser/Profile restart.
        session = next(
            s
            for s in self.store.list_sessions()
            if s.task_id == task.task_id and s.state == "RELEASED"
        )
        profile = self.service.repository.get_profile(identity.profile_id)
        if profile.maintenance_session_id == session.control_token:
            await self.service.profile_use.end_maintenance(
                identity.profile_id, session.control_token
            )
        try:
            await self.service.runtime.close_login(session.control_token)
            session = self.store.get_session(session.session_id)
            self.store.save_session(session.model_copy(update={"state": "CLOSED"}))
        except Exception:
            logging.getLogger(__name__).warning(
                "passive_challenge_cleanup_pending task=%s",
                task.task_id,
            )
        self.service.repository.append_event(
            AccessEvent(
                operation_id=task.operation_id,
                site_id=task.site_id,
                combination_id=task.combination_id,
                category="NATIVE_CHALLENGE_AUTO_CLEARED",
                payload={
                    "task_id": task.task_id,
                    "target_id": task.original_target_id,
                    "instance_id": task.browser_instance_id,
                    "crawler": "READY",
                },
            )
        )
        return True

    async def run_once(self):
        runtime = self.service.runtime
        socket = getattr(runtime, "supervisor_socket", None)
        if not socket:
            return
        client = ChromeSupervisorClient(socket, owner_id=runtime.controller_id)
        slots = asyncio.Semaphore(2)

        async def inspect(task):
            identity = self.service.repository.get_identity(task.identity_id)
            if not identity:
                return
            if identity.runtime_kind is BrowserRuntimeKind.MANAGED_PLAYWRIGHT:
                from .runtime import profile_browser_processes

                profile = self.service.repository.get_profile(identity.profile_id)
                root = self.service.runtime.browser_pool.root
                if not await asyncio.to_thread(
                    profile_browser_processes, root / profile.directory_key
                ):
                    self.store.mark_instance_lost(task.identity_id, task.browser_instance_id)
                return
            try:
                async with slots, asyncio.timeout(3):
                    status = await client.status(task.identity_id)
                    if not status.get("running"):
                        if status.get("known_missing") is False:
                            return
                        lost = True
                    elif status["instance_id"] != task.browser_instance_id:
                        lost = True
                    else:
                        targets = await asyncio.to_thread(_target_ids, status["cdp_http_url"])
                        lost = task.original_target_id not in targets
                    updated = task.model_copy(
                        update={
                            "state": "TARGET_LOST" if lost else task.state,
                            "last_check": utc_now(),
                        }
                    )
                    self.store.save_challenge(updated)
                    if lost:
                        target = self.store.get_target(task.allocation_id)
                        if target:
                            self.store.save_target(target.model_copy(update={"state": "LOST"}))
                if not lost and task.site_id == "investing" and task.purpose == "CRAWLER":
                    latest = self.store.get_challenge(task.task_id)
                    async with slots, asyncio.timeout(15):
                        await self.recover_passive_investing(latest, client)
            except Exception:
                return  # Unreachable is not proof that an original page disappeared.

        await asyncio.gather(
            *(inspect(task) for task in self.store.list_challenges() if task.state != "TARGET_LOST")
        )

    async def run(self):
        while True:
            await self.run_once()
            await asyncio.sleep(60)
