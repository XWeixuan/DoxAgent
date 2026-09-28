"""Online, per-database recovery set using the existing production backup API.

Run in a disposable container with the current production image and /data mounted.
No credentials, browser Profiles, mutable workspaces, or service configuration copied.
"""

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from doxagent.v2_read import cli
from doxagent.v2_read.maintenance import atomic_json, backup_manifest, digest


def main():
    os.nice(15)
    root = Path("/data")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = root / "backups" / (stamp + "-verified-current")
    files = {
        "research": "research/research.sqlite3",
        "initialization": "initialization/control.sqlite3",
        "bus": "bus/bus.sqlite3",
        "runtime": "runtime/runtime.sqlite3",
        "read": "read/v2.sqlite3",
        "scheduler": "scheduler/scheduler.sqlite3",
        "usage": "usage/usage.sqlite3",
        "o4": "o4/o4.sqlite3",
        "crawler-plane": "crawler-plane/crawler_plane.sqlite3",
    }
    for path in sorted((root / "events").glob("US/*/event_library.sqlite3")):
        files["events-US-" + path.parent.name] = str(path.relative_to(root))
    sources = [
        SimpleNamespace(source=name, path=root / relative) for name, relative in files.items()
    ]
    if not all(s.path.is_file() for s in sources):
        raise RuntimeError("Required source database missing")
    if os.statvfs("/data").f_bavail * os.statvfs("/data").f_frsize < 45 * 2**30:
        raise RuntimeError("Insufficient working headroom for a new recovery set")
    roots = [
        root / "initialization/artifacts",
        root / "runtime/runtime_artifacts",
        root / "prebuilt/cdecr",
    ]
    roots = [p for p in roots if p.is_dir()]
    original = cli.backup

    def with_progress(source, destination):
        print(json.dumps({"phase": "backup", "source": str(source)}), flush=True)
        # WAL writers can otherwise restart the incremental backup repeatedly.
        # Pin a read-only snapshot in this disposable process. Writers continue
        # appending WAL; no live migration/checkpoint or application restart.
        connect = sqlite3.connect

        def pinned_connect(database, *args, **kwargs):
            db = connect(database, *args, **kwargs)
            if kwargs.get("uri") and "mode=ro" in str(database):
                db.execute("BEGIN")
                db.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
            return db

        sqlite3.connect = pinned_connect
        try:
            original(source, destination)
        finally:
            sqlite3.connect = connect
        print(json.dumps({"phase": "database_complete", "file": destination.name}), flush=True)

    cli.backup = with_progress
    manifest = backup_manifest(sources, target, artifact_roots=roots)
    manifest["scope"] = {
        "included": [
            "13 application and Event Library databases",
            "native/content/receipt immutable files",
            "initialization published artifacts",
            "Runtime artifacts",
            "available CDECR prebuilt artifacts",
        ],
        "excluded": [
            "credentials",
            "browser Profiles",
            "mutable workspaces",
            "deployment configuration",
        ],
        "consistency": (
            "Each database is internally consistent; not one simultaneous cross-database snapshot."
        ),
    }
    atomic_json(target / "manifest.json", manifest)
    checked = []
    for entry in manifest["databases"]:
        path = (target / entry["file"]).resolve(strict=True)
        if path.parent != target.resolve() or digest(path) != entry["sha256"]:
            raise RuntimeError("Backup database digest mismatch")
        # The saved file is independent from live writers. This scans the backup,
        # never VACUUMs/checkpoints or locks the live application database.
        with sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
            result = db.execute("PRAGMA quick_check").fetchall()
            if result != [("ok",)]:
                raise RuntimeError("Backup quick_check failed: " + entry["file"])
            tables = db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[
                0
            ]
            if not tables:
                raise RuntimeError("Saved database has no schema")
        checked.append(entry["file"])
        print(json.dumps({"phase": "verified", "file": entry["file"]}), flush=True)
    for artifact in manifest["artifacts"]:
        path = (target / artifact["file"]).resolve(strict=True)
        if not path.is_relative_to(target.resolve()) or digest(path) != artifact["sha256"]:
            raise RuntimeError("Backup artifact digest mismatch")
    with sqlite3.connect(
        (target / "initialization.sqlite3").as_uri() + "?mode=ro&immutable=1", uri=True
    ) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        table = next((t for t in tables if t == "initialization_runs"), None)
        if table:
            columns = [r[1] for r in db.execute("PRAGMA table_info(" + table + ")")]
            if "ticker" in columns:
                rows = db.execute(
                    "SELECT ticker FROM " + table + " WHERE ticker='MU' LIMIT 1"
                ).fetchall()
                if not rows:
                    raise RuntimeError("Current saved initialization does not include MU")
    verification = {
        "completed_at": datetime.now(UTC).isoformat(),
        "databases_verified": checked,
        "artifacts_verified": len(manifest["artifacts"]),
        "method": (
            "Independent saved-database reads, SHA256 and SQLite quick_check; "
            "not a full application restart drill."
        ),
        "old_recovery_points_retained": True,
    }
    atomic_json(target / "verification.json", verification)
    print(
        json.dumps(
            {
                "backup_directory": str(target),
                "databases": len(checked),
                "artifacts": len(manifest["artifacts"]),
                "verification": "passed",
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
