"""Explicit, audited ledger repairs; callers hold the stopped-worker writer lock."""

import json
from datetime import date, datetime
from typing import Any

from doxagent.persistent_runtime_v2.journal import encode

from .repository import ExecutionRepository
from .sessions import ET, Sessions


def set_session(
    repo: ExecutionRepository,
    day: str,
    *,
    open_at: str | None,
    close_at: str | None,
    closed: bool,
    reason: str,
) -> dict[str, Any]:
    selected = date.fromisoformat(day)
    if not reason.strip():
        raise ValueError("reason required")
    if closed:
        if open_at or close_at:
            raise ValueError("closed session cannot include hours")
        value: dict[str, Any] = {"is_session": False}
    else:
        if not open_at or not close_at:
            raise ValueError("open and close timestamps required")
        start, end = datetime.fromisoformat(open_at), datetime.fromisoformat(close_at)
        if (
            start.tzinfo is None
            or end.tzinfo is None
            or start >= end
            or start.astimezone(ET).date() != selected
            or end.astimezone(ET).date() != selected
        ):
            raise ValueError("invalid session timezone, date or bounds")
        value = {"is_session": True, "open_at": start.isoformat(), "close_at": end.isoformat()}
    value.update(reason=reason, updated_at=repo.journal.clock().isoformat())
    repo.journal.set("calendar_overrides", "XNYS:" + day, value)
    repo.journal.set("execution_calendar_audit", value["updated_at"] + ":" + day, value)
    return value


def replan_exit(repo: ExecutionRepository, lot_id: str, reason: str) -> dict[str, Any]:
    lot = repo.require("lots", lot_id)
    execution = repo.require("executions", lot_id)
    job = repo.require("jobs", lot_id + ":exit")
    now = repo.journal.clock()
    if (
        not reason.strip()
        or lot["remaining_qty"] <= 0
        or datetime.fromisoformat(lot["scheduled_exit"]) <= now
        or job["state"] in {"ACTIVE", "RECONCILE_REQUIRED", "DONE", "FAILED"}
        or repo.attempts(job["id"])
    ):
        raise ValueError("only unstarted future exits may be replanned")
    due = (
        Sessions(repo.journal)
        .exit_at(
            datetime.fromisoformat(lot["first_fill_at"]),
            repo.profile(execution["profile_revision"]).strategy.exit_offset_minutes,
        )
        .isoformat()
    )
    audit = {
        "previous_due_at": lot["scheduled_exit"],
        "due_at": due,
        "reason": reason,
        "at": now.isoformat(),
    }
    with repo.journal.transaction() as db:
        db.execute(
            "INSERT OR REPLACE INTO runtime_values VALUES('execution_exit_overrides',?,?)",
            (lot_id, encode(audit)),
        )
        db.execute(
            "INSERT OR REPLACE INTO runtime_values VALUES('execution_exit_replans',?,?)",
            (lot_id + ":" + now.isoformat(), encode(audit)),
        )
        lot["scheduled_exit"] = due
        job.update(due_at=due, state="READY")
        db.execute("UPDATE te_lots SET payload=? WHERE id=?", (encode(lot), lot_id))
        db.execute(
            "UPDATE te_jobs SET due_at=?,state='READY',payload=? WHERE id=?",
            (due, json.dumps(job), job["id"]),
        )
    return audit
