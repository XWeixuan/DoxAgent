"""Persistent EXIT failure latch, independent of mutable lot/job projections."""

import json

from .capital import get

NAMESPACE = "execution_exit_locks"


def locked(db, job_id):
    return get(db, NAMESPACE, job_id)


def initialize(db):
    # Covers raw UPDATEs (lot rebuilds included), not just repository.finish.
    for operation in ("INSERT", "UPDATE"):
        db.execute(f"""CREATE TRIGGER IF NOT EXISTS te_exit_failure_{operation.lower()}
        AFTER {operation} ON te_jobs WHEN NEW.leg='EXIT' AND NEW.state='FAILED'
        BEGIN INSERT OR IGNORE INTO runtime_values(namespace,key,payload)
        VALUES('{NAMESPACE}',NEW.id,json_object('job_id',NEW.id,
        'version',lower(hex(randomblob(16))), 'failed_at',strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        'reason',json_extract(NEW.payload,'$.reason'))); END""")
    db.execute(f"""INSERT OR IGNORE INTO runtime_values(namespace,key,payload)
        SELECT '{NAMESPACE}',id,json_object('job_id',id,'version',lower(hex(randomblob(16))),
        'failed_at',strftime('%Y-%m-%dT%H:%M:%fZ','now'),'reason',json_extract(payload,'$.reason'))
        FROM te_jobs WHERE leg='EXIT' AND state='FAILED'""")


def summary(db, identity):
    result = []
    for row in db.execute(
        "SELECT payload FROM te_jobs WHERE execution_id=? AND leg='EXIT'", (identity,)
    ):
        job = json.loads(row[0])
        latch = locked(db, job["id"])
        lot = db.execute(
            "SELECT payload FROM te_lots WHERE id=?", (job.get("leg_id", identity),)
        ).fetchone()
        pending = db.execute(
            "SELECT 1 FROM te_attempts WHERE job_id=? "
            "AND state NOT IN ('SETTLED','NOT_SENT') LIMIT 1",
            (job["id"],),
        ).fetchone()
        remaining = str(json.loads(lot[0])["remaining_qty"]) if lot else "0"
        resolved = bool(
            latch
            and latch.get("resolution") == "MANUALLY_CLOSED"
            and remaining == "0"
            and not pending
        )
        result.append(
            dict(
                job_id=job["id"],
                role=job.get("role", "MAIN"),
                state="DONE" if resolved else "FAILED" if latch else job["state"],
                reason=job.get("reason")
                if resolved
                else (latch or {}).get("reason") or job.get("reason") or job.get("error"),
                remaining_quantity=str(json.loads(lot[0])["remaining_qty"]) if lot else "0",
                requires_manual_review=not resolved and bool(latch or job["state"] == "FAILED"),
                unresolved_order=bool(pending),
            )
        )
    return result
