"""Idempotent terminal receipt repair. Dry-run by default; no model execution.

Run inside the production container so native rollout paths resolve normally.
RuntimeJournal writes preserve the existing CDC/projection path; never edit Read
metric buckets directly. Worker DB repair is optional and uses compare-and-swap.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

from doxagent.codex_worker.turn_usage import read_turn_usage, rollout_path
from doxagent.persistent_runtime_v2.journal import RuntimeJournal


def corrected_telemetry(job: dict, home: Path) -> dict | None:
    if job.get("status") not in {"succeeded", "failed", "cancelled"}:
        return None
    previous = dict(job.get("telemetry") or {})
    if previous.get("usage_complete") is True:
        return previous
    partial = dict(previous, usage_source=previous.get("usage_source", "sdk_last_request"),
                   usage_complete=False)
    thread, turn = job.get("thread_id"), job.get("turn_id")
    if not thread or not turn:
        return partial
    try:
        path = rollout_path(home, thread)
        if path is None:
            return partial
        usage, complete = read_turn_usage(path, thread, turn)
    except (OSError, ValueError, sqlite3.Error):
        return partial
    if not complete:
        return partial
    telemetry = previous
    telemetry.update(usage=usage, observed_usage=usage,
                     usage_source="native_turn_receipts_v1", usage_complete=True)
    return telemetry


def repair_runtime(path: Path, home: Path, *, apply: bool = False) -> dict:
    journal = RuntimeJournal(path, initialize=False)
    counts: Counter = Counter()
    # Pagination avoids holding a read transaction across a production repair.
    for namespace in ("worker_invocations", "worker_receipts"):
        after = ""
        while True:
            with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                db.row_factory = journal.content.row
                rows = db.execute(
                    "SELECT key,payload FROM runtime_values WHERE namespace=? AND key>? "
                    "ORDER BY key LIMIT 100", (namespace, after)
                ).fetchall()
            if not rows:
                break
            for row in rows:
                after = row["key"]
                value = json.loads(row["payload"])
                if not isinstance(value, dict):
                    continue
                job = value.get("job") if namespace == "worker_invocations" else value
                if not isinstance(job, dict):
                    continue
                counts["scanned"] += 1
                telemetry = corrected_telemetry(job, home)
                if telemetry is None:
                    counts["unrecoverable_or_active"] += 1
                    continue
                if telemetry == job.get("telemetry"):
                    counts["unchanged"] += 1
                    continue
                old = row["payload"]
                job["telemetry"] = telemetry
                counts["repairable"] += 1
                if apply:
                    with journal.transaction(operation="repair_codex_usage") as db:
                        current = db.execute(
                            "SELECT payload FROM runtime_values WHERE namespace=? AND key=?",
                            (namespace, after),
                        ).fetchone()
                        if current is None or current["payload"] != old:
                            counts["concurrent_change_skipped"] += 1
                            continue
                        db.execute(
                            "UPDATE runtime_values SET payload=? WHERE namespace=? AND key=?",
                            (journal.content.encode(value), namespace, after),
                        )
                        counts["updated"] += 1
    return dict(counts)


def repair_jobs(path: Path, home: Path, *, apply: bool = False) -> dict:
    counts: Counter = Counter()
    after = 0
    while True:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            rows = db.execute("SELECT sequence,id,payload FROM jobs WHERE sequence>? "
                              "ORDER BY sequence LIMIT 100", (after,)).fetchall()
        if not rows:
            break
        for sequence, identity, raw in rows:
            after = sequence
            job = json.loads(raw)
            counts["scanned"] += 1
            telemetry = corrected_telemetry(job, home)
            if telemetry is None:
                counts["unrecoverable_or_active"] += 1
                continue
            if telemetry == job.get("telemetry"):
                counts["unchanged"] += 1
                continue
            job["telemetry"] = telemetry
            counts["repairable"] += 1
            if apply:
                with closing(sqlite3.connect(path, timeout=30)) as db, db:
                    changed = db.execute("UPDATE jobs SET payload=? WHERE id=? AND payload=?",
                                         (json.dumps(job), identity, raw)).rowcount
                    counts["updated" if changed else "concurrent_change_skipped"] += 1
    return dict(counts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", required=True, type=Path)
    parser.add_argument("--runtime-db", required=True, type=Path)
    parser.add_argument("--worker-db", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = {"apply": args.apply,
              "runtime": repair_runtime(args.runtime_db, args.codex_home, apply=args.apply)}
    if args.worker_db:
        result["worker"] = repair_jobs(args.worker_db, args.codex_home, apply=args.apply)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
