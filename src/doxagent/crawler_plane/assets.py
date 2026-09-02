"""Filesystem ownership for working copies, immutable releases and artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

from doxagent.crawler_plane.schema import ExecutionArtifact, NetworkCassette


class CrawlerAssetStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.working_root = self.root / "working"
        self.releases_root = self.root / "releases"
        self.cassettes_root = self.root / "cassettes"
        self.artifacts_root = self.root / "artifacts"
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o777)
        for path in (
            self.working_root,
            self.releases_root,
            self.cassettes_root,
            self.artifacts_root,
        ):
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(0o777)

    def working_path(self, crawler_id: str, version: int) -> Path:
        return self._inside(self.working_root / crawler_id / f"v{version}")

    def release_path(self, crawler_id: str, version: int) -> Path:
        return self._inside(self.releases_root / crawler_id / f"v{version}")

    def create_working(self, crawler_id: str, version: int, *, base: Path | None = None) -> Path:
        target = self.working_path(crawler_id, version)
        if target.exists():
            raise FileExistsError(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        if base is None:
            target.mkdir()
        else:
            resolved = base.resolve()
            if not resolved.is_dir():
                raise FileNotFoundError(resolved)
            shutil.copytree(resolved, target)
        self._make_writable(target)
        return target

    def promote(self, crawler_id: str, version: int) -> Path:
        source = self.working_path(crawler_id, version)
        target = self.release_path(crawler_id, version)
        if not source.is_dir():
            raise FileNotFoundError(source)
        if target.exists():
            raise FileExistsError(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".{target.name}.promoting-{uuid4().hex}"
        source.replace(temporary)
        temporary.replace(target)
        self._make_read_only(target)
        return target

    def digest(self, path: str | Path) -> str:
        root = Path(path).resolve()
        if not root.is_dir():
            raise FileNotFoundError(root)
        digest = hashlib.sha256()
        for item in sorted(
            value
            for value in root.rglob("*")
            if value.is_file() and value.suffix != ".pyc" and "__pycache__" not in value.parts
        ):
            relative = item.relative_to(root).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(item.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    def save_cassette(self, cassette: NetworkCassette) -> NetworkCassette:
        directory = self._inside(self.cassettes_root / cassette.crawler_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{cassette.cassette_id}.json"
        saved = cassette.model_copy(update={"path": str(path)})
        self._atomic_json(path, saved.model_dump(mode="json"))
        return saved

    def load_cassette(self, ref: str) -> NetworkCassette:
        candidate = Path(ref)
        if not candidate.is_absolute():
            candidate = self.cassettes_root / ref
        candidate = self._inside(candidate)
        return NetworkCassette.model_validate_json(candidate.read_text(encoding="utf-8"))

    def save_artifact(
        self,
        execution_id: str,
        kind: str,
        name: str,
        content: bytes,
    ) -> ExecutionArtifact:
        safe_name = Path(name).name
        directory = self._inside(self.artifacts_root / execution_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = self._inside(directory / safe_name)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, path)
        path.chmod(0o666)
        return ExecutionArtifact(
            execution_id=execution_id,
            kind=kind,
            path=str(path),
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )

    def _inside(self, path: Path) -> Path:
        resolved = path.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError("crawler asset path escapes the Crawler Plane root")
        return resolved

    @staticmethod
    def _atomic_json(path: Path, value: object) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str),
            encoding="utf-8",
        )
        os.replace(temporary, path)
        path.chmod(0o666)

    @staticmethod
    def _make_read_only(root: Path) -> None:
        for item in root.rglob("*"):
            if item.is_file():
                item.chmod(0o444)
            elif item.is_dir():
                item.chmod(0o555)
        root.chmod(0o555)

    @staticmethod
    def _make_writable(root: Path) -> None:
        root.chmod(0o777)
        for item in root.rglob("*"):
            item.chmod(0o777 if item.is_dir() else 0o666)


__all__ = ["CrawlerAssetStore"]
