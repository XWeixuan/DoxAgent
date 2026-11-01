"""Small FIFO read model derived from owned allocations, never broker net positions."""

from collections import deque
from datetime import UTC, datetime
from decimal import Decimal

ZERO = Decimal(0)


def dec(value):
    return Decimal(str(value))


def stamp(value):
    return datetime.fromisoformat(value).astimezone(UTC).isoformat()


def fold_leg(lot, allocations, fills, fees):
    queue, timeline, realized, entry_value, entry_qty = deque(), [], ZERO, ZERO, ZERO
    exit_value, exit_qty, fee_total = ZERO, ZERO, ZERO
    complete, basis_ok = True, True
    realized_known = True
    sign = lot["sign"]
    resolved = []
    for allocation in allocations:
        if allocation["kind"] == "ADJUSTMENT":
            basis_ok = False
            continue
        fill = fills.get((allocation["account"], allocation["fill_id"]))
        if not fill:
            basis_ok = False
            continue
        resolved.append((stamp(fill["time"]), fill["exec_id"], allocation, fill))
    for at, _, allocation, fill in sorted(resolved, key=lambda item: item[:2]):
        qty = dec(allocation["qty"])
        if qty <= 0:
            continue
        price = dec(fill["price"])
        fee = fees.get((fill["account"], fill["exec_id"]))
        known = bool(fee and fee.get("currency") == "USD" and dec(fee["commission"]).is_finite())
        complete &= known
        charge = dec(fee["commission"]) * qty / dec(fill["quantity"]) if known else ZERO
        fee_total += charge
        delta = ZERO
        delta_known = True
        if allocation["kind"] == "ENTRY":
            queue.append([qty, price, charge / qty, known])
            entry_value += qty * price
            entry_qty += qty
        elif allocation["kind"] in {"EXIT", "FIFO_OFFSET"}:
            delta_known = known
            remaining, cost, entry_fee = qty, ZERO, ZERO
            while remaining and queue:
                item = queue[0]
                used = min(remaining, item[0])
                cost += used * item[1]
                entry_fee += used * item[2]
                delta_known &= item[3]
                remaining -= used
                item[0] -= used
                if not item[0]:
                    queue.popleft()
            basis_ok &= remaining == 0
            delta_known &= basis_ok
            realized_known &= delta_known
            delta = (qty * price - cost) * sign - entry_fee - charge
            realized += delta
            exit_value += qty * price
            exit_qty += qty
        timeline.append(
            dict(
                at=at,
                kind=allocation["kind"],
                known=bool(basis_ok and all(item[3] for item in queue)),
                realized_known=bool(delta_known),
                fee=str(charge) if known else None,
                qty=str(sum((item[0] for item in queue), ZERO)),
                cost=str(sum((item[0] * item[1] for item in queue), ZERO)),
                entry_fee=str(sum((item[0] * item[2] for item in queue), ZERO)),
                realized=str(realized),
                delta=str(delta),
            )
        )
    remaining = sum((item[0] for item in queue), ZERO)
    basis_ok &= remaining == dec(lot.get("remaining_qty", 0))
    return dict(
        id=lot["id"],
        role=lot.get("role", "MAIN"),
        asset=lot["ticker"],
        sign=sign,
        qty=str(remaining),
        entry_qty=str(entry_qty),
        exit_qty=str(exit_qty),
        entry_price=str(entry_value / entry_qty) if entry_qty else None,
        exit_price=str(exit_value / exit_qty) if exit_qty else None,
        notional=str(entry_value),
        realized=str(realized) if basis_ok and realized_known else None,
        fees=str(fee_total) if complete else None,
        known=bool(complete and basis_ok),
        basis_known=bool(basis_ok),
        opened_at=stamp(lot["first_fill_at"]) if lot.get("first_fill_at") else None,
        exit_at=stamp(lot["scheduled_exit"]) if lot.get("scheduled_exit") else None,
        timeline=timeline,
    )


def state_at(leg, at):
    return next(
        (p for p in reversed(leg["timeline"]) if p["at"] < at),
        dict(qty="0", cost="0", entry_fee="0", realized="0"),
    )


def unrealized(leg, at, quote):
    state = state_at(leg, at)
    qty = dec(state["qty"])
    if not qty:
        return ZERO
    side = "bid" if leg["sign"] == 1 else "ask"
    if not leg["basis_known"] or not state["known"] or not quote or quote.get(side) is None:
        return None
    return (qty * dec(quote[side]) - dec(state["cost"])) * leg["sign"] - dec(state["entry_fee"])
