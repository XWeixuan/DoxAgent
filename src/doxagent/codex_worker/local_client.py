"""Async adapter for deterministic local workflow tests and embedded fallback."""

from doxagent.codex_worker.schema import WorkspaceFileResponse, WorkspaceInventory
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

    async def publish(self, run_id: str, paths: list[str]) -> WorkspaceInventory:
        return self.store.publish(run_id, paths)
