"""W3-only inventory, verified legacy input retirement, and derivative GC.

Default commands are dry runs. Inputs need a verified surviving shared source;
active jobs, Fork operations, and resumable Runtime threads remain protected.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import ExitStack, closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from doxagent.persistent_runtime_v2.w3_research_store import W3ResearchStore, atomic, compact, sha


def readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def inventory(root, runtime_db):
    root = Path(root).resolve()
    now = datetime.now(UTC).isoformat()
    protected, known = {}, set()
    if not Path(runtime_db).is_file():
        raise ValueError("Runtime journal is required to establish protection")
    with closing(readonly(runtime_db)) as db:
        for row in db.execute("SELECT * FROM runtime_v2_w3_threads"):
            if row["kind"] == "TEMPLATE":
                continue
            run = row["workspace"]
            known.add(run)
            if (
                row["owner_case_id"]
                or row["status"] in {"ACTIVE", "CREATING", "DELETING"}
                and (row["expires_at"] or "") > now
            ):
                protected[run] = "active/resumable Runtime Thread"
    worker = root / "worker-jobs.sqlite3"
    if not worker.is_file():
        raise ValueError("Worker journal is required to establish protection")
    with closing(readonly(worker)) as db:
        for row in db.execute("SELECT request,status FROM jobs WHERE request IS NOT NULL"):
            request = json.loads(row[0])
            if request.get("node") != "persistent_runtime_w3":
                continue
            known.add(request["run_id"])
            if row[1] in {"queued", "running"}:
                protected[request["run_id"]] = "active Worker job"
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='w3_thread_operations'").fetchone():
            for row in db.execute(
                "SELECT request FROM w3_thread_operations WHERE status='CREATING'"
            ):
                protected[json.loads(row[0])["run_id"]] = "Fork awaiting reconciliation"
    sources = {}
    for manifest in (root / ".w3-packs").glob("*/manifest.json"):
        if len(manifest.parent.name) != 64:
            continue
        for name, digest in json.loads(manifest.read_text()).items():
            path = (manifest.parent / "files" / name).resolve()
            if path.is_relative_to(root) and path.is_file() and not path.is_symlink():
                sources.setdefault(digest, path)
    files = []
    for run in sorted(known):
        workspace = (root / run).resolve()
        if workspace.parent != root or not workspace.is_dir() or workspace.is_symlink():
            continue
        candidates = list((workspace / "context/research").rglob("*"))
        candidates += list((workspace / "attempts").glob("*/reference_view.md"))
        for path in candidates:
            if (
                not path.is_file()
                or path.is_symlink()
                or not path.resolve().is_relative_to(workspace)
            ):
                continue
            digest = sha(path.read_bytes())
            source = sources.get(digest)
            valid = bool(
                source and source.resolve() != path.resolve() and sha(source.read_bytes()) == digest
            )
            files.append(
                dict(
                    run_id=run,
                    path=str(path),
                    relative=str(path.relative_to(workspace)),
                    sha256=digest,
                    bytes=path.stat().st_size,
                    shared_source=str(source) if source else None,
                    can_retire=run not in protected and valid,
                    reason=protected.get(run)
                    or ("verified duplicate" if valid else "unique/unverified source"),
                )
            )
    return dict(
        root=str(root),
        runtime_db=str(Path(runtime_db).resolve()),
        at=now,
        files=files,
        reclaimable_bytes=sum(f["bytes"] for f in files if f["can_retire"]),
    )


def retire(report):
    # Fence both existing owners while checking protection and retiring inputs.
    # This is an explicit offline maintenance command, never a Case hot path.
    with ExitStack() as stack:
        for path in sorted(
            {
                str(Path(report["runtime_db"]).resolve()),
                str(Path(report["root"]).resolve() / "worker-jobs.sqlite3"),
            }
        ):
            db = stack.enter_context(closing(sqlite3.connect(path, timeout=5)))
            db.execute("BEGIN IMMEDIATE")
        return _retire_locked(report)


def _retire_locked(report):
    """Revalidate protection and hashes; delete only named duplicate input files."""
    fresh = inventory(report["root"], report["runtime_db"])
    allowed = {f["path"]: f for f in fresh["files"] if f["can_retire"]}
    root = Path(report["root"]).resolve()
    retired = []
    for entry in report["files"]:
        current = allowed.get(entry["path"])
        if not entry["can_retire"] or not current or current["sha256"] != entry["sha256"]:
            continue
        path, source = Path(current["path"]), Path(current["shared_source"])
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            raise ValueError("retirement target escaped workspace")
        if (
            sha(path.read_bytes()) != current["sha256"]
            or sha(source.read_bytes()) != current["sha256"]
        ):
            raise ValueError("retirement source/target changed")
        record = root / ".w3-legacy-recovery" / (current["run_id"] + ".json")
        mapping = json.loads(record.read_bytes()) if record.exists() else {}
        mapping[current["relative"]] = dict(source=str(source), sha256=current["sha256"])
        atomic(record, compact(mapping).encode())
        path.unlink()
        retired.append(current["path"])
    return retired


def restore(root, run_id):
    root = Path(root).resolve()
    workspace = (root / run_id).resolve()
    if workspace.parent != root or not run_id.startswith("w3-"):
        raise ValueError("invalid W3 recovery workspace")
    mapping = json.loads((root / ".w3-legacy-recovery" / (run_id + ".json")).read_bytes())
    for rel, item in mapping.items():
        path = (workspace / rel).resolve()
        if not path.is_relative_to(workspace):
            raise ValueError("recovery path escaped workspace")
        content = Path(item["source"]).read_bytes()
        if sha(content) != item["sha256"]:
            raise ValueError("recovery source checksum mismatch")
        atomic(path, content)


def references(store):
    """Upstream lifecycle owners can inspect exact retained sources without writes."""
    with closing(store.connect()) as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT id,ticker,kind,source_sha,source_path,state "
                "FROM w3_asset_builds ORDER BY ticker,kind,id"
            )
        ]


def extend_context(store, case_id, days=7):
    if not case_id or not 1 <= days <= 365:
        raise ValueError("case-id and 1..365 extension days required")
    until = (datetime.now(UTC) + timedelta(days=days)).isoformat()
    with closing(store.connect()) as db, db:
        cursor = db.execute(
            "UPDATE w3_case_contexts SET expires_at=max(expires_at,?) WHERE case_id=?",
            (until, case_id),
        )
        if cursor.rowcount == 0:
            raise ValueError("unknown frozen Case")
    return {"case_id": case_id, "expires_at_at_least": until}


def restore_archive(root, run_id):
    import zipfile

    root = Path(root).resolve()
    target = (root / run_id).resolve()
    if not run_id.startswith("w3-ready-") or target.parent != root or target.exists():
        raise ValueError("archive restore requires an absent dedicated W3 workspace")
    with zipfile.ZipFile(root / ".w3-case-archives" / (run_id + ".zip")) as archive:
        for name in archive.namelist():
            path = (target / name).resolve()
            if not path.is_relative_to(target):
                raise ValueError("archive member escaped workspace")
        archive.extractall(target)
    return {"restored": str(target)}


def gc_derivatives(store, *, apply=False):
    """Only reproducible views, never canonical sources, audit or Case outputs."""
    import time
    started = time.monotonic()
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    protected = set()
    candidates = []
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        for row in db.execute(
            "SELECT manifest_sha FROM w3_case_contexts WHERE expires_at>?",
            (datetime.now(UTC).isoformat(),),
        ):
            manifest = json.loads((store.contexts / (row[0] + ".json")).read_bytes())
            protected.update(manifest.get("components", {}).values())
        # Keep the newest ready source of each ticker/kind warm.
        latest = db.execute(
            "SELECT id,ticker,kind,updated_at FROM w3_asset_builds "
            "WHERE state='READY' ORDER BY updated_at DESC"
        ).fetchall()
        seen = set()
        for row in latest:
            key = row["ticker"], row["kind"]
            if key not in seen:
                protected.add(row["id"])
                seen.add(key)
        for path in store.packs.iterdir() if store.packs.exists() else []:
            if len(candidates) >= 64 or time.monotonic() - started >= 0.25:
                break
            if not path.is_dir() or path.is_symlink() or path.name in protected:
                continue
            row = db.execute(
                "SELECT source_path,source_sha FROM w3_asset_builds WHERE id=?", (path.name,)
            ).fetchone()
            if not row or datetime.fromtimestamp(path.stat().st_mtime, UTC) > cutoff:
                continue
            source = Path(row["source_path"])
            if not source.is_file() or sha(source.read_bytes()) != row["source_sha"]:
                continue
            candidates.append(str(path))
            if apply:
                import shutil

                target = path.resolve()
                if target.parent != store.packs.resolve():
                    raise ValueError("GC target escaped owned pack root")
                tombstone = store.root / ".w3-retired" / path.name
                tombstone.parent.mkdir(exist_ok=True)
                if tombstone.exists():
                    continue
                path.rename(tombstone)
                db.execute("UPDATE w3_asset_builds SET state='COLD' WHERE id=?", (path.name,))
                shutil.rmtree(tombstone)
    return candidates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "inventory",
            "retire",
            "restore",
            "gc",
            "references",
            "extend-context",
            "restore-archive",
        ],
    )
    parser.add_argument("--root", required=True)
    parser.add_argument("--runtime-db")
    parser.add_argument("--report")
    parser.add_argument("--run-id")
    parser.add_argument("--case-id")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.command == "inventory":
        result = inventory(args.root, args.runtime_db)
        if args.report:
            atomic(Path(args.report), compact(result).encode())
    elif args.command == "retire":
        report = json.loads(Path(args.report).read_bytes())
        if Path(report["root"]).resolve() != Path(args.root).resolve():
            raise ValueError("report root mismatch")
        result = retire(report) if args.apply else report
    elif args.command == "restore":
        result = restore(args.root, args.run_id) if args.apply else {"requires_apply": True}
    elif args.command == "references":
        result = references(W3ResearchStore(args.root))
    elif args.command == "extend-context":
        result = (
            extend_context(W3ResearchStore(args.root), args.case_id, args.days)
            if args.apply
            else {"requires_apply": True}
        )
    elif args.command == "restore-archive":
        result = restore_archive(args.root, args.run_id) if args.apply else {"requires_apply": True}
    else:
        result = gc_derivatives(W3ResearchStore(args.root), apply=args.apply)
    print(compact(result))


if __name__ == "__main__":
    main()
