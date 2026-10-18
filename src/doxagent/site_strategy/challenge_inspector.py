"""Read local DevTools metadata only; no publisher navigation or driver attach."""

import asyncio
import json
import urllib.request
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
            except Exception:
                return  # Unreachable is not proof that an original page disappeared.

        await asyncio.gather(
            *(inspect(task) for task in self.store.list_challenges() if task.state != "TARGET_LOST")
        )

    async def run(self):
        while True:
            await self.run_once()
            await asyncio.sleep(60)
