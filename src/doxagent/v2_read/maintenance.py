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
    for root_name in ("native-files", "content-files", "receipt-files", "receipt-packs"):
        for path in (destination / root_name).rglob("*"):
            if path.is_symlink():
                raise ValueError("backup content must not contain symlinks")
            if path.is_file():
                relative = path.relative_to(destination).as_posix()
                if relative not in tracked:
                    manifest["artifacts"].append({"file":relative,"sha256":digest(path)})
    atomic_json(destination / "manifest.json", manifest)
    return manifest


def collect(store, *, now=None, limit=500, budget=.1, time_floor=None):
    """Keep all 24h views/cursors, including the pre-event image needed by REMOVE."""
    with store.connect() as schema_db:
        v4 = schema_db.execute("SELECT 1 FROM sqlite_master WHERE name='object_history'").fetchone()
    if v4:
        from .serving_gc import collect as collect_serving
        return collect_serving(store,now=now,limit=limit,budget=budget,time_floor=time_floor)
    if not 1 <= limit <= 5000:
        raise ValueError("GC limit must be 1..5000")
    now = now or datetime.now(UTC)
    import time
    counts = {}
    scanned = 0
    from .settings import Limits
    cutoff = instant(now - timedelta(hours=Limits.load().mvcc_hours))
    # The retention index may cover a busy day's million commits. Read it
    # before taking the writer lock; batch write time stays bounded separately.
    if time_floor is None:
        with store.connect() as reader:
            row = reader.execute("SELECT MIN(seq) FROM commit_retention INDEXED BY commit_retention_at WHERE projected_at>=?", (cutoff,)).fetchone()
            time_floor = row[0] if row[0] is not None else store.highwater(reader)
    floor = time_floor
    with store.connect(write=True, timeout=.05) as db:
        until = time.monotonic() + budget
        db.set_progress_handler(lambda: time.monotonic() >= until, 1000)
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
        gap_sources = {row[0] for row in db.execute("SELECT DISTINCT source FROM gaps")}
        # Unknown repair dependencies still protect business history and aggregates.
        # Poll state is Bus-local technical state, never a runtime provenance input.
        # Its expired intervals may be collected while runtime repairs remain pinned.
        protected = bool(gap_sources)
        technical_only = protected and "bus" not in gap_sources
        object_filter = " AND kind='native:poll_states'" if technical_only else ""
        effective_floor = floor if not protected or technical_only else 0
        for table in ("views", "cursors"):
            counts[table] = db.execute(
                f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} "
                "WHERE expires_at<=? LIMIT ?)",
                (instant(now), limit),
            ).rowcount
        for table in ("objects", "contributions", "metric_buckets", "changes"):
            if protected and table in {"contributions", "metric_buckets"}:
                counts[table] = 0
                continue
            key = "gc_scan_" + table
            prior = db.execute("SELECT value FROM read_meta WHERE key=?", (key,)).fetchone()
            next_cursor = None
            if technical_only and table == "objects":
                key = 'gc_poll_objects'
                prior = db.execute("SELECT value FROM read_meta WHERE key=?", (key,)).fetchone()
                coordinate = json.loads(prior[0]) if prior else ['', '', '', 0]
                selected = db.execute(
                    "SELECT rowid,ticker,sort_key,id FROM objects INDEXED BY objects_page "
                    "WHERE kind='native:poll_states' AND (ticker,sort_key,id,rowid)>(?,?,?,?) "
                    "ORDER BY ticker,sort_key,id,rowid LIMIT ?", (*coordinate, limit)).fetchall()
                candidates = [r[0] for r in selected]
                next_cursor = encode([selected[-1][1], selected[-1][2], selected[-1][3], selected[-1][0]]) if selected else encode(['', '', '', 0])
            elif technical_only and table == "changes":
                key = 'gc_poll_changes'
                prior = db.execute("SELECT value FROM read_meta WHERE key=?", (key,)).fetchone()
                ticker, sequence, ordinal = json.loads(prior[0]) if prior else ['', 0, -1]
                tickers = [r[0] for r in db.execute("SELECT DISTINCT ticker FROM object_current WHERE kind='native:poll_states' ORDER BY ticker")]
                ticker = ticker if ticker in tickers else tickers[0] if tickers else ''
                selected = db.execute(
                    "SELECT rowid,seq,ordinal FROM changes INDEXED BY changes_scope "
                    "WHERE ticker=? AND kind='native:poll_states' AND (seq,ordinal)>(?,?) "
                    "ORDER BY seq,ordinal LIMIT ?", (ticker, sequence, ordinal, limit)).fetchall()
                candidates = [r[0] for r in selected]
                next_ticker = tickers[(tickers.index(ticker) + 1) % len(tickers)] if tickers else ''
                next_cursor = encode([ticker, selected[-1][1], selected[-1][2]]) if selected else encode([next_ticker, 0, -1])
            else:
                candidates = [r[0] for r in db.execute(
                    f"SELECT rowid FROM {table} WHERE rowid>? ORDER BY rowid LIMIT ?",
                    (int(prior[0]) if prior else 0, limit))]
            scanned += len(candidates)
            counts[table] = 0
            if candidates:
                placeholders = ','.join('?' for _ in candidates)
                eligible = "seq<?" if table == "changes" else "valid_to IS NOT NULL AND valid_to<?"
                predicate = (f"rowid IN ({placeholders}) AND " + eligible
                             + (object_filter if table in {"objects", "changes"} else ""))
                params = [*candidates, effective_floor]
                if table == "objects" and not protected:
                    db.execute("INSERT OR IGNORE INTO business_revisions SELECT * FROM objects NOT INDEXED WHERE "
                               + predicate + " AND kind NOT IN ('ticker','navigation','capture_coverage','native:runtime_tasks','native:runtime_values','native:poll_states')", params)
                counts[table] = db.execute(f"DELETE FROM {table} NOT INDEXED WHERE " + predicate, params).rowcount
            db.execute("INSERT INTO read_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, next_cursor if next_cursor is not None else str(candidates[-1] if candidates else 0)))
        # Unresolved repairs still rely on the original source-to-read receipt mapping.
        cursor = db.execute("SELECT value FROM read_meta WHERE key='gc_commit_scan'").fetchone()
        candidates = [row[0] for row in db.execute(
            "SELECT seq FROM commit_retention WHERE seq>? AND seq<? ORDER BY seq LIMIT ?",
            (int(cursor[0]) if cursor else 0, floor, limit))]
        counts["commits"] = 0
        if candidates:
            placeholders = ','.join('?' for _ in candidates)
            counts["commits"] = db.execute(
                "DELETE FROM commits WHERE seq IN (" + placeholders + ") "
                "AND NOT EXISTS(SELECT 1 FROM gaps g WHERE g.source=commits.source) "
                "AND EXISTS(SELECT 1 FROM checkpoints cp WHERE cp.source=commits.source AND cp.position>=CAST(commits.source_event AS INTEGER)) "
                "AND source_event NOT GLOB '*[^0-9]*' "
                "AND NOT EXISTS(SELECT 1 FROM changes WHERE changes.seq=commits.seq)", candidates).rowcount
            db.execute("DELETE FROM commit_retention WHERE seq IN (" + placeholders + ") "
                       "AND NOT EXISTS(SELECT 1 FROM commits WHERE commits.seq=commit_retention.seq)", candidates)
        db.execute("INSERT INTO read_meta(key,value) VALUES('gc_commit_scan',?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (str(candidates[-1] if candidates else 0),))
        db.execute("UPDATE read_meta SET value=max(CAST(value AS INTEGER),?) WHERE key='retained_floor'", (floor,))
    # Immutable content identities and unpositioned deduplication receipts are retained.
    return {"retention_floor": floor, "time_floor": time_floor, "deleted": counts, "scanned": scanned,
            "protected_sources": sorted(gap_sources),
            "policy": "BUS_POLL_ONLY" if technical_only else "PINNED" if protected else "ALL_EXPIRED"}


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


def reproject_trade_outcomes(store, *, ticker=None, limit=100):
    """Resume a bounded, idempotent derived-read update without touching source ledgers.

    The projector lock is shared with the live read writer. Each Case is re-read
    after taking it, so an arriving execution cannot be replaced by stale evidence.
    """
    if not 1 <= limit <= 500:
        raise ValueError("limit must be 1..500")
    from uuid import uuid4
    from doxagent.trade_execution.worker import WriterLock
    from .graph import project as project_graph
    from .trade_outcomes import project_case_trade, with_execution_state

    key = "trade_outcomes_v2:" + (ticker or "*")
    total = 0
    with WriterLock(Path(str(store.path) + ".projector")):
        while True:
            with store.connect() as db:
                row = db.execute("SELECT value FROM read_meta WHERE key=?", (key,)).fetchone()
                cursor = json.loads(row[0]) if row else ["", ""]
                if cursor == ["DONE"]:
                    return {"ticker": ticker, "processed": total, "complete": True}
                rows = db.execute(
                    "SELECT ticker,id FROM object_current WHERE kind='case' AND valid_to IS NULL "
                    "AND (? IS NULL OR ticker=?) AND (ticker>? OR (ticker=? AND id>?)) "
                    "ORDER BY ticker,id LIMIT ?",
                    (ticker, ticker, cursor[0], cursor[0], cursor[1], limit),
                ).fetchall()
            if not rows:
                with store.connect(write=True) as db:
                    db.execute("INSERT INTO read_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, encode(["DONE"])))
                return {"ticker": ticker, "processed": total, "complete": True}
            for case_ticker, case_id in rows:
                summary = store.get("case", case_ticker, case_id)
                if summary:
                    with store.connect() as db:
                        siblings = [json.loads(row[0]) for row in db.execute(
                            "SELECT payload FROM object_current WHERE kind='execution' AND ticker=? AND parent=? AND valid_to IS NULL",
                            (case_ticker, case_id),
                        )]
                    executions = [with_execution_state(item) for item in siblings]
                    incoming = [
                        {"kind": "execution", "ticker": case_ticker, "id": item["execution_id"],
                         "parent": case_id, "sort": item["triggered_at"].get("value") or summary["received_at"], "data": item}
                        for item in executions
                    ]
                    case_record, count = project_case_trade(store, case_ticker, summary, incoming)
                    graph_records, graph_counts = project_graph(store, case_ticker, case_record["data"], incoming)
                    store.ingest(
                        "trade_outcomes_v1", f"{case_ticker}:{case_id}:{uuid4().hex}",
                        [*incoming, case_record, *graph_records],
                        contributions=[count, *graph_counts],
                    )
                    total += 1
                with store.connect(write=True) as db:
                    db.execute("INSERT INTO read_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, encode([case_ticker, case_id])))


def activate_alias(alias, target, sources):
    """Switch at a verified source watermark; restart API/projector to adopt this generation."""
    store = ReadStore(target)
    report_path = Path(str(store.path) + ".rebuild.json")
    if not report_path.is_file():
        raise ValueError("verified rebuild report required")
    with store.connect() as db:
        if unaccepted_gaps(store, db):
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
    for source in sources:
        if hasattr(source,'claim_serving_facts'):source.claim_serving_facts(store.path)
    return value


def verify_shadow(store, sources):
    heads = {source.source: source.head() for source in sources}
    if not heads or any(store.position(key) < value for key, value in heads.items()):
        raise ValueError("source watermarks are not closed")
    with store.connect() as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("read store integrity check failed")
        if unaccepted_gaps(store, db):
            raise ValueError("read store has unresolved gaps")
        report = {"format": "doxagent.v2.rebuild.1", "path": str(store.path),
                  "generation": db.execute("SELECT id FROM generation").fetchone()[0],
                  "heads": heads, "schema": store.VERSION, "seq": store.highwater(db),
                  "at": instant(datetime.now(UTC)),
                  "objects_by_kind": dict(db.execute("SELECT kind,count(*) FROM objects WHERE valid_to IS NULL GROUP BY kind"))}
    atomic_json(str(store.path) + ".rebuild.json", report)
    return report


def unaccepted_gaps(store, db):
    """Only previously audited exceptions can cross a serving-generation reset.

    They continue to mark coverage incomplete and remain repairable. This does
    not convert an unverified event into a verified business event.
    """
    actual = {tuple(r) for r in db.execute('SELECT source,event,reason FROM gaps')}
    report_path = Path(str(store.path)+'.serving.json')
    if not report_path.is_file():
        return actual
    baseline = json.loads(report_path.read_text(encoding='utf-8'))
    generation = db.execute('SELECT id FROM generation').fetchone()[0]
    if baseline.get('generation') != generation or not baseline.get('verified'):
        return actual
    return actual - {tuple(r) for r in baseline.get('baseline_gaps',[])}


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


def run_gc(store, *, once=False, interval=15, budget=2):
    import time
    from doxagent.trade_execution.worker import WriterLock
    with WriterLock(Path(str(store.path) + ".maintenance")):
        batch = 128
        while True:
            started = time.monotonic()
            total = {}
            errors = []
            result = {}
            import sqlite3
            while time.monotonic() - started < budget:
                try:
                    result = collect(store, limit=batch, time_floor=result.get("time_floor"))
                except sqlite3.OperationalError as exc:
                    errors.append(str(exc))
                    if "locked" not in str(exc):
                        batch //= 2
                    if batch < 10:
                        break
                    continue
                for name, count in result["deleted"].items():
                    total[name] = total.get(name, 0) + count
                if not any(result["deleted"].values()) and not result.get("scanned"):
                    break
                batch = min(500, batch * 2)
            print(json.dumps({"maintenance": "gc", "deleted": total, "errors": errors,
                              "retention_floor": result.get("retention_floor"),
                              "protected_sources": result.get("protected_sources"),
                              "policy": result.get("policy"),
                              "elapsed_seconds": time.monotonic() - started}), flush=True)
            from .artifact_registry import collect_diagnostics
            try:
                files = collect_diagnostics(store, now=datetime.now(UTC), limit=25)
                if files:
                    print(json.dumps({'maintenance':'content-gc','quarantined':len(files)}),flush=True)
            except (OSError, ValueError, sqlite3.OperationalError) as exc:
                print(json.dumps({'maintenance':'content-gc','error':str(exc)}),flush=True)
            if once:
                return
            time.sleep(interval)
