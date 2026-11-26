"""Small recovery policy for the existing maintenance task ledger."""

from __future__ import annotations

import json

COMPATIBILITY_REVISION = "o2-frozen-view-export-20261107"


def transient_failure(code: str, detail: str) -> bool:
    """Retry transport/storage faults, not arbitrary ValueError or bad output."""
    if any(marker in detail.lower() for marker in ("quarantined", "cleanup failed", "uncertain")):
        return False  # Receipt/slot reconciliation owns these; never mint blind dispatches.
    return code in {
        "CODEX_START_TIMEOUT",
        "CODEX_TURN_TIMEOUT",
        "TimeoutError",
        "WORKER_INFRA_RECOVERY_EXHAUSTED",
        "SERVICE_UNAVAILABLE",
    } or any(
        marker in detail.lower()
        for marker in (
            "database is locked",
            "database table is locked",
            "sqlite_busy",
            "start_thread exceeded its timeout budget",
            "owned sdk process exited",
            "required mcp servers failed to initialize",
        )
    )


def _covered(journal, before, after, *, o2_only=False):
    # A declared compensation is acknowledged only after actual record coverage.
    specs = (
        ("candidates", "runtime_v2_candidates", "candidate_identity"),
        ("trades", "runtime_v2_trade_records", "trade_record_id"),
        ("badcases", "runtime_v2_badcases", "badcase_id"),
        ("gaps", "runtime_v2_w3_coverage_gaps", "coverage_gap_id"),
    )

    def identity(kind, item):
        return (
            f"runtime-v2-msg:{item['source_message_id']}:{item['candidate_index']}"
            if kind == "candidates"
            else item[dict((s[0], s[2]) for s in specs)[kind]]
        )

    with journal.transaction(write=False, expand=False) as db:
        for kind, table, key in specs:
            if o2_only and kind != 'candidates':
                continue  # The successor explicitly deferred these O3 obligations.
            covered = {identity(kind, item) for item in after.get(kind, [])}
            for item in before.get(kind, []):
                value = identity(kind, item)
                if value in covered:
                    continue
                column = key if kind == "candidates" else f"json_extract(payload_json,'$.{key}')"
                row = db.execute(
                    f"SELECT daily_status FROM {table} WHERE {column}=?", (value,)
                ).fetchone()
                if row and row[0] == "PROCESSED":
                    continue
                if (
                    kind == "candidates"
                    and db.execute(
                        "SELECT 1 FROM runtime_v2_admission_exclusions WHERE source_message_id=? "
                        "UNION ALL SELECT 1 FROM runtime_values "
                        "WHERE namespace='invalid_admissions' "
                        "AND key=? AND payload!='false' LIMIT 1",
                        (item["source_message_id"], item["source_message_id"]),
                    ).fetchone()
                ):
                    continue
                return False
    return True


def acknowledge_compensations(journal, ticker):
    cursors = getattr(journal, '_compensation_cursors', {})
    with journal.transaction(write=False, expand=False) as db:
        if not db.execute("SELECT 1 FROM runtime_tasks WHERE ticker=? AND kind='MAINTENANCE' AND status='FAILED' "
                          "AND json_extract(receipt,'$.compensated_by') IS NULL LIMIT 1", (ticker,)).fetchone():
            return
        rows = db.execute(
            "SELECT id,json_extract(inputs,'$.compensates') AS compensates FROM runtime_tasks "
            "WHERE ticker=? AND kind='MAINTENANCE' AND status='SUCCEEDED' AND id>? "
            "ORDER BY id LIMIT 32", (ticker,cursors.get(ticker,'')),
        ).fetchall()
        cursors[ticker] = rows[-1]['id'] if len(rows)==32 else ''
        journal._compensation_cursors = cursors
        identities = sorted({identity for row in rows for identity in json.loads(row['compensates'] or '[]')})
        if not identities:return
        pending = {r[0] for r in db.execute(
            "SELECT id FROM runtime_tasks WHERE id IN (SELECT value FROM json_each(?)) "
            "AND status='FAILED' AND json_extract(receipt,'$.compensated_by') IS NULL", (json.dumps(identities),),
        )}
    for row in rows:
        ids = pending.intersection(json.loads(row["compensates"] or '[]'))
        if not ids:
            continue
        successor = journal.get_task(row["id"])
        for identity in ids:
            prior = journal.get_task(identity)
            before, after = prior["receipt"].get("frame"), successor["receipt"].get("frame")
            if before is None or after is None or not _covered(journal, before, after, o2_only=successor['receipt'].get('o3_outcome')=='SKIPPED_BY_CONTROL'):
                continue
            receipt = {**prior["receipt"], "compensated_by": successor["id"]}
            if successor['receipt'].get('o3_outcome')=='SKIPPED_BY_CONTROL':
                receipt['o3_obligations_deferred'] = True
            encoded = journal.content.encode(receipt)
            with journal.transaction(operation="maintenance.compensated", expand=False) as db:
                changed = db.execute(
                    "UPDATE runtime_tasks SET receipt=? WHERE id=? AND status='FAILED'",
                    (encoded, identity),
                ).rowcount
                if changed:
                    db.execute("UPDATE runtime_gaps SET closed=1 WHERE task_id=?", (identity,))
            pending.discard(identity)


def recover_failed(journal, ticker, *, limit=20, after="", running_mode=None):
    acknowledge_compensations(journal, ticker)
    with journal.transaction(write=False, expand=False) as db:
        rows = db.execute(
            "SELECT t.id,json_extract(t.receipt,'$.last_phase_failure.code') AS code,"
            "json_extract(t.receipt,'$.compatibility_recovery_revision') AS revision,"
            "coalesce(g.detail,'') AS detail FROM runtime_tasks t "
            "LEFT JOIN runtime_gaps g ON g.task_id=t.id AND g.id=t.id "
            "WHERE t.ticker=? AND t.kind IN ('MAINTENANCE','SWEEP') AND t.status='FAILED' "
            "AND json_extract(t.receipt,'$.maintenance_retired') IS NULL "
            "AND json_extract(t.receipt,'$.compensated_by') IS NULL "
            "AND t.due_at<=? AND t.id>? ORDER BY t.id LIMIT ?",
            (ticker, journal.clock().isoformat(), after, limit),
        ).fetchall()
    for row in rows:
        transient = transient_failure(row["code"] or "", row["detail"])
        compatibility = row["revision"] != COMPATIBILITY_REVISION and (
            " is not retained for " in row["detail"]
            or "resume state does not match the frozen maintenance input" in row["detail"]
        )
        if not transient and not compatibility:
            continue
        task = journal.get_task(row["id"])
        base = task.get("receipt", {}).get("frame", {}).get("base")
        if task["kind"] == "SWEEP":
            base = (journal.get("sweep_bundle", task["id"]) or {}).get("inputs")
        frozen_mode = base.get("running_mode", "FULL") if base else None
        if running_mode is not None and frozen_mode is not None and frozen_mode != running_mode:
            continue
        receipt = dict(task["receipt"])
        if compatibility:
            receipt["compatibility_recovery_revision"] = COMPATIBILITY_REVISION
            failure = receipt.get("last_phase_failure", {})
            counters = dict(receipt.get("phase_failures", {}))
            counters.pop(f"{failure.get('scope')}:{failure.get('phase')}", None)
            receipt["phase_failures"] = counters
        receipt["resume_reason"] = "automatic maintenance recovery"
        encoded = journal.content.encode(receipt)
        with journal.transaction(operation="maintenance.recover", expand=False) as db:
            db.execute(
                "UPDATE runtime_tasks SET status='PENDING',generation=generation+1,"
                "owner=NULL,lease_until=NULL,receipt=?,due_at=?,updated_at=? "
                "WHERE id=? AND status='FAILED'",
                (encoded, journal.clock().isoformat(), journal.clock().isoformat(), task["id"]),
            )
    return rows[-1]["id"] if len(rows) == limit else ""


def health(journal):
    with journal.transaction(write=False, expand=False) as db:
        failed = db.execute(
            "SELECT count(*) FROM runtime_tasks WHERE kind='MAINTENANCE' AND status='FAILED' "
            "AND json_extract(receipt,'$.maintenance_retired') IS NULL "
            "AND json_extract(receipt,'$.compensated_by') IS NULL",
        ).fetchone()[0]
        oldest = db.execute(
            "SELECT id,due_at FROM runtime_tasks WHERE kind='MAINTENANCE' "
            "AND status IN ('PENDING','RUNNING') ORDER BY due_at,id LIMIT 1",
        ).fetchone()
    return dict(
        uncompensated_maintenance_failures=failed,
        oldest_maintenance_task=oldest[0] if oldest else None,
        oldest_maintenance_due=oldest[1] if oldest else None,
    )
