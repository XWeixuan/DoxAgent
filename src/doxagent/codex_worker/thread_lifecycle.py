"""Authenticated, idempotent lifecycle for registered W3 threads only."""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from doxagent.control_io import read_io, write_io
from doxagent.w3_timing import file_trace, observed, span

from .cleanup import await_owned
from .startup_admission import PriorityGate, startup_scope


class _WireResponse(BaseModel):
    model_config = ConfigDict(extra="allow")


class LifecycleCleanupError(RuntimeError):
    code = "CAPSULE_CLEANUP_QUARANTINED"


class AppServerThreads:
    """Finite, request-owned helpers; status and cleanup never block Fork globally."""

    def __init__(self, config=None):
        self.config, self.client = config, None
        self.clients = set()
        self._initialize_lock = asyncio.Lock()
        self._rpc_gate = PriorityGate(max_pending=64)
        self.admission = None
        self.root = None
        self.generation = None
        self.identities = {}
        self.quarantined = False
        self._closing = {}
        self._closing_processes = {}
        self._quarantine_check_at = 0.0

    async def request(self, method, params):
        # Queue cancellation must not touch the client serving another request.
        async with self._rpc_gate.acquire(
            priority=2 if method == "thread/delete" else 0, timeout=30, label=method
        ):
            if self.quarantined:
                await self._reconcile_quarantine()
            if self.quarantined:
                raise LifecycleCleanupError("previous helper owner has not exited")
            return await self._request(method, params)

    async def ensure_initialized(self):
        await self.request("__initialize__", {})

    async def _request(self, method, params):
        from openai_codex.async_client import AsyncCodexClient

        async with self._initialize_lock:
            if self.client is None:
                from openai_codex import CodexConfig

                identity = "lifecycle-" + uuid4().hex
                config = self.config or CodexConfig()
                config = replace(
                    config,
                    env={**os.environ, **(config.env or {}), "DOXAGENT_CAPSULE_ID": identity},
                )
                client = AsyncCodexClient(config)
                self.identities[client] = {"identity": identity, "generation": self.generation}
                self._record(client, "CREATED")
                try:

                    async def initialize():
                        async with asyncio.timeout(90):
                            await client.start()
                            proc = getattr(getattr(client, "_sync", None), "_proc", None)
                            if proc is not None:
                                from .process_tree import owned_processes

                                owned = await asyncio.to_thread(owned_processes, identity)
                                self.identities[client].update(
                                    pid=proc.pid, start_ticks=owned.get(proc.pid, (None, 0))[0]
                                )
                            self._record(client, "SDK_INITIALIZE")
                            await client.initialize()
                            self.identities[client]["sdk_ready"] = True
                            self._record(client, "SDK_READY")

                    async def initialize_owned():
                        try:
                            await initialize()
                        except BaseException:
                            await self._close_client(client)
                            raise

                    if self.admission is not None:
                        async with self.admission.boot(
                            priority=2 if method == "thread/delete" else None
                        ):
                            await initialize_owned()
                    else:
                        await initialize_owned()
                except BaseException:
                    await self._close_client(client)
                    raise
                self.client = client
                self.clients.add(client)
            client = self.client

        if method == "__initialize__":
            return {}

        async def rpc():
            with span("lifecycle.rpc", method=method):
                result = await client.request(method, params, response_model=_WireResponse)
            return result.model_dump(mode="json")

        try:
            return await asyncio.wait_for(rpc(), 10)
        except BaseException as exc:
            fatal = isinstance(
                exc, (TimeoutError, asyncio.CancelledError, ConnectionError, EOFError)
            ) or any(word in type(exc).__name__ for word in ("TransportClosed", "ProcessExited"))
            if not fatal:
                raise
            await self._close_client(client)
            raise

    def _record(self, client, stage):
        value = self.identities.get(client, {})
        value["stage"] = stage
        proc = getattr(getattr(client, "_sync", None), "_proc", None)
        try:
            value["returncode"] = proc.poll() if proc is not None else None
        except OSError:
            value["returncode"] = None
        if self.root is not None:
            directory = Path(self.root) / "lifecycle-owners"
            try:
                directory.mkdir(exist_ok=True, parents=True)
                target = directory / (value["identity"] + ".json")
                temporary = target.with_suffix(".tmp")
                temporary.write_text(json.dumps(value), encoding="utf-8")
                temporary.replace(target)
            except OSError:
                pass

    async def _close_client(self, client):
        task = self._closing.get(client)
        if task is None:
            if client not in self.identities and client not in self.clients:
                return
            task = asyncio.create_task(self._close_client_owned(client))
            self._closing[client] = task
        await await_owned(task)

    async def _close_client_owned(self, client):
        identity = self.identities.get(client, {}).get("identity")
        proc = getattr(getattr(client, "_sync", None), "_proc", None)
        self._closing_processes[client] = proc
        self.identities[client]["closing_since"] = time.monotonic()
        self._record(client, "CLOSING")
        try:
            async with asyncio.timeout(15):
                try:
                    async with asyncio.timeout(1):
                        await client.close()
                except Exception:
                    pass  # A failed graceful close must still reach the owned kill.
                from .process_tree import PopenProcess, terminate_owned

                await terminate_owned(
                    PopenProcess(proc) if proc is not None else None, identity, kill_group=False
                )
        except BaseException as exc:
            self.quarantined = True
            self.identities[client]["cleanup_error"] = f"{type(exc).__name__}: {str(exc)[:250]}"
            self.clients.add(client)
            if self.admission and not self.identities[client].get("sdk_ready"):
                self.admission.quarantined.add(identity)
            self._record(client, "QUARANTINED")
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise LifecycleCleanupError("owned helper process cleanup failed") from exc
        self._forget_client(client)

    def _forget_client(self, client):
        identity = self.identities.get(client, {}).get("identity")
        self._record(client, "CLOSED")
        if self.admission:
            self.admission.quarantined.discard(identity)
        self.clients.discard(client)
        self.identities.pop(client, None)
        self._closing.pop(client, None)
        self._closing_processes.pop(client, None)
        if self.client is client:
            self.client = None

    async def _reconcile_quarantine(self):
        if time.monotonic() < self._quarantine_check_at:
            return
        self._quarantine_check_at = time.monotonic() + 30
        from .process_tree import owned_processes

        for client, value in list(self.identities.items()):
            if value.get("stage") != "QUARANTINED":
                continue
            proc = self._closing_processes.get(client)
            gone = (
                not await asyncio.to_thread(owned_processes, value["identity"])
                if os.name == "posix"
                else proc is not None and proc.poll() is not None
            )
            if gone:
                self._forget_client(client)
        self.quarantined = any(v.get("stage") == "QUARANTINED" for v in self.identities.values())

    async def reconcile_cleanup(self):
        if not self.quarantined:
            return
        try:
            async with asyncio.timeout(1):
                async with self._rpc_gate.acquire(priority=2, timeout=1, label="cleanup-reconcile"):
                    await self._reconcile_quarantine()
        except TimeoutError:
            pass  # Retry on the existing maintenance tick; never interrupt an RPC owner.

    def snapshot(self):
        return dict(
            gate=self._rpc_gate.snapshot(),
            quarantined=self.quarantined,
            closing=len(self._closing),
            owners=[
                {k: value.get(k) for k in ("identity", "pid", "stage", "cleanup_error")}
                for value in list(self.identities.values())[:8]
            ],
            oldest_cleanup_seconds=max(
                (
                    time.monotonic() - v["closing_since"]
                    for v in self.identities.values()
                    if "closing_since" in v
                ),
                default=0.0,
            ),
        )

    async def close(self):
        async with self._rpc_gate.acquire(timeout=30):
            await asyncio.gather(*(self._close_client(c) for c in list(self.clients)))


class WorkerThreadLifecycle:
    def __init__(self, jobs, workspaces, transport=None):
        self.jobs, self.workspaces = jobs, workspaces
        self.transport = transport or AppServerThreads()
        self.transport.admission = getattr(getattr(jobs, "_runtime", None), "admission", None)
        self.transport.root = jobs.store.root
        self.transport.generation = getattr(jobs, "generation", None)
        self.locks: dict[str, asyncio.Lock] = {}
        self.preparations: dict[str, asyncio.Task] = {}
        with closing(jobs.store.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS w3_thread_registry (
                    thread_id TEXT PRIMARY KEY, kind TEXT NOT NULL, parent_id TEXT,
                    run_id TEXT NOT NULL, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS w3_thread_operations (
                    operation_key TEXT PRIMARY KEY, request TEXT NOT NULL,
                    thread_id TEXT, status TEXT NOT NULL);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(w3_thread_operations)")}
            for name in (
                "created_at",
                "updated_at",
                "step",
                "error_code",
                "error_message",
                "execution_generation",
                "capsule_id",
                "recovery_count",
                "owner_token",
                "fork_dispatched_at",
                "startup_attempts",
                "next_retry_at",
                "last_error",
                "prepare_deadline_at",
                "startup_receipt",
                "full_query_started_at",
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE w3_thread_operations ADD COLUMN {name} TEXT")

    def operation(self, key):
        with closing(self.jobs.store.connect()) as db:
            cursor = db.execute("SELECT * FROM w3_thread_operations WHERE operation_key=?", (key,))
            row = cursor.fetchone()
            if row is None:
                return None
            value = dict(zip((col[0] for col in cursor.description), row, strict=True))
        value.pop("request", None)
        return value

    def _operation_update(self, key, owner=None, **values):
        from datetime import UTC, datetime

        values["updated_at"] = datetime.now(UTC).isoformat()
        with closing(self.jobs.store.connect()) as db, db:
            db.execute(
                "UPDATE w3_thread_operations SET "
                + ",".join(k + "=?" for k in values)
                + " WHERE operation_key=?"
                + (" AND owner_token=?" if owner else ""),
                (*values.values(), key, *((owner,) if owner else ())),
            )

    async def prepare(self, request):
        value, legacy, recover = await write_io(
            self.jobs.store.path, self._accept_prepare, request, retry=True
        )
        key = request.operation_key
        if (
            value["status"] not in {"COMPLETED", "FAILED"}
            and key not in self.preparations
            and float(value.get("next_retry_at") or 0) <= time.time()
        ):
            task = asyncio.create_task(self._prepare_operation(request, legacy, recover))
            self.preparations[key] = task
            task.add_done_callback(lambda _: self.preparations.pop(key, None))
        return value

    def _accept_prepare(self, request):
        from datetime import UTC, datetime

        raw = request.model_dump(mode="json")
        key = request.operation_key
        if key not in {"w3-fork:" + request.record_id, "w3-slot-fork:" + request.record_id}:
            raise ValueError("operation key must identify the original record")
        canonical = json.dumps(raw, sort_keys=True)
        parent = self._row(request.parent_thread_id)
        if not parent or parent["kind"] != "TEMPLATE" or parent["status"] != "ACTIVE":
            raise ValueError("prepare requires an active registered template")
        with closing(self.jobs.store.connect()) as db, db:
            old = db.execute(
                "SELECT request,status FROM w3_thread_operations WHERE operation_key=?", (key,)
            ).fetchone()
            legacy = False
            if old and old[0] != canonical:
                prior = json.loads(old[0])
                legacy = (
                    "operation_key" not in prior
                    and all(prior.get(k) == raw.get(k) for k in ("parent_thread_id", "run_id"))
                    and prior.get("model") == request.profile.get("model")
                    and (prior.get("model_provider") == request.profile.get("provider"))
                )
                if not legacy:
                    raise ValueError("prepare key reused with different parameters")
            db.execute(
                "INSERT OR IGNORE INTO w3_thread_operations"
                "(operation_key,request,status,created_at,updated_at,step,"
                "fork_dispatched_at,startup_attempts) "
                "VALUES(?,?,'CREATING',?,?,'ACCEPTED','','0')",
                (key, canonical, datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat()),
            )
        value = self.operation(key)
        if request.source_case_id is not None and not value.get("prepare_deadline_at"):
            accepted = (
                datetime.fromisoformat(value["created_at"]).timestamp()
                if value.get("created_at")
                else time.time()
            )
            self._operation_update(
                key,
                prepare_deadline_at=str(accepted + getattr(self.jobs, "w3_prepare_timeout", 180)),
            )
            value = self.operation(key)
        return value, legacy, bool(old)

    async def _prepare_operation(self, request, legacy, recover):
        with file_trace(
            self.jobs.store.root / "boundary-timing",
            operation_key=request.operation_key,
            plane="prepare",
        ):
            return await self._prepare_operation_observed(request, legacy, recover)

    @observed("lifecycle.prepare_operation")
    async def _prepare_operation_observed(self, request, legacy, recover):
        from .schema import ThreadForkRequest, WorkerRunRequest

        key = request.operation_key
        owner = uuid4().hex
        await write_io(
            self.jobs.store.path,
            self._operation_update,
            key,
            owner_token=owner,
            step="PREPARING",
            execution_generation=getattr(self.jobs, "generation", None),
            retry=True,
        )
        started = time.time()
        initial = await read_io(self.operation, key)
        binding = {
            "deadline": initial.get("prepare_deadline_at"),
            "checked": 0.0,
            "priority": 0 if request.source_case_id is not None else 1,
        }
        if not binding["deadline"] and request.source_case_id is not None:
            accepted = initial.get("created_at")
            from datetime import datetime

            accepted = datetime.fromisoformat(accepted).timestamp() if accepted else started
            binding["deadline"] = str(accepted + getattr(self.jobs, "w3_prepare_timeout", 180))

        async def refresh_binding():
            if binding["priority"] == 0 or time.monotonic() - binding["checked"] < 0.25:
                return
            binding["checked"] = time.monotonic()
            if key.startswith("w3-slot-fork:"):

                def read_slot_binding():
                    with closing(self.jobs.store.connect()) as db:
                        row = db.execute(
                            "SELECT prepare_deadline_at,case_id FROM w3_ready_slots WHERE id=?",
                            (request.record_id,),
                        ).fetchone()
                    return row

                row = await read_io(read_slot_binding)
                if row and row[1]:
                    binding.update(priority=0, deadline=row[0])

        async def budget():
            await refresh_binding()
            deadline = binding["deadline"]
            if deadline and initial.get("prepare_deadline_at") != str(deadline):
                await write_io(
                    self.jobs.store.path,
                    self._operation_update,
                    key,
                    owner=owner,
                    prepare_deadline_at=str(deadline),
                    retry=True,
                )
                initial["prepare_deadline_at"] = str(deadline)
            return float(deadline) if deadline else None

        def priority():
            return binding["priority"]

        async def checkpoint(stage, **values):
            current = await read_io(self.operation, key)
            if current.get("owner_token") != owner:
                raise asyncio.CancelledError("prepare owner replaced")
            deadline = await budget()
            if deadline and time.time() >= deadline - 15:
                raise TimeoutError("bound prepare deadline exceeded")
            update = {"step": stage}
            if stage == "BOOT_ATTEMPT":
                attempts = int(current.get("startup_attempts") or 0)
                if attempts >= 2:
                    raise ValueError("prepare exhausted two physical boot attempts")
                update["startup_attempts"] = str(attempts + 1)
            if stage == "FORK_DISPATCHED":
                update["fork_dispatched_at"] = str(time.time())
            if "capsule_id" in values:
                update["capsule_id"] = values["capsule_id"]
            if "thread_id" in values:
                update["thread_id"] = values["thread_id"]
            if "startup_receipt" in values:
                update["startup_receipt"] = json.dumps(values["startup_receipt"])
            await write_io(
                self.jobs.store.path, self._operation_update, key, owner=owner, **update, retry=True
            )

        async def execute():
            while not getattr(self.jobs, "_processes_recovered", True):
                await checkpoint("WAIT_PROCESS_RECOVERY")
                await asyncio.sleep(0.05)
            if legacy:

                def legacy_prepare():
                    with closing(self.jobs.store.connect()) as db:
                        prior = json.loads(
                            db.execute(
                                "SELECT request FROM w3_thread_operations WHERE operation_key=?",
                                (key,),
                            ).fetchone()[0]
                        )
                    return prior

                prior = await read_io(legacy_prepare)
                return (await self.fork(ThreadForkRequest.model_validate(prior)))["thread_id"]
            cwd = await asyncio.to_thread(self.workspaces.ensure_run, request.run_id)
            current = await read_io(self.operation, key)
            if current.get("thread_id"):
                return current["thread_id"]
            if recover and current.get("fork_dispatched_at") != "":
                await checkpoint("RECONCILING")
                receipt = cwd / ".w3-fork-receipt.json"
                if receipt.is_file():
                    value = json.loads(receipt.read_text())
                    if (
                        value.get("run_id") == request.run_id
                        and value.get("parent_id") == request.parent_thread_id
                    ):
                        return value["thread_id"]
                # NULL is the legacy UNKNOWN state. A failed query is never an empty result.
                initialize = getattr(self.transport, "ensure_initialized", None)
                if initialize is not None:
                    await initialize()
                found = {}
                for fast in (True, False):
                    if not fast:
                        if (await read_io(self.operation, key)).get("full_query_started_at"):
                            raise RuntimeError(
                                "full recovery query already attempted; "
                                "awaiting receipt/state index"
                            )
                        await write_io(
                            self.jobs.store.path,
                            self._operation_update,
                            key,
                            owner=owner,
                            full_query_started_at=str(time.time()),
                            retry=True,
                        )
                    cursor = None
                    async with asyncio.timeout(10):
                        while True:
                            params = {"cwd": str(cwd), "limit": 100, "useStateDbOnly": fast}
                            if cursor:
                                params["cursor"] = cursor
                            listed = await self.transport.request("thread/list", params)
                            for t in listed.get("data", []):
                                if (
                                    t.get("cwd") == str(cwd)
                                    and t.get("forkedFromId") == request.parent_thread_id
                                ):
                                    found[t["id"]] = t
                            cursor = listed.get("nextCursor")
                            if not cursor:
                                break
                    if found:
                        break
                if len(found) > 1:
                    raise ValueError("ambiguous persisted prepare")
                if found:
                    return next(iter(found))
                await write_io(
                    self.jobs.store.path,
                    self._operation_update,
                    key,
                    owner=owner,
                    fork_dispatched_at="",
                    retry=True,
                )
            profile = request.profile
            execution = WorkerRunRequest(
                workflow_version="persistent_runtime_w3_v1",
                research_lane="persistent_runtime",
                ticker=profile["ticker"],
                node="persistent_runtime_w3",
                agent_role="w3_duty_trading_expert",
                run_id=request.run_id,
                attempt_id=request.attempt_id,
                cutoff_at=request.cutoff_at,
                prompt="",
                output_schema={},
                task_phase=request.phase,
                model=profile.get("model"),
                model_provider=profile.get("provider"),
                effort=profile.get("effort") or "max",
                read_only=True,
                data_mcp_enabled=profile.get("data_mcp_enabled") == "true",
                research_assets_mcp_enabled=profile.get("research_assets_mcp_enabled") == "true",
                w3_entry_version=profile.get("w3_entry_version"),
                web_search_mode=profile.get("web_search_mode") or "live",
                timeout_seconds=int(profile.get("timeout_seconds") or "600"),
            )
            runtime = self.jobs._runtime
            if getattr(runtime, "manages_startup_capacity", False):
                return await runtime.prepare_fork(execution, cwd, request.parent_thread_id)
            # Embedded/legacy runtimes lack stage callbacks; conservatively mark dispatch.
            await checkpoint("FORK_DISPATCHED")
            async with self.jobs._startup:
                return await runtime.prepare_fork(execution, cwd, request.parent_thread_id)

        timer = None
        try:
            # This task owns execution directly: cancelling it awaits capsule cleanup.
            with startup_scope(
                key=key, priority=priority, deadline=await budget(), progress=checkpoint
            ):
                async with asyncio.timeout(None) as timer:

                    async def monitor():
                        while True:
                            deadline = await budget()
                            if deadline:
                                timer.reschedule(
                                    asyncio.get_running_loop().time()
                                    + max(0, deadline - time.time() - 15)
                                )
                                return
                            await asyncio.sleep(0.25)

                    watcher = asyncio.create_task(monitor())
                    try:
                        thread_id = await execute()
                    finally:
                        watcher.cancel()
                        await asyncio.gather(watcher, return_exceptions=True)
            if (await read_io(self.operation, key)).get("owner_token") != owner:
                return

            def register_prepared_child():
                with closing(self.jobs.store.connect()) as db, db:
                    db.execute(
                        "INSERT OR IGNORE INTO w3_thread_registry VALUES(?,?,?,?,'ACTIVE')",
                        (
                            thread_id,
                            "PREPARED" if request.source_case_id is None else "CASE",
                            request.parent_thread_id,
                            request.run_id,
                        ),
                    )

            await write_io(self.jobs.store.path, register_prepared_child, retry=True)
            await write_io(
                self.jobs.store.path,
                self._operation_update,
                key,
                owner=owner,
                thread_id=thread_id,
                status="COMPLETED",
                step="READY",
                error_code=None,
                error_message=None,
                last_error=None,
                next_retry_at=None,
                retry=True,
            )
        except BaseException as exc:
            current = await read_io(self.operation, key)
            deadline = await budget()
            terminal = (
                isinstance(exc, ValueError)
                or any(
                    word in str(exc).lower()
                    for word in (
                        "invalid transport",
                        "invalid configuration",
                        "authentication",
                        "unauthorized",
                    )
                )
                or (deadline and time.time() >= deadline - 15)
                or (isinstance(exc, TimeoutError) and timer is not None and timer.expired())
                or (
                    int(current.get("startup_attempts") or 0) >= 2
                    and current.get("fork_dispatched_at") == ""
                )
                or getattr(exc, "code", None) == "CAPSULE_CLEANUP_QUARANTINED"
            )
            await write_io(
                self.jobs.store.path,
                self._operation_update,
                key,
                owner=owner,
                status="FAILED" if terminal else "CREATING",
                step="RECONCILING",
                recovery_count=str(int(current.get("recovery_count") or 0) + 1),
                next_retry_at=str(time.time() + 1),
                last_error=str(exc) or type(exc).__name__,
                startup_receipt=json.dumps(getattr(exc, "startup_receipt", None))
                if getattr(exc, "startup_receipt", None)
                else current.get("startup_receipt"),
                error_code=getattr(exc, "code", type(exc).__name__),
                error_message=str(exc) or type(exc).__name__,
                retry=True,
            )
            if isinstance(exc, asyncio.CancelledError):
                raise

    def _row(self, thread_id):
        with closing(self.jobs.store.connect()) as db:
            row = db.execute(
                "SELECT thread_id,kind,parent_id,run_id,status FROM w3_thread_registry "
                "WHERE thread_id=?",
                (thread_id,),
            ).fetchone()
        return (
            dict(zip(("thread_id", "kind", "parent_id", "run_id", "status"), row, strict=True))
            if row
            else None
        )

    async def _state_threads(self, cwd):
        """Bounded state-index lookup only; never scan rollout bodies for GC."""
        cursor = None
        found = {}
        async with asyncio.timeout(10):
            while True:
                params = dict(cwd=str(cwd), limit=100, useStateDbOnly=True)
                if cursor:
                    params["cursor"] = cursor
                result = await self.transport.request("thread/list", params)
                for value in result.get("data", []):
                    if value.get("cwd") == str(cwd):
                        found[value["id"]] = value
                cursor = result.get("nextCursor")
                if not cursor:
                    return found

    async def retiring_child(self, key, run_id, child=None, *, case=False):
        """Adopt only an original, verified child for deletion, never for reuse."""
        task = self.preparations.get(key)
        if task is not None and not task.done():
            raise ValueError("prepare still has a live owner")

        def retirement_operation():
            with closing(self.jobs.store.connect()) as db:
                row = db.execute(
                    "SELECT request,thread_id FROM w3_thread_operations WHERE operation_key=?",
                    (key,),
                ).fetchone()
            return row

        row = await read_io(retirement_operation)
        if row is None:
            raise ValueError("missing retirement operation")
        request = json.loads(row[0])
        parent = request.get("parent_thread_id")
        registered_parent = await read_io(self._row, parent)
        if (
            request.get("run_id") != run_id
            or not registered_parent
            or registered_parent["kind"] != "TEMPLATE"
        ):
            raise ValueError("retirement requires the original run and registered parent")
        cwd = self.workspaces._run_root(run_id)
        child = child or row[1]
        receipt = cwd / ".w3-fork-receipt.json"
        recorded = json.loads(receipt.read_text()) if receipt.is_file() else {}
        receipt_matches = (
            recorded.get("run_id") == run_id
            and recorded.get("parent_id") == parent
            and bool(recorded.get("thread_id"))
        )
        if receipt_matches and child and child != recorded["thread_id"]:
            raise ValueError("retirement receipt conflicts with the recorded child")
        child = child or (recorded["thread_id"] if receipt_matches else None)
        registered = await read_io(self._row, child) if child else None
        if registered:
            if (
                registered["kind"] == "TEMPLATE"
                or registered["run_id"] != run_id
                or registered["parent_id"] != parent
            ):
                raise ValueError("retirement child belongs to a different owner")
            return child
        if not receipt_matches:
            matches = [
                t
                for t in (await self._state_threads(cwd)).values()
                if t.get("forkedFromId") == parent and (not child or t["id"] == child)
            ]
            if len(matches) != 1:
                raise ValueError("retirement unresolved; no unique child in receipt/state index")
            child = matches[0]["id"]
        if await read_io(self.active, child):
            raise ValueError("retirement child has an active or queued job")

        def register_retirement_child():
            with closing(self.jobs.store.connect()) as db, db:
                db.execute(
                    "INSERT OR IGNORE INTO w3_thread_registry VALUES(?,?,?,?, 'ACTIVE')",
                    (child, "CASE" if case else "PREPARED", parent, run_id),
                )

        await write_io(self.jobs.store.path, register_retirement_child, retry=True)
        registered = await read_io(self._row, child)
        if (
            registered["run_id"] != run_id
            or registered["parent_id"] != parent
            or registered["kind"] == "TEMPLATE"
        ):
            raise ValueError("retirement registration identity conflict")
        return child

    def validate_run(self, request):
        if request.task_phase is None:
            return
        if request.thread_id is None:
            if request.task_phase != "w3_template":
                raise ValueError("W3 Case phases require their registered Fork thread")
            return
        row = self._row(request.thread_id)
        expected = "TEMPLATE" if request.task_phase == "w3_template" else "CASE"
        if (
            not row
            or row["kind"] != expected
            or row["status"] != "ACTIVE"
            or (row["run_id"] != request.run_id)
        ):
            raise ValueError(
                "W3 request cannot run on an unregistered, protected or deleted thread"
            )

    def active(self, thread_id):
        with closing(self.jobs.store.connect()) as db:
            return bool(
                db.execute(
                    "SELECT 1 FROM jobs WHERE status IN ('queued','running') AND "
                    "(json_extract(payload,'$.thread_id')=? "
                    "OR json_extract(request,'$.thread_id')=?)",
                    (thread_id, thread_id),
                ).fetchone()
            )

    async def register_template(self, job_id):
        job = await read_io(self.jobs.get, job_id)
        request = await read_io(self.jobs.store.request, job_id)
        if (
            not job
            or job.status != "succeeded"
            or not job.thread_id
            or not request
            or (request.task_phase != "w3_template")
        ):
            raise ValueError("only a successful template initialization may register a template")
        old = await read_io(self._row, job.thread_id)
        if old and old["kind"] != "TEMPLATE":
            raise ValueError("cannot promote a Case thread to a template")

        def register_template_row():
            with closing(self.jobs.store.connect()) as db, db:
                db.execute(
                    "INSERT OR IGNORE INTO w3_thread_registry VALUES(?,'TEMPLATE',NULL,?,'ACTIVE')",
                    (job.thread_id, job.run_id),
                )

        await write_io(self.jobs.store.path, register_template_row, retry=True)
        return await read_io(self._row, job.thread_id)

    async def register_legacy(self, job_id):
        job = await read_io(self.jobs.get, job_id)
        request = await read_io(self.jobs.store.request, job_id)
        if (
            not job
            or not job.thread_id
            or not request
            or request.task_phase is not None
            or (str(request.node) != "persistent_runtime_w3")
        ):
            raise ValueError("legacy registration requires a recorded W3 job")
        old = await read_io(self._row, job.thread_id)
        if old and old["kind"] == "TEMPLATE":
            raise ValueError("cannot demote a protected template")

        def register_legacy_row():
            with closing(self.jobs.store.connect()) as db, db:
                db.execute(
                    "INSERT OR IGNORE INTO w3_thread_registry VALUES(?,'LEGACY_CASE',NULL,?,'ACTIVE')",
                    (job.thread_id, job.run_id),
                )

        await write_io(self.jobs.store.path, register_legacy_row, retry=True)
        return await read_io(self._row, job.thread_id)

    async def fork(self, request):
        raw = request.model_dump(mode="json") if hasattr(request, "model_dump") else dict(request)
        root = getattr(self.jobs.store, "root", None)
        with file_trace(
            root / "boundary-timing" if root else None,
            operation_key=raw["idempotency_key"],
            plane="lifecycle",
        ):
            return await self._fork_observed(request)

    @observed("lifecycle.fork_operation")
    async def _fork_observed(self, request):
        raw = request.model_dump(mode="json") if hasattr(request, "model_dump") else dict(request)
        key = raw["idempotency_key"]
        async with self.locks.setdefault(key, asyncio.Lock()):
            parent = await read_io(self._row, raw["parent_thread_id"])
            if not parent or parent["kind"] != "TEMPLATE" or parent["status"] != "ACTIVE":
                raise ValueError("Fork requires a registered active W3 template")
            if await read_io(self.active, parent["thread_id"]):
                raise ValueError("template initialization is still active")

            def accept_fork():
                with closing(self.jobs.store.connect()) as db, db:
                    prior = db.execute(
                        "SELECT request,thread_id FROM w3_thread_operations WHERE operation_key=?",
                        (key,),
                    ).fetchone()
                    canonical = json.dumps(raw, sort_keys=True)
                    if prior and prior[0] != canonical:
                        raise ValueError("Fork idempotency key reused with different parameters")
                    if prior and prior[1]:
                        return prior[1]
                    db.execute(
                        "INSERT OR IGNORE INTO w3_thread_operations"
                        "(operation_key,request,thread_id,status) "
                        "VALUES(?,?,NULL,'CREATING')",
                        (key, canonical),
                    )

            prior_thread = await write_io(self.jobs.store.path, accept_fork, retry=True)
            if prior_thread:
                return await read_io(self._row, prior_thread)
            cwd = str(await read_io(self.workspaces.ensure_run, raw["run_id"]))
            listed = await self.transport.request("thread/list", {"cwd": cwd, "limit": 100})
            recovered = [
                t
                for t in listed.get("data", [])
                if t.get("cwd") == cwd and t.get("forkedFromId") == parent["thread_id"]
            ]
            if len(recovered) > 1:
                raise ValueError("ambiguous persisted Fork operation")
            if recovered:
                thread = recovered[0]
            else:
                response = await self.transport.request(
                    "thread/fork",
                    {
                        "threadId": parent["thread_id"],
                        "cwd": cwd,
                        "model": raw.get("model"),
                        "modelProvider": raw.get("model_provider"),
                        "approvalPolicy": "never",
                        "sandbox": "danger-full-access",
                    },
                )
                thread = response["thread"]

            def publish_fork():
                with closing(self.jobs.store.connect()) as db, db:
                    db.execute(
                        "INSERT OR IGNORE INTO w3_thread_registry VALUES(?,'CASE',?,?,'ACTIVE')",
                        (thread["id"], parent["thread_id"], raw["run_id"]),
                    )
                    db.execute(
                        "UPDATE w3_thread_operations SET thread_id=?,status='COMPLETED' "
                        "WHERE operation_key=?",
                        (thread["id"], key),
                    )

            await write_io(self.jobs.store.path, publish_fork, retry=True)
            return await read_io(self._row, thread["id"])

    async def status(self, thread_id):
        root = getattr(self.jobs.store, "root", None)
        with file_trace(
            root / "boundary-timing" if root else None,
            thread_id=thread_id,
            method="status",
            plane="lifecycle",
        ):
            return await self._status_observed(thread_id)

    async def _status_observed(self, thread_id):
        row = await read_io(self._row, thread_id)
        if not row:
            raise KeyError(thread_id)
        row["active"] = await read_io(self.active, thread_id)
        row["exists"] = row["status"] != "DELETED"
        if row["exists"]:
            try:
                response = await self.transport.request(
                    "thread/read", {"threadId": thread_id, "includeTurns": False}
                )
                state = response.get("thread", {}).get("status", {})
                row["active"] = row["active"] or state.get("type") == "active"
            except Exception as exc:
                if self._missing(exc):
                    row["exists"] = False
                elif "not loaded" in str(exc).lower():
                    value = (
                        await self._state_threads(self.workspaces._run_root(row["run_id"]))
                    ).get(thread_id)
                    if value is None:
                        raise
                    row["active"] = row["active"] or value.get("status", {}).get("type") == "active"
                else:
                    raise
        return row

    @staticmethod
    def _missing(exc):
        return any(
            s in str(exc).lower()
            for s in ("not found", "no rollout", "does not exist", "unable to find")
        )

    async def delete(self, thread_id):
        root = getattr(self.jobs.store, "root", None)
        with file_trace(
            root / "boundary-timing" if root else None,
            thread_id=thread_id,
            method="delete",
            plane="lifecycle",
        ):
            return await self._delete_observed(thread_id)

    async def _delete_observed(self, thread_id):
        async with self.locks.setdefault("delete:" + thread_id, asyncio.Lock()):
            row = await read_io(self._row, thread_id)
            if not row or row["kind"] == "TEMPLATE":
                raise ValueError("only registered W3 Case threads may be deleted")
            if row["status"] == "DELETED":
                if row["run_id"].startswith("w3-case-"):
                    await read_io(self.workspaces.delete_w3_workspace, row["run_id"])
                elif row["run_id"].startswith("w3-ready-"):
                    await asyncio.to_thread(
                        self.workspaces.retire_w3_ready_workspace,
                        row["run_id"],
                        preserve_evidence=row["kind"] == "CASE",
                    )
                return dict(thread_id=thread_id, status="DELETED")
            if await read_io(self.active, thread_id):
                raise ValueError("cannot delete a thread with an active or queued job")

            def mark_deleting():
                with closing(self.jobs.store.connect()) as db, db:
                    db.execute(
                        "UPDATE w3_thread_registry SET status='DELETING' WHERE thread_id=?",
                        (thread_id,),
                    )

            await write_io(self.jobs.store.path, mark_deleting, retry=True)
            state = await self.status(thread_id)
            if state["active"]:

                def restore_active():
                    with closing(self.jobs.store.connect()) as db, db:
                        db.execute(
                            "UPDATE w3_thread_registry SET status='ACTIVE' WHERE thread_id=?",
                            (thread_id,),
                        )

                await write_io(self.jobs.store.path, restore_active, retry=True)
                raise ValueError("cannot delete a thread with an active or queued job")
            release = getattr(self.jobs._runtime, "release_thread", None)
            if release:
                await release(thread_id)
            if state["exists"]:
                try:
                    await self.transport.request("thread/delete", {"threadId": thread_id})
                except Exception as exc:
                    if not self._missing(exc):
                        raise

            def mark_deleted():
                with closing(self.jobs.store.connect()) as db, db:
                    db.execute(
                        "UPDATE w3_thread_registry SET status='DELETED' WHERE thread_id=?",
                        (thread_id,),
                    )

            await write_io(self.jobs.store.path, mark_deleted, retry=True)
            if row["run_id"].startswith("w3-case-"):
                await asyncio.to_thread(self.workspaces.delete_w3_workspace, row["run_id"])
            elif row["run_id"].startswith("w3-ready-"):
                await asyncio.to_thread(
                    self.workspaces.retire_w3_ready_workspace,
                    row["run_id"],
                    preserve_evidence=row["kind"] == "CASE",
                )
            return dict(thread_id=thread_id, status="DELETED")

    async def close(self):
        for task in list(self.preparations.values()):
            task.cancel()
        await asyncio.gather(*list(self.preparations.values()), return_exceptions=True)
        await self.transport.close()
