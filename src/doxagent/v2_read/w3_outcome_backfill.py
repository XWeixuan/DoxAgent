"""Bounded, rerunnable W3 outcome reprojection. Never writes the runtime source."""

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .native_content import NativeContent
from .projectors import DomainProjectors
from .repository import ReadStore, encode


def source_has_output(db, case):
    for table in (
        "runtime_v2_archives",
        "runtime_v2_badcases",
        "runtime_v2_trade_records",
        "runtime_v2_w3_coverage_gaps",
    ):
        if db.execute(f"SELECT 1 FROM {table} WHERE case_id=?", (case["case_id"],)).fetchone():
            return True
    if db.execute(
        "SELECT 1 FROM runtime_v2_candidates WHERE source_message_id=? LIMIT 1",
        (case["source"]["source_message_id"],),
    ).fetchone():
        return True
    for namespace, key in (
        ("trade_intents", "trade:" + case["case_id"]),
        ("analysis_trade_decisions", "trade:" + case["case_id"]),
        ("candidates", "candidate:" + case["case_id"]),
    ):
        if db.execute(
            "SELECT 1 FROM runtime_values WHERE namespace=? AND key=?", (namespace, key)
        ).fetchone():
            return True
    return False


def reproject(store, runtime_db, case_ids, *, apply=False):
    """Caller holds the existing Read projector lock; scope is explicit and <=100."""
    identities = list(dict.fromkeys(case_ids))
    if not 1 <= len(identities) <= 100:
        raise ValueError("supply 1 to 100 explicit Case IDs")
    path = Path(runtime_db).resolve()
    codec = NativeContent(path)
    report = []
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        for identity in identities:
            row = db.execute(
                "SELECT payload_json FROM runtime_v2_cases WHERE case_id=?", (identity,)
            ).fetchone()
            if row is None:
                raise ValueError("source Case not found: " + identity)
            case = codec.decode(json.loads(row[0]))
            ticker = case["source"]["snapshot"]["ticker"]
            existing = store.get("case", ticker, identity)
            if existing is None:
                raise ValueError("project source receipts before repairing Case: " + identity)
            event = dict(
                table_name="runtime_v2_cases",
                entity_id=identity,
                operation="BACKFILL",
                seq=existing["revision"],
                row=dict(payload_json=encode(case)),
                w3_outcomes_complete=not source_has_output(db, case),
            )
            records, counts = DomainProjectors(store)(event)
            summary = next(r["data"] for r in records if r["kind"] == "case")
            # Read revisions change on ingest; receipt identity follows immutable
            # source evidence, so replaying the same repair cannot feed itself.
            digest = hashlib.sha256(encode(["w3-outcomes-v5.1", case]).encode()).hexdigest()
            if apply:
                store.ingest("w3-outcome-reprojection", digest, records, contributions=counts)
            report.append(
                dict(
                    case_id=identity,
                    applied=apply,
                    results=summary["results"],
                    w3_decision=summary.get("w3_decision"),
                )
            )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-db", type=Path, required=True)
    parser.add_argument("--runtime-db", type=Path, required=True)
    parser.add_argument("--case-id", action="append", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from doxagent.trade_execution.worker import WriterLock

    store = ReadStore(args.read_db)
    with WriterLock(Path(str(store.path) + ".projector")), store.projection_session():
        print(
            json.dumps(
                reproject(store, args.runtime_db, args.case_id, apply=args.apply),
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
