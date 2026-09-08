"""Realized PNL from owned EXIT allocations and the original entry cost shares."""

from collections import deque
from datetime import datetime
from decimal import Decimal

from doxagent.semantic_clock import semantic_day

from .executions import ExecutionProjector, opaque


def project(store, event, records):
    table = event["table_name"]
    if table not in {"te_allocations", "te_lots", "te_fills", "te_events"}:
        return [], []
    from .projector import native

    value = native(event)
    adapter = ExecutionProjector(store)
    record = records[0]
    if table == "te_allocations":
        lot_id = value["lot_id"]
        execution = adapter.find("te_executions", lot_id)
        ticker = execution["ticker"]
        record.update(
            ticker=ticker,
            parent=lot_id,
            id=opaque(value["account"], value["fill_id"], lot_id, value["kind"]),
        )
    elif table == "te_lots":
        lot_id = value["id"]
        execution = adapter.find("te_executions", lot_id)
        ticker = execution["ticker"]
        record.update(ticker=ticker, parent=lot_id, id=lot_id)
    else:
        if table == "te_events" and event["row"]["kind"] != "fees":
            return [], []
        lot_id, ticker = record.get("parent"), record["ticker"]
        if not lot_id:
            return [], []
        execution = adapter.find("te_executions", lot_id)
    intent = execution["intent"]
    case_id = intent.get("case_id") or intent.get("trade", {}).get("case_id")
    if not case_id or not store.get("business_provenance", ticker, case_id):
        return [], []
    allocations = adapter.rows("native:te_allocations", ticker, lot_id)
    fills = adapter.rows("native:te_fills", ticker, lot_id)
    fees = adapter.rows("commission", ticker, lot_id)
    for pending in records:
        if pending["kind"] == "native:te_allocations":
            if pending["data"] is None:
                allocations.pop(pending["id"], None)
            else:
                allocations[pending["id"]] = pending["data"]
        if pending["kind"] == "native:te_fills" and pending["data"]:
            fills[pending["id"]] = pending["data"]
        if pending["kind"] == "commission" and pending["data"]:
            fees[pending["id"]] = pending["data"]
    resolved = []
    adjusted_basis = any(a["kind"] == "ADJUSTMENT" for a in allocations.values())
    for allocation in allocations.values():
        if allocation["kind"] == "ADJUSTMENT":
            # External/manual adjustments have no provable unit cost in this contract.
            continue
        fill_id = opaque(allocation["account"], allocation["fill_id"])
        fill = fills.get(fill_id)
        if not fill:
            fill = adapter.find("te_fills", fill_id)
        resolved.append((fill["time"], allocation["fill_id"], allocation, fill))
    queue, observations = deque(), {}
    environment = (intent.get("execution_pin") or {}).get("profile", {}).get("environment")
    if environment not in {"PAPER", "LIVE"}:
        return [], []
    metric = environment.lower() + "_realized_net_pnl"

    def fee_share(fill):
        fee = fees.get(opaque(fill["account"], fill["exec_id"]))
        if not fee:
            return Decimal(0), True, False
        if fee.get("currency") != "USD":
            return Decimal(0), False, True
        return Decimal(fee["commission"]) / Decimal(fill["quantity"]), False, False

    for _, _, allocation, fill in sorted(resolved):
        qty = Decimal(allocation["qty"])
        if qty <= 0:
            continue
        price = Decimal(fill["price"])
        commission, pending, unsupported = fee_share(fill)
        if allocation["kind"] == "ENTRY":
            queue.append([qty, price, commission, pending, unsupported])
            continue
        remaining, cost, entry_fee, unknown = qty, Decimal(0), Decimal(0), False
        while remaining and queue:
            item = queue[0]
            used = min(remaining, item[0])
            cost += used * item[1]
            entry_fee += used * item[2]
            unknown |= item[3]
            unsupported |= item[4]
            remaining -= used
            item[0] -= used
            if item[0] == 0:
                queue.popleft()
        if allocation["kind"] != "EXIT":
            continue  # FIFO offsets consume cost shares, not product realized PNL.
        identity = opaque(fill["account"], fill["family"], lot_id)
        amount = (price * qty - cost) * (1 if intent["trade"]["decision"] == "LONG" else -1)
        amount -= entry_fee + commission * qty
        observations[identity] = {
            "amount": None if remaining or adjusted_basis or unsupported else str(amount),
            "day": str(semantic_day(datetime.fromisoformat(fill["time"]))),
            "provisional": pending or unknown,
            "reason": "UNSUPPORTED_COMMISSION_CURRENCY"
            if unsupported
            else "COST_BASIS_MISSING"
            if remaining or adjusted_basis
            else ("COMMISSION_PENDING" if pending or unknown else None),
            "environment": environment,
        }
    previous = adapter.rows("realized_pnl", ticker, lot_id)
    output, contributions = [], []
    for identity in set(previous) | set(observations):
        observation = observations.get(identity)
        prior = previous.get(identity)
        day = (observation or prior)["day"]
        output.append(
            {
                "kind": "realized_pnl",
                "ticker": ticker,
                "id": identity,
                "parent": lot_id,
                "day": day,
                "data": observation,
            }
        )
        for name, amount in (
            (metric, (observation or {}).get("amount") or "0"),
            (
                metric + "_samples",
                str(int(bool(observation and observation["amount"] is not None))),
            ),
            (metric + "_provisional", str(int(bool(observation and observation["provisional"])))),
        ):
            contributions.append(
                {"metric": name, "ticker": ticker, "entity": identity, "day": day, "value": amount}
            )
    return output, contributions
