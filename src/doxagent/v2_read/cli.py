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
        if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("backup integrity check failed")
    finally:
        destination.close()
        origin.close()


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
