from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from doxagent.cdecr_integration.prebuilt import (
    CDECRPrebuiltManifest,
    CDECRPrebuiltStore,
    PrebuiltError,
    RegistryArtifact,
    create_archive,
    import_registry,
    message_set_sha256,
    sha256_file,
    validate_bundle,
)

FINGERPRINT = "sha256:" + "c" * 64
COMPATIBILITY = "cdecr-prebuilt-runtime-v1"


def _bundle(root: Path, *, ticker: str = "MU", status: str = "FINALIZED") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    registry = root / "runtime.sqlite3"
    message_ids = ["message-1", "message-2"]
    with closing(sqlite3.connect(registry)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(
            """
            CREATE TABLE doxagent_ticker_binding (
                singleton INTEGER PRIMARY KEY, market TEXT, ticker TEXT,
                runtime_scope TEXT, binding_version TEXT
            );
            CREATE TABLE bulk_epochs (
                epoch_id TEXT PRIMARY KEY, status TEXT, message_ids_json TEXT
            );
            CREATE TABLE source_messages (message_id TEXT PRIMARY KEY);
            """
        )
        db.execute(
            "INSERT INTO doxagent_ticker_binding VALUES(1,?,?,?,?)",
            ("US", ticker, f"cdecr:US:{ticker}", "cdecr-per-ticker-registry-v1"),
        )
        db.execute(
            "INSERT INTO bulk_epochs VALUES(?,?,?)",
            ("epoch-1", status, json.dumps(message_ids)),
        )
        db.executemany("INSERT INTO source_messages VALUES(?)", [(item,) for item in message_ids])
        db.commit()
    digest = sha256_file(registry)
    identity = f"cdecr-US-{ticker}-20260909T020000Z-{digest[-12:]}"
    destination = root.parent / identity
    root.rename(destination)
    registry = destination / "runtime.sqlite3"
    now = datetime.now(UTC)
    manifest = CDECRPrebuiltManifest(
        bundle_id=identity,
        market="US",
        ticker=ticker,
        runtime_scope=f"cdecr:US:{ticker}",
        research_cutoff_at=now - timedelta(hours=1),
        generated_at=now,
        code_revision="test-revision",
        compatibility_version=COMPATIBILITY,
        configuration_fingerprint=FINGERPRINT,
        epoch_id="epoch-1",
        message_ids=message_ids,
        message_set_sha256=message_set_sha256(message_ids),
        document_count=2,
        eligible_document_count=2,
        registry=RegistryArtifact(size_bytes=registry.stat().st_size, sha256=digest),
    )
    (destination / "manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    return destination


def test_validator_rejects_invalid_epoch_and_compatibility(tmp_path: Path) -> None:
    invalid = _bundle(tmp_path / "invalid-work", status="RUNNING")
    with pytest.raises(PrebuiltError, match="manifest epoch is not FINALIZED"):
        validate_bundle(invalid, containing_root=tmp_path)
    valid = _bundle(tmp_path / "valid-work", ticker="AMD")
    with pytest.raises(PrebuiltError, match="CDECR_PREBUILT_INCOMPATIBLE"):
        validate_bundle(
            valid,
            containing_root=tmp_path,
            expected_compatibility_version="other-version",
        )


def test_archive_publish_claim_and_single_consumer(tmp_path: Path) -> None:
    source = _bundle(tmp_path / "source-work")
    store = CDECRPrebuiltStore(tmp_path / "store")
    archive = store.root / "incoming" / f"{source.name}.tar.zst"
    create_archive(source, archive)
    published = store.publish(
        archive,
        compatibility_version=COMPATIBILITY,
        fingerprint=FINGERPRINT,
    )
    assert published.path.parent == store.root / "ready" / "US" / "MU"
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                store.claim_ready,
                market="US",
                ticker="MU",
                operation_id=f"operation-{index}",
                compatibility_version=COMPATIBILITY,
                fingerprint=FINGERPRINT,
                max_age_hours=24,
            )
            for index in range(2)
        ]
    outcomes = []
    for future in futures:
        try:
            outcomes.append(future.result())
        except PrebuiltError:
            outcomes.append(None)
    assert sum(value is not None for value in outcomes) == 1


def test_import_is_idempotent_and_never_overwrites_different_target(tmp_path: Path) -> None:
    bundle_path = _bundle(tmp_path / "bundle-work")
    bundle = validate_bundle(bundle_path, containing_root=tmp_path)
    target = tmp_path / "workspace" / "registry" / "runtime.sqlite3"
    assert import_registry(bundle, target)
    assert not import_registry(bundle, target)
    target.write_bytes(b"different")
    with pytest.raises(PrebuiltError, match="CDECR_PREBUILT_TARGET_CONFLICT"):
        import_registry(bundle, target)


def test_validation_does_not_create_wal_sidecars(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "bundle-work")
    validate_bundle(bundle, containing_root=tmp_path, integrity_check=True)
    assert {path.name for path in bundle.iterdir()} == {"manifest.json", "runtime.sqlite3"}


def test_symlink_and_extra_files_never_validate(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "bundle-work")
    (bundle / "unexpected.txt").write_text("no", encoding="utf-8")
    with pytest.raises(PrebuiltError, match="exactly two files"):
        validate_bundle(bundle, containing_root=tmp_path)
