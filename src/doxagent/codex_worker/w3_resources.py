"""W3-only inventory, verified legacy input retirement, and derivative GC.

Default commands are dry runs. Inputs need a verified surviving shared source;
active jobs, Fork operations, and resumable Runtime threads remain protected.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
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
    with closing(store.connect()) as db:
        manifests = db.execute('SELECT manifest_sha FROM w3_case_contexts WHERE case_id=?', (case_id,)).fetchall()
    for row in manifests:
        manifest = json.loads((store.contexts / (row[0] + '.json')).read_bytes())
        store.protect_components(manifest.get('components', {}), until)
    with closing(store.connect()) as db, db:
        cursor = db.execute(
            "UPDATE w3_case_contexts SET expires_at=max(expires_at,?),status='EXTENDED' WHERE case_id=?",
            (until, case_id),
        )
        if cursor.rowcount == 0:
            raise ValueError("unknown frozen Case")
    return {"case_id": case_id, "expires_at_at_least": until}


def retire_settled_contexts(store, runtime_db, *, now=None):
    """End the online obligation of completed no-trade phases, retaining exact replay inputs."""
    if not Path(runtime_db).is_file():
        return []
    now = now or datetime.now(UTC)
    cursor = getattr(store, '_settled_context_cursor', '')
    with closing(store.connect()) as db:
        candidates = db.execute(
            "SELECT token_hash,case_id,expires_at,status FROM w3_case_contexts "
            "WHERE token_hash>? ORDER BY token_hash LIMIT 128", (cursor,),
        ).fetchall()
    # Explicit extensions remain in force, even when the original Case is closed.
    settled = []
    cutoff = (now - timedelta(hours=24)).isoformat()
    deadline = time.monotonic() + .25
    last = cursor
    exhausted = True
    with closing(readonly(runtime_db)) as runtime:
        runtime.execute('PRAGMA busy_timeout=100')
        for candidate in candidates:
            if time.monotonic() >= deadline:
                exhausted = False
                break
            last = candidate['token_hash']
            if candidate['status'] != 'ACTIVE':
                continue
            case_id = candidate['case_id']
            if not runtime.execute(
                "SELECT 1 FROM runtime_v2_cases c WHERE c.case_id=? AND c.status='COMPLETED' "
                "AND c.technical_status='OK' AND c.updated_at<=? "
                "AND NOT EXISTS (SELECT 1 FROM runtime_v2_trade_records WHERE case_id=c.case_id) "
                "AND NOT EXISTS (SELECT 1 FROM runtime_v2_effects WHERE case_id=c.case_id AND status<>'COMPLETED') "
                "AND NOT EXISTS (SELECT 1 FROM runtime_v2_w3_threads WHERE owner_case_id=c.case_id)",
                (case_id, cutoff),
            ).fetchone():
                continue
            stages = {row['phase']: row for row in runtime.execute(
                "SELECT phase,status,committed_at,result_json FROM runtime_v2_w3_stages WHERE case_id=?",
                (case_id,),
            )}
            trade = stages.get('trade_decision')
            operations = stages.get('post_decision_operations')
            if not trade or not operations or any(
                stage['status'] != 'COMMITTED' or not stage['committed_at'] or stage['committed_at'] > cutoff
                for stage in (trade, operations)
            ):
                continue
            operations_result = json.loads(operations['result_json'] or '{}')
            if (json.loads(trade['result_json'] or '{}').get('decision') != 'NO_TRADE'
                or operations_result.get('watch_actions') != [] or operations_result.get('pin_actions') != []):
                continue
            if runtime.execute(
                "SELECT 1 FROM runtime_v2_w3_stages s JOIN runtime_values v ON v.key=s.worker_job_id "
                "AND v.namespace IN ('w3_ack_pending','w3_resident_release') WHERE s.case_id=? LIMIT 1",
                (case_id,),
            ).fetchone():
                continue
            settled.append((candidate['token_hash'], candidate['expires_at']))
    if settled:
        with closing(store.connect()) as db, db:
            db.execute('PRAGMA busy_timeout=100')
            db.executemany("UPDATE w3_case_contexts SET status='RETIRED' "
                           "WHERE token_hash=? AND status='ACTIVE' AND expires_at=?", settled)
    store._settled_context_cursor = '' if exhausted and len(candidates) < 128 else last
    return [token_hash for token_hash, _ in settled]


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
    """Bounded metadata selection; only atomic rename occurs under the writer."""
    import time
    import shutil
    started = time.monotonic()
    now = datetime.now(UTC).isoformat()
    cutoff = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
    with closing(store.connect()) as db:
        rows = db.execute("SELECT id,source_path,source_sha FROM w3_asset_builds a "
            "WHERE state='READY' AND retained_until<? AND updated_at<? "
            "AND EXISTS (SELECT 1 FROM w3_asset_builds b WHERE b.ticker=a.ticker "
            "AND b.kind=a.kind AND b.state='READY' AND b.updated_at>a.updated_at) LIMIT 16",
            (now, cutoff)).fetchall()
    # Old NULL watermarks stay protected until a bounded pass covers all live contexts.
    if apply and not getattr(store, '_gc_backfilled', False):
        cursor = getattr(store, '_gc_context_cursor', '')
        with closing(store.connect()) as db:
            contexts = db.execute("SELECT token_hash,manifest_sha,expires_at FROM w3_case_contexts "
                "WHERE token_hash>? AND expires_at>? AND status<>'RETIRED' "
                "ORDER BY token_hash LIMIT 128", (cursor, now)).fetchall()
        watermarks = {}
        for context in contexts:
            if time.monotonic() - started >= .25:
                break
            manifest = json.loads((store.contexts / (context["manifest_sha"] + ".json")).read_bytes())
            until = context["expires_at"]
            for identity in manifest.get("components", {}).values():
                watermarks[identity] = max(watermarks.get(identity, until), until)
            cursor = context["token_hash"]
        if watermarks:
            with closing(store.connect()) as db, db:
                db.executemany(
                    "UPDATE w3_asset_builds SET retained_until=? WHERE id=? "
                    "AND (retained_until IS NULL OR retained_until<?)",
                    [(until, identity, until) for identity, until in watermarks.items()],
                )
        store._gc_context_cursor = cursor
        if not contexts:
            with closing(store.connect()) as db, db:
                db.execute("UPDATE w3_asset_builds SET retained_until='1970-01-01' WHERE retained_until IS NULL")
            store._gc_backfilled = True
    candidates = []
    # A failed post-commit delete must not leave a permanent tombstone.
    retired = store.root / '.w3-retired'
    if apply and retired.exists():
        for n, path in enumerate(retired.iterdir()):
            if n >= 8 or time.monotonic() - started >= .25:
                break
            with closing(store.connect()) as db:
                owner = db.execute('SELECT state FROM w3_asset_builds WHERE id=?', (path.name,)).fetchone()
            if owner and (owner[0] == 'COLD' or (store.packs / path.name).exists()):
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
    for row in rows:
        if time.monotonic() - started >= .25:
            break
        path = store.packs / row['id']
        source = Path(row['source_path'])
        if not path.is_dir() or path.is_symlink() or not source.is_file():
            continue
        from doxagent.codex_runtime.published_storage import read_local_body

        try:
            read_local_body(source, row['source_sha'])
        except (OSError, ValueError):
            continue
        candidates.append(str(path))
        if not apply:
            continue
        tombstone = store.root / '.w3-retired' / row['id']
        tombstone.parent.mkdir(exist_ok=True)
        moved = False
        try:
            with closing(store.connect()) as db, db:
                db.execute('BEGIN IMMEDIATE')
                changed = db.execute("UPDATE w3_asset_builds SET state='COLD' WHERE id=? "
                    "AND state='READY' AND retained_until<? AND source_sha=?",
                    (row['id'], now, row['source_sha'])).rowcount
                if not changed or tombstone.exists():
                    db.rollback()
                    continue
                path.rename(tombstone)
                moved = True
        except BaseException:
            if moved and not path.exists():
                tombstone.rename(path)
            raise
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
