"""Contained, atomic workspace operations for the worker service."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import threading
import zipfile
from collections.abc import Callable
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from doxagent.codex_runtime.errors import (
    AttemptConflict,
    ImmutableWorkspacePath,
    InvalidWorkspacePath,
)
from doxagent.codex_worker.schema import WorkspaceFileResponse, WorkspaceInventory
from doxagent.observations.models import PersistedObservation
from doxagent.observations.store import AttemptObservationStore

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_IMMUTABLE_TOP_LEVEL = {"context", "published"}


class LocalWorkspaceStore:
    """Run-scoped file store with no raw host path exposure.

    Context is write-once, artifacts remain mutable until publication, and
    published files are immutable. Every mutation is serialized per process and
    uses atomic replacement so API and worker processes never observe partial
    files.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def ensure_run(self, run_id: str) -> Path:
        run_root = self._run_root(run_id)
        with self._lock:
            for name in ("context", "attempts", "artifacts", "audit", "published"):
                (run_root / name).mkdir(parents=True, exist_ok=True)
        return run_root

    def snapshot(self, run_id: str, snapshot_id: str) -> None:
        """Immutable pre-node copy, outside the agent-visible workspace."""
        self._validate_identifier(snapshot_id, "snapshot_id")
        source = self.ensure_run(run_id)
        destination = self._snapshot_root(run_id, snapshot_id)
        with self._lock:
            if destination.is_dir():
                return
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._copy_atomic(source, destination)

    def fork_snapshot(self, run_id: str, snapshot_id: str, destination_run_id: str) -> None:
        self._validate_identifier(snapshot_id, "snapshot_id")
        self._validate_identifier(run_id, "run_id")
        destination = self._run_root(destination_run_id)
        source = self._snapshot_root(run_id, snapshot_id)
        with self._lock:
            marker = destination / "audit" / "initialization-fork.json"
            identity = {"source_run_id": run_id, "snapshot_id": snapshot_id}
            if marker.is_file() and json.loads(marker.read_text(encoding="utf-8")) == identity:
                return
            if destination.exists():
                raise AttemptConflict("snapshot fork destination already exists")
            if not source.is_dir():
                raise FileNotFoundError("initialization snapshot not found")
            self._copy_atomic(source, destination, identity=identity)

    def _snapshot_root(self, run_id: str, snapshot_id: str) -> Path:
        # Avoid repeating long run/attempt IDs in Windows workspace paths.
        identity = hashlib.sha256(f"{run_id}:{snapshot_id}".encode()).hexdigest()[:32]
        return self.root / ".init" / identity

    def _copy_atomic(
        self, source: Path, destination: Path, *, identity: dict[str, str] | None = None
    ) -> None:
        if source.is_symlink() or any(
            item.is_symlink() or not self._is_contained(item, source) for item in source.rglob("*")
        ):
            raise InvalidWorkspacePath("snapshot cannot include symlinks or escaping paths")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".s-", dir=destination.parent) as temporary:
            staging = Path(temporary) / "workspace"
            shutil.copytree(source, staging)
            if identity is not None:
                marker = staging / "audit" / "initialization-fork.json"
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(json.dumps(identity, sort_keys=True), encoding="utf-8")
            os.replace(staging, destination)

    def ensure_attempt(self, run_id: str, attempt_id: str) -> Path:
        self._validate_identifier(attempt_id, "attempt_id")
        attempt = self.ensure_run(run_id) / "attempts" / attempt_id
        with self._lock:
            (attempt / "input").mkdir(parents=True, exist_ok=True)
            (attempt / "output").mkdir(parents=True, exist_ok=True)
            (attempt / "audit").mkdir(parents=True, exist_ok=True)
        return attempt

    def write_text(
        self,
        run_id: str,
        relative_path: str,
        content: str,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceFileResponse:
        target = self._resolve(run_id, relative_path, for_write=True)
        raw = content.encode("utf-8")
        with self._lock:
            if target.exists():
                current_hash = self._sha256_path(target)
                if expected_sha256 is not None and current_hash != expected_sha256:
                    raise AttemptConflict("workspace file changed since it was read")
                if self._is_immutable(relative_path):
                    if target.read_bytes() == raw:
                        return self._metadata(run_id, target, include_content=False)
                    raise ImmutableWorkspacePath(f"{self._top_level(relative_path)} is immutable")
            elif expected_sha256 is not None:
                raise AttemptConflict("expected workspace file does not exist")
            target.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(target, raw)
        return self._metadata(run_id, target, include_content=False)

    def read_text(self, run_id: str, relative_path: str) -> WorkspaceFileResponse:
        target = self._resolve(run_id, relative_path)
        if not target.is_file():
            raise FileNotFoundError(relative_path)
        result = self._metadata(run_id, target, include_content=False)
        return result.model_copy(update={"content": target.read_text(encoding="utf-8")})

    def inventory(self, run_id: str) -> WorkspaceInventory:
        run_root = self.ensure_run(run_id)
        files = [
            self._metadata(run_id, path, include_content=False)
            for path in sorted(run_root.rglob("*"))
            if path.is_file() and self._is_contained(path, run_root)
        ]
        return WorkspaceInventory(run_id=run_id, files=files)

    def publish(self, run_id: str, artifact_paths: list[str]) -> WorkspaceInventory:
        """Copy selected artifacts into an immutable checksum-addressed release."""

        run_root = self.ensure_run(run_id)
        sources = [self._resolve(run_id, item) for item in artifact_paths]
        for source in sources:
            if not source.is_file() or source.relative_to(run_root).parts[0] != "artifacts":
                raise InvalidWorkspacePath("only artifact files may be published")
        manifest_items = [
            {
                "relative_path": source.relative_to(run_root).as_posix(),
                "sha256": self._sha256_path(source),
                "size_bytes": source.stat().st_size,
            }
            for source in sources
        ]
        release_id = hashlib.sha256(
            json.dumps(manifest_items, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:20]
        release_root = run_root / "published" / release_id
        with self._lock:
            if not release_root.exists():
                staging = Path(
                    tempfile.mkdtemp(prefix=f".{release_id}-", dir=run_root / "published")
                )
                try:
                    for source in sources:
                        destination = staging / source.relative_to(run_root / "artifacts")
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, destination)
                    self._atomic_write(
                        staging / "manifest.json",
                        json.dumps(
                            {"release_id": release_id, "artifacts": manifest_items},
                            ensure_ascii=False,
                            indent=2,
                        ).encode("utf-8"),
                    )
                    os.replace(staging, release_root)
                finally:
                    if staging.exists():
                        shutil.rmtree(staging)
        return self.inventory(run_id)

    def export_zip(
        self,
        run_id: str,
        target: BinaryIO,
        *,
        control_attempt_id: str | None = None,
    ) -> str:
        self.ensure_run(run_id)
        exported: list[dict[str, object]] = []
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in self.inventory(run_id).files:
                path = self._resolve(run_id, item.relative_path)
                archive.write(path, arcname=item.relative_path)
                exported.append(item.model_dump(mode="json"))
            if control_attempt_id is not None:
                self._validate_identifier(control_attempt_id, "control_attempt_id")
                control_root = (self.root / ".control").resolve()
                control_attempt = (control_root / run_id / control_attempt_id).resolve()
                if not self._is_contained(control_attempt, control_root):
                    raise InvalidWorkspacePath("control attempt escaped workspace root")
                if control_attempt.is_dir():
                    for path in sorted(control_attempt.rglob("*")):
                        if not path.is_file() or not self._is_contained(path, control_attempt):
                            continue
                        if path.name in {
                            "observations.sqlite3",
                            "observations.sqlite3-shm",
                            "observations.sqlite3-wal",
                        }:
                            continue
                        relative = path.relative_to(control_attempt).as_posix()
                        arcname = f".control/{run_id}/{control_attempt_id}/{relative}"
                        archive.write(path, arcname=arcname)
                        exported.append(
                            {
                                "relative_path": arcname,
                                "sha256": self._sha256_path(path),
                                "size_bytes": path.stat().st_size,
                            }
                        )
                    database = control_attempt / "observations.sqlite3"
                    if database.is_file():
                        with tempfile.TemporaryDirectory(prefix="doxagent-export-") as temporary:
                            snapshot = Path(temporary) / "observations.sqlite3"
                            with closing(sqlite3.connect(database, timeout=10)) as source:
                                with closing(sqlite3.connect(snapshot)) as destination:
                                    source.backup(destination)
                            arcname = f".control/{run_id}/{control_attempt_id}/observations.sqlite3"
                            archive.write(snapshot, arcname=arcname)
                            exported.append(
                                {
                                    "relative_path": arcname,
                                    "sha256": self._sha256_path(snapshot),
                                    "size_bytes": snapshot.stat().st_size,
                                }
                            )
        return hashlib.sha256(json.dumps(exported, sort_keys=True).encode()).hexdigest()

    def delete_attempt(self, run_id: str, attempt_id: str) -> None:
        self._validate_identifier(run_id, "run_id")
        self._validate_identifier(attempt_id, "attempt_id")
        attempt = self.ensure_run(run_id) / "attempts" / attempt_id
        control_attempt = (self.root / ".control" / run_id / attempt_id).resolve()
        control_root = (self.root / ".control").resolve()
        if not self._is_contained(control_attempt, control_root):
            raise InvalidWorkspacePath("attempt control path escaped workspace root")
        with self._lock:
            if attempt.exists():
                _remove_read_only_tree(attempt)
            if control_attempt.exists():
                _remove_read_only_tree(control_attempt)

    def read_attempt_observations(
        self,
        run_id: str,
        attempt_id: str,
    ) -> list[PersistedObservation]:
        """Read the private canonical store without trusting workspace projections."""

        self._validate_identifier(run_id, "run_id")
        self._validate_identifier(attempt_id, "attempt_id")
        control_root = (self.root / ".control").resolve()
        database = (control_root / run_id / attempt_id / "observations.sqlite3").resolve()
        if not self._is_contained(database, control_root):
            raise InvalidWorkspacePath("observation control path escaped workspace root")
        if not database.is_file():
            return []
        uri = database.as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=10)) as connection:
            rows = connection.execute(
                "SELECT record_json FROM observations ORDER BY alias_number"
            ).fetchall()
        values = [PersistedObservation.model_validate_json(row[0]) for row in rows]
        if any(item.run_id != run_id or item.attempt_id != attempt_id for item in values):
            raise InvalidWorkspacePath("observation store scope mismatch")
        return values

    def import_attempt_observations(
        self,
        run_id: str,
        attempt_id: str,
        observations: list[PersistedObservation],
    ) -> list[PersistedObservation]:
        """Rehydrate verified upstream observations into a new attempt-local alias store."""

        attempt_root = self.ensure_attempt(run_id, attempt_id)
        store = AttemptObservationStore(
            control_root=self.root / ".control" / run_id / attempt_id,
            mirror_root=attempt_root / "audit" / "observations",
            run_id=run_id,
            attempt_id=attempt_id,
        )
        imported: list[PersistedObservation] = []
        for observation in observations:
            if observation.run_id != run_id or observation.attempt_id != attempt_id:
                raise InvalidWorkspacePath("imported observation scope mismatch")
            imported.append(store.save_observation(observation.model_copy(update={"alias": ""})))
        return imported

    def _run_root(self, run_id: str) -> Path:
        self._validate_identifier(run_id, "run_id")
        path = (self.root / run_id).resolve()
        if not self._is_contained(path, self.root):
            raise InvalidWorkspacePath("run escaped workspace root")
        return path

    def _resolve(self, run_id: str, relative_path: str, *, for_write: bool = False) -> Path:
        run_root = self.ensure_run(run_id)
        normalized = relative_path.replace("\\", "/")
        pure = PurePosixPath(normalized)
        if (
            pure.is_absolute()
            or not pure.parts
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise InvalidWorkspacePath("path must be a normalized non-empty relative path")
        if ":" in pure.parts[0]:
            raise InvalidWorkspacePath("drive-qualified paths are forbidden")
        target = run_root.joinpath(*pure.parts)
        self._reject_link_components(target, run_root, include_target=not for_write)
        resolved_parent = target.parent.resolve()
        if not self._is_contained(resolved_parent, run_root):
            raise InvalidWorkspacePath("path escaped run workspace")
        return target

    @staticmethod
    def _validate_identifier(value: str, name: str) -> None:
        if not _IDENTIFIER.fullmatch(value):
            raise InvalidWorkspacePath(f"invalid {name}")

    @staticmethod
    def _top_level(relative_path: str) -> str:
        return PurePosixPath(relative_path.replace("\\", "/")).parts[0]

    @staticmethod
    def _is_immutable(relative_path: str) -> bool:
        parts = PurePosixPath(relative_path.replace("\\", "/")).parts
        return parts[0] in _IMMUTABLE_TOP_LEVEL or (
            len(parts) >= 3 and parts[0] == "attempts" and parts[2] == "input"
        )

    @staticmethod
    def _is_contained(path: Path, parent: Path) -> bool:
        try:
            path.resolve().relative_to(parent.resolve())
            return True
        except ValueError:
            return False

    @staticmethod
    def _reject_link_components(target: Path, run_root: Path, *, include_target: bool) -> None:
        current = run_root
        relative_parts = target.relative_to(run_root).parts
        checked_parts = relative_parts if include_target else relative_parts[:-1]
        for part in checked_parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise InvalidWorkspacePath("workspace links are forbidden")

    @staticmethod
    def _atomic_write(target: Path, raw: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, target)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    @staticmethod
    def _sha256_path(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _metadata(self, run_id: str, path: Path, *, include_content: bool) -> WorkspaceFileResponse:
        relative_path = path.relative_to(self._run_root(run_id)).as_posix()
        content_type = "application/json" if path.suffix.lower() == ".json" else "text/plain"
        return WorkspaceFileResponse(
            relative_path=relative_path,
            sha256=self._sha256_path(path),
            size_bytes=path.stat().st_size,
            content_type=content_type,
            content=path.read_text(encoding="utf-8") if include_content else None,
        )


def _remove_read_only_tree(path: Path) -> None:
    def clear_and_retry(
        function: Callable[[str], object],
        target: str,
        _error_info: object,
    ) -> None:
        os.chmod(target, stat.S_IWRITE)
        function(target)

    shutil.rmtree(path, onerror=clear_and_retry)
