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

    async def write_text(
        self, run_id: str, relative_path: str, content: str
    ) -> WorkspaceFileResponse:
        return self.store.write_text(run_id, relative_path, content)

    async def read_text(self, run_id: str, relative_path: str) -> WorkspaceFileResponse:
        return self.store.read_text(run_id, relative_path)

    async def inventory(self, run_id: str) -> WorkspaceInventory:
        return self.store.inventory(run_id)

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
