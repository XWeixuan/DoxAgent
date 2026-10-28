"""Offline Site metadata migration; stdlib only, no browser or Profile deletion.

Run with the formal Site container stopped. Each identity revision is retained;
the JSONL receipt contains original metadata for an explicit rollback.
"""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def migrate(path, receipt, *, apply=False):
    now = datetime.now(UTC).isoformat()
    counts = Counter()
    with sqlite3.connect(path, timeout=3) as db:
        db.row_factory = sqlite3.Row
        scopes = [
            json.loads(r[0]) for r in db.execute("SELECT data_json FROM source_access_scopes")
        ]
        protected = {s["owner_site_id"] for s in scopes if s.get("source_id")}
        # Aggregators do not make unrelated final publishers protected sources.
        protected -= {"generic", "yahoo", "yahoo_finance", "google_news"}
        sessions = {
            r[0]
            for r in db.execute("SELECT identity_id FROM maintenance_sessions WHERE state='OPEN'")
        }
        paid = set()
        for row in db.execute("SELECT identity_id,data_json FROM datadome_solver_jobs"):
            job = json.loads(row[1])
            if job.get("status") in {"PENDING", "SUBMITTING", "SUBMITTED", "APPLYING"}:
                paid.add(row[0])
        strategies = {
            r[0]: json.loads(r[1])
            for r in db.execute(
                "SELECT r.site_id,r.spec_json FROM site_strategy_heads h JOIN site_strategy_revisions r "
                "ON r.site_id=h.site_id AND r.revision=h.active_revision"
            )
        }
        rows = db.execute(
            "SELECT r.* FROM browser_identity_heads h JOIN browser_identity_revisions r "
            "ON r.identity_id=h.identity_id AND r.revision=h.active_revision"
        ).fetchall()
        by_id = {r["identity_id"]: r for r in rows}
        Path(receipt).parent.mkdir(parents=True, exist_ok=True)
        with Path(receipt).open("a", encoding="utf-8") as out:
            for scope in scopes:
                if not scope["scope_id"].startswith("publisher:"):
                    continue
                site = scope["owner_site_id"]
                auth = strategies.get(site, {}).get("auth", {})
                for identity_id in (
                    scope["primary_identity_id"],
                    scope.get("fallback_identity_id"),
                ):
                    if not identity_id or identity_id not in by_id:
                        continue
                    row = by_id[identity_id]
                    spec = json.loads(row["spec_json"])
                    profile_row = db.execute(
                        "SELECT data_json FROM browser_profiles WHERE profile_id=?",
                        (spec["profile_id"],),
                    ).fetchone()
                    profile = json.loads(profile_row[0]) if profile_row else {}
                    if (
                        site in protected
                        or identity_id in sessions | paid
                        or spec.get("credential_ref")
                        or profile.get("credential_ref")
                        or auth.get("requirement") == "required"
                        or auth.get("body_requirement") == "required"
                    ):
                        counts["preserved_protected_or_human"] += 1
                        continue
                    runtime = db.execute(
                        "SELECT data_json FROM site_runtime WHERE runtime_key=?",
                        ("scope:" + scope["scope_id"],),
                    ).fetchone()
                    capability = (
                        (
                            json.loads(runtime[0])
                            .get("combinations", {})
                            .get(identity_id, {})
                            .get("capabilities", {})
                            .get("BODY", {})
                        )
                        if runtime
                        else {}
                    )
                    proven = capability.get("result") in {"FULL", "SHORT_FULL"}
                    mode = (
                        "public_context" if site == "generic" and not proven else "public_identity"
                    )
                    recovery_row = db.execute(
                        "SELECT * FROM browser_recoveries WHERE identity_id=?", (identity_id,)
                    ).fetchone()
                    recovery = json.loads(recovery_row["data_json"]) if recovery_row else None
                    change = spec.get("execution_mode", "identity") != mode
                    park = (
                        recovery
                        and recovery.get("automation_active", True)
                        and recovery.get("phase") != "RESOLVED"
                    )
                    if not change and not park:
                        continue
                    counts[mode] += int(change)
                    counts["parked_recovery"] += int(bool(park))
                    out.write(
                        canonical(
                            {
                                "at": now,
                                "apply": apply,
                                "identity": spec,
                                "recovery": recovery,
                                "mode": mode,
                            }
                        )
                        + "\n"
                    )
                    out.flush()
                    if not apply:
                        continue
                    if change:
                        updated = {**spec, "execution_mode": mode, "revision": spec["revision"] + 1}
                        payload = canonical(updated)
                        db.execute(
                            "INSERT INTO browser_identity_revisions VALUES(?,?,?,?,?,?)",
                            (
                                identity_id,
                                updated["revision"],
                                payload,
                                hashlib.sha256(payload.encode()).hexdigest(),
                                now,
                                "lean-startup-20261029",
                            ),
                        )
                        db.execute(
                            "UPDATE browser_identity_heads SET active_revision=?,updated_at=? "
                            "WHERE identity_id=? AND active_revision=?",
                            (updated["revision"], now, identity_id, spec["revision"]),
                        )
                    if park:
                        recovery.update(
                            automation_active=False,
                            lease_owner=None,
                            lease_until=None,
                            generation=recovery["generation"] + 1,
                            updated_at=now,
                            last_error="demand_only_recovery",
                        )
                        db.execute(
                            "UPDATE browser_recoveries SET data_json=?,generation=?,updated_at=? "
                            "WHERE identity_id=? AND generation=?",
                            (
                                canonical(recovery),
                                recovery["generation"],
                                now,
                                identity_id,
                                recovery_row["generation"],
                            ),
                        )
                    db.commit()  # No large configuration transaction or file I/O under a writer.
    return dict(counts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(args.db, args.receipt, apply=args.apply), sort_keys=True))
