"""Verified external close economics attached to an existing quantity adjustment.

These are financial facts only: never insert strategy fills or rebuild lots.
"""

import json
from datetime import datetime

from doxagent.persistent_runtime_v2.journal import encode

from .strategy import decimal


def attach(repo, adjustment_id, fill, *, actor, evidence):
    if not actor.strip() or not evidence.strip():
        raise ValueError("manual close evidence and actor required")
    with repo.transaction() as db:
        row = db.execute(
            "SELECT account,con_id,payload FROM te_lot_adjustments WHERE id=?", (adjustment_id,)
        ).fetchone()
        if not row:
            raise ValueError("existing quantity adjustment required")
        account, con_id, payload = row
        adjustment = json.loads(payload)
        qty = -decimal(adjustment["quantity_delta"])
        if (
            qty <= 0
            or fill["account"] != account
            or fill["con_id"] != con_id
            or decimal(fill["quantity"]) != qty
            or fill["side"] != ("SELL" if adjustment["sign"] == 1 else "BUY")
            or datetime.fromisoformat(fill["time"]) != datetime.fromisoformat(adjustment["time"])
            or decimal(fill["price"]) <= 0
        ):
            raise ValueError("manual close does not match verified quantity adjustment")
        if db.execute(
            "SELECT 1 FROM te_fills WHERE account=? AND family=?",
            (account, fill["exec_id"].rsplit(".", 1)[0]),
        ).fetchone():
            raise ValueError("close already recorded as strategy fill")
        fee = db.execute(
            "SELECT payload FROM runtime_values WHERE namespace='execution_fees' AND key=?",
            (account + ":" + fill["exec_id"],),
        ).fetchone()
        fee = json.loads(fee[0]) if fee else None
        if not fee or fee.get("currency") != "USD" or not decimal(fee["commission"]).is_finite():
            raise ValueError("verified USD commission required")
        finance = dict(
            fill=fill, commission=fee["commission"], currency="USD", actor=actor, evidence=evidence
        )
        old = adjustment.get("financial_close")
        if old:
            if old != finance:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            return old
        for (other,) in db.execute(
            "SELECT payload FROM te_lot_adjustments WHERE account=?", (account,)
        ):
            known = json.loads(other).get("financial_close", {}).get("fill", {})
            if known.get("exec_id", "").rsplit(".", 1)[0] == fill["exec_id"].rsplit(".", 1)[0]:
                raise ValueError("manual fill already attributed")
        adjustment["financial_close"] = finance
        db.execute(
            "UPDATE te_lot_adjustments SET payload=? WHERE id=?",
            (encode(adjustment), adjustment_id),
        )
        db.execute(
            "INSERT INTO te_events(account,kind,payload,created_at) VALUES(?,?,?,?)",
            (
                account,
                "manual_close_financial",
                encode(dict(adjustment_id=adjustment_id, **finance)),
                repo.journal.clock().isoformat(),
            ),
        )
        return finance


def closes(db, account):
    for (payload,) in db.execute(
        "SELECT payload FROM te_lot_adjustments WHERE account=?", (account,)
    ):
        adjustment = json.loads(payload)
        if finance := adjustment.get("financial_close"):
            yield {
                **finance["fill"],
                "lot_id": adjustment["lot_id"],
                "attempt_id": "manual-finance:" + adjustment["id"],
                "commission": finance["commission"],
                "manual_financial": True,
            }


def resolve(allocations, adjustments, fills, fees):
    """Replace a financially verified ADJUSTMENT with an EXIT in read-only folds."""
    result = []
    for allocation in allocations:
        finance = adjustments.get(allocation["fill_id"], {}).get("financial_close")
        if allocation["kind"] == "ADJUSTMENT" and finance:
            fill = {**finance["fill"], "family": finance["fill"]["exec_id"].rsplit(".", 1)[0]}
            key = (allocation["account"], fill["exec_id"])
            fills[key] = fill
            fees[key] = dict(commission=finance["commission"], currency=finance["currency"])
            allocation = {
                **allocation,
                "kind": "EXIT",
                "qty": str(-decimal(allocation["qty"])),
                "fill_id": fill["exec_id"],
            }
        result.append(allocation)
    return result
