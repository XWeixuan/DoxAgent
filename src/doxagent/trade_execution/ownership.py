"""Contract-local residual ownership; no external trade ledger or broker requests."""

import json
from decimal import Decimal

from .capital import get, put
from .strategy import decimal


class PositionAttributionInvalid(Exception):
    pass


class PositionReconcilePending(Exception):
    pass


def check(repo, job, contract, sync, generation=None):
    account, cid = job["account"], contract["con_id"]
    if not sync.get("complete") or (
        generation is not None and sync.get("connection_generation", generation) != generation
    ):
        raise PositionReconcilePending("POSITION_SNAPSHOT_UNAVAILABLE")
    actual = decimal(sync["positions"].get(cid, sync["positions"].get(str(cid), 0)))
    if not actual.is_finite():
        raise PositionReconcilePending("POSITION_SNAPSHOT_INVALID")
    with repo.transaction(write=False) as db:
        attempts = [
            json.loads(r[0])
            for r in db.execute(
                "SELECT payload FROM te_attempts WHERE account=? "
                "AND json_extract(payload,'$.contract.con_id')=?",
                (account, cid),
            )
        ]
        related = {a["id"]: a for a in attempts if a.get("contract", {}).get("con_id") == cid}
        if any(a["state"] not in {"SETTLED", "NOT_SENT"} for a in related.values()):
            raise PositionReconcilePending("POSITION_ORDER_UNRESOLVED")
        for r in db.execute(
            "SELECT payload FROM runtime_values WHERE namespace='execution_event_gaps'"
        ):
            gap = json.loads(r[0])
            if gap.get("account") == account and gap.get("con_id") in {None, cid}:
                raise PositionReconcilePending("UNRESOLVED_FILL_GAP")
        fills = repo._effective_fills(db, account, cid)
        quantities = {}
        for f in fills:
            quantities[f["attempt_id"]] = quantities.get(f["attempt_id"], Decimal(0)) + decimal(
                f["quantity"]
            )
        for a in related.values():
            if a.get("broker_filled") is not None and decimal(a["broker_filled"]) != quantities.get(
                a["id"], 0
            ):
                raise PositionReconcilePending("FILL_RECONCILIATION_GAP")
        # These are bounded rowid ranges, not a scan of historical events.
        for table, marker in [
            ("te_fills", "ownership_fill_start"),
            ("te_lot_adjustments", "ownership_adjust_start"),
        ]:
            if (
                marker in sync
                and db.execute(
                    "SELECT 1 FROM " + table + " WHERE rowid>? AND account=? AND con_id=? LIMIT 1",
                    (sync[marker], account, cid),
                ).fetchone()
            ):
                raise PositionReconcilePending("POSITION_SNAPSHOT_CHANGED")
        cursor = get(db, "trade_execution", "event_cursor") or 0
        for r in db.execute(
            "SELECT payload FROM te_events WHERE id>? AND account=? AND kind='fill'",
            (cursor, account),
        ):
            event = json.loads(r[0])
            if event.get("con_id") == cid and any(
                event.get("order_ref") == a["order_ref"]
                or (event.get("client_id"), event.get("order_id"))
                == (a["client_id"], a["order_id"])
                for a in related.values()
            ):
                raise PositionReconcilePending("FILL_RECONCILIATION_GAP")
        adjustments = [
            json.loads(r[0])
            for r in db.execute(
                "SELECT payload FROM te_lot_adjustments WHERE account=? AND con_id=?",
                (account, cid),
            )
        ]
        owned = sum(
            (decimal(f["quantity"]) * (1 if f["side"] == "BUY" else -1) for f in fills), Decimal(0)
        )
        owned += sum((decimal(a["quantity_delta"]) * a["sign"] for a in adjustments), Decimal(0))
        lots = [
            json.loads(r[0])
            for r in db.execute(
                "SELECT payload FROM te_lots WHERE account=? AND con_id=?", (account, cid)
            )
        ]
        if any(lot_value.get("unattributed_qty") for lot_value in lots) or owned != sum(
            (decimal(lot_value["remaining_qty"]) * lot_value["sign"] for lot_value in lots),
            Decimal(0),
        ):
            raise PositionAttributionInvalid("POSITION_LEDGER_INCONSISTENT")
        key = f"{account}:{cid}"
        old = get(db, "execution_position_baselines", key)
        residual = actual - owned
    if old is None or decimal(old["quantity"]) != residual:
        # Normal checks hold no Runtime writer lock; only an actual baseline
        # change gets a short atomic baseline+audit write.
        with repo.transaction() as db:
            previous = get(db, "execution_position_baselines", key)
            if previous is not None and decimal(previous["quantity"]) == residual:
                return
            value = dict(quantity=str(residual), at=sync["at"])
            put(db, "execution_position_baselines", key, value)
            db.execute(
                "INSERT INTO te_events(account,kind,payload,created_at) VALUES(?,?,?,?)",
                (
                    account,
                    "position_baseline_adjusted",
                    json.dumps(
                        dict(
                            con_id=cid,
                            previous=previous,
                            actual=str(actual),
                            owned=str(owned),
                            baseline=value,
                            job_id=job["id"],
                        )
                    ),
                    repo.journal.clock().isoformat(),
                ),
            )
