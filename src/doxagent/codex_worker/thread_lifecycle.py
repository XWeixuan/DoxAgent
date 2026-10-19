"""Authenticated, idempotent lifecycle for registered W3 threads only."""

from __future__ import annotations

import asyncio
import json
from contextlib import closing

from pydantic import BaseModel, ConfigDict

from doxagent.w3_timing import file_trace, observed, span


class _WireResponse(BaseModel):
    model_config = ConfigDict(extra="allow")


class AppServerThreads:
    """Finite, request-owned helpers; status and cleanup never block Fork globally."""

    def __init__(self, config=None):
        self.config, self.client = config, None
        self.clients = set()
        self._initialize_lock = asyncio.Lock()

    async def request(self, method, params):
        from openai_codex.async_client import AsyncCodexClient

        async with asyncio.timeout(15), self._initialize_lock:
            if self.client is None:
                client = (
                    AsyncCodexClient(self.config) if self.config is not None else AsyncCodexClient()
                )
                try:
                    async with asyncio.timeout(10):
                        await client.start()
                        await client.initialize()
                except BaseException:
                    await asyncio.wait_for(client.close(), 3)
                    raise
                self.client = client
                self.clients.add(client)
            client = self.client

        async def rpc():
            with span("lifecycle.rpc", method=method):
                result = await client.request(method, params, response_model=_WireResponse)
            return result.model_dump(mode="json")

        try:
            return await asyncio.wait_for(rpc(), 10)
        except BaseException:
            owned_process = getattr(getattr(client, "_sync", None), "_proc", None)
            try:
                with span("lifecycle.client_close", method=method):
                    await asyncio.wait_for(client.close(), 5)
            finally:
                if owned_process is not None and owned_process.poll() is None:
                    owned_process.kill()
                    await asyncio.to_thread(owned_process.wait, timeout=3)
                self.clients.discard(client)
                if self.client is client:
                    self.client = None
            raise

    async def close(self):
        await asyncio.gather(*(asyncio.wait_for(c.close(), 5) for c in list(self.clients)))


class WorkerThreadLifecycle:
    def __init__(self, jobs, workspaces, transport=None):
        self.jobs, self.workspaces = jobs, workspaces
        self.transport = transport or AppServerThreads()
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

    def _operation_update(self, key, **values):
        from datetime import UTC, datetime

        values["updated_at"] = datetime.now(UTC).isoformat()
        with closing(self.jobs.store.connect()) as db, db:
            db.execute(
                "UPDATE w3_thread_operations SET "
                + ",".join(k + "=?" for k in values)
                + " WHERE operation_key=?",
                (*values.values(), key),
            )

    async def prepare(self, request):
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
                "(operation_key,request,status,created_at,updated_at,step) "
                "VALUES(?,?,'CREATING',?,?,'ACCEPTED')",
                (key, canonical, datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat()),
            )
        value = self.operation(key)
        if value["status"] not in {"COMPLETED", "FAILED"} and key not in self.preparations:
            task = asyncio.create_task(self._prepare_operation(request, legacy, bool(old)))
            self.preparations[key] = task
            task.add_done_callback(lambda _: self.preparations.pop(key, None))
        return value

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
        prior_errors = int(self.operation(key).get("recovery_count") or 0)
        self._operation_update(
            key, step="PREPARING", execution_generation=getattr(self.jobs, "generation", None)
        )

        async def execute():
            while not getattr(self.jobs, "_processes_recovered", True):
                await asyncio.sleep(0.05)
            if legacy:
                with closing(self.jobs.store.connect()) as db:
                    prior = json.loads(
                        db.execute(
                            "SELECT request FROM w3_thread_operations WHERE operation_key=?", (key,)
                        ).fetchone()[0]
                    )
                return (await self.fork(ThreadForkRequest.model_validate(prior)))["thread_id"]
            cwd = await asyncio.to_thread(self.workspaces.ensure_run, request.run_id)
            if recover:
                self._operation_update(key, step="RECONCILING")
                receipt = cwd / ".w3-fork-receipt.json"
                if receipt.is_file():
                    value = json.loads(receipt.read_text())
                    if (
                        value.get("run_id") == request.run_id
                        and value.get("parent_id") == request.parent_thread_id
                    ):
                        return value["thread_id"]
                found = []
                for fast in (True, False):
                    cursor = None
                    while True:
                        params = {"cwd": str(cwd), "limit": 100, "useStateDbOnly": fast}
                        if cursor:
                            params["cursor"] = cursor
                        listed = await self.transport.request("thread/list", params)
                        found.extend(
                            t
                            for t in listed.get("data", [])
                            if t.get("cwd") == str(cwd)
                            and t.get("forkedFromId") == request.parent_thread_id
                        )
                        cursor = listed.get("nextCursor")
                        if not cursor:
                            break
                    if found:
                        break
                if len(found) > 1:
                    raise ValueError("ambiguous persisted prepare")
                if found:
                    return found[0]["id"]
            self._operation_update(key, step="FORKING")
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
            if request.source_case_id is None:
                # Let already-dispatched business startups acquire the existing
                # finite semaphore before speculative empty-slot replenishment.
                while getattr(self.jobs, "_starting", None):
                    await asyncio.sleep(0.02)
            async with self.jobs._startup:
                return await self.jobs._runtime.prepare_fork(
                    execution, cwd, request.parent_thread_id
                )

        try:
            running = asyncio.create_task(execute())
            try:
                thread_id = await asyncio.wait_for(asyncio.shield(running), 10)
            except TimeoutError:
                self._operation_update(key, step="FORK_SLOW")
                thread_id = await asyncio.wait_for(running, 55)
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
            self._operation_update(
                key,
                thread_id=thread_id,
                status="COMPLETED",
                step="READY",
                error_code=None,
                error_message=None,
            )
        except BaseException as exc:
            # CapsuleRuntime destroys the owned process before raising; retry queries
            # the original operation and reconciles its fixed cwd, never changes key.
            self._operation_update(
                key,
                status="FAILED"
                if (
                    isinstance(exc, ValueError)
                    or prior_errors >= 2
                    or getattr(exc, "code", None) == "CAPSULE_CLEANUP_QUARANTINED"
                )
                else "CREATING",
                recovery_count=str(prior_errors + 1),
                step="RECONCILING",
                error_code=getattr(exc, "code", type(exc).__name__),
                error_message=str(exc) or type(exc).__name__,
            )

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
        job = self.jobs.get(job_id)
        request = self.jobs.store.request(job_id)
        if (
            not job
            or job.status != "succeeded"
            or not job.thread_id
            or not request
            or (request.task_phase != "w3_template")
        ):
            raise ValueError("only a successful template initialization may register a template")
        old = self._row(job.thread_id)
        if old and old["kind"] != "TEMPLATE":
            raise ValueError("cannot promote a Case thread to a template")
        with closing(self.jobs.store.connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO w3_thread_registry VALUES(?,'TEMPLATE',NULL,?,'ACTIVE')",
                (job.thread_id, job.run_id),
            )
        return self._row(job.thread_id)

    async def register_legacy(self, job_id):
        job = self.jobs.get(job_id)
        request = self.jobs.store.request(job_id)
        if (
            not job
            or not job.thread_id
            or not request
            or request.task_phase is not None
            or (str(request.node) != "persistent_runtime_w3")
        ):
            raise ValueError("legacy registration requires a recorded W3 job")
        old = self._row(job.thread_id)
        if old and old["kind"] == "TEMPLATE":
            raise ValueError("cannot demote a protected template")
        with closing(self.jobs.store.connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO w3_thread_registry VALUES(?,'LEGACY_CASE',NULL,?,'ACTIVE')",
                (job.thread_id, job.run_id),
            )
        return self._row(job.thread_id)

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
            parent = self._row(raw["parent_thread_id"])
            if not parent or parent["kind"] != "TEMPLATE" or parent["status"] != "ACTIVE":
                raise ValueError("Fork requires a registered active W3 template")
            if self.active(parent["thread_id"]):
                raise ValueError("template initialization is still active")
            with closing(self.jobs.store.connect()) as db, db:
                prior = db.execute(
                    "SELECT request,thread_id FROM w3_thread_operations WHERE operation_key=?",
                    (key,),
                ).fetchone()
                canonical = json.dumps(raw, sort_keys=True)
                if prior and prior[0] != canonical:
                    raise ValueError("Fork idempotency key reused with different parameters")
                if prior and prior[1]:
                    return self._row(prior[1])
                db.execute(
                    "INSERT OR IGNORE INTO w3_thread_operations"
                    "(operation_key,request,thread_id,status) "
                    "VALUES(?,?,NULL,'CREATING')",
                    (key, canonical),
                )
            cwd = str(self.workspaces.ensure_run(raw["run_id"]))
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
            return self._row(thread["id"])

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
        row = self._row(thread_id)
        if not row:
            raise KeyError(thread_id)
        row["active"] = self.active(thread_id)
        row["exists"] = row["status"] != "DELETED"
        if row["exists"]:
            try:
                response = await self.transport.request(
                    "thread/read", {"threadId": thread_id, "includeTurns": False}
                )
                state = response.get("thread", {}).get("status", {})
                row["active"] = row["active"] or state.get("type") == "active"
            except Exception as exc:
                if not self._missing(exc):
                    raise
                row["exists"] = False
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
            row = self._row(thread_id)
            if not row or row["kind"] == "TEMPLATE":
                raise ValueError("only registered W3 Case threads may be deleted")
            if row["status"] == "DELETED":
                if row["run_id"].startswith("w3-case-"):
                    self.workspaces.delete_w3_workspace(row["run_id"])
                elif row["run_id"].startswith("w3-ready-"):
                    await asyncio.to_thread(
                        self.workspaces.retire_w3_ready_workspace,
                        row["run_id"],
                        preserve_evidence=row["kind"] == "CASE",
                    )
                return dict(thread_id=thread_id, status="DELETED")
            if self.active(thread_id):
                raise ValueError("cannot delete a thread with an active or queued job")
            with closing(self.jobs.store.connect()) as db, db:
                db.execute(
                    "UPDATE w3_thread_registry SET status='DELETING' WHERE thread_id=?",
                    (thread_id,),
                )
            state = await self.status(thread_id)
            if state["active"]:
                with closing(self.jobs.store.connect()) as db, db:
                    db.execute(
                        "UPDATE w3_thread_registry SET status='ACTIVE' WHERE thread_id=?",
                        (thread_id,),
                    )
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
            with closing(self.jobs.store.connect()) as db, db:
                db.execute(
                    "UPDATE w3_thread_registry SET status='DELETED' WHERE thread_id=?", (thread_id,)
                )
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
