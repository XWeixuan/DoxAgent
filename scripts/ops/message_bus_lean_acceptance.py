"""Read-only lean pipeline receipt. Run in the formal Bus container, not a new worker.

Raw cycle detail is retained for 48h; keep this probe bounded to at most 120m.
Long windows use saved receipts/hour summaries, never a full Native history scan.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


def sample(bus_path, minutes=30, now=None):
    from doxagent.message_bus_v2.cadence import cadence, distribution
    from doxagent.message_bus_v2.collection_plane.store import path_for

    now = now or datetime.now(timezone.utc)
    since = now - timedelta(minutes=minutes)
    database = Path(bus_path).resolve()
    acquisition = path_for(database)
    with sqlite3.connect(acquisition.as_uri() + "?mode=ro", uri=True, timeout=1) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        rows = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM cycles WHERE (status='SCHEDULE' AND dispatch_at<=?) OR "
                "(status!='SCHEDULE' AND dispatch_at>=? AND dispatch_at<=?) ORDER BY dispatch_at",
                (now.isoformat(), since.isoformat(), now.isoformat()),
            )
        ]
        physical = {
            r["run_id"]: json.loads(r["requests_json"])
            for r in db.execute(
                "SELECT run_id,requests_json FROM physical_cycles WHERE finished_at>=? AND finished_at<=?",
                (since.isoformat(), now.isoformat()),
            )
        }
        manifest = [
            dict(r)
            for r in db.execute(
                "SELECT binding_id,unit,source_id,ticker,required,head_json FROM units"
            )
        ]
    receipts = []
    for row in rows:
        proof = json.loads(row.pop("data_json"))
        proof["requests"] = physical.get(proof.get("physical_run_id"), proof.get("requests", []))
        row.update(proof)
        winner = next(
            (
                r
                for r in row["requests"]
                if r.get("physical_request_id") == row.get("winning_request_id")
                and r.get("proof_basis") == "TRANSPORT_SEND"
            ),
            None,
        )
        if row.get("list_validated") and winner:
            receipts.append(
                {
                    **row,
                    "observed_at": row["listing_received_at"],
                    "request_started_at": winner["request_started_at"],
                    "committed_at": row["finished_at"],
                }
            )
    cycle = cadence(rows, receipts, since, now)
    units = {(r["binding_id"], r["unit"]): r for r in manifest}
    for unit in cycle["units"]:
        known = units.get((unit["binding_id"], unit["unit"]), {})
        unit.update(source_id=known.get("source_id"), ticker=known.get("ticker"))
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1) as db:
        db.row_factory = sqlite3.Row
        articles = [
            dict(r)
            for r in db.execute(
                "SELECT article_key,source_id,url,first_seen_at,state,outcome,acquired_at,checked_at,"
                "fetch_total,full_total,cache_total,last_error FROM live_articles "
                "WHERE first_seen_at>=? AND first_seen_at<? AND state!='MERGED' ORDER BY first_seen_at",
                (since.isoformat(), now.isoformat()),
            )
        ]
        deliveries = [
            dict(r)
            for r in db.execute(
                "SELECT d.article_key,d.ticker,d.source_id,d.state,d.created_at,d.consumable_at,d.last_error "
                "FROM live_deliveries d JOIN live_articles a USING(article_key) "
                "WHERE a.first_seen_at>=? AND a.first_seen_at<? AND a.state!='MERGED'",
                (since.isoformat(), now.isoformat()),
            )
        ]
    first = {r["article_key"]: datetime.fromisoformat(r["first_seen_at"]) for r in articles}
    for row in deliveries:
        start = first[row["article_key"]]
        end = datetime.fromisoformat(row["consumable_at"]) if row["consumable_at"] else None
        row["capture_to_consumable_seconds"] = (end - start).total_seconds() if end else None
        row["unfinished_age_seconds"] = (now - start).total_seconds() if not end else None
    # An undecided item remains in the denominator. Explicit irrelevance/cancel
    # and bootstrap suppression have their own visible terminal reasons.
    required = [
        r
        for r in deliveries
        if r["state"] not in {"IRRELEVANT", "SUPPRESSED", "ADMISSION_REJECTED", "CANCELLED"}
    ]
    eligible = [
        r for r in articles if r["outcome"] != "NOT_APPLICABLE" and r["state"] != "NOT_APPLICABLE"
    ]
    full = [r for r in eligible if r["outcome"] in {"FULL", "SHORT_FULL"}]
    latencies = [
        r["capture_to_consumable_seconds"]
        for r in required
        if r["state"] == "PUBLISHED" and r["capture_to_consumable_seconds"] is not None
    ]
    return {
        "at": now.isoformat(),
        "since": since.isoformat(),
        "cycle": cycle,
        "manifest": manifest,
        "articles": articles,
        "deliveries": deliveries,
        "body": {
            "eligible": len(eligible),
            "full": len(full),
            "ratio": len(full) / len(eligible) if eligible else None,
            "basis": "CANONICAL_FIRST_SEEN_ALL_ELIGIBLE_PROGRAM_LABELS_REQUIRE_PUBLISHER_SPOT_CHECK",
        },
        "delivery": {
            "required_including_unfinished": len(required),
            "completed": len(latencies),
            "within_10s": sum(v <= 10 for v in latencies),
            "within_10s_ratio": sum(v <= 10 for v in latencies) / len(required)
            if required
            else None,
            "completed_seconds": distribution(latencies),
        },
        "files": {
            str(p): p.stat().st_size if p.exists() else 0
            for path in (database, acquisition)
            for p in (path, Path(str(path) + "-wal"))
        },
        "limits": [
            "T1 is Owner stream consumability; Read/UI T2 is separate.",
            "No independent upstream coverage or FULL quality proof is fabricated.",
            "No-article windows cannot prove BODY or delivery targets.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bus-path")
    parser.add_argument("--minutes", type=int, choices=range(1, 121), default=30)
    args = parser.parse_args()
    if not args.bus_path:
        from doxagent.settings import DoxAgentSettings

        args.bus_path = DoxAgentSettings().message_bus_v2_sqlite_path
    print(json.dumps(sample(args.bus_path, args.minutes), ensure_ascii=False))


if __name__ == "__main__":
    main()
