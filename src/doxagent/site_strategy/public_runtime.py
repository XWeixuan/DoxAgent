"""Public publisher browsers exist only while real work is in flight.

Protected identities retain their existing independent worker/driver. Public
contexts share an anonymous browser per environment and exit with the burst.
"""

import asyncio
import json
import logging
import os
import signal

from .browser_runtime import BrowserRuntimeManager, RuntimeProvenance
from .runtime import SiteAccessRuntime
from .schema import BrowserProfile, BrowserRuntimeKind, digest_json


class _ContextLease:
    def __init__(self, adapter, entry, context, page, identity, allocation):
        self.adapter, self.entry, self.context = adapter, entry, context
        self.page, self.browser_cdp = page, entry.cdp
        self.allocation = allocation
        self.provenance = RuntimeProvenance(
            identity.identity_id,
            identity.revision,
            BrowserRuntimeKind.MANAGED_PLAYWRIGHT,
            "public:" + entry.instance_id,
            entry.egress.generation,
        )
        self.closed = False

    async def __aenter__(self):
        return self.page

    async def __aexit__(self, *_):
        if self.closed:
            return
        owner = self.adapter.pool.target_owner
        await owner.event("state", {"allocation_id": self.allocation, "state": "CLOSE_PENDING"})
        async with asyncio.timeout(5):
            await self.context.close()
        self.closed = True
        self.entry.active_pages -= 1
        await owner.event("state", {"allocation_id": self.allocation, "state": "CLOSED"})


class PublicContexts:
    def __init__(self, pool):
        self.pool = pool

    async def page(self, identity, egress, *, deadline=None):
        await self.pool.start()
        key = digest_json(
            [identity.environment.model_dump(mode="json"), egress.egress_id, egress.generation]
        )[:24]
        profile = BrowserProfile(
            profile_id="public-" + key,
            site_id="generic",
            bound_egress_id=egress.egress_id,
            directory_key="public-" + key,
            environment=identity.environment,
        )
        entry = await self.pool._entry(profile, egress)
        # Account before awaiting page creation; close_idle cannot retire it.
        entry.active_pages += 1
        context = None
        try:
            env = identity.environment
            context = await entry.context.browser.new_context(
                locale=env.locale,
                timezone_id=env.timezone_id,
                viewport={"width": env.window_width, "height": env.window_height},
                accept_downloads=False,
            )
            owner = self.pool.target_owner
            allocation = await owner.event(
                "allocate",
                {
                    "identity_id": identity.identity_id,
                    "instance_id": "public:" + entry.instance_id,
                    "epoch": egress.generation,
                },
            )
            page = await context.new_page()
            session = await context.new_cdp_session(page)
            try:
                target_id = (await session.send("Target.getTargetInfo"))["targetInfo"]["targetId"]
            finally:
                await session.detach()
            await owner.event(
                "bind", {"allocation_id": allocation["allocation_id"], "target_id": target_id}
            )
            page._doxagent_target_id = target_id
            page._doxagent_allocation_id = allocation["allocation_id"]
            page._doxagent_target_owner = owner
            return _ContextLease(self, entry, context, page, identity, allocation["allocation_id"])
        except BaseException:
            if context is not None:
                async with asyncio.timeout(5):
                    await context.close()
            entry.active_pages -= 1
            raise

    async def close(self):
        await self.pool.close()

    async def close_idle(self):
        return 0  # Real-work burst ownership, never an idle maintenance scan.


class PublicExecution:
    def __init__(self, repository, resolver, options):
        self.repository, self.resolver = repository, resolver
        self.options = dict(options)
        self.options["profile_root"] = str(options["profile_root"]) + "/.public-runtime"
        self.options["supervisor_socket"] = None
        self.lock = asyncio.Lock()
        self.users = 0
        self.runtime = None
        self.cleanup_pending = False
        self._closing_task = None
        self._cleanup_owner = None
        self._identities = set()

    async def _closed_facts(self):
        from .governance_repository import GovernanceRepository
        from .schema import IdentityOperationalState, utc_now
        from .storage import registry_io

        store = GovernanceRepository(self.repository)
        for identity_id in self._identities:
            state = await registry_io(self.repository.get_identity_runtime, identity_id)
            if state.instance_id and state.instance_id.startswith("public:"):
                await registry_io(store.mark_instance_lost, identity_id, state.instance_id)
                await registry_io(
                    self.repository.save_identity_runtime,
                    state.model_copy(
                        update={
                            "operational_state": IdentityOperationalState.STOPPED,
                            "generation": state.generation + 1,
                            "updated_at": utc_now(),
                        }
                    ),
                    expected_generation=state.generation,
                )
        self._identities.clear()

    async def _close_runtime(self, runtime):
        """Only this anonymous burst's processes may be force-released."""
        from .supervisor_state import process_record, validated_process

        pool = runtime.browser_pool
        if self._closing_task is None:
            records = []
            for entry in getattr(pool, "_entries", {}).values():
                directory = pool.root / entry.profile.directory_key
                try:
                    record = json.loads((directory / "doxagent-profile-manifest.json").read_text())[
                        "chrome_process"
                    ]
                    if record:
                        records.append((record, f"--user-data-dir={directory.resolve()}"))
                except (OSError, ValueError, KeyError):
                    pass
            connection = getattr(
                getattr(getattr(pool, "_playwright", None), "_impl_obj", None), "_connection", None
            )
            node = getattr(getattr(connection, "_transport", None), "_proc", None)
            node_record = (
                process_record(node.pid) if node is not None and os.name == "posix" else None
            )
            self._cleanup_owner = (records, node, node_record)
            self._closing_task = asyncio.create_task(runtime.close())
        records, node, node_record = self._cleanup_owner
        closing = self._closing_task
        self.cleanup_pending = True
        done, _ = await asyncio.wait({closing}, timeout=5)
        if done and not closing.cancelled() and closing.exception() is None:
            await self._closed_facts()
            self.cleanup_pending = False
            self._closing_task = self._cleanup_owner = None
            return True
        # A stuck SDK cancellation does not own a right to keep public Chrome.
        # Exact startup PID/arguments fence this from every protected identity.
        try:
            for record, argument in records:
                if process_record(record["pid"]) is not None:
                    process = validated_process(record, required_arguments=[argument])
                    process.terminate()
                    waiter = asyncio.create_task(process.wait())
                    done, _ = await asyncio.wait({waiter}, timeout=1)
                    if not done:
                        waiter.cancel()
                        await asyncio.gather(waiter, return_exceptions=True)
                        process.send_signal(signal.SIGKILL)
            if node is not None and node.returncode is None:
                current = process_record(node.pid) if node_record else None
                if os.name == "nt" or (
                    current and current["start_time"] == node_record["start_time"]
                ):
                    node.terminate()
            done, _ = await asyncio.wait({closing}, timeout=2)
            confirmed = bool(records) and all(
                not (p := process_record(record["pid"])) or p["start_time"] != record["start_time"]
                for record, _ in records
            )
            if confirmed and node is not None and node.returncode is not None:
                for entry in (
                    *getattr(pool, "_entries", {}).values(),
                    *getattr(pool, "_unclean_entries", []),
                ):
                    entry.owner_lock.release()
                pool._owner_lock.release()
                await self._closed_facts()
                self.cleanup_pending = False
                self._closing_task = self._cleanup_owner = None
                closing.add_done_callback(
                    lambda task: task.exception() if not task.cancelled() else None
                )
                return True
        except (OSError, RuntimeError):
            pass
        self.cleanup_pending = True
        logging.getLogger(__name__).error("public_browser_cleanup_unconfirmed; runtime retained")
        # Consume failure without hiding the occupied runtime or refetching a body.
        closing.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        return False

    async def execute(self, request, resolved, identity, egress):
        async with self.lock:
            if self.cleanup_pending:
                if not await self._close_runtime(self.runtime):
                    raise RuntimeError("public_browser_cleanup_pending")
                self.runtime = None
            if self.runtime is None:
                runtime = SiteAccessRuntime(self.repository, self.resolver, **self.options)
                runtime.browser_runtimes = BrowserRuntimeManager(
                    PublicContexts(runtime.browser_pool),
                    None,
                    max_pages=self.options.get("browser_max_pages", 4),
                )
                self.runtime = runtime
            runtime = self.runtime
            self.users += 1
            if identity is not None:
                self._identities.add(identity.identity_id)
        try:
            request = request.model_copy(
                update={
                    "recipe_parameters": {**request.recipe_parameters, "_public_ephemeral": True}
                }
            )
            return await runtime._browser(request, resolved, identity, egress)
        finally:
            async with self.lock:
                self.users -= 1
                if not self.users:
                    try:
                        if await self._close_runtime(runtime):
                            self.runtime = None
                    except Exception:
                        # A completed download is not invalidated by its local
                        # cleanup ledger. Retain this runtime for a later retry.
                        self.cleanup_pending = True
                        logging.getLogger(__name__).exception("public_browser_cleanup_pending")

    async def close(self):
        async with self.lock:
            if self.runtime is not None:
                if await self._close_runtime(self.runtime):
                    self.runtime = None
