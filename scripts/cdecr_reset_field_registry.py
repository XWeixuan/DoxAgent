"""One-time clean Field Registry cutover preserving immutable sources and mentions."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from cdecr.registry import SQLiteCDECRRegistry


def _backup(source: Path, destination: Path) -> None:
    with sqlite3.connect(source) as source_connection:
        source_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        with sqlite3.connect(destination) as destination_connection:
            source_connection.backup(destination_connection)


def reset_field_registry(
    database: Path, *, backup: Path, reuse_backup: bool = False
) -> dict[str, object]:
    database = database.resolve()
    backup = backup.resolve()
    if database == backup:
        raise ValueError("backup path must differ from the database path")
    if not database.is_file():
        raise FileNotFoundError(database)
    if backup.exists() and not reuse_backup:
        raise FileExistsError(backup)
    replacement = database.with_name(f".{database.name}.field-reset.tmp")
    if replacement.exists():
        raise FileExistsError(replacement)

    if not backup.exists():
        _backup(database, backup)
    old = SQLiteCDECRRegistry(database)
    sources = old.list_all_sources(limit=100_000)
    mentions = old.list_all_mentions(limit=1_000_000)

    new = SQLiteCDECRRegistry(replacement)
    new.initialize()
    for source in sources:
        fingerprint = old.get_source_fingerprint(source.message_id)
        if fingerprint is None:
            raise RuntimeError(f"missing source fingerprint for {source.message_id}")
        new.save_source(source, fingerprint=fingerprint)
    for mention in mentions:
        new.save_mention(mention)

    if len(new.list_all_sources(limit=100_000)) != len(sources):
        raise RuntimeError("source count changed during clean registry cutover")
    if len(new.list_all_mentions(limit=1_000_000)) != len(mentions):
        raise RuntimeError("mention count changed during clean registry cutover")
    if new.list_field_registry_entries(limit=1):
        raise RuntimeError("replacement Field Registry is not clean")

    for suffix in ("-wal", "-shm"):
        sidecar = database.with_name(database.name + suffix).resolve()
        if sidecar.parent != database.parent:
            raise RuntimeError("refusing to remove a SQLite sidecar outside the database directory")
        sidecar.unlink(missing_ok=True)
    os.replace(replacement, database)
    return {
        "database": str(database),
        "backup": str(backup),
        "source_count": len(sources),
        "mention_count": len(mentions),
        "field_registry_count": 0,
        "completed_at": datetime.now(UTC).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reuse-backup", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        raise SystemExit("refusing mutation without --apply")
    print(
        json.dumps(
            reset_field_registry(
                args.database,
                backup=args.backup,
                reuse_backup=args.reuse_backup,
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
