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
    tracked = {entry["file"] for entry in manifest["artifacts"]}
    for root_name in ("native-files", "content-files", "receipt-files"):
        for path in (destination / root_name).rglob("*"):
            if path.is_symlink():
                raise ValueError("backup content must not contain symlinks")
            if path.is_file():
                relative = path.relative_to(destination).as_posix()
                if relative not in tracked:
                    manifest["artifacts"].append({"file":relative,"sha256":digest(path)})
    atomic_json(destination / "manifest.json", manifest)
    return manifest


def collect(store, *, now=None, limit=500, budget=.1):
    """Keep all 24h views/cursors, including the pre-event image needed by REMOVE."""
    if not 1 <= limit <= 5000:
        raise ValueError("GC limit must be 1..5000")
    now = now or datetime.now(UTC)
    import time
    counts = {}
    with store.connect(write=True, timeout=.05) as db:
        until = time.monotonic() + budget
        db.set_progress_handler(lambda: time.monotonic() >= until, 1000)
        from .settings import Limits
        cutoff = instant(now - timedelta(hours=Limits.load().mvcc_hours))
        row = db.execute("SELECT MIN(seq) FROM commit_retention WHERE projected_at>=?", (cutoff,)).fetchone()
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
        if db.execute("SELECT 1 FROM gaps LIMIT 1").fetchone():
            floor = 0  # Dependency repair can require an earlier pre-image.
        for table in ("views", "cursors"):
            counts[table] = db.execute(
                f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} "
                "WHERE expires_at<=? LIMIT ?)",
                (instant(now), limit),
            ).rowcount
        # Legacy closed business intervals must reach the durable directory before GC.
        db.execute("INSERT OR IGNORE INTO business_revisions SELECT * FROM objects WHERE rowid IN (SELECT rowid FROM objects WHERE valid_to IS NOT NULL AND valid_to<? LIMIT ?) AND kind NOT IN ('ticker','navigation','capture_coverage','native:runtime_tasks','native:runtime_values','native:poll_states')", (floor,limit))
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
        # Unresolved repairs still rely on the original source-to-read receipt mapping.
        if not db.execute("SELECT 1 FROM gaps LIMIT 1").fetchone():
            counts["commits"] = db.execute(
                "DELETE FROM commits WHERE seq IN (SELECT seq FROM commits WHERE seq<? "
                "AND EXISTS(SELECT 1 FROM checkpoints cp WHERE cp.source=commits.source AND cp.position>=CAST(commits.source_event AS INTEGER)) AND source_event NOT GLOB '*[^0-9]*' "
                "AND seq NOT IN (SELECT seq FROM changes) LIMIT ?)", (floor, limit)
            ).rowcount
            db.execute("DELETE FROM commit_retention WHERE seq IN (SELECT seq FROM commit_retention WHERE seq<? AND seq NOT IN (SELECT seq FROM commits) LIMIT ?)", (floor, limit))
        db.execute("UPDATE read_meta SET value=max(CAST(value AS INTEGER),?) WHERE key='retained_floor'", (floor,))
    # Immutable content identities and unpositioned deduplication receipts are retained.
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


def compact(source, target):
    """Offline shadow compaction preserving all issued tokens and public sequence IDs."""
    from .cli import backup
    source, target = Path(source).resolve(strict=True), Path(target).resolve()
    if source == target or target.exists():
        raise ValueError("compaction requires a new shadow path")
    import shutil
    target.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(target.parent).free < source.stat().st_size * 3:
        raise ValueError("shadow conversion needs room for backup, WAL and VACUUM temporary pages")
    backup(source, target)
    store = ReadStore(target)
    store.migrate()
    # Convert on the offline shadow only; published IDs and interval coordinates stay intact.
    import sqlite3
    reader = sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        for table in ("objects", "business_revisions"):
            cursor = reader.execute("SELECT rowid,payload FROM " + table + " ORDER BY rowid")
            while batch := cursor.fetchmany(100):
                prepared = [(store.content_codec.encode(store.content_codec.decode(json.loads(raw)),
                             keep={"inputs", "source", "summary", "consumed", "effective", "polling"}), identity)
                            for identity, raw in batch]
                with store.connect(write=True) as db:
                    db.executemany("UPDATE " + table + " SET payload=? WHERE rowid=?", prepared)
        cursor = reader.execute("SELECT id,ticker,kind,body FROM contents WHERE length(body)>0")
        while batch := cursor.fetchmany(1):
            identity, ticker, kind, body = batch[0]
            from .content_files import ContentFiles
            sha = ContentFiles(target.parent / "content-files").put(bytes(body))
            with store.connect(write=True) as db:
                db.execute("INSERT OR IGNORE INTO content_locations VALUES(?,?)", (identity,sha))
                db.execute("UPDATE contents SET body=x'' WHERE id=?", (identity,))
    finally:
        reader.close()
    with store.connect(write=True) as db:
        db.execute("INSERT OR IGNORE INTO business_revisions SELECT * FROM objects WHERE valid_to IS NOT NULL AND kind NOT IN ('ticker','navigation','capture_coverage','native:runtime_tasks','native:runtime_values','native:poll_states')")
        # Replay reads exact version rows, not these historical duplicate payloads.
        db.execute("UPDATE changes SET before_payload=NULL,after_payload=NULL "
                   "WHERE before_payload IS NOT NULL OR after_payload IS NOT NULL")
    import sqlite3
    with sqlite3.connect(target) as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.execute("VACUUM")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("compacted database integrity failed")
    return {"source": str(source), "target": str(target), "source_bytes": source.stat().st_size,
            "target_bytes": target.stat().st_size, "tokens_preserved": True,
            "requires_catchup_and_verify": True}


def run_gc(store, *, once=False, interval=60, budget=2):
    import time
    from doxagent.trade_execution.worker import WriterLock
    with WriterLock(Path(str(store.path) + ".maintenance")):
        while True:
            started = time.monotonic()
            total = {}
            import sqlite3
            batch = 500
            while time.monotonic() - started < budget:
                try:
                    result = collect(store, limit=batch)
                except sqlite3.OperationalError:
                    batch //= 2
                    if batch < 10:
                        break
                    continue
                for name, count in result["deleted"].items():
                    total[name] = total.get(name, 0) + count
                if not any(result["deleted"].values()):
                    break
            print(json.dumps({"maintenance": "gc", "deleted": total,
                              "elapsed_seconds": time.monotonic() - started}), flush=True)
            if once:
                return
            time.sleep(interval)
