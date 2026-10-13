"""Incremental V21 upgrade using verified compressed backups and existing writer fences."""

from __future__ import annotations

import gzip
import hashlib
import json
from contextlib import ExitStack
from pathlib import Path


def file_digest(path: Path, *, compressed: bool = False) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    opener = gzip.open if compressed else open
    with opener(path, "rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def upgrade_v21(locations: dict[str, Path], backup_manifest: Path) -> dict:
    from doxagent.trade_execution.worker import WriterLock
    from doxagent.v2_read.outbox import SourceOutbox
    from doxagent.workflows.codex_document3.state_v21 import StateV21

    manifest = json.loads(backup_manifest.read_text(encoding="utf-8"))
    with ExitStack() as stack:
        for path in (
            locations["runtime"].with_suffix(".migration"),
            locations["runtime"],
            Path(str(locations["runtime"]) + ".v2-control"),
            Path(str(locations["runtime"]) + ".v2-delivery"),
            Path(str(locations["scheduler"]) + ".v2-scheduler"),
            Path(str(locations["read"]) + ".projector"),
        ):
            stack.enter_context(WriterLock(path))
        for name in ("research", "initialization", "runtime", "bus", "read"):
            item = manifest["databases"][name]
            source = locations[name].resolve()
            if source != Path(item["source"]).resolve():
                raise ValueError(f"backup source mismatch: {name}")
            expected = (item["size"], item["sha256"])
            if file_digest(source) != expected:
                raise ValueError(f"database changed since backup: {name}")
            if file_digest(Path(item["archive"]), compressed=True) != expected:
                raise ValueError(f"compressed backup mismatch: {name}")
            wal = Path(str(source) + "-wal")
            if wal.exists() and wal.stat().st_size:
                raise ValueError(f"database WAL must be checkpointed before upgrade: {name}")
        StateV21(locations["research"])
        captured = {}
        backfilled = 0
        for name in ("research", "initialization", "bus", "runtime"):
            source = SourceOutbox(locations[name], name)
            with source.connection() as db:
                prior = (
                    db.execute(
                        "SELECT position FROM v2_backfill_checkpoint "
                        "WHERE table_name='runtime_values'"
                    ).fetchone()
                    if name == "runtime"
                    else None
                )
            captured[name] = source.migrate()
            if name == "runtime":
                if prior:
                    with source.connection(write=True) as db:
                        db.execute(
                            "UPDATE v2_backfill_checkpoint SET position=? "
                            "WHERE table_name='runtime_values'",
                            (prior[0],),
                        )
                while count := source.backfill(
                    "runtime_values",
                    limit=500,
                    namespaces=("policy_direction_usage", "policy_watch"),
                ):
                    backfilled += count
        receipt = {
            "upgrade": "document-v21",
            "capture_tables": captured,
            "runtime_values_backfilled": backfilled,
            "backup_manifest": str(backup_manifest),
        }
        target = backup_manifest.parent / "upgrade-v21-receipt.json"
        target.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        return receipt
