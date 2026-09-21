"""Consistent, access-controlled Profile snapshots for browser upgrades."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import tarfile
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .repository import SiteStrategyRepository
from .runtime import OwnerFileLock
from .schema import BrowserProfile


class ProfileSnapshotManager:
    def __init__(self, profile_root: str | Path, repository: SiteStrategyRepository) -> None:
        self.profile_root = Path(profile_root).resolve()
        self.repository = repository
        self.snapshot_root = (self.profile_root.parent / "snapshots").resolve()
        self.snapshot_root.mkdir(parents=True, exist_ok=True)

    def create(self, profile: BrowserProfile, *, browser_version: str) -> dict[str, str]:
        source = (self.profile_root / profile.directory_key).resolve()
        if source.parent != self.profile_root or not source.is_dir():
            raise ValueError("profile directory is missing or outside the profile root")
        lock = OwnerFileLock(self.profile_root / ".profile-locks" / f"{profile.directory_key}.lock")
        lock.acquire()
        try:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            snapshot_id = f"{profile.profile_id}-{stamp}"
            archive = self.snapshot_root / f"{snapshot_id}.tar.gz"
            with tempfile.TemporaryDirectory(dir=self.snapshot_root) as temp_name:
                temporary = Path(temp_name)
                self.repository.backup_to(temporary / "site_strategy.sqlite3")
                metadata = {
                    "snapshot_id": snapshot_id,
                    "profile": profile.model_dump(mode="json"),
                    "browser_version": browser_version,
                    "created_at": datetime.now(UTC).isoformat(),
                }
                (temporary / "snapshot.json").write_text(
                    json.dumps(metadata, sort_keys=True), encoding="utf-8"
                )
                with tarfile.open(archive, "w:gz") as bundle:
                    bundle.add(source, arcname="profile", recursive=True)
                    bundle.add(temporary / "site_strategy.sqlite3", arcname="registry.sqlite3")
                    bundle.add(temporary / "snapshot.json", arcname="snapshot.json")
            os.chmod(archive, 0o600)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            manifest = archive.with_suffix(".json")
            manifest.write_text(
                json.dumps({**metadata, "sha256": digest, "archive": archive.name}, sort_keys=True),
                encoding="utf-8",
            )
            os.chmod(manifest, 0o600)
            profile_manifest = source / "doxagent-profile-manifest.json"
            current_manifest: dict[str, object] = {}
            if profile_manifest.is_file():
                current_manifest = json.loads(profile_manifest.read_text(encoding="utf-8"))
            temporary_manifest = profile_manifest.with_suffix(".json.tmp")
            temporary_manifest.write_text(
                json.dumps(
                    {**current_manifest, "last_snapshot_id": snapshot_id}, sort_keys=True
                ),
                encoding="utf-8",
            )
            os.replace(temporary_manifest, profile_manifest)
            os.chmod(profile_manifest, 0o600)
            self._prune(profile.profile_id)
            return {"snapshot_id": snapshot_id, "sha256": digest, "archive": str(archive)}
        finally:
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
        actual_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if not expected_digest or not hmac.compare_digest(expected_digest, actual_digest):
            raise ValueError("snapshot checksum mismatch")
        lock = OwnerFileLock(self.profile_root / ".profile-locks" / f"{profile.directory_key}.lock")
        lock.acquire()
        try:
            target = (self.profile_root / profile.directory_key).resolve()
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

    def _prune(self, profile_id: str) -> None:
        threshold = datetime.now(UTC) - timedelta(days=14)
        manifests = sorted(
            self.snapshot_root.glob(f"{profile_id}-*.tar.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for index, manifest in enumerate(manifests):
            modified = datetime.fromtimestamp(manifest.stat().st_mtime, UTC)
            if index < 2 or modified >= threshold:
                continue
            archive_name = json.loads(manifest.read_text(encoding="utf-8")).get("archive")
            archive = self.snapshot_root / str(archive_name)
            if archive.is_file():
                archive.unlink()
            manifest.unlink()


__all__ = ["ProfileSnapshotManager"]
