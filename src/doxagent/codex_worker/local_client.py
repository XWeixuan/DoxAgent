"""Async adapters for deterministic local workflow tests and embedded fallback."""

from __future__ import annotations

import asyncio

from doxagent.codex_worker.jobs import WorkerJobManager
from doxagent.codex_worker.schema import (
    WorkerJob,
    WorkerRunRequest,
    WorkspaceFileResponse,
    WorkspaceInventory,
)
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.observations.models import PersistedObservation


class LocalWorkspaceClient:
    def __init__(self, store: LocalWorkspaceStore) -> None:
        self.store = store

    async def ensure_research_pack(self, pack_id, files):
        await asyncio.to_thread(self.store.ensure_research_pack, pack_id, files)

    async def materialize_case(self, run_id, packs, files):
        await asyncio.to_thread(self.store.materialize_case, run_id, packs, files)

    async def write_text(
        self, run_id: str, relative_path: str, content: str
    ) -> WorkspaceFileResponse:
        return self.store.write_text(run_id, relative_path, content)

    async def read_text(self, run_id: str, relative_path: str) -> WorkspaceFileResponse:
        return self.store.read_text(run_id, relative_path)

    async def inventory(self, run_id: str, *, prefixes=None) -> WorkspaceInventory:
        return self.store.inventory(run_id, prefixes=prefixes)

    async def snapshot(self, run_id: str, snapshot_id: str) -> None:
        self.store.snapshot(run_id, snapshot_id)

    async def fork_snapshot(self, run_id: str, snapshot_id: str, destination_run_id: str) -> None:
        self.store.fork_snapshot(run_id, snapshot_id, destination_run_id)

    async def read_attempt_observations(
        self,
        run_id: str,
        attempt_id: str,
    ) -> list[PersistedObservation]:
        return self.store.read_attempt_observations(run_id, attempt_id)

    async def import_attempt_observations(
        self,
        run_id: str,
        attempt_id: str,
        observations: list[PersistedObservation],
    ) -> list[PersistedObservation]:
        return self.store.import_attempt_observations(run_id, attempt_id, observations)

    async def publish(self, run_id: str, paths: list[str]) -> WorkspaceInventory:
        return self.store.publish(run_id, paths)


class EmbeddedCodexWorkerClient:
    """Run the normal WorkerJobManager in-process for bounded node-loop evaluation."""

    async def register_template(self, job_id):
        return await self.manager.thread_lifecycle.register_template(job_id)

    async def register_legacy_thread(self, job_id):
        return await self.manager.thread_lifecycle.register_legacy(job_id)

    async def fork_thread(self, request):
        return await self.manager.thread_lifecycle.fork(request)

    async def get_thread_operation(self, key):
        return self.manager.thread_lifecycle.operation(key)

    async def prepare_thread(self, request):
        from doxagent.codex_runtime.errors import W3PreparationError

        value = await self.manager.thread_lifecycle.prepare(request)
        for _ in range(300):
            if value["status"] == "COMPLETED":
                return value
            if value["status"] == "FAILED":
                raise W3PreparationError(
                    value.get("error_code") or "w3_prepare_failed",
                    value.get("error_message") or "W3 prepare failed",
                )
            if value.get("step") == "RECONCILING":
                await self.manager.thread_lifecycle.prepare(request)
            await asyncio.sleep(self.poll_seconds)
            value = self.manager.thread_lifecycle.operation(request.operation_key)
        raise W3PreparationError("w3_prepare_pending", "Preparation is in progress")

    async def thread_status(self, thread_id):
        return await self.manager.thread_lifecycle.status(thread_id)

    async def delete_thread(self, thread_id):
        return await self.manager.thread_lifecycle.delete(thread_id)

    async def acknowledge_job(self, job_id):
        self.manager.store.acknowledge(job_id)

    def __init__(self, manager: WorkerJobManager, *, poll_seconds: float = 0.1) -> None:
        self.manager = manager
        self.poll_seconds = poll_seconds

    async def run(self, request: WorkerRunRequest) -> WorkerJob:
        job = await self.manager.submit(request)
        while job.status in {"queued", "running"}:
            await asyncio.sleep(self.poll_seconds)
            current = self.manager.get(job.job_id)
            if current is None:
                raise RuntimeError(f"embedded worker lost job {job.job_id}")
            job = current
        return job

    async def cancel(self, job_id: str) -> WorkerJob | None:
        return await self.manager.cancel(job_id)
