"""Actual API invocation records from the admitted V2 initialization ledger."""

from datetime import datetime
from decimal import Decimal

from doxagent.semantic_clock import semantic_day

from .runtime import timing
from .usage import accounting, observations


def project(value):
    ticker, identity = value["ticker"], value["invocation_id"]
    if not value.get("control_operation_id"):
        return [], []
    day = str(semantic_day(datetime.fromisoformat(value["started_at"])))
    dimensions = {
        "scope": "API",
        "node": value["node"],
        "round": value.get("round") or "UNKNOWN_ROUND",
        "provider": value["provider"],
        "model": value.get("model") or "unknown",
    }
    usage = {
        **value["usage"],
        "provider_usage": value.get("provider_usage") or value["usage"].get("provider_usage") or {},
        "provider_endpoint": value.get("provider_endpoint"),
    }
    normalized, money = accounting(
        dimensions["model"], dimensions["provider"], "API", value["started_at"], usage
    )
    record = {
        "kind": "usage",
        "ticker": ticker,
        "id": identity,
        "parent": value["initialization_id"],
        "day": day,
        "data": {
            "invocation_id": identity,
            **dimensions,
            "usage": normalized,
            "normalization_version": normalized["normalization_version"],
            "accounting": {
                key: format(amount, "f") if isinstance(amount, Decimal) else amount
                for key, amount in money.items()
            },
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
            dimensions["model"], dimensions["provider"], "API", value["started_at"], usage
        ).items()
    ]
    return [record], counts
