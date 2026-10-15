"""Transient Policy WATCH routing evidence, using the existing Runtime journal."""

from __future__ import annotations

from datetime import datetime, timedelta

from doxagent.semantic_clock import boundary

from .journal import encode
from .policy_consumption import get_value, put_value
from .w2_alignment import related_ids


def watch_snapshot(db, ticker, now):
    from .message_pin import active_states

    rows = active_states(db, "policy_watch", ticker, now)
    return dict(limit=2, used=len(rows), items=[{
        k: r[k] for k in ("policy_id", "reason", "start_semantic_day", "expires_at")
    } for r in rows])


def apply_v4_actions(db, *, case, result, frozen, now):
    """WATCH is routing state, not an unconsumed trade authorization."""
    from .message_pin import active_states

    known = {p["policy_id"] for p in case.frozen_inputs["projection"]["policies"]}
    related = set(case.w2_final.policy_ids if case.w2_final.policy_hit else [])
    related.update(related_ids(case.w2_final))
    related.update(result.policy.policy_ids)
    visible = {r["policy_id"]: r for r in frozen.get("watch_rows", [])}
    cancelled = {a.policy_id for a in result.watch_actions if a.action == "CANCEL"}
    actions = {a.policy_id: a for a in result.watch_actions if a.action == "SET"}
    actions.update({a.policy_id: a for a in result.watch_actions if a.action == "CANCEL"})
    outcomes = []
    ordered = [a for a in actions.values() if a.action == "CANCEL"] + [
        a for a in actions.values() if a.action == "SET"
    ]
    for action in ordered:
        pid = action.policy_id
        key = encode([case.ticker, pid])
        current = get_value(db, "policy_watch", key)
        active = current and datetime.fromisoformat(current["expires_at"]) > now
        status = "APPLIED"
        if action.action == "CANCEL":
            expected = visible.get(pid)
            if active and expected and current.get("watch_id", current["source_case_id"]) == (
                expected.get("watch_id", expected["source_case_id"])
            ):
                db.execute("DELETE FROM runtime_values WHERE namespace='policy_watch' AND key=?",
                           (key,))
            else:
                status = "STALE_OR_NOT_VISIBLE"
        elif pid not in known or pid not in related:
            status = "OUTSIDE_POLICY_CONTEXT"
        elif pid in cancelled:
            status = "CANCEL_WINS"
        elif active:
            status = "ALREADY_ACTIVE_NO_EXTENSION"
        elif boundary(case.trading_date + timedelta(days=2)) <= now:
            status = "SOURCE_WINDOW_EXPIRED"
        elif len(active_states(db, "policy_watch", case.ticker, now)) >= 2:
            status = "CAPACITY_FULL"
        else:
            from .journal import digest

            put_value(db, "policy_watch", key, dict(
                ticker=case.ticker, policy_id=pid, source_case_id=case.case_id,
                watch_id="watch_" + digest([case.case_id, pid])[:24], reason=action.reason,
                start_semantic_day=case.trading_date.isoformat(),
                expires_at=boundary(case.trading_date + timedelta(days=2)).isoformat(),
                retain_after_ready=True,
            ))
        outcomes.append(dict(policy_id=pid, action=action.action, status=status))
    return outcomes


def freeze_followup(db, case, now):
    existing = case.frozen_inputs.get("policy_watch_followup")
    if existing is not None:
        return existing
    w2 = case.w2_final
    ids = set(w2.policy_ids if w2 and w2.policy_hit else [])
    if w2:
        ids.update(related_ids(w2))
    rows = []
    for pid in sorted(ids):
        value = get_value(db, "policy_watch", encode([case.ticker, pid]))
        if value and datetime.fromisoformat(value["expires_at"]) > now:
            rows.append(value)
    case.frozen_inputs["policy_watch_followup"] = rows
    return rows


def apply_actions(db, *, case, result, now):
    prior = get_value(db, "policy_watch_applied", case.case_id)
    if prior is not None:
        return prior
    known = {
        p["policy_id"]: p for p in case.frozen_inputs.get("projection", {}).get("policies", [])
    }
    w2 = case.w2_final
    related = set(w2.policy_ids if w2 and w2.policy_hit else [])
    if w2:
        related.update(related_ids(w2))
    selected = result.policy_execution.selected_condition if result.policy_execution else None
    no_trade = selected is None and not result.expert_trade.trade
    frozen = {v["policy_id"]: v for v in case.frozen_inputs.get("policy_watch_followup", [])}
    actions = {a.policy_id: a for a in result.watch_actions if a.action == "SET"}
    actions.update({a.policy_id: a for a in result.watch_actions if a.action == "CANCEL"})
    outcomes = []
    for pid, action in actions.items():
        key = encode([case.ticker, pid])
        current = get_value(db, "policy_watch", key)
        reason = None
        if pid not in known or pid not in related:
            reason = "OUTSIDE_W2_POLICY_CONTEXT"
        elif action.action == "CANCEL":
            expected = frozen.get(pid)
            if current and expected and current["source_case_id"] == expected["source_case_id"]:
                db.execute(
                    "DELETE FROM runtime_values WHERE namespace='policy_watch' AND key=?", (key,)
                )
            else:
                reason = "STALE_OR_NO_FROZEN_WATCH"
        elif not no_trade:
            reason = "FINAL_TRADE_DECISION"
        elif get_value(db, "candidates", f"candidate:{case.case_id}"):
            reason = "CLOSED_CANDIDATE"
        elif current and datetime.fromisoformat(current["expires_at"]) > now:
            reason = "ALREADY_ACTIVE_NO_EXTENSION"
        else:
            expires = boundary(case.trading_date + timedelta(days=2))
            states = [
                s
                for p in case.frozen_inputs.get("policy_usage", [])
                if p["policy_id"] == pid
                for s in p["directions"]
            ]
            from .policy_consumption import unresolved_migration, usage

            available = not unresolved_migration(db, case.ticker, pid) and any(
                not usage(db, case.ticker, pid, s["direction"])["consumed"] for s in states
            )
            later = db.execute(
                "SELECT 1 FROM runtime_values WHERE namespace='policy_consumptions' "
                "AND json_extract(payload,'$.ticker')=? AND json_extract(payload,'$.policy_id')=? "
                "AND julianday(json_extract(payload,'$.consumed_at'))>=julianday(?) LIMIT 1",
                (case.ticker, pid, case.created_at.isoformat()),
            ).fetchone()
            if expires <= now:
                reason = "SOURCE_WINDOW_EXPIRED"
            elif not available:
                reason = "NO_EXECUTABLE_DIRECTION"
            elif later:
                reason = "READY_AFTER_CASE_STARTED"
            else:
                put_value(
                    db,
                    "policy_watch",
                    key,
                    dict(
                        ticker=case.ticker,
                        policy_id=pid,
                        source_case_id=case.case_id,
                        reason=action.reason,
                        start_semantic_day=case.trading_date.isoformat(),
                        expires_at=expires.isoformat(),
                    ),
                )
        outcomes.append(dict(policy_id=pid, action=action.action, status=reason or "APPLIED"))
    receipt = dict(ticker=case.ticker, case_id=case.case_id, outcomes=outcomes)
    put_value(db, "policy_watch_applied", case.case_id, receipt)
    return receipt
