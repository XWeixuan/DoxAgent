"""One compact post-launch revenue record per formal LONG/SHORT intent."""

import hashlib
import json

from doxagent.v2_read.executions import ExecutionProjector, opaque
from doxagent.v2_read.projector import native

from .ledger import dec, fold_leg, stamp


def account_key(account):
    return hashlib.sha256((account or "").encode()).hexdigest()


def activation(store):
    with store.connect() as db:
        row = db.execute("SELECT value FROM read_meta WHERE key='revenue_audit_v1'").fetchone()
    return json.loads(row[0]) if row else None


def project(store, event, pending):
    output = _project(store, event, pending)
    if event["table_name"] != "te_events" or event["row"].get("kind") != "fees":
        return output
    # One broker fill can close allocations owned by several earlier strategies.
    fee = native(event)
    with store.connect() as db:
        related = list(
            db.execute(
                "SELECT DISTINCT ticker,parent FROM object_current "
                "WHERE kind='native:te_allocations' "
                "AND json_extract(payload,'$.account')=? AND json_extract(payload,'$.fill_id')=?",
                (event["row"]["account"], fee["exec_id"]),
            )
        )
    adapter = ExecutionProjector(store)
    for _, lot_id in related:
        job = adapter.find("te_jobs", lot_id + ":entry")
        execution = adapter.find("te_executions", job["execution_id"])
        synthetic = {
            **event,
            "table_name": "te_executions",
            "row": {"payload": json.dumps(execution)},
        }
        output.extend(_project(store, synthetic, pending))
    return list({(r["ticker"], r["id"]): r for r in output}.values())


def _project(store, event, pending):
    table, row = event["table_name"], event["row"]
    if table not in {
        "runtime_values",
        "te_executions",
        "te_jobs",
        "te_attempts",
        "te_fills",
        "te_allocations",
        "te_lots",
        "te_events",
        "te_capital_events",
    }:
        return []
    if table == "runtime_values" and row["namespace"] not in {
        "trade_intents",
        "analysis_trade_decisions",
    }:
        return []
    if table == "te_events" and row.get("kind") != "fees":
        return []
    enabled = activation(store)
    if not enabled:
        return []
    value = native(event)
    if table == "te_capital_events":
        capital_at = stamp(row["effective_at"])
        if capital_at < enabled["since"]:
            return []
        return [
            dict(
                kind="revenue_capital",
                ticker="",
                id=row["id"],
                parent=account_key(row["account"]),
                sort=capital_at,
                data={
                    **value,
                    "kind": row["kind"],
                    "amount": row["amount"],
                    "at": capital_at,
                    "account": None,
                },
            )
        ]
    adapter = ExecutionProjector(store)
    execution = None
    if table == "runtime_values":
        if row["namespace"] not in {"trade_intents", "analysis_trade_decisions"}:
            return []
        intent = value
        identity = intent.get("intent_id", row["key"])
        try:
            execution = adapter.find("te_executions", identity)
        except ValueError:
            pass
    else:
        group = next(
            (
                r.get("parent")
                for r in pending
                if r["kind"]
                in {"native:te_jobs", "native:te_attempts", "native:te_fills", "commission"}
            ),
            None,
        )
        if table == "te_executions":
            execution = value
        elif group:
            execution = adapter.find("te_executions", group)
        elif table in {"te_allocations", "te_lots"}:
            lot_id = value.get("lot_id", value.get("id"))
            job = adapter.find("te_jobs", lot_id + ":entry")
            execution = adapter.find("te_executions", job["execution_id"])
        else:
            return []
        intent, identity = execution["intent"], execution["id"]
    if execution:
        intent = execution["intent"]
    audit = intent.get("audit_context") or {}
    if audit.get("decision_at"):
        audit = {**audit, "decision_at": stamp(audit["decision_at"])}
    recorded_at = (
        stamp(intent.get("released_at") or intent.get("recorded_at") or audit.get("decision_at"))
        if audit.get("version") == 1
        else ""
    )
    if audit.get("version") != 1 or recorded_at < enabled["since"]:
        return []
    if intent.get("trade", {}).get("decision") not in {"LONG", "SHORT"}:
        return []
    env = audit.get("environment")
    if env not in {"PAPER", "LIVE"}:
        return []
    ticker = intent["ticker"]

    def rows(kind, parent):
        values = adapter.rows(kind, ticker, parent)
        for item in pending:
            if item["kind"] == kind and item.get("parent") == parent:
                if item["data"] is None:
                    values.pop(item["id"], None)
                else:
                    values[item["id"]] = item["data"]
        return list(values.values())

    legs, actual, main_cap = [], False, None
    if execution:
        fills = rows("native:te_fills", identity)
        # Highest IB correction is authoritative, including a zero-quantity bust.
        effective = {}
        for fill in fills:
            key = (fill["account"], fill["family"])
            if key not in effective or fill["correction"] > effective[key]["correction"]:
                effective[key] = fill
        actual = any(dec(f["quantity"]) > 0 for f in effective.values())
        fill_map = {(f["account"], f["exec_id"]): f for f in effective.values()}
        fees = {(execution["account"], f["exec_id"]): f for f in rows("commission", identity)}
        leg_ids = [identity] + ([identity + ":hedge"] if len(execution.get("legs", [])) > 1 else [])
        # Leg IDs come from actual ENTRY jobs, rather than assumed ticker/net position.
        jobs = rows("native:te_jobs", identity)
        leg_ids = {j.get("leg_id", identity) for j in jobs if j["leg"] == "ENTRY"} or set(leg_ids)
        for leg_id in sorted(leg_ids):
            lot = next(
                (r["data"] for r in pending if r["kind"] == "native:te_lots" and r["id"] == leg_id),
                None,
            )
            lot = lot or store.get("native:te_lots", ticker, leg_id)
            if lot:
                allocations = rows("native:te_allocations", leg_id)
                for allocation in allocations:
                    key = (allocation["account"], allocation["fill_id"])
                    if allocation["kind"] == "ADJUSTMENT" or key in fill_map:
                        continue
                    try:
                        fill_map[key] = adapter.find("te_fills", opaque(*key))
                    except ValueError:
                        continue
                    # FIFO netting may use an opposite strategy's fill and fee.
                    found = next(
                        (
                            p["data"]
                            for p in pending
                            if p["kind"] == "commission" and p["id"] == opaque(*key)
                        ),
                        None,
                    )
                    if not found:
                        with store.connect() as db:
                            fee_row = db.execute(
                                "SELECT payload FROM object_current "
                                "WHERE kind='commission' AND id=?",
                                (opaque(*key),),
                            ).fetchone()
                            found = json.loads(fee_row[0]) if fee_row else None
                    if found:
                        fees[key] = found
                legs.append(fold_leg(lot, allocations, fill_map, fees))
        main_cap = execution.get("plan", {}).get("main_cap")
    status = intent.get("status", intent.get("trade_disposition", "UNKNOWN"))
    terminal = status not in {"READY", "UNKNOWN", "SUBMITTED", "DELIVERED", "EXECUTION_ACCEPTED"}
    if execution:
        terminal = execution.get("entry_result") in {
            "FILLED",
            "PARTIAL_FILLED",
            "DIRECTION_DISABLED",
            "UNFILLED",
            "REJECTED",
            "FAILED",
            "CANCELLED",
            "EXPIRED",
        } or execution.get("group_state") in {"CLOSED", "ENTRY_FAILED"}
    kind = "ACTUAL" if actual else "UNEXECUTED" if terminal else "PENDING"
    summary = dict(
        id=identity,
        case_id=intent.get("case_id"),
        ticker=ticker,
        direction=intent["trade"]["decision"],
        environment=env,
        account_key=account_key(audit.get("account")),
        title=audit.get("title") or ticker,
        intent_at=audit["decision_at"],
        recorded_at=recorded_at,
        kind=kind,
        status=(execution or {}).get("entry_result") or status,
        reason=(execution or {}).get("entry_reason")
        or (intent.get("delivery_failure") or {}).get("code")
        or intent.get("trade_disposition")
        or status,
        completed=bool(
            actual
            and legs
            and all(dec(leg_item["qty"]) == 0 for leg_item in legs)
            and (execution or {}).get("group_state") == "CLOSED"
        ),
        main_cap=main_cap,
        exit_offset_minutes=audit.get("exit_offset_minutes", 30),
        legs=legs,
    )
    return [
        dict(
            kind="revenue_record",
            ticker=env,
            id=identity,
            parent=summary["account_key"],
            sort=audit["decision_at"],
            data=summary,
        )
    ]
