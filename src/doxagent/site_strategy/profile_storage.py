"""Consistent, access-controlled Profile snapshots for browser upgrades."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import tarfile
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from .repository import SiteStrategyRepository
from .runtime import OwnerFileLock, profile_browser_processes
from .schema import BrowserProfile


class ProfileSnapshotManager:
    _RUNTIME_SINGLETONS = {"SingletonLock", "SingletonSocket", "SingletonCookie"}
    _REGENERABLE = {
        "Cache",
        "Code Cache",
        "GPUCache",
        "GrShaderCache",
        "ShaderCache",
        "DawnGraphiteCache",
        "DawnWebGPUCache",
        "BrowserMetrics",
        "Crashpad",
    }

    def __init__(self, profile_root: str | Path, repository: SiteStrategyRepository) -> None:
        self.profile_root = Path(profile_root).resolve()
        self.repository = repository
        self.snapshot_root = (self.profile_root.parent / "snapshots").resolve()
        self.snapshot_root.mkdir(parents=True, exist_ok=True)
        self._batch_lock = threading.Lock()
        self._registry_hashes = {}

    @staticmethod
    def _hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def create(
        self,
        profile: BrowserProfile,
        *,
        browser_version: str,
        batch_id: str | None = None,
    ) -> dict[str, str]:
        source = (self.profile_root / profile.directory_key).resolve()
        if source.parent != self.profile_root or not source.is_dir():
            raise ValueError("profile directory is missing or outside the profile root")
        lock = OwnerFileLock(self.profile_root / ".profile-locks" / f"{profile.directory_key}.lock")
        lock.acquire()
        archive = None
        manifest = None
        committed = False
        try:
            if profile_browser_processes(source):
                raise RuntimeError("profile has a live Chrome writer")
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            snapshot_id = f"{profile.profile_id}-{stamp}"
            archive = self.snapshot_root / f"{snapshot_id}.tar.gz"
            with tempfile.TemporaryDirectory(dir=self.snapshot_root) as temp_name:
                temporary = Path(temp_name)
                batch = batch_id or snapshot_id
                if not batch or any(
                    char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
                    for char in batch
                ):
                    raise ValueError("invalid snapshot batch id")
                registry = self.snapshot_root / f"registry-{batch}.sqlite3"
                with self._batch_lock:
                    batch_lock = OwnerFileLock(
                        self.snapshot_root / ".batch-locks" / f"{batch}.lock"
                    )
                    batch_lock.acquire()
                    try:
                        if not registry.exists():
                            staged_registry = temporary / "site_strategy.sqlite3"
                            self.repository.backup_to(staged_registry)
                            os.chmod(staged_registry, 0o600)
                            os.replace(staged_registry, registry)
                        registry_hash = self._registry_hashes.get(registry)
                        if registry_hash is None:
                            registry_hash = self._hash(registry)
                            self._registry_hashes[registry] = registry_hash
                    finally:
                        batch_lock.release()
                metadata = {
                    "snapshot_id": snapshot_id,
                    "profile": profile.model_dump(mode="json"),
                    "browser_version": browser_version,
                    "created_at": datetime.now(UTC).isoformat(),
                    "registry_backup": registry.name,
                    "registry_sha256": registry_hash,
                }
                (temporary / "snapshot.json").write_text(
                    json.dumps(metadata, sort_keys=True), encoding="utf-8"
                )
                staged_archive = temporary / archive.name
                with tarfile.open(staged_archive, "w:gz") as bundle:

                    def profile_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
                        if Path(info.name).name in self._RUNTIME_SINGLETONS or any(
                            part in self._REGENERABLE for part in Path(info.name).parts
                        ):
                            return None
                        if info.issym() or info.islnk() or not (info.isfile() or info.isdir()):
                            raise ValueError(f"unsupported special file in profile: {info.name}")
                        return info

                    bundle.add(
                        source,
                        arcname="profile",
                        recursive=True,
                        filter=profile_filter,
                    )
                    bundle.add(temporary / "snapshot.json", arcname="snapshot.json")
                os.replace(staged_archive, archive)
            os.chmod(archive, 0o600)
            digest = self._hash(archive)
            manifest = archive.with_suffix(".json")
            manifest.write_text(
                json.dumps({**metadata, "sha256": digest, "archive": archive.name}, sort_keys=True),
                encoding="utf-8",
            )
            os.chmod(manifest, 0o600)
            committed = True
            profile_manifest = source / "doxagent-profile-manifest.json"
            current_manifest: dict[str, object] = {}
            if profile_manifest.is_file():
                current_manifest = json.loads(profile_manifest.read_text(encoding="utf-8"))
            temporary_manifest = profile_manifest.with_suffix(".json.tmp")
            temporary_manifest.write_text(
                json.dumps({**current_manifest, "last_snapshot_id": snapshot_id}, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary_manifest, profile_manifest)
            os.chmod(profile_manifest, 0o600)
            self._prune(profile.profile_id)
            return {"snapshot_id": snapshot_id, "sha256": digest, "archive": str(archive)}
        finally:
            if not committed:
                if archive:
                    archive.unlink(missing_ok=True)
                if manifest:
                    manifest.unlink(missing_ok=True)
            lock.release()

    def restore(self, profile: BrowserProfile, snapshot_id: str) -> dict[str, str]:
        if not snapshot_id.startswith(f"{profile.profile_id}-"):
            raise ValueError("snapshot does not belong to this profile")
        archive = (self.snapshot_root / f"{snapshot_id}.tar.gz").resolve()
        if archive.parent != self.snapshot_root or not archive.is_file():
            raise FileNotFoundError(snapshot_id)
        manifest = archive.with_suffix(".json")
        if not manifest.is_file():
            raise FileNotFoundError(f"{snapshot_id} manifest")
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
        if metadata.get("snapshot_id") != snapshot_id:
            raise ValueError("snapshot manifest identity mismatch")
        expected_digest = str(metadata.get("sha256") or "")
        actual_digest = self._hash(archive)
        if not expected_digest or not hmac.compare_digest(expected_digest, actual_digest):
            raise ValueError("snapshot checksum mismatch")
        lock = OwnerFileLock(self.profile_root / ".profile-locks" / f"{profile.directory_key}.lock")
        lock.acquire()
        try:
            target = (self.profile_root / profile.directory_key).resolve()
            if profile_browser_processes(target):
                raise RuntimeError("profile has a live Chrome writer")
            if target.parent != self.profile_root:
                raise ValueError("profile directory is outside the profile root")
            with tempfile.TemporaryDirectory(dir=self.profile_root) as temp_name:
                temporary = Path(temp_name)
                with tarfile.open(archive, "r:gz") as bundle:
                    for member in bundle.getmembers():
                        member_path = (temporary / member.name).resolve()
                        if temporary not in member_path.parents and member_path != temporary:
                            raise ValueError("unsafe path in profile snapshot")
                        if not (member.isfile() or member.isdir()):
                            raise ValueError("unsafe special file in profile snapshot")
                    bundle.extractall(temporary)
                restored = temporary / "profile"
                if not restored.is_dir():
                    raise ValueError("snapshot profile payload is missing")
                quarantine = self.profile_root / (
                    f"{profile.directory_key}.quarantine-"
                    + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                )
                if target.exists():
                    target.replace(quarantine)
                try:
                    shutil.move(str(restored), str(target))
                except Exception:
                    if quarantine.exists() and not target.exists():
                        quarantine.replace(target)
                    raise
            return {"snapshot_id": snapshot_id, "profile_directory": str(target)}
        finally:
            lock.release()

    def maintain(self) -> None:
        """Compensate a missed create-time prune using the existing owner loop."""
        for profile in self.repository.list_profiles():
            if profile.maintenance_session_id:continue
            lock=OwnerFileLock(self.profile_root/'.profile-locks'/f'{profile.directory_key}.lock')
            try:
                lock.acquire()
                self._prune(profile.profile_id)
            except RuntimeError:
                continue  # A browser/restore owns this profile; next pass retries.
            finally:lock.release()

    def _prune(self, profile_id: str) -> None:
        manifests = sorted(
            self.snapshot_root.glob(f"{profile_id}-*.tar.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        ordinary = 0
        for manifest in manifests:
            metadata = json.loads(manifest.read_text(encoding="utf-8"))
            if metadata.get("protected"):
                continue
            ordinary += 1
            if ordinary <= 2:
                continue
            archive = (self.snapshot_root / str(metadata.get("archive"))).resolve()
            if archive.parent != self.snapshot_root:
                raise ValueError("snapshot archive is outside snapshot root")
            if archive.is_file():
                archive.unlink()
            manifest.unlink()
        references = set()
        for manifest in self.snapshot_root.glob("*.tar.json"):
            references.add(json.loads(manifest.read_text(encoding="utf-8")).get("registry_backup"))
        for registry in self.snapshot_root.glob("registry-*.sqlite3"):
            if (
                registry.name not in references
                and time.time() - registry.stat().st_mtime > 7 * 86400
            ):
                registry.unlink()
                self._registry_hashes.pop(registry, None)


__all__ = ["ProfileSnapshotManager"]
