"""Case-level trade outcome derived from formal intents and effective ENTRY fills."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from doxagent.api_v2.dto import validate

TRADE_RESULTS = {"TRADE_INTENT", "TRADE_EXECUTION", "TRADE_NOT_EXECUTED"}
TERMINAL_UNSENT = {
    "DUPLICATE_POLICY",
    "DUPLICATE_REALTIME_OUTPUT",
    "DUPLICATE_EVENT_TRADE",
    "EXPIRED_SEMANTIC_DAY",
    "OUTPUT_RECORDED",
}


def classify_execution(summary: dict) -> tuple[str, list[str]]:
    """An order status never proves an ENTRY fill or a terminal non-execution."""
    raw = (summary.get("filled_quantity") or {}).get("value")
    try:
        quantity = Decimal(raw) if raw is not None else None
    except (InvalidOperation, TypeError):
        quantity = None
    intent_status = summary.get("intent_status")
    intake = summary.get("intake_status")
    result = summary.get("entry_result")
    reason = summary.get("entry_reason")
    if quantity is not None and quantity > 0:
        return "EXECUTED", [reason] if reason else []
    if result in {"FILLED", "PARTIAL_FILLED"}:
        return "UNKNOWN", ["ENTRY_FILL_EVIDENCE_MISSING"]
    if result in {"FAILED", "DIRECTION_DISABLED"}:
        return "NOT_EXECUTED", [reason or result]
    if intent_status == "UNKNOWN" or intake == "UNKNOWN":
        return "UNKNOWN", ["DELIVERY_UNKNOWN"]
    if intake == "NOT_RECEIVED" and intent_status in TERMINAL_UNSENT:
        return "NOT_EXECUTED", [intent_status]
    if intent_status in {"READY", "EXECUTION_ACCEPTED"} or intake == "EXECUTION_ACCEPTED":
        return "PENDING", []
    return "UNKNOWN", ["EXECUTION_EVIDENCE_INCOMPLETE"]


def with_execution_state(summary: dict) -> dict:
    state, reasons = classify_execution(summary)
    return {**summary, "execution_state": state, "execution_reason_codes": reasons}


def rollup_case_trade(case: dict, executions: list[dict]) -> dict:
    """One Case may have several intents, but appears once per result node."""
    by_intent = {item["intent_id"]: item for item in executions}
    states = [classify_execution(item) for item in by_intent.values()]
    if not states:
        state = "NOT_APPLICABLE"
    elif any(name == "EXECUTED" for name, _ in states):
        state = "EXECUTED"
    elif any(name == "UNKNOWN" for name, _ in states):
        state = "UNKNOWN"
    elif any(name == "PENDING" for name, _ in states):
        state = "PENDING"
    else:
        state = "NOT_EXECUTED"
    reasons = sorted({reason for _, codes in states for reason in codes})
    results = set(case.get("results", [])) - TRADE_RESULTS
    if by_intent:
        results.add("TRADE_INTENT")
    if state == "EXECUTED":
        results.add("TRADE_EXECUTION")
    elif state == "NOT_EXECUTED":
        results.add("TRADE_NOT_EXECUTED")
    settled = case["status"] in {"COMPLETED", "FAILED", "UNAVAILABLE"} and state not in {
        "PENDING",
        "UNKNOWN",
    }
    disposition = case.get("trade_disposition", "NOT_EVALUATED")
    if any(item.get("intake_status") == "EXECUTION_ACCEPTED" for item in by_intent.values()):
        disposition = "EXECUTION_ACCEPTED"
    return validate(
        "CaseSummary",
        {
            **case,
            "results": sorted(results),
            "trade": {"intent_count": len(by_intent), "state": state, "reason_codes": reasons},
            "trade_disposition": disposition,
            "result_settled": settled,
        },
    )


def project_case_trade(store, ticker: str, case: dict, incoming: list[dict]):
    """Overlay this receipt on indexed siblings before producing one Case record."""
    import json

    with store.connect() as db:
        siblings = {
            row[0]: json.loads(row[1])
            for row in db.execute(
                "SELECT id,payload FROM object_current WHERE kind='execution' AND ticker=? "
                "AND parent=? AND valid_to IS NULL",
                (ticker, case["case_id"]),
            )
        }
    for record in incoming:
        if (
            record["kind"] == "execution"
            and record["ticker"] == ticker
            and record.get("parent") == case["case_id"]
        ):
            if record.get("data") is None:
                siblings.pop(record["id"], None)
            else:
                siblings[record["id"]] = record["data"]
    updated = rollup_case_trade(case, list(siblings.values()))
    return (
        {
            "kind": "case",
            "ticker": ticker,
            "id": case["case_id"],
            "data": updated,
            "sort": updated["received_at"],
            "day": updated["semantic_day"],
            "parent": updated["stream_item_id"],
            "source_id": updated["source"]["source_id"],
            "route": updated["resolved_route"] or updated["initial_route"],
        },
        {
            "metric": "executed_cases",
            "ticker": ticker,
            "entity": case["case_id"],
            "day": case["semantic_day"],
            "value": "1" if updated["trade"]["state"] == "EXECUTED" else "0",
        },
    )


def legacy_case_summary(store, ticker: str, case: dict, seq: int) -> dict:
    """Temporary API compatibility for old Case rows until derived-read backfill.

    Only old rows pay for the sibling lookup; new projections already carry trade.
    """
    if "trade" in case:
        return case
    import json

    with store.connect() as db:
        rows = db.execute(
            "SELECT payload FROM objects WHERE kind='execution' AND ticker=? AND parent=? "
            "AND valid_from<=? AND (valid_to IS NULL OR valid_to>?)",
            (ticker, case["case_id"], seq, seq),
        ).fetchall()
    return rollup_case_trade(case, [json.loads(row[0]) for row in rows])
