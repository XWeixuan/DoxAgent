"""Owned execution summaries use highest-correction fills, never broker order status."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from doxagent.api_v2.dto import available, missing, validate
from doxagent.semantic_clock import semantic_day

from .projector import native
from .repository import ReadStore, encode, instant


def opaque(*parts: Any) -> str:
    return hashlib.sha256(encode(parts).encode()).hexdigest()


def pending_intent(store, ticker, intent):
    identity = intent["intent_id"]  # Native executor admission uses this same immutable identity.
    existing = store.get("execution", ticker, identity)
    if existing and existing["intake_status"] == "EXECUTION_ACCEPTED":
        return []
    pin = intent.get("execution_pin") or {}
    summary = {
        "execution_id": identity, "intent_id": identity, "case_id": intent["case_id"],
        "environment": available(pin["profile"]["environment"]) if pin else missing(),
        "profile_revision": available(pin["revision"]) if pin else missing(),
        "direction": intent["trade"]["decision"], "intent_status": intent["status"],
        "intake_status": "UNKNOWN" if intent["status"] == "UNKNOWN" else "NOT_RECEIVED",
        "entry_result": None, "entry_reason": None, "has_actual_fill": False,
        "triggered_at": available(instant(datetime.fromisoformat(intent["released_at"]))),
        "accepted_at": missing(), "first_fill_at": missing(),
        "filled_quantity": available("0"), "filled_notional_usd": available("0"),
    }
    return [{"kind": "execution", "ticker": ticker, "id": identity, "parent": intent["case_id"],
             "sort": intent["released_at"], "data": validate("ExecutionSummary", summary)}]


class ExecutionProjector:
    def __init__(self, store: ReadStore) -> None:
        self.store = store

    def find(self, table: str, identity: str) -> dict[str, Any]:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM objects WHERE kind=? AND id=? AND valid_to IS NULL",
                ("native:" + table, identity),
            ).fetchone()
        if not row:
            raise ValueError("execution dependency not indexed")
        return json.loads(row[0])

    def rows(self, kind: str, ticker: str, parent: str) -> dict[str, dict]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT id,payload FROM objects WHERE kind=? AND ticker=? AND parent=? "
                "AND valid_to IS NULL",
                (kind, ticker, parent),
            )
            return {r[0]: json.loads(r[1]) for r in rows}

    def project(self, event: dict, record: dict) -> tuple[list[dict], list[dict]]:
        table, value, row = event["table_name"], native(event), event["row"]
        if table not in {"te_executions", "te_jobs", "te_attempts", "te_fills", "te_events"}:
            return [], []
        fee = None
        job = None
        if table == "te_executions":
            execution = value
        elif table == "te_jobs":
            job = value
            execution = self.find("te_executions", job["execution_id"])
        elif table == "te_attempts":
            job = self.find("te_jobs", value["job_id"])
            execution = self.find("te_executions", job["execution_id"])
        else:
            if table == "te_events":
                if row["kind"] != "fees":
                    return [], []
                fee = value
                value = self.find("te_fills", opaque(row["account"], fee["exec_id"]))
            attempt = self.find("te_attempts", value["attempt_id"])
            job = self.find("te_jobs", attempt["job_id"])
            execution = self.find("te_executions", job["execution_id"])
        ticker, identity = execution["ticker"], execution["id"]
        intent = execution["intent"]
        case_id = intent.get("case_id") or intent.get("trade", {}).get("case_id")
        if not case_id:
            return [], []  # Historical test intake has no business Case provenance.
        if not self.store.get("business_provenance", ticker, case_id):
            raise ValueError("PROVENANCE_UNVERIFIED")
        record.update(ticker=ticker, parent=identity)
        record["id"] = (
            value["id"]
            if table in {"te_executions", "te_jobs", "te_attempts"}
            else (
                opaque(row["account"], row["exec_id"])
                if table == "te_fills"
                else event["entity_id"]
            )
        )
        records = []
        fills = self.rows("native:te_fills", ticker, identity)
        commissions = self.rows("commission", ticker, identity)
        if table == "te_fills":
            record["data"] = {
                **value,
                "family": row["family"],
                "correction": row["correction"],
                "leg": job["leg"],
            }
            fills[record["id"]] = record["data"]
        if fee:
            key = opaque(row["account"], fee["exec_id"])
            commissions[key] = fee
            records.append(
                {"kind": "commission", "ticker": ticker, "id": key, "parent": identity, "data": fee}
            )
        if table == "te_attempts":
            order = validate(
                "OrderSummary",
                {
                    "order_attempt_id": value["id"],
                    "execution_id": identity,
                    "leg": job["leg"],
                    "side": value["side"],
                    "order_type": value["order_type"],
                    "quantity": str(value["quantity"]),
                    "limit_price": str(value["limit_price"])
                    if value.get("limit_price") is not None
                    else None,
                    "state": value["state"],
                    "broker_status": value.get("broker_status"),
                    "sent_at": available(instant(datetime.fromisoformat(value["sent_at"])))
                    if value.get("sent_at")
                    else missing(),
                    "settled_at": available(instant(datetime.fromisoformat(value["settled_at"])))
                    if value.get("settled_at")
                    else missing(),
                },
            )
            records.append(
                {
                    "kind": "order",
                    "ticker": ticker,
                    "id": value["id"],
                    "parent": identity,
                    "sort": value["prepared_at"],
                    "data": order,
                }
            )
        effective: dict[str, dict] = {}
        for fill in fills.values():
            family = fill["family"]
            if family not in effective or fill["correction"] > effective[family]["correction"]:
                effective[family] = fill
        for family, fill in effective.items():
            commission = commissions.get(opaque(fill["account"], fill["exec_id"]))
            fee_value = missing("COMMISSION_PENDING")
            if commission and commission["currency"] == "USD":
                amount = Decimal(commission["commission"])
                if not amount.is_finite():
                    raise ValueError("invalid broker commission")
                fee_value = available(format(amount, "f"))
            elif commission:
                fee_value = missing("NOT_SUPPORTED", "UNAVAILABLE")
            public = validate(
                "Fill",
                {
                    "fill_id": opaque(fill["account"], fill["exec_id"]),
                    "execution_id": identity,
                    "order_attempt_id": fill["attempt_id"],
                    "correction_revision": fill["correction"],
                    "leg": fill["leg"],
                    "side": fill["side"],
                    "executed_at": instant(datetime.fromisoformat(fill["time"])),
                    "quantity": str(fill["quantity"]),
                    "price": str(fill["price"]),
                    "commission_usd": fee_value,
                },
            )
            records.append(
                {
                    "kind": "fill",
                    "ticker": ticker,
                    "id": opaque(fill["account"], family),
                    "parent": identity,
                    "sort": public["executed_at"],
                    "data": public,
                }
            )
        positive = [f for f in effective.values() if Decimal(f["quantity"]) > 0]
        entry = [f for f in positive if f["leg"] == "ENTRY"]
        pin = intent.get("execution_pin") or {}
        environment = (pin.get("profile") or {}).get("environment")
        first_fill = min((f["time"] for f in entry), default=None)
        summary = validate(
            "ExecutionSummary",
            {
                "execution_id": identity,
                "intent_id": intent["intent_id"],
                "case_id": case_id,
                "environment": available(environment) if environment else missing(),
                "profile_revision": available(execution["profile_revision"]),
                "direction": intent["trade"]["decision"],
                "intent_status": intent["status"],
                "intake_status": "EXECUTION_ACCEPTED",
                "entry_result": execution.get("entry_result"),
                "entry_reason": execution.get("entry_reason"),
                "has_actual_fill": bool(positive),
                "triggered_at": available(instant(datetime.fromisoformat(intent["released_at"])))
                if intent.get("released_at")
                else missing(),
                "accepted_at": available(instant(datetime.fromisoformat(execution["created_at"]))),
                "first_fill_at": available(instant(datetime.fromisoformat(first_fill)))
                if first_fill
                else missing(),
                "filled_quantity": available(
                    format(sum((Decimal(f["quantity"]) for f in entry), Decimal(0)), "f")
                ),
                "filled_notional_usd": available(
                    format(
                        sum(
                            (Decimal(f["quantity"]) * Decimal(f["price"]) for f in entry),
                            Decimal(0),
                        ),
                        "f",
                    )
                ),
            },
        )
        records.append(
            {
                "kind": "execution",
                "ticker": ticker,
                "id": identity,
                "parent": case_id,
                "sort": summary["accepted_at"]["value"],
                "data": summary,
            }
        )
        contributions = [
            {
                "metric": "trade_executed",
                "ticker": ticker,
                "entity": identity,
                "day": str(
                    semantic_day(datetime.fromisoformat(first_fill or execution["created_at"]))
                ),
                "dimensions": {"environment": environment},
                "value": "1" if entry else "0",
            }
        ]
        policy_id = intent["trade"].get("executed_policy_id")
        ar = intent["trade"].get("activation_revision")
        if policy_id and ar:
            contributions.append(
                {
                    **contributions[0],
                    "metric": "policy_executed",
                    "dimensions": {
                        "business_key": policy_id + ":" + ar,
                        "policy_id": policy_id,
                        "ar": ar,
                    },
                }
            )
        case = self.store.get("case", ticker, case_id)
        if case:
            siblings = self.rows("execution", ticker, case_id)
            siblings[identity] = summary
            has_fill = any(
                Decimal(item["filled_quantity"].get("value") or "0") > 0
                for item in siblings.values()
            )
            results = set(case["results"])
            if has_fill:
                results.add("TRADE_EXECUTION")
            else:
                results.discard("TRADE_EXECUTION")
            case.update(results=sorted(results), trade_disposition="EXECUTION_ACCEPTED")
            case["result_settled"] = case["status"] in {
                "COMPLETED",
                "FAILED",
                "UNAVAILABLE",
            } and all(item["entry_result"] is not None for item in siblings.values())
            records.append(
                {
                    "kind": "case",
                    "ticker": ticker,
                    "id": case_id,
                    "data": validate("CaseSummary", case),
                    "sort": case["received_at"],
                    "day": case["semantic_day"],
                    "parent": case["stream_item_id"],
                    "source_id": case["source"]["source_id"],
                    "route": case["resolved_route"] or case["initial_route"],
                }
            )
            contributions.append(
                {
                    "metric": "executed_cases",
                    "ticker": ticker,
                    "entity": case_id,
                    "day": case["semantic_day"],
                    "value": "1" if has_fill else "0",
                }
            )
        return records, contributions
