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
        async with asyncio.timeout(5):
            await self.context.close()
        self.closed = True
        self.entry.active_pages -= 1


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
            page = await context.new_page()
            # The context is the physical owner. Anonymous pages have no durable
            # identity lifecycle and cannot become retained login/challenge pages.
            return _ContextLease(self, entry, context, page, identity, None)
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
        self.options["browser_headless"] = True
        self.lock = asyncio.Lock()
        self.users = 0
        self.runtime = None
        self.cleanup_pending = False
        self._closing_task = None
        self._cleanup_owner = None
        self._idle_task = None

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
        # Wait for a close already in progress without holding the owner lock.
        # There is never a second anonymous runtime to hide an unconfirmed close.
        if self._idle_task and self.cleanup_pending:
            async with self.lock:
                closing = None
                if self.cleanup_pending:
                    if self._idle_task is None or self._idle_task.done():
                        self._idle_task=asyncio.create_task(self._finish_cleanup(self.runtime))
                    closing=self._idle_task
            if closing:await asyncio.shield(closing)
        async with self.lock:
            if self._idle_task and not self.cleanup_pending:
                self._idle_task.cancel()
                self._idle_task = None
            if self.cleanup_pending:
                raise RuntimeError("public_browser_cleanup_pending")
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
                    self._idle_task = asyncio.create_task(self._retire_idle(runtime))

    async def _retire_idle(self, runtime):
        await asyncio.sleep(2)
        async with self.lock:
            if self.users or self.runtime is not runtime:
                return
            self.cleanup_pending = True
        await self._finish_cleanup(runtime)

    async def _finish_cleanup(self, runtime):
        try:
            closed = await self._close_runtime(runtime)
            async with self.lock:
                if closed and self.runtime is runtime:
                    self.runtime = None
                    self.cleanup_pending=False
        except Exception:
            logging.getLogger(__name__).exception("public_browser_cleanup_pending")

    async def close(self):
        if self._idle_task:
            if not self.cleanup_pending:
                self._idle_task.cancel()
            await asyncio.gather(self._idle_task, return_exceptions=True)
        async with self.lock:
            if self.runtime is not None:
                if await self._close_runtime(self.runtime):
                    self.runtime = None
                    self.cleanup_pending=False
