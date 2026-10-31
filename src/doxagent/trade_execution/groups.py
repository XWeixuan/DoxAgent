"""MAIN then HEDGE dependencies inside the existing executor transaction."""

from __future__ import annotations

import json

from doxagent.persistent_runtime_v2.journal import encode

from .planning import TradingConfig, assets
from .strategy import decimal

TERMINAL = {"DONE", "FAILED"}


def initialize_execution(value, profile):
    config = TradingConfig.model_validate(
        value["intent"].get("trading_config", {"environment": profile.environment})
    )
    if config.environment != profile.environment:
        raise ValueError("ACCOUNT_ENVIRONMENT_MISMATCH")
    value.update(
        ownership_version=2,
        group_state="OPENING",
        trading_config=config.model_dump(mode="json"),
        account_type=profile.account_type,
    )
    legs = assets(
        value["ticker"], value["intent"]["trade"]["decision"], profile.account_type, config
    )
    for leg in legs:
        leg["id"] = value["id"] if leg["role"] == "MAIN" else value["id"] + ":hedge"
    value["legs"] = legs
    return value


def job_leg(execution, job):
    return next(
        (
            leg
            for leg in execution.get("legs", [])
            if leg["id"] == job.get("leg_id", execution["id"])
        ),
        None,
    )


def save_job(db, job):
    db.execute(
        "UPDATE te_jobs SET state=?,due_at=?,payload=? WHERE id=?",
        (job["state"], job["due_at"], encode(job), job["id"]),
    )


def request_exit(db, identity, now, reason):
    row = db.execute("SELECT payload FROM te_executions WHERE id=?", (identity,)).fetchone()
    if not row:
        return
    execution = json.loads(row[0])
    if execution.get("ownership_version") != 2:
        return
    execution.update(
        exit_requested_at=execution.get("exit_requested_at", now),
        exit_reason=execution.get("exit_reason", reason),
        group_state="CLOSING",
    )
    db.execute("UPDATE te_executions SET payload=? WHERE id=?", (encode(execution), identity))
    for (payload,) in db.execute(
        "SELECT payload FROM te_jobs WHERE execution_id=?", (identity,)
    ).fetchall():
        job = json.loads(payload)
        if job["leg"] == "ENTRY" and job["state"] not in TERMINAL:
            job.update(exit_requested_at=now, due_at=now)
            if job["state"] == "WAIT_MAIN":
                job.update(state="DONE", result="FAILED", reason="POSITION_EXIT_REQUESTED")
            save_job(db, job)
        elif job["leg"] == "EXIT" and job["state"] != "DONE":
            job.update(exit_requested_at=now,exit_reason=reason)
            if job.get("role","MAIN") == "MAIN":
                job["due_at"] = min(now,job["due_at"])
            save_job(db,job)


def main_notional(repo, db, identity, account):
    ids = {
        r[0]
        for r in db.execute("SELECT id FROM te_attempts WHERE job_id=?", (identity + ":entry",))
    }
    return sum(
        (
            decimal(f["quantity"]) * decimal(f["price"])
            for f in repo._effective_fills(db, account)
            if f["attempt_id"] in ids
        ),
        decimal(0),
    )


def advance(repo, identity, *, db=None):
    if db is None:
        with repo.transaction() as connection:
            return advance(repo, identity, db=connection)
    row = db.execute("SELECT payload FROM te_executions WHERE id=?", (identity,)).fetchone()
    if not row:
        return
    execution = json.loads(row[0])
    if execution.get("ownership_version") != 2:
        return
    jobs = {
        r[0]: json.loads(r[1])
        for r in db.execute("SELECT id,payload FROM te_jobs WHERE execution_id=?", (identity,))
    }
    if execution.get("budget_usd"):
        base = execution.setdefault("grant_budget_usd", execution["budget_usd"])
        plan = execution["plan"]
        fills = repo._effective_fills(db, execution["account"])
        spent = {"MAIN": decimal(0), "HEDGE": decimal(0)}
        attempts = {
            r[0]: r[1]
            for r in db.execute(
                "SELECT a.id,a.job_id FROM te_attempts a JOIN te_jobs j ON j.id=a.job_id WHERE "
                "j.execution_id=?",
                (identity,),
            )
        }
        for fill in fills:
            job = jobs.get(attempts.get(fill["attempt_id"]))
            if job and job["leg"] == "ENTRY":
                spent[job.get("role", "MAIN")] += decimal(fill["quantity"]) * decimal(fill["price"])
        excess = sum(
            (max(decimal(0), spent[role] - decimal(plan[role.lower() + "_cap"])) for role in spent),
            decimal(0),
        )
        execution["budget_usd"] = str(decimal(base) + excess)
    main = jobs.get(identity + ":entry")
    if not main:
        return
    now = repo.journal.clock().isoformat()
    main_row = db.execute("SELECT payload FROM te_lots WHERE id=?", (identity,)).fetchone()
    main_lot = json.loads(main_row[0]) if main_row else None
    if main_lot and main_lot["remaining_qty"] and main_lot["scheduled_exit"] <= now:
        request_exit(db, identity, now, "SESSION_CUTOFF")
        execution.update(
            exit_requested_at=execution.get("exit_requested_at", now),
            exit_reason=execution.get("exit_reason", "SESSION_CUTOFF"),
            group_state="CLOSING",
        )
    hedge_entry = jobs.get(identity + ":hedge:entry")
    if hedge_entry and hedge_entry["state"] not in TERMINAL and main["state"] in TERMINAL:
        actual = main_notional(repo, db, identity, execution["account"])
        if actual and not execution.get("exit_requested_at"):
            changed = decimal(hedge_entry.get("main_notional_usd", 0)) != actual
            if changed:
                hedge_entry.pop("target_quantity", None)
                hedge_entry.update(main_notional_usd=str(actual), due_at=now)
            if hedge_entry["state"] == "WAIT_MAIN":
                hedge_entry.update(state="READY", due_at=now)
        else:
            reason = "MAIN_NOT_FILLED" if not actual else "POSITION_EXIT_REQUESTED"
            hedge_entry.update(entry_abandoned_at=now, entry_abandon_reason=reason, due_at=now)
            busy = db.execute(
                "SELECT 1 FROM te_attempts WHERE job_id=? AND state NOT IN ('SETTLED','NOT_SENT') LIMIT 1",
                (hedge_entry["id"],),
            ).fetchone()
            if not busy:
                hedge_entry.update(state="DONE", result="FAILED", reason=reason)
        save_job(db, hedge_entry)
    if execution.get("exit_requested_at"):
        for job in jobs.values():
            if job["leg"] != "EXIT" or job["state"] in TERMINAL:
                continue
            if job.get("role", "MAIN") == "MAIN":
                job.update(due_at=min(now, job["due_at"]))
                save_job(db, job)
    unsettled = [
        json.loads(r[0])
        for r in db.execute(
            "SELECT a.payload FROM te_attempts a JOIN te_jobs j ON j.id=a.job_id WHERE "
            "j.execution_id=? AND a.state NOT IN ('SETTLED','NOT_SENT')",
            (identity,),
        )
    ]
    main_busy = any(jobs[a["job_id"]].get("role", "MAIN") == "MAIN" for a in unsettled)
    main_flat = not main_lot or main_lot["remaining_qty"] == 0
    hedge_exit = jobs.get(identity + ":hedge:exit")
    if (
        hedge_exit
        and not main_flat
        and hedge_exit["state"] not in TERMINAL | {"ACTIVE", "RECONCILE_REQUIRED"}
    ):
        hedge_exit["state"] = "WAIT_MAIN"
        save_job(db, hedge_exit)
    if main_flat and not main_busy and main["state"] in TERMINAL:
        hedge_exit = jobs.get(identity + ":hedge:exit")
        if hedge_exit and hedge_exit["state"] not in TERMINAL:
            hedge_exit.update(
                state="READY" if hedge_exit["state"] == "WAIT_MAIN" else hedge_exit["state"],
                due_at=min(now, hedge_exit["due_at"]),
                exit_requested_at=execution.get("exit_requested_at",now),
                exit_reason=execution.get("exit_reason","MAIN_CLOSED"),
            )
            save_job(db, hedge_exit)
    leg_ids = {leg["id"] for leg in execution.get("legs", [])}
    lots = [
        json.loads(r[0])
        for r in db.execute("SELECT payload FROM te_lots WHERE account=?", (execution["account"],))
        if json.loads(r[0])["id"] in leg_ids
    ]
    execution["leg_quantities"] = {
        lot.get("role", "MAIN"): str(lot["remaining_qty"] * lot["sign"]) for lot in lots
    }
    entries_done = all(j["state"] in TERMINAL for j in jobs.values() if j["leg"] == "ENTRY")
    if (
        entries_done
        and not unsettled
        and not any(lot["remaining_qty"] or lot.get("unattributed_qty") for lot in lots)
    ):
        execution["group_state"] = "CLOSED"
    elif execution.get("group_state") == "CLOSED":
        execution.update(
            group_state="CLOSING", exit_requested_at=now, exit_reason="LATE_FILL_RECONCILED"
        )
        request_exit(db, identity, now, "LATE_FILL_RECONCILED")
    elif entries_done and not execution.get("exit_requested_at"):
        execution["group_state"] = "OPEN"
    if any(lot.get("unattributed_qty") for lot in lots):
        execution["group_state"] = "EXIT_BLOCKED"
    db.execute("UPDATE te_executions SET payload=? WHERE id=?", (encode(execution), identity))
    from .capital import CapitalLedger

    CapitalLedger(repo).release_slots(db, execution["account"])


def entry_block_reason(execution, job, now, configuration=None):
    if execution.get("exit_requested_at"):
        return "POSITION_EXIT_REQUESTED"
    config = configuration if configuration is not None else execution.get("trading_config") or {}
    from doxagent.semantic_clock import semantic_day

    if semantic_day(now).isoformat() in config.get("banned_semantic_dates", []):
        return "BANNED_TRADE_DAY"
    return None
