"""Authenticated, idempotent lifecycle for registered W3 threads only."""

from __future__ import annotations

import asyncio
import json
from contextlib import closing

from pydantic import BaseModel, ConfigDict


class _WireResponse(BaseModel):
    model_config = ConfigDict(extra="allow")


class AppServerThreads:
    """Use the public SDK transport for methods missing from its flat API."""

    def __init__(self):
        self.client = None
        self.lock = asyncio.Lock()

    async def request(self, method, params):
        if self.client is None:
            async with self.lock:
                if self.client is None:
                    from openai_codex.async_client import AsyncCodexClient

                    client = AsyncCodexClient()
                    try:
                        await client.start()
                        await client.initialize()
                    except BaseException:
                        await client.close()
                        raise
                    self.client = client
        response = await self.client.request(method, params, response_model=_WireResponse)
        return response.model_dump(mode="json")

    async def close(self):
        if self.client is not None:
            await self.client.close()
            self.client = None


class WorkerThreadLifecycle:
    def __init__(self, jobs, workspaces, transport=None):
        self.jobs, self.workspaces = jobs, workspaces
        self.transport = transport or AppServerThreads()
        self.locks: dict[str, asyncio.Lock] = {}
        with closing(jobs.store.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS w3_thread_registry (
                    thread_id TEXT PRIMARY KEY, kind TEXT NOT NULL, parent_id TEXT,
                    run_id TEXT NOT NULL, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS w3_thread_operations (
                    operation_key TEXT PRIMARY KEY, request TEXT NOT NULL,
                    thread_id TEXT, status TEXT NOT NULL);
            """)

    def _row(self, thread_id):
        with closing(self.jobs.store.connect()) as db:
            row = db.execute(
                "SELECT thread_id,kind,parent_id,run_id,status FROM w3_thread_registry "
                "WHERE thread_id=?",
                (thread_id,),
            ).fetchone()
        return (
            dict(zip(("thread_id", "kind", "parent_id", "run_id", "status"), row, strict=True))
            if row else None
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
        if not row or row["kind"] != expected or row["status"] != "ACTIVE" or (
            row["run_id"] != request.run_id
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
                    "INSERT OR IGNORE INTO w3_thread_operations VALUES(?,?,NULL,'CREATING')",
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
        async with self.locks.setdefault("delete:" + thread_id, asyncio.Lock()):
            row = self._row(thread_id)
            if not row or row["kind"] == "TEMPLATE":
                raise ValueError("only registered W3 Case threads may be deleted")
            if row["status"] == "DELETED":
                if row["run_id"].startswith("w3-case-"):
                    self.workspaces.delete_w3_workspace(row["run_id"])
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
                    db.execute("UPDATE w3_thread_registry SET status='ACTIVE' WHERE thread_id=?",
                               (thread_id,))
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
                self.workspaces.delete_w3_workspace(row["run_id"])
            return dict(thread_id=thread_id, status="DELETED")

    async def close(self):
        await self.transport.close()
