"""Small group-scoped management inbox consumed by the existing order writer."""

import json
from datetime import datetime

from doxagent.persistent_runtime_v2.journal import digest, encode

from . import groups
from .capital import get, put
from .exit_guard import NAMESPACE, locked

PENDING = "execution_management_pending"
RECEIPTS = "execution_management_receipts"


def submit(repo, request):
    if request.get("action") not in {"EARLY_EXIT", "RESUME_FAILED_EXIT"}:
        raise ValueError("INVALID_MANAGEMENT_ACTION")
    for key in ("request_id", "execution_id", "account", "actor", "reason"):
        if not isinstance(request.get(key), str) or not request[key].strip():
            raise ValueError("MANAGEMENT_FIELD_REQUIRED:" + key)
    if len(request["request_id"]) > 120:
        raise ValueError("INVALID_REQUEST_ID")
    identity = request["request_id"]
    with repo.transaction() as db:
        old = get(db, RECEIPTS, identity) or get(db, PENDING, identity)
        signature = digest(request)
        if old:
            if old["signature"] != signature:
                raise ValueError("MANAGEMENT_IDEMPOTENCY_CONFLICT")
            return old
        group = db.execute(
            "SELECT account FROM te_executions WHERE id=?", (request["execution_id"],)
        ).fetchone()
        if not group or group[0] != request["account"]:
            raise ValueError("MANAGEMENT_ACCOUNT_OR_GROUP_MISMATCH")
        value = dict(request=request, signature=signature, status="PENDING")
        put(db, PENDING, identity, value)
        return value


def view(repo, identity, request_id=None):
    with repo.transaction(write=False) as db:
        group = repo.require("executions", identity)
        jobs = [
            json.loads(r[0])
            for r in db.execute("SELECT payload FROM te_jobs WHERE execution_id=?", (identity,))
        ]
        lots = [
            json.loads(r[0])
            for r in db.execute("SELECT payload FROM te_lots WHERE account=?", (group["account"],))
            if json.loads(r[0]).get("group_id", json.loads(r[0])["id"]) == identity
        ]
        receipt = None
        if request_id:
            receipt = get(db, RECEIPTS, request_id) or get(db, PENDING, request_id)
            if (
                receipt
                and receipt.get("execution_id", receipt.get("request", {}).get("execution_id"))
                != identity
            ):
                raise ValueError("MANAGEMENT_REQUEST_GROUP_MISMATCH")
        return dict(
            request_receipt=receipt,
            execution_id=identity,
            account=group["account"],
            group_state=group.get("group_state"),
            lots={lot_value["id"]: lot_value["remaining_qty"] for lot_value in lots},
            lot_details=lots,
            unsettled_attempts=[
                json.loads(row[0])
                for row in db.execute(
                    "SELECT a.payload FROM te_attempts a JOIN te_jobs j ON j.id=a.job_id "
                    "WHERE j.execution_id=? AND a.state NOT IN ('SETTLED','NOT_SENT')",
                    (identity,),
                )
            ],
            locks={
                j["id"]: locked(db, j["id"])
                for j in jobs
                if j["leg"] == "EXIT" and locked(db, j["id"])
            },
            jobs=[
                {
                    k: j.get(k)
                    for k in ("id", "leg", "state", "reason", "exit_reason", "exit_requested_at")
                }
                for j in jobs
            ],
        )


def apply(repo, pending, snapshot=None):
    request = pending["request"]
    identity = request["execution_id"]
    rid = request["request_id"]
    now = repo.journal.clock()
    with repo.transaction() as db:
        old = get(db, RECEIPTS, rid)
        if old:
            return old
        group_row = db.execute(
            "SELECT payload FROM te_executions WHERE id=?", (identity,)
        ).fetchone()
        group = json.loads(group_row[0]) if group_row else None
        if not group or group["account"] != request["account"]:
            raise ValueError("MANAGEMENT_ACCOUNT_OR_GROUP_MISMATCH")
        jobs = [
            json.loads(r[0])
            for r in db.execute("SELECT payload FROM te_jobs WHERE execution_id=?", (identity,))
        ]
        exits = [j for j in jobs if j["leg"] == "EXIT"]
        locks = {j["id"]: locked(db, j["id"]) for j in exits if locked(db, j["id"])}
        if any(j["state"] == "FAILED" for j in exits) and not locks:
            raise ValueError("FAILED_EXIT_LOCK_MISSING")
        lots = {}
        for leg in group.get("legs", []):
            row = db.execute("SELECT payload FROM te_lots WHERE id=?", (leg["id"],)).fetchone()
            if row:
                lots[leg["id"]] = json.loads(row[0])
        if request["action"] == "EARLY_EXIT" and locks:
            raise ValueError("FAILED_EXIT_REQUIRES_REVIEW")
        if request["action"] == "RESUME_FAILED_EXIT":
            evidence = request.get("verification") or {}
            if not locks:
                raise ValueError("NO_FAILED_EXIT_TO_RESUME")
            if not snapshot or not snapshot.get("complete") or not snapshot.get("history_complete"):
                raise ValueError("FAILED_EXIT_BROKER_HISTORY_REQUIRED")
            age = (now - datetime.fromisoformat(evidence["verified_at"])).total_seconds()
            if (
                not 0 <= age <= 300
                or evidence.get("account") != group["account"]
                or evidence.get("execution_id") != identity
            ):
                raise ValueError("FAILED_EXIT_VERIFICATION_EXPIRED_OR_WRONG_SCOPE")
            if evidence.get("failure_versions") != {
                k: v["version"] for k, v in locks.items()
            } or evidence.get("lot_quantities") != {
                k: lot_value["remaining_qty"] for k, lot_value in lots.items()
            }:
                raise ValueError("FAILED_EXIT_VERIFICATION_VERSION_CHANGED")
            if (
                evidence.get("disposition") not in {"REMAINING_EXIT_CONFIRMED", "MANUALLY_CLOSED"}
                or not all(
                    evidence.get(k) is True
                    for k in (
                        "broker_history_checked",
                        "strategy_ledger_checked",
                        "user_manual_operations_checked",
                    )
                )
                or not evidence.get("evidence_refs")
            ):
                raise ValueError("FAILED_EXIT_MANUAL_OPERATION_REVIEW_REQUIRED")
            for lot in lots.values():
                cid = str(lot["con_id"])
                actual = snapshot["positions"].get(
                    cid, snapshot["positions"].get(lot["con_id"], "0")
                )
                if str(actual) != str(evidence.get("broker_positions", {}).get(cid)) or lot.get(
                    "unattributed_qty"
                ):
                    raise ValueError("FAILED_EXIT_POSITION_EVIDENCE_CHANGED")
            # Related shared contracts count, not only the target group's attempts.
            conids = {lot_value["con_id"] for lot_value in lots.values()}
            for r in db.execute(
                "SELECT payload FROM te_attempts WHERE account=?", (group["account"],)
            ):
                a = json.loads(r[0])
                if a.get("contract", {}).get("con_id") in conids and a["state"] not in {
                    "SETTLED",
                    "NOT_SENT",
                }:
                    raise ValueError("FAILED_EXIT_ORDER_UNRESOLVED")
            for r in db.execute(
                "SELECT payload FROM runtime_values WHERE namespace='execution_event_gaps'"
            ):
                g = json.loads(r[0])
                if g.get("account") == group["account"] and (
                    g.get("con_id") in conids or g.get("con_id") is None
                ):
                    raise ValueError("FAILED_EXIT_ATTRIBUTION_GAP")
            manual_closed = evidence["disposition"] == "MANUALLY_CLOSED"
            remaining = any(lot_value["remaining_qty"] for lot_value in lots.values())
            if manual_closed and remaining:
                raise ValueError("VERIFY_AND_ADJUST_MANUAL_CLOSE_BEFORE_CONFIRMING")
            if not manual_closed and not remaining:
                raise ValueError("NO_REMAINING_POSITION_DO_NOT_RESUME")
            # Strict recovery verifies strategy ledger separately from personal P.
            from decimal import Decimal

            from .strategy import decimal

            for cid in conids:
                effective = repo._effective_fills(db, group["account"], cid)
                net = sum(
                    (decimal(f["quantity"]) * (1 if f["side"] == "BUY" else -1) for f in effective),
                    Decimal(0),
                )
                for row in db.execute(
                    "SELECT payload FROM te_lot_adjustments WHERE account=? AND con_id=?",
                    (group["account"], cid),
                ):
                    adjustment = json.loads(row[0])
                    net += decimal(adjustment["quantity_delta"]) * adjustment["sign"]
                related_lots = [
                    json.loads(row[0])
                    for row in db.execute(
                        "SELECT payload FROM te_lots WHERE account=? AND con_id=?",
                        (group["account"], cid),
                    )
                ]
                if any(
                    lot_value.get("unattributed_qty") for lot_value in related_lots
                ) or net != sum(
                    (
                        decimal(lot_value["remaining_qty"]) * lot_value["sign"]
                        for lot_value in related_lots
                    ),
                    Decimal(0),
                ):
                    raise ValueError("FAILED_EXIT_STRATEGY_LEDGER_INCONSISTENT")
            for j in exits:
                if j["id"] not in locks:
                    continue
                if manual_closed:
                    # Keep the latch permanently: a later rebuild must not turn
                    # an explicitly manually retired position into a new trade.
                    put(
                        db,
                        NAMESPACE,
                        j["id"],
                        dict(locks[j["id"]], resolution="MANUALLY_CLOSED", review_request_id=rid),
                    )
                    j.update(state="DONE", reason="MANUALLY_CLOSED_BROKER_VERIFIED")
                    groups.save_job(db, j)
                    continue
                db.execute(
                    "DELETE FROM runtime_values WHERE namespace=? AND key=?", (NAMESPACE, j["id"])
                )
                lot = lots.get(j.get("leg_id", identity))
                j.update(
                    state=("WAIT_MAIN" if j.get("role") == "HEDGE" else "READY")
                    if lot and lot["remaining_qty"]
                    else "DONE",
                    failures=0,
                    generation=j.get("generation", 0) + 1,
                    recovery_request_id=rid,
                    error=None,
                    due_at=now.isoformat(),
                )
                groups.save_job(db, j)
        outstanding = db.execute(
            "SELECT 1 FROM te_attempts a JOIN te_jobs j ON j.id=a.job_id "
            "WHERE j.execution_id=? AND a.state NOT IN ('SETTLED','NOT_SENT') LIMIT 1",
            (identity,),
        ).fetchone()
        finished = (
            not any(lot_value["remaining_qty"] for lot_value in lots.values())
            and not outstanding
            and all(j["state"] in {"DONE", "FAILED"} for j in jobs if j["leg"] == "ENTRY")
        )
        if not finished:
            reason = (
                "MANUAL_EARLY_EXIT"
                if request["action"] == "EARLY_EXIT"
                else "VERIFIED_FAILED_EXIT_RESUME"
            )
            from doxagent.exit_strategy.repository import ExitRepository

            managed = db.execute(
                "SELECT payload FROM ts_positions WHERE id=?", (identity,)
            ).fetchone()
            if managed:
                managed_value = json.loads(managed[0])
                allowed_legs = {leg["id"] for leg in group.get("legs", [])}
                if (
                    managed_value["account"] != group["account"]
                    or not {leg["lot_id"] for leg in managed_value["legs"]} <= allowed_legs
                ):
                    raise ValueError("MANAGEMENT_POSITION_GROUP_MISMATCH")
                ExitRepository(repo.journal)._request_in(
                    db,
                    identity,
                    reason,
                    now.isoformat(),
                    evidence=dict(request_id=rid, actor=request["actor"]),
                )
            groups.request_exit(db, identity, now.isoformat(), reason)
            # Existing exit-request idempotency may return early; make newly approved
            # remaining jobs due through the same group dependency calculation.
        groups.advance(repo, identity, db=db)
        result = dict(
            signature=pending["signature"],
            status="APPLIED",
            action=request["action"],
            execution_id=identity,
            already_closed=finished,
            at=now.isoformat(),
            actor=request["actor"],
            reason=request["reason"],
            released_locks=locks,
        )
        put(db, RECEIPTS, rid, result)
        db.execute("DELETE FROM runtime_values WHERE namespace=? AND key=?", (PENDING, rid))
        db.execute(
            "INSERT INTO te_events(account,kind,payload,created_at) VALUES(?,?,?,?)",
            (
                group["account"],
                "execution_management_applied",
                encode(dict(request_id=rid, **result)),
                now.isoformat(),
            ),
        )
        return result


async def process(executor):
    repo = executor.repository
    with repo.transaction(write=False) as db:
        pending = [
            json.loads(r[0])
            for r in db.execute(
                "SELECT payload FROM runtime_values WHERE namespace=? ORDER BY key LIMIT 4",
                (PENDING,),
            )
        ]
    for item in pending:
        r = item["request"]
        try:
            snapshot = None
            if r["action"] == "RESUME_FAILED_EXIT":
                group = repo.require("executions", r["execution_id"])
                snapshot = await executor._sync(
                    executor.broker(group["profile_revision"]), group["account"], force=True
                )
            apply(repo, item, snapshot)
        except Exception as exc:
            from .recovery import storage_busy

            if storage_busy(exc):
                continue
            with repo.transaction() as db:
                put(
                    db,
                    RECEIPTS,
                    r["request_id"],
                    dict(
                        signature=item["signature"],
                        status="REJECTED",
                        error=str(exc),
                        execution_id=r["execution_id"],
                    ),
                )
                db.execute(
                    "DELETE FROM runtime_values WHERE namespace=? AND key=?",
                    (PENDING, r["request_id"]),
                )
