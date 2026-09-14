"""Explicit local migrations, consistent backups, bounded backfill and projection."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.v2_control.repository import ControlRepository

from .artifacts import PublishedArtifacts
from .formal import FormalProjectors
from .outbox import TABLES, SourceOutbox
from .projector import ProjectionWorker
from .repository import ReadStore


def backup(source: Path, target: Path) -> None:
    if target.exists():
        raise ValueError("backup destination already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    origin = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    destination = sqlite3.connect(target)
    try:
        origin.backup(destination, pages=256, sleep=0.05)
        if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("backup integrity check failed")
    finally:
        destination.close()
        origin.close()
    from .content_files import ContentFiles
    ContentFiles(source.parent / "content-files").backup(target, target.parent / "content-files")
    native_root = source.parent / "native-files"
    if native_root.exists():
        identities = [path.parent.name for path in native_root.glob("*/*/manifest.json")]
        ContentFiles(native_root).copy(identities, target.parent / "native-files")

    with sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_receipt_archive'").fetchone():
            identities = [r[0] for r in db.execute("SELECT DISTINCT digest FROM v2_receipt_archive")]
            for (name,) in db.execute("SELECT source FROM v2_source_state"):
                ContentFiles(source.parent / "receipt-files" / name).copy(identities, target.parent / "receipt-files" / name)



def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "migrate",
            "backfill",
            "project",
            "diagnose",
            "backup",
            "rebuild",
            "switch",
            "gc",
            "gc-worker",
            "inventory",
            "diagnostics-gc",
            "compact",
            "archive",
            "import-history",
            "verify",
        ),
    )
    parser.add_argument("--read-db", type=Path, default=Path(".tmp/v2_read.sqlite3"))
    parser.add_argument("--runtime-db", type=Path)
    parser.add_argument("--event-root", type=Path)
    parser.add_argument("--source", action="append", default=[], metavar="KIND=PATH")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--table")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--artifact-root", type=Path, action="append", default=[])
    parser.add_argument("--alias", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--target", type=Path)
    args = parser.parse_args()
    if not 1 <= args.limit <= 500:
        parser.error("limit must be 1..500")
    sources = []
    for spec in args.source:
        if "=" not in spec:
            parser.error("source must be KIND=PATH")
        kind, path = spec.split("=", 1)
        if kind not in TABLES or any(s.source == kind for s in sources):
            parser.error("source kind must be known and unique within one read store")
        sources.append(SourceOutbox(path, kind))
    store = ReadStore(args.read_db)
    if args.command == "migrate":
        report = {s.source: s.migrate(dry_run=True) for s in sources}
        if not args.dry_run:
            if not args.backup_dir:
                parser.error("migrations require a fresh backup directory")
            paths = {s.path for s in sources}
            if args.runtime_db:
                paths.add(args.runtime_db.resolve())
            for index, path in enumerate(sorted(paths)):
                backup(path, args.backup_dir / f"source-{index}.sqlite3")
            if args.runtime_db:
                ControlRepository(RuntimeJournal(args.runtime_db, initialize=False)).migrate()
            for source in sources:
                source.migrate()
            store.migrate()
        print(
            json.dumps(
                {"dry_run": args.dry_run, "sources": report, "read_schema": ReadStore.VERSION}
            )
        )
    elif args.command == "inventory":
        from .artifact_registry import inventory
        print(json.dumps(inventory(store,limit=args.limit)))
    elif args.command == "diagnostics-gc":
        from .artifact_registry import collect_diagnostics
        from datetime import datetime, UTC
        print(json.dumps({"quarantined":collect_diagnostics(store,now=datetime.now(UTC),limit=min(args.limit,10))}))
    elif args.command == "backup":
        if not args.backup_dir:
            parser.error("backup directory required")
        from .maintenance import backup_manifest

        print(
            json.dumps(backup_manifest(sources, args.backup_dir, artifact_roots=args.artifact_root))
        )
    elif args.command == "import-history":
        from .history import import_manifest

        if not args.manifest or not args.runtime_db:
            parser.error("import-history requires manifest and runtime-db")
        print(
            json.dumps(
                import_manifest(args.runtime_db, sources, args.manifest, dry_run=args.dry_run)
            )
        )
    elif args.command == "verify":
        from .maintenance import verify_shadow

        print(json.dumps(verify_shadow(store, sources)))
    elif args.command == "compact":
        if not args.target:
            parser.error("compact requires --target with a new shadow database path")
        from .maintenance import compact
        print(json.dumps(compact(args.read_db, args.target)))
    elif args.command == "gc-worker":
        from .maintenance import run_gc
        run_gc(store, once=args.once)
    elif args.command == "gc":
        from .maintenance import collect

        print(json.dumps(collect(store, limit=args.limit)))
    elif args.command == "rebuild":
        from .maintenance import rebuild

        if not sources:
            parser.error("rebuild requires explicit sources")
        print(json.dumps(rebuild(sources, args.read_db, event_root=args.event_root)))
    elif args.command == "switch":
        from .maintenance import activate_alias

        if not args.alias or not sources:
            parser.error("switch requires an alias and the complete source set")
        print(json.dumps(activate_alias(args.alias, args.read_db, sources)))
    elif args.command == "archive":
        if not args.manifest or not sources:
            parser.error("archive requires source registrations and a verified backup --manifest")
        from datetime import datetime, UTC, timedelta
        from .maintenance import digest
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        if manifest.get("format") != "doxagent.v2.backup.1":
            parser.error("unsupported checkpoint manifest")
        archived = {}
        for source in sources:
            entry = next((item for item in manifest["databases"] if item["source"] == source.source and Path(item["original"]).resolve() == source.path), None)
            if not entry:
                parser.error("checkpoint does not contain this source")
            checkpoint = (args.manifest.parent / entry["file"]).resolve(strict=True)
            if checkpoint.parent != args.manifest.parent.resolve() or digest(checkpoint) != entry["sha256"]:
                parser.error("checkpoint checksum or location invalid")
            archived[source.source] = source.archive(checkpoint, before=(datetime.now(UTC)-timedelta(days=7)).isoformat(), limit=args.limit)
        print(json.dumps({"archived": archived}))
    elif args.command == "backfill":
        if len(sources) != 1 or not args.table:
            parser.error("backfill needs exactly one source and --table")
        print(json.dumps({"captured": sources[0].backfill(args.table, limit=args.limit)}))
    elif args.command == "diagnose":
        with store.connect() as db:
            print(
                json.dumps(
                    {
                        "read_seq": store.highwater(db),
                        "database_bytes": store.path.stat().st_size,
                        "wal_bytes": Path(str(store.path)+"-wal").stat().st_size if Path(str(store.path)+"-wal").exists() else 0,
                        "free_page_bytes": db.execute("PRAGMA freelist_count").fetchone()[0] * db.execute("PRAGMA page_size").fetchone()[0],
                        "retained_floor": db.execute("SELECT value FROM read_meta WHERE key='retained_floor'").fetchone()[0],
                        "active_view_pins": db.execute("SELECT count(*) FROM views WHERE expires_at>strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0],
                        "checkpoints": [dict(r) for r in db.execute("SELECT * FROM checkpoints")],
                        "gap_count": db.execute("SELECT COUNT(*) FROM gaps").fetchone()[0],
                        "schema": db.execute("SELECT version FROM schema_meta").fetchone()[0],
                        "source_health": [dict(r) for r in db.execute("SELECT * FROM source_health")],
                        "generation": db.execute("SELECT id FROM generation").fetchone()[0],
                    }
                )
            )
    else:
        if args.dry_run:
            parser.error("dry-run applies only to migrations")
        research = next(
            (PublishedArtifacts(s.path) for s in sources if s.source == "research"), None
        )
        worker = ProjectionWorker(
            store, sources, FormalProjectors(store, research, args.event_root)
        )
        from doxagent.trade_execution.worker import WriterLock

        with WriterLock(Path(str(store.path) + ".projector")):
            while True:
                worker.tick(limit=args.limit)
                if args.once:
                    return
                time.sleep(1)


if __name__ == "__main__":
    main()
