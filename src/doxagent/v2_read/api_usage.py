"""Actual API invocation records from the admitted V2 initialization ledger."""

from datetime import datetime

from doxagent.semantic_clock import semantic_day

from .runtime import timing
from .usage import observations


def project(value):
    ticker, identity = value["ticker"], value["invocation_id"]
    if not value.get("control_operation_id"):
        return [], []
    day = str(semantic_day(datetime.fromisoformat(value["started_at"])))
    dimensions = {
        "scope": "API",
        "node": value["node"],
        "provider": value["provider"],
        "model": value.get("model") or "unknown",
    }
    record = {
        "kind": "usage",
        "ticker": ticker,
        "id": identity,
        "parent": value["initialization_id"],
        "day": day,
        "data": {
            "invocation_id": identity,
            **dimensions,
            "usage": value["usage"],
            "timing": timing(value["started_at"], value.get("finished_at")),
        },
    }
    counts = [
        {
            "metric": name,
            "ticker": ticker,
            "entity": identity,
            "day": day,
            "dimensions": dimensions,
            "value": amount,
        }
        for name, amount in observations(
            dimensions["model"], dimensions["provider"], "API", value["started_at"], value["usage"]
        ).items()
    ]
    return [record], counts
