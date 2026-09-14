"""Targeted sweep reconciliation. Defaults to read-only; run with workers stopped."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from doxagent.message_bus_v2.admission import AdmissionContext, evaluate_admission
from doxagent.semantic_clock import boundary, semantic_day


def reconcile(path: str, sweep_id: str, *, apply: bool = False) -> dict:
    db = sqlite3.connect(f"file:{path}?mode={'rw' if apply else 'ro'}", uri=True)
    db.row_factory = sqlite3.Row
    parent = db.execute("SELECT * FROM runtime_tasks WHERE id=?", (sweep_id,)).fetchone()
    if parent is None:
        raise ValueError("sweep not found")
    inputs = json.loads(parent["inputs"])
    cutoff = datetime.fromisoformat(inputs["cutoff"])
    start = boundary(semantic_day(cutoff) - timedelta(days=1))
    context = AdmissionContext(
        mode="CLOSED_SWEEP",
        sweep_id=sweep_id,
        source_task_id="incident-reconciliation",
        window_start=start,
        cutoff=cutoff,
    )
    frozen = db.execute(
        "SELECT payload FROM runtime_values WHERE namespace='sweep_members' AND key=?", (sweep_id,)
    ).fetchone()
    if frozen is None:
        raise ValueError("frozen manifest not found")
    members = json.loads(frozen[0])
    entries = []
    for identity in members:
        row = db.execute("SELECT * FROM runtime_tasks WHERE id=?", (identity,)).fetchone()
        source = json.loads(row["inputs"])["source"]
        basis = source.get("publication_time_basis", "EXACT")
        # The old Reuters adapter exposed only the search-result calendar date.
        if "reuters" in source.get("source_id", "").lower():
            basis = "DATE"
        reason = evaluate_admission(
            datetime.fromisoformat(source["published_at"].replace("Z", "+00:00")),
            context,
            cutoff,
            basis,
        )
        entries.append(
            {
                "task_id": identity,
                "source_message_id": source["source_message_id"],
                "source_id": source["source_id"],
                "published_at": source["published_at"],
                "basis": basis,
                "reason": reason,
            }
        )
    excluded = [e for e in entries if e["reason"]]
    effective = [e["task_id"] for e in entries if not e["reason"]]
    ids = [e["source_message_id"] for e in excluded]
    q = ",".join("?" for _ in ids) or "NULL"
    candidates = db.execute(
        f"SELECT count(*) FROM runtime_v2_candidates WHERE source_message_id IN ({q})", ids
    ).fetchone()[0]
    cases = db.execute(
        f"SELECT * FROM runtime_v2_cases WHERE source_message_id IN ({q})", ids
    ).fetchall()
    maintain = db.execute(
        "SELECT * FROM runtime_tasks WHERE id=?", ("maintain:" + sweep_id,)
    ).fetchone()
    receipt = json.loads(maintain["receipt"]) if maintain else {}
    trades = db.execute(
        f"SELECT count(*) FROM runtime_v2_trade_records WHERE case_id IN (SELECT case_id FROM runtime_v2_cases WHERE source_message_id IN ({q}))",
        ids,
    ).fetchone()[0]
    report = {
        "repair_id": "freshness-v1:" + sweep_id,
        "sweep_id": sweep_id,
        "window_start": start.isoformat(),
        "cutoff": cutoff.isoformat(),
        "frozen": len(members),
        "excluded": len(excluded),
        "retained": len(effective),
        "affected_cases": len(cases),
        "affected_provisional": candidates,
        "affected_trades": trades,
        "maintenance_started": bool(receipt),
        "entries": entries,
        "applied": False,
    }

    def restore_unstarted_retained() -> int:
        """Shutdown can fail tasks before Case creation; preserve their admission identity."""
        resumed = 0
        now = datetime.now(UTC).isoformat()
        with db:
            for identity in effective:
                row = db.execute("SELECT * FROM runtime_tasks WHERE id=?", (identity,)).fetchone()
                source_id = json.loads(row["inputs"])["source"]["source_message_id"]
                existing_case = db.execute(
                    "SELECT 1 FROM runtime_v2_cases WHERE source_message_id=?", (source_id,)
                ).fetchone()
                if row["status"] != "FAILED" or existing_case:
                    continue
                db.execute(
                    "INSERT OR IGNORE INTO runtime_values VALUES(?,?,?)",
                    ("admission_retained_task_history", identity, json.dumps(dict(row))),
                )
                db.execute(
                    "UPDATE runtime_tasks SET status='PENDING',failures=0,token=token+1,lease_until=NULL,due_at=?,updated_at=? WHERE id=?",
                    (now, now, identity),
                )
                resumed += 1
            if resumed:
                row = db.execute(
                    "SELECT receipt FROM runtime_tasks WHERE id=?", (sweep_id,)
                ).fetchone()
                db.execute(
                    "INSERT OR IGNORE INTO runtime_values VALUES(?,?,?)",
                    ("admission_retained_parent_history", sweep_id, row[0]),
                )
                db.execute(
                    "UPDATE runtime_tasks SET status='PENDING',receipt=?,token=token+1,lease_until=NULL,due_at=?,updated_at=? WHERE id=?",
                    (
                        json.dumps({"repair_id": report["repair_id"], "retained_resumed": resumed}),
                        now,
                        now,
                        sweep_id,
                    ),
                )
        return resumed

    if not apply:
        db.close()
        return report
    existing = db.execute(
        "SELECT 1 FROM runtime_values WHERE namespace='admission_reconciliation' AND key=?",
        (report["repair_id"],),
    ).fetchone()
    if existing:
        report["already_applied"] = True
        report["retained_resumed"] = restore_unstarted_retained()
        db.close()
        return report
    if trades or receipt:
        raise ValueError(
            "Published/maintenance/trade state requires reviewed reconciliation; no writes made"
        )
    now = datetime.now(UTC).isoformat()

    def value(namespace: str, key: str, payload: object) -> None:
        db.execute(
            "INSERT INTO runtime_values VALUES(?,?,?) ON CONFLICT(namespace,key) DO UPDATE SET payload=excluded.payload",
            (namespace, key, json.dumps(payload)),
        )

    with db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS runtime_v2_admission_exclusions (source_message_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL)"
        )
        for entry in excluded:
            audit = {**entry, "repair_id": report["repair_id"], "checked_at": now}
            value("invalid_admissions", entry["source_message_id"], audit)
            db.execute(
                "INSERT OR REPLACE INTO runtime_v2_admission_exclusions VALUES(?,?)",
                (entry["source_message_id"], json.dumps(audit)),
            )
            task_receipt = json.loads(
                db.execute(
                    "SELECT receipt FROM runtime_tasks WHERE id=?", (entry["task_id"],)
                ).fetchone()[0]
            )
            task_receipt.update(admission_excluded=True, repair_id=report["repair_id"])
            db.execute(
                "UPDATE runtime_tasks SET status='FAILED',receipt=?,token=token+1,lease_until=NULL,updated_at=? WHERE id=?",
                (json.dumps(task_receipt), now, entry["task_id"]),
            )
        for row in cases:
            original = json.loads(row["payload_json"])
            value("admission_excluded_case_history", row["case_id"], original)
            amended = {
                **original,
                "status": "FAILED",
                "technical_status": "FAILED",
                "error_code": "invalid_admission",
                "error_message": "Excluded by " + report["repair_id"],
                "updated_at": now,
            }
            db.execute(
                "UPDATE runtime_v2_cases SET status='FAILED',technical_status='FAILED',payload_json=?,updated_at=? WHERE case_id=?",
                (json.dumps(amended), now, row["case_id"]),
            )
        value("sweep_effective_members", sweep_id, effective)
        value(
            "w3_batch",
            sweep_id,
            {"members": effective, "ready": False, "repair_id": report["repair_id"]},
        )
        # Restart only the affected sweep over the effective set. Completed valid Case
        # receipts remain idempotent; original frozen manifest and parent inputs stay intact.
        db.execute(
            "UPDATE runtime_tasks SET status='PENDING',receipt=?,token=token+1,lease_until=NULL,due_at=?,updated_at=? WHERE id=?",
            (json.dumps({"repair_id": report["repair_id"]}), now, now, sweep_id),
        )
        if maintain:
            amended_inputs = {
                **json.loads(maintain["inputs"]),
                "members": effective,
                "repair_id": report["repair_id"],
            }
            db.execute(
                "UPDATE runtime_tasks SET inputs=?,status='PENDING',token=token+1,lease_until=NULL WHERE id=?",
                (json.dumps(amended_inputs), maintain["id"]),
            )
        report["applied"] = True
        value("admission_reconciliation", report["repair_id"], report)
    report["retained_resumed"] = restore_unstarted_retained()
    db.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--sweep", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = reconcile(args.runtime, args.sweep, apply=args.apply)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "entries"}, ensure_ascii=False))
