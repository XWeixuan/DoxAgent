"""Immutable CDECR prebuilt bundles and their local filesystem lifecycle."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import sqlite3
import tarfile
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager, closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from cdecr.config import CDECRSettings
from doxagent.event_library.contracts import StrictModel

MANIFEST_NAME = "manifest.json"
REGISTRY_NAME = "runtime.sqlite3"


class PrebuiltError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


class RegistryArtifact(StrictModel):
    file: Literal["runtime.sqlite3"] = "runtime.sqlite3"
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class CDECRPrebuiltManifest(StrictModel):
    contract_version: Literal["cdecr-prebuilt-bundle-v1"] = "cdecr-prebuilt-bundle-v1"
    bundle_id: str = Field(pattern=r"^cdecr-[A-Z0-9._-]+-[A-Z0-9._-]+-[0-9TZ]+-[0-9a-f]{12}$")
    market: str = Field(min_length=1)
    ticker: str = Field(min_length=1)
    runtime_scope: str = Field(min_length=1)
    research_cutoff_at: datetime
    generated_at: datetime
    code_revision: str = Field(min_length=1)
    compatibility_version: str = Field(min_length=1)
    configuration_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    epoch_id: str = Field(min_length=1)
    epoch_status: Literal["FINALIZED"] = "FINALIZED"
    message_ids: list[str]
    message_set_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    document_count: int = Field(ge=0)
    eligible_document_count: int = Field(ge=0)
    registry: RegistryArtifact

    @field_validator("market", "ticker")
    @classmethod
    def normalize_scope(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("research_cutoff_at", "generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("bundle timestamps must include timezone")
        return value

    @field_validator("message_ids")
    @classmethod
    def require_sorted_unique_ids(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("message IDs must not be blank")
        if values != sorted(set(values)):
            raise ValueError("message IDs must be sorted and unique")
        return values

    @model_validator(mode="after")
    def verify_identity(self) -> CDECRPrebuiltManifest:
        if self.runtime_scope != f"cdecr:{self.market}:{self.ticker}":
            raise ValueError("runtime scope does not match market and ticker")
        if self.message_set_sha256 != message_set_sha256(self.message_ids):
            raise ValueError("message set digest mismatch")
        if self.document_count != len(self.message_ids):
            raise ValueError("document count must match the message set")
        if self.eligible_document_count > self.document_count:
            raise ValueError("eligible document count exceeds document count")
        if not self.bundle_id.endswith(self.registry.sha256[-12:]):
            raise ValueError("bundle identity does not match registry digest")
        return self


class CDECRPrebuiltRef(StrictModel):
    contract_version: Literal["cdecr-prebuilt-ref-v1"] = "cdecr-prebuilt-ref-v1"
    bundle_id: str = Field(min_length=1)
    claim_owner: str = Field(min_length=1)
    registry_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    epoch_id: str = Field(min_length=1)


class CDECRPrebuiltConsumptionReceipt(StrictModel):
    contract_version: Literal["cdecr-prebuilt-consumption-v1"] = "cdecr-prebuilt-consumption-v1"
    initialization_id: str
    control_operation_id: str
    bundle_id: str
    registry_sha256: str
    epoch_id: str
    research_cutoff_at: datetime
    adopted_at: datetime
    target_registry: str


class ValidatedBundle(StrictModel):
    path: Path
    manifest: CDECRPrebuiltManifest


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def message_set_sha256(message_ids: Iterable[str]) -> str:
    payload = json.dumps(list(message_ids), ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def configuration_fingerprint(settings: CDECRSettings | None = None) -> str:
    resolved = settings or CDECRSettings()
    excluded = {
        "sqlite_path",
        "supabase_url",
        "supabase_publishable_key",
        "dashscope_api_key",
        "dashscope_fallback_api_key",
        "dashscope_fallback_api_keys_csv",
        "deepseek_api_key",
    }
    payload = {
        "settings": resolved.model_dump(mode="json", exclude=excluded),
        "implementation_sha256": _implementation_sha256(),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def export_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    with closing(sqlite3.connect(source)) as source_db:
        with source_db:
            source_db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            with closing(sqlite3.connect(destination)) as destination_db:
                with destination_db:
                    source_db.backup(destination_db)
                    destination_db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    with destination.open("r+b") as stream:
        os.fsync(stream.fileno())


def validate_bundle(
    bundle_path: str | Path,
    *,
    containing_root: str | Path,
    expected_compatibility_version: str | None = None,
    expected_configuration_fingerprint: str | None = None,
    max_age_hours: int | None = None,
    now: datetime | None = None,
    integrity_check: bool = False,
) -> ValidatedBundle:
    root = Path(containing_root).resolve()
    bundle = Path(bundle_path)
    if bundle.is_symlink():
        raise PrebuiltError("CDECR_PREBUILT_INVALID", "bundle path is a symlink")
    resolved = bundle.resolve()
    if resolved == root or root not in resolved.parents or not resolved.is_dir():
        raise PrebuiltError("CDECR_PREBUILT_INVALID", "bundle escapes configured root")
    entries = list(resolved.iterdir())
    if {item.name for item in entries} != {MANIFEST_NAME, REGISTRY_NAME}:
        raise PrebuiltError("CDECR_PREBUILT_INVALID", "bundle must contain exactly two files")
    if any(item.is_symlink() or not item.is_file() for item in entries):
        raise PrebuiltError("CDECR_PREBUILT_INVALID", "bundle contents must be regular files")
    try:
        manifest = CDECRPrebuiltManifest.model_validate_json(
            (resolved / MANIFEST_NAME).read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise PrebuiltError("CDECR_PREBUILT_INVALID", "manifest validation failed") from exc
    if manifest.bundle_id != resolved.name:
        raise PrebuiltError("CDECR_PREBUILT_INVALID", "bundle directory identity mismatch")
    if expected_compatibility_version is not None and (
        manifest.compatibility_version != expected_compatibility_version
    ):
        raise PrebuiltError("CDECR_PREBUILT_INCOMPATIBLE", "compatibility version mismatch")
    if expected_configuration_fingerprint is not None and (
        manifest.configuration_fingerprint != expected_configuration_fingerprint
    ):
        raise PrebuiltError("CDECR_PREBUILT_INCOMPATIBLE", "configuration mismatch")
    current = now or datetime.now(UTC)
    if manifest.research_cutoff_at > current or manifest.generated_at > current:
        raise PrebuiltError("CDECR_PREBUILT_INVALID", "bundle timestamp is in the future")
    if max_age_hours is not None and manifest.generated_at < current - timedelta(
        hours=max_age_hours
    ):
        raise PrebuiltError("CDECR_PREBUILT_STALE", "bundle exceeds configured maximum age")
    registry = resolved / REGISTRY_NAME
    if registry.stat().st_size != manifest.registry.size_bytes:
        raise PrebuiltError("CDECR_PREBUILT_INVALID", "registry size mismatch")
    if sha256_file(registry) != manifest.registry.sha256:
        raise PrebuiltError("CDECR_PREBUILT_INVALID", "registry digest mismatch")
    _validate_registry(registry, manifest, integrity_check=integrity_check)
    return ValidatedBundle(path=resolved, manifest=manifest)


def _validate_registry(
    registry: Path, manifest: CDECRPrebuiltManifest, *, integrity_check: bool
) -> None:
    try:
        with closing(
            sqlite3.connect(f"file:{registry.as_posix()}?mode=ro&immutable=1", uri=True)
        ) as db:
            db.row_factory = sqlite3.Row
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise PrebuiltError("CDECR_PREBUILT_INVALID", "SQLite quick_check failed")
            if integrity_check and db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise PrebuiltError("CDECR_PREBUILT_INVALID", "SQLite integrity_check failed")
            binding = db.execute(
                "SELECT market,ticker,runtime_scope,binding_version "
                "FROM doxagent_ticker_binding WHERE singleton=1"
            ).fetchone()
            expected_binding = (
                manifest.market,
                manifest.ticker,
                manifest.runtime_scope,
                "cdecr-per-ticker-registry-v1",
            )
            if binding is None or tuple(str(value) for value in binding) != expected_binding:
                raise PrebuiltError("CDECR_PREBUILT_INVALID", "registry binding mismatch")
            epoch = db.execute(
                "SELECT status,message_ids_json FROM bulk_epochs WHERE epoch_id=?",
                (manifest.epoch_id,),
            ).fetchone()
            if epoch is None or str(epoch["status"]) != "FINALIZED":
                raise PrebuiltError("CDECR_PREBUILT_INVALID", "manifest epoch is not FINALIZED")
            epoch_ids = sorted(str(item) for item in json.loads(str(epoch["message_ids_json"])))
            if epoch_ids != manifest.message_ids:
                raise PrebuiltError("CDECR_PREBUILT_INVALID", "epoch message IDs mismatch")
            unfinished = db.execute(
                "SELECT epoch_id FROM bulk_epochs WHERE status<>'FINALIZED' LIMIT 1"
            ).fetchone()
            if unfinished is not None:
                raise PrebuiltError("CDECR_PREBUILT_INVALID", "registry contains unfinished epoch")
            placeholders = ",".join("?" for _ in manifest.message_ids)
            found = 0
            if placeholders:
                found = int(
                    db.execute(
                        "SELECT COUNT(*) FROM source_messages WHERE message_id IN "
                        f"({placeholders})",
                        manifest.message_ids,
                    ).fetchone()[0]
                )
            if found != len(manifest.message_ids):
                raise PrebuiltError("CDECR_PREBUILT_INVALID", "registry source set is incomplete")
    except PrebuiltError:
        raise
    except (sqlite3.Error, KeyError, TypeError, ValueError) as exc:
        raise PrebuiltError("CDECR_PREBUILT_INVALID", "registry validation failed") from exc


def import_registry(bundle: ValidatedBundle, target: str | Path) -> bool:
    destination = Path(target).resolve()
    source = bundle.path / REGISTRY_NAME
    if destination.exists():
        if destination.is_symlink() or not destination.is_file():
            raise PrebuiltError("CDECR_PREBUILT_TARGET_CONFLICT", "target is not a regular file")
        if sha256_file(destination) == bundle.manifest.registry.sha256:
            return False
        raise PrebuiltError("CDECR_PREBUILT_TARGET_CONFLICT", "target digest differs")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + f".{bundle.manifest.bundle_id}.tmp")
    if temporary.exists():
        temporary.unlink()
    try:
        shutil.copyfile(source, temporary)
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        if sha256_file(temporary) != bundle.manifest.registry.sha256:
            raise PrebuiltError("CDECR_PREBUILT_INVALID", "copied registry digest mismatch")
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    finally:
        if temporary.exists():
            temporary.unlink()
    return True


class CDECRPrebuiltStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        for name in ("incoming", "ready", "claimed", "consumed", "rejected"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        (self.root / ".locks").mkdir(parents=True, exist_ok=True)

    def publish(
        self,
        source: str | Path,
        *,
        compatibility_version: str,
        fingerprint: str,
        max_age_hours: int | None = None,
    ) -> ValidatedBundle:
        incoming = self.root / "incoming"
        source_path = Path(source).resolve()
        if source_path == incoming or incoming not in source_path.parents:
            raise PrebuiltError("CDECR_PREBUILT_INVALID", "publish source must be in incoming")
        extracted: Path | None = None
        try:
            if source_path.is_dir():
                candidate = source_path
            else:
                extracted = incoming / f".publish-{uuid4().hex}"
                extracted.mkdir()
                candidate = _extract_archive(source_path, extracted)
            validated = validate_bundle(
                candidate,
                containing_root=incoming,
                expected_compatibility_version=compatibility_version,
                expected_configuration_fingerprint=fingerprint,
                max_age_hours=max_age_hours,
                integrity_check=True,
            )
            with self._ticker_lock(validated.manifest.market, validated.manifest.ticker):
                ticker_root = (
                    self.root / "ready" / validated.manifest.market / validated.manifest.ticker
                )
                ticker_root.mkdir(parents=True, exist_ok=True)
                if any(ticker_root.iterdir()):
                    raise PrebuiltError(
                        "CDECR_PREBUILT_ALREADY_CLAIMED",
                        "ticker already has a ready bundle; resolve it before publishing",
                    )
                destination = ticker_root / validated.manifest.bundle_id
            os.replace(validated.path, destination)
            _fsync_directory(ticker_root)
            if extracted is not None and source_path.exists():
                source_path.unlink()
            return ValidatedBundle(path=destination, manifest=validated.manifest)
        except Exception as exc:
            self._reject(source_path, exc)
            raise
        finally:
            if extracted and extracted.exists():
                shutil.rmtree(extracted)

    def claim_ready(
        self,
        *,
        market: str,
        ticker: str,
        operation_id: str,
        compatibility_version: str,
        fingerprint: str,
        max_age_hours: int,
    ) -> tuple[CDECRPrebuiltRef, CDECRPrebuiltManifest] | None:
        with self._ticker_lock(market, ticker):
            claimed = self._claimed_path(operation_id)
            existing = [item for item in claimed.iterdir()] if claimed.exists() else []
            if existing:
                if len(existing) != 1:
                    raise PrebuiltError(
                        "CDECR_PREBUILT_INVALID", "claim owner has multiple bundles"
                    )
                validated = validate_bundle(
                    existing[0],
                    containing_root=self.root,
                    expected_compatibility_version=compatibility_version,
                    expected_configuration_fingerprint=fingerprint,
                    max_age_hours=max_age_hours,
                )
                return self._ref(validated.manifest, operation_id), validated.manifest
            ready = self.root / "ready" / market.upper() / ticker.upper()
            entries = list(ready.iterdir()) if ready.exists() else []
            candidates = [item for item in entries if item.is_dir()]
            if entries and len(candidates) != len(entries):
                raise PrebuiltError("CDECR_PREBUILT_INVALID", "ready slot contains invalid entries")
            if not entries:
                return None
            if len(candidates) != 1:
                raise PrebuiltError("CDECR_PREBUILT_INVALID", "ticker has multiple ready bundles")
            validated = validate_bundle(
                candidates[0],
                containing_root=self.root,
                expected_compatibility_version=compatibility_version,
                expected_configuration_fingerprint=fingerprint,
                max_age_hours=max_age_hours,
            )
            if (
                validated.manifest.market != market.upper()
                or validated.manifest.ticker != ticker.upper()
            ):
                raise PrebuiltError("CDECR_PREBUILT_INVALID", "ready path identity mismatch")
            claimed.mkdir(parents=True, exist_ok=True)
            destination = claimed / validated.manifest.bundle_id
            os.replace(validated.path, destination)
            _fsync_directory(claimed)
            return self._ref(validated.manifest, operation_id), validated.manifest

    def claimed_bundle(
        self,
        reference: CDECRPrebuiltRef,
        *,
        compatibility_version: str,
        fingerprint: str,
        max_age_hours: int,
    ) -> ValidatedBundle:
        path = self._claimed_path(reference.claim_owner) / reference.bundle_id
        validated = validate_bundle(
            path,
            containing_root=self.root,
            expected_compatibility_version=compatibility_version,
            expected_configuration_fingerprint=fingerprint,
            # Staleness is an admission rule. Once a run owns the immutable claim,
            # wall-clock aging must not break deterministic recovery.
            max_age_hours=None,
        )
        if (
            validated.manifest.registry.sha256 != reference.registry_sha256
            or validated.manifest.epoch_id != reference.epoch_id
        ):
            raise PrebuiltError("CDECR_PREBUILT_INVALID", "claimed reference mismatch")
        return validated

    def release_claim(self, operation_id: str) -> bool:
        owner = self._claimed_path(operation_id)
        bundles = [item for item in owner.iterdir()] if owner.exists() else []
        if not bundles:
            return False
        if len(bundles) != 1:
            raise PrebuiltError("CDECR_PREBUILT_INVALID", "claim owner has multiple bundles")
        manifest = CDECRPrebuiltManifest.model_validate_json(
            (bundles[0] / MANIFEST_NAME).read_text(encoding="utf-8")
        )
        with self._ticker_lock(manifest.market, manifest.ticker):
            ready = self.root / "ready" / manifest.market / manifest.ticker
            ready.mkdir(parents=True, exist_ok=True)
            if any(ready.iterdir()):
                raise PrebuiltError("CDECR_PREBUILT_ALREADY_CLAIMED", "ready slot is occupied")
            os.replace(bundles[0], ready / manifest.bundle_id)
            owner.rmdir()
            _fsync_directory(ready)
        return True

    def consume(
        self, reference: CDECRPrebuiltRef, receipt: CDECRPrebuiltConsumptionReceipt
    ) -> Path:
        source = self._claimed_path(reference.claim_owner) / reference.bundle_id
        destination = self.root / "consumed" / receipt.initialization_id / reference.bundle_id
        receipt_path = destination.parent / f"{reference.bundle_id}.consumption-receipt.json"
        if destination.exists():
            if not receipt_path.exists():
                _atomic_json(receipt_path, receipt.model_dump(mode="json"))
                return destination
            existing = CDECRPrebuiltConsumptionReceipt.model_validate_json(
                receipt_path.read_text(encoding="utf-8")
            )
            if existing != receipt.model_copy(update={"adopted_at": existing.adopted_at}):
                raise PrebuiltError("CDECR_PREBUILT_TARGET_CONFLICT", "receipt conflict")
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
        _atomic_json(receipt_path, receipt.model_dump(mode="json"))
        owner = self._claimed_path(reference.claim_owner)
        if owner.exists() and not any(owner.iterdir()):
            owner.rmdir()
        _fsync_directory(destination.parent)
        return destination

    def list_ready(self, *, ticker: str | None = None) -> list[CDECRPrebuiltManifest]:
        base = self.root / "ready"
        candidates = base.glob(f"*/{ticker.upper()}/*") if ticker else base.glob("*/*/*")
        manifests = []
        for path in candidates:
            manifests.append(
                CDECRPrebuiltManifest.model_validate_json(
                    (path / MANIFEST_NAME).read_text(encoding="utf-8")
                )
            )
        return sorted(manifests, key=lambda item: item.bundle_id)

    def reconcile_claims(
        self,
        *,
        has_run: Callable[[str], bool],
        grace_hours: int,
        now: datetime | None = None,
    ) -> list[str]:
        current = now or datetime.now(UTC)
        released: list[str] = []
        for owner in (self.root / "claimed").iterdir():
            if not owner.is_dir() or has_run(owner.name):
                continue
            age = current.timestamp() - owner.stat().st_mtime
            if age >= timedelta(hours=grace_hours).total_seconds() and self.release_claim(
                owner.name
            ):
                released.append(owner.name)
        return released

    def _claimed_path(self, operation_id: str) -> Path:
        if not operation_id or Path(operation_id).name != operation_id:
            raise PrebuiltError("CDECR_PREBUILT_INVALID", "invalid claim owner")
        return self.root / "claimed" / operation_id

    def _ticker_lock(self, market: str, ticker: str) -> AbstractContextManager[None]:
        safe_market = market.strip().upper()
        safe_ticker = ticker.strip().upper()
        if Path(safe_market).name != safe_market or Path(safe_ticker).name != safe_ticker:
            raise PrebuiltError("CDECR_PREBUILT_INVALID", "invalid ticker lock scope")
        return _exclusive_file_lock(self.root / ".locks" / f"{safe_market}-{safe_ticker}.lock")

    @staticmethod
    def _ref(manifest: CDECRPrebuiltManifest, owner: str) -> CDECRPrebuiltRef:
        return CDECRPrebuiltRef(
            bundle_id=manifest.bundle_id,
            claim_owner=owner,
            registry_sha256=manifest.registry.sha256,
            epoch_id=manifest.epoch_id,
        )

    def _reject(self, source: Path, exc: Exception) -> None:
        destination = self.root / "rejected" / f"{source.stem}-{uuid4().hex[:8]}"
        destination.mkdir(parents=True, exist_ok=False)
        _atomic_json(
            destination / "rejection_receipt.json",
            {
                "source": source.name,
                "error": type(exc).__name__,
                "rejected_at": datetime.now(UTC).isoformat(),
            },
        )


def create_archive(bundle: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    zstandard: Any = importlib.import_module("zstandard")
    with destination.open("wb") as raw:
        compressor = zstandard.ZstdCompressor(level=10)
        with compressor.stream_writer(raw) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|") as archive:
                for name in (MANIFEST_NAME, REGISTRY_NAME):
                    archive.add(bundle / name, arcname=f"{bundle.name}/{name}", recursive=False)


def _extract_archive(archive_path: Path, destination: Path) -> Path:
    def extract(archive: tarfile.TarFile) -> Path:
        top: str | None = None
        names: set[str] = set()
        for member in archive:
            parts = Path(member.name).parts
            if (
                len(parts) != 2
                or parts[0] in {"", ".", ".."}
                or Path(parts[0]).name != parts[0]
                or parts[1] not in {MANIFEST_NAME, REGISTRY_NAME}
                or not member.isfile()
            ):
                raise PrebuiltError("CDECR_PREBUILT_INVALID", "archive layout is invalid")
            top = top or parts[0]
            if top != parts[0] or parts[1] in names:
                raise PrebuiltError("CDECR_PREBUILT_INVALID", "archive identities are ambiguous")
            names.add(parts[1])
            target = destination / parts[0] / parts[1]
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise PrebuiltError("CDECR_PREBUILT_INVALID", "archive member is unreadable")
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
        if top is None or names != {MANIFEST_NAME, REGISTRY_NAME}:
            raise PrebuiltError("CDECR_PREBUILT_INVALID", "archive is incomplete")
        return destination / top

    if archive_path.name.endswith(".tar.zst"):
        zstandard: Any = importlib.import_module("zstandard")
        with archive_path.open("rb") as raw:
            with zstandard.ZstdDecompressor().stream_reader(raw) as reader:
                with tarfile.open(fileobj=reader, mode="r|") as archive:
                    return extract(archive)
    with tarfile.open(archive_path, mode="r:*") as archive:
        return extract(archive)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, sort_keys=True, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _implementation_sha256() -> str:
    source_root = Path(__file__).resolve().parents[2] / "cdecr"
    digest = hashlib.sha256()
    for path in sorted(source_root.rglob("*.py")):
        digest.update(path.relative_to(source_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


@contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"0")
            stream.flush()
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.02)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl: Any = importlib.import_module("fcntl")
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
