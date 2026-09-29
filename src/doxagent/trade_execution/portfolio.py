"""Account-wide USD exposure and margin admission arithmetic."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .strategy import decimal


def account_funds(snapshot: dict[str, Any]) -> dict[str, Decimal]:
    values = snapshot.get("account_values") or {}
    if str(values.get("AccountReady", "")).lower() not in {"true", "1"}:
        raise ValueError("BROKER_ACCOUNT_NOT_READY")
    result = {}
    for key in ("NetLiquidation", "AvailableFunds", "LookAheadAvailableFunds"):
        item = values.get(key)
        if not isinstance(item, dict) or item.get("currency") != "USD":
            raise ValueError("BROKER_ACCOUNT_VALUE_UNAVAILABLE")
        result[key] = decimal(item["value"])
    if result["NetLiquidation"] <= 0:
        raise ValueError("BROKER_EQUITY_UNAVAILABLE")
    return result


def gross_exposure(snapshot: dict[str, Any]) -> tuple[Decimal, dict[str, Decimal]]:
    """Broker portfolio, including manual positions; zero positions contribute nothing."""
    positions = {str(key): decimal(qty) for key, qty in snapshot.get("positions", {}).items()}
    rows = snapshot.get("portfolio")
    if not isinstance(rows, list):
        raise ValueError("BROKER_PORTFOLIO_UNAVAILABLE")
    seen: set[str] = set()
    by_ticker: dict[str, Decimal] = {}
    for row in rows:
        con_id = str(row["con_id"])
        qty = decimal(row["quantity"])
        if con_id in seen or qty != positions.get(con_id, Decimal(0)):
            raise ValueError("BROKER_PORTFOLIO_POSITION_MISMATCH")
        seen.add(con_id)
        if not qty:
            continue
        if row.get("security_type") != "STK" or row.get("currency") != "USD":
            raise ValueError("BROKER_PORTFOLIO_VALUATION_UNSUPPORTED")
        value = abs(decimal(row["market_value"]))
        symbol = str(row["symbol"]).upper()
        by_ticker[symbol] = by_ticker.get(symbol, Decimal(0)) + value
    if any(qty and con_id not in seen for con_id, qty in positions.items()):
        raise ValueError("BROKER_PORTFOLIO_INCOMPLETE")
    return sum(by_ticker.values(), Decimal(0)), by_ticker


def margin_after(
    funds: dict[str, Decimal], what_if: dict[str, Any], equity: Decimal
) -> tuple[Decimal, Decimal]:
    if what_if.get("status") != "VALIDATED":
        raise ValueError("MARGIN_CHECK_UNAVAILABLE")
    rows = what_if.get("rows") or []
    if len(rows) != 1:
        raise ValueError("MARGIN_CHECK_UNAVAILABLE")
    row = rows[0]
    message = str(row.get("reject_reason") or row.get("warning") or "").lower()
    if any(part in message for part in ("margin", "insufficient funds", "equity with loan")):
        raise ValueError("INSUFFICIENT_MARGIN")
    if row.get("reject_reason"):
        raise ValueError("MARGIN_CHECK_UNAVAILABLE")
    change = decimal(row["initial_margin_change"])
    after = funds["AvailableFunds"] - change
    look_ahead = funds["LookAheadAvailableFunds"] - change
    if min(after, look_ahead) < equity * Decimal("0.20"):
        raise ValueError("INSUFFICIENT_MARGIN")
    return after, look_ahead
