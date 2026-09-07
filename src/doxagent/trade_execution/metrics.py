"""Execution measurements derived from recorded quotes and broker fills, without estimates."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from .strategy import decimal


def attempt_metrics(attempt: dict[str, Any], fills: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [f for f in fills if f["attempt_id"] == attempt["id"]]
    quantity = sum((decimal(f["quantity"]) for f in selected), Decimal(0))
    notional = sum((decimal(f["quantity"]) * decimal(f["price"]) for f in selected), Decimal(0))
    average = notional / quantity if quantity else None
    quote = attempt["quote"]
    side_quote = decimal(quote["ask" if attempt["side"] == "BUY" else "bid"])
    slippage = (
        (average / side_quote - 1) * (1 if attempt["side"] == "BUY" else -1) * 10000
        if average is not None
        else None
    )
    times = sorted(datetime.fromisoformat(f["time"]) for f in selected)
    sent = datetime.fromisoformat(attempt["sent_at"]) if attempt.get("sent_at") else None
    return {
        "filled_qty": str(quantity),
        "filled_notional": str(notional),
        "avg_fill_price": str(average) if average is not None else None,
        "effective_slippage_bps": str(slippage) if slippage is not None else None,
        "spread_at_submission": str(decimal(quote["ask"]) - decimal(quote["bid"]))
        if quote.get("ask") and quote.get("bid")
        else None,
        "time_to_first_fill_seconds": (times[0] - sent).total_seconds() if times and sent else None,
        "time_to_last_fill_seconds": (times[-1] - sent).total_seconds() if times and sent else None,
        "time_basis": "broker_execution_time_minus_local_submit_time",
        "cancel_to_terminal_seconds": (
            datetime.fromisoformat(attempt["terminal_observed_at"])
            - datetime.fromisoformat(attempt["cancel_sent_at"])
        ).total_seconds()
        if attempt.get("terminal_observed_at") and attempt.get("cancel_sent_at")
        else None,
    }
