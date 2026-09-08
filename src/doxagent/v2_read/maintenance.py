"""Offline-safe backups, shadow rebuilds and bounded read-history retention."""

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .repository import ReadStore, encode, instant


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        stream.write(encode(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def backup_manifest(sources, destination, *, artifact_roots=()):
    from .cli import backup

    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {
        "format": "doxagent.v2.backup.1",
        "at": instant(datetime.now(UTC)),
        "consistency": "PER_DATABASE_SQLITE_BACKUP",
        "databases": [],
        "artifacts": [],
    }
    for source in sources:
        target = destination / (source.source + ".sqlite3")
        backup(source.path, target)
        manifest["databases"].append(
            {
                "source": source.source,
                "original": str(source.path),
                "file": target.name,
                "sha256": digest(target),
            }
        )
    # Only explicitly selected immutable published roots; never enumerate workspaces implicitly.
    import shutil

    for ordinal, root in enumerate(artifact_roots):
        root = Path(root).resolve(strict=True)
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError("immutable backup roots must not contain symlinks")
            if not path.is_file():
                continue
            relative = Path("artifacts") / str(ordinal) / path.relative_to(root)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            before = digest(path)
            shutil.copyfile(path, target)
            if digest(target) != before or digest(path) != before:
                raise ValueError("immutable artifact changed during backup")
            manifest["artifacts"].append(
                {"original": str(path), "file": relative.as_posix(), "sha256": before}
            )
    atomic_json(destination / "manifest.json", manifest)
    return manifest


def collect(store, *, now=None, limit=500):
    """Keep all 24h views/cursors, including the pre-event image needed by REMOVE."""
    if not 1 <= limit <= 5000:
        raise ValueError("GC limit must be 1..5000")
    now = now or datetime.now(UTC)
    counts = {}
    with store.connect(write=True) as db:
        cutoff = instant(now - timedelta(days=1))
        row = db.execute("SELECT MIN(seq) FROM commits WHERE at>=?", (cutoff,)).fetchone()
        floor = row[0] if row[0] is not None else store.highwater(db)
        row = db.execute(
            "SELECT MIN(seq) FROM views WHERE expires_at>?", (instant(now),)
        ).fetchone()
        if row[0] is not None:
            floor = min(floor, row[0])
        row = db.execute(
            "SELECT MIN(CAST(json_extract(payload,'$.seq') AS INTEGER)) "
            "FROM cursors WHERE expires_at>?",
            (instant(now),),
        ).fetchone()
        if row[0] is not None:
            floor = min(floor, max(0, row[0] - 1))
        for table in ("views", "cursors"):
            counts[table] = db.execute(
                f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} "
                "WHERE expires_at<=? LIMIT ?)",
                (instant(now), limit),
            ).rowcount
        for table in ("objects", "contributions", "metric_buckets"):
            counts[table] = db.execute(
                f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} "
                "WHERE valid_to IS NOT NULL AND valid_to<? LIMIT ?)",
                (floor, limit),
            ).rowcount
        counts["changes"] = db.execute(
            "DELETE FROM changes WHERE rowid IN (SELECT rowid FROM changes WHERE seq<? LIMIT ?)",
            (floor, limit),
        ).rowcount
    # Commit deduplication and immutable content identities are intentionally retained.
    return {"retention_floor": floor, "deleted": counts}


def rebuild(sources, target, *, event_root=None, max_batches=10000):
    from .artifacts import PublishedArtifacts
    from .formal import FormalProjectors
    from .projector import ProjectionWorker

    target = Path(target).resolve()
    if target.exists():
        raise ValueError("shadow target must be new")
    store = ReadStore(target)
    store.migrate()
    research = next((PublishedArtifacts(s.path) for s in sources if s.source == "research"), None)
    worker = ProjectionWorker(store, sources, FormalProjectors(store, research, event_root))
    for _ in range(max_batches):
        worker.tick(limit=500)
        heads = {s.source: s.head() for s in sources}
        if all(store.position(key) >= value for key, value in heads.items()):
            with store.connect() as db:
                if db.execute("SELECT 1 FROM gaps LIMIT 1").fetchone():
                    raise ValueError("shadow has source gaps; diagnose and repair before cutover")
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("shadow integrity check failed")
                report = {
                    "format": "doxagent.v2.rebuild.1",
                    "generation": db.execute("SELECT id FROM generation").fetchone()[0],
                    "path": str(target),
                    "heads": heads,
                    "schema": store.VERSION,
                    "seq": store.highwater(db),
                    "at": instant(datetime.now(UTC)),
                    "objects": db.execute(
                        "SELECT COUNT(*) FROM objects WHERE valid_to IS NULL"
                    ).fetchone()[0],
                }
            atomic_json(str(target) + ".rebuild.json", report)
            return report
    raise ValueError("shadow catch-up batch budget exhausted; resume projection on the shadow")


def activate_alias(alias, target, sources):
    """Switch at a verified source watermark; restart API/projector to adopt this generation."""
    store = ReadStore(target)
    report_path = Path(str(store.path) + ".rebuild.json")
    if not report_path.is_file():
        raise ValueError("verified rebuild report required")
    with store.connect() as db:
        if db.execute("SELECT 1 FROM gaps LIMIT 1").fetchone():
            raise ValueError("source gaps prevent cutover")
        generation = db.execute("SELECT id FROM generation").fetchone()[0]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report["generation"] != generation or set(report["heads"]) != {s.source for s in sources}:
        raise ValueError("generation/source set mismatch")
    if any(store.position(s.source) < s.head() for s in sources):
        raise ValueError("shadow must catch up before switching the alias")
    value = {
        "format": "doxagent.v2.read-alias.1",
        "path": str(store.path),
        "generation": generation,
        "switched_at": instant(datetime.now(UTC)),
        "cursor_policy": "RESET_OTHER_GENERATIONS",
    }
    atomic_json(alias, value)
    return value


def verify_shadow(store, sources):
    heads = {source.source: source.head() for source in sources}
    if not heads or any(store.position(key) < value for key, value in heads.items()):
        raise ValueError("source watermarks are not closed")
    with store.connect() as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("read store integrity check failed")
        if db.execute("SELECT 1 FROM gaps LIMIT 1").fetchone():
            raise ValueError("read store has unresolved gaps")
        report = {"format": "doxagent.v2.rebuild.1", "path": str(store.path),
                  "generation": db.execute("SELECT id FROM generation").fetchone()[0],
                  "heads": heads, "schema": store.VERSION, "seq": store.highwater(db),
                  "at": instant(datetime.now(UTC)),
                  "objects_by_kind": dict(db.execute("SELECT kind,count(*) FROM objects WHERE valid_to IS NULL GROUP BY kind"))}
    atomic_json(str(store.path) + ".rebuild.json", report)
    return report
