"""WorkspaceClient implementation rooted directly at a removable Pilot case."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path, PurePosixPath

from doxagent.codex_runtime.errors import InvalidWorkspacePath
from doxagent.codex_worker.schema import WorkspaceFileResponse, WorkspaceInventory
from doxagent.observations.models import PersistedObservation


class PilotCaseWorkspace:
    """Minimal direct-root workspace used only while materializing a case."""

    def __init__(self, case_root: str | Path) -> None:
        self.root = Path(case_root).resolve()

    async def write_text(
        self, run_id: str, relative_path: str, content: str
    ) -> WorkspaceFileResponse:
        del run_id
        target = self._resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return self._metadata(target, include_content=False)

    async def read_text(self, run_id: str, relative_path: str) -> WorkspaceFileResponse:
        del run_id
        target = self._resolve(relative_path)
        if not target.is_file():
            raise FileNotFoundError(relative_path)
        return self._metadata(target, include_content=True)

    async def inventory(self, run_id: str) -> WorkspaceInventory:
        files = [
            self._metadata(path, include_content=False)
            for path in sorted(self.root.rglob("*"))
            if path.is_file() and not path.is_symlink()
        ]
        return WorkspaceInventory(run_id=run_id, files=files)

    async def read_attempt_observations(
        self, run_id: str, attempt_id: str
    ) -> list[PersistedObservation]:
        database = self._resolve(f".control/{run_id}/{attempt_id}/observations.sqlite3")
        if not database.is_file():
            return []
        uri = database.as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=10)) as connection:
            rows = connection.execute(
                "SELECT record_json FROM observations ORDER BY alias_number"
            ).fetchall()
        return [PersistedObservation.model_validate_json(row[0]) for row in rows]

    async def import_attempt_observations(
        self,
        run_id: str,
        attempt_id: str,
        observations: list[PersistedObservation],
    ) -> list[PersistedObservation]:
        del run_id, attempt_id, observations
        raise NotImplementedError("Pilot case generation never imports observations")

    async def publish(self, run_id: str, paths: list[str]) -> WorkspaceInventory:
        del run_id, paths
        raise NotImplementedError("Pilot cases cannot publish")

    def _resolve(self, relative_path: str) -> Path:
        pure = PurePosixPath(relative_path.replace("\\", "/"))
        if (
            pure.is_absolute()
            or not pure.parts
            or any(part in {"", ".", ".."} for part in pure.parts)
            or ":" in pure.parts[0]
        ):
            raise InvalidWorkspacePath("Pilot case path must be normalized and relative")
        target = self.root.joinpath(*pure.parts)
        try:
            target.resolve().relative_to(self.root)
        except ValueError as exc:
            raise InvalidWorkspacePath("Pilot case path escaped root") from exc
        current = target
        while current != self.root:
            if current.exists() and current.is_symlink():
                raise InvalidWorkspacePath("Pilot case links are forbidden")
            current = current.parent
        return target

    def _metadata(self, path: Path, *, include_content: bool) -> WorkspaceFileResponse:
        raw = path.read_bytes()
        suffix = path.suffix.lower()
        content_type = "application/json" if suffix == ".json" else "text/plain"
        return WorkspaceFileResponse(
            relative_path=path.relative_to(self.root).as_posix(),
            sha256=hashlib.sha256(raw).hexdigest(),
            size_bytes=len(raw),
            content_type=content_type,
            content=raw.decode("utf-8") if include_content else None,
        )
