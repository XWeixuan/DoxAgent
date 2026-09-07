"""Pure price/quantity/attempt decisions, preserving the approved strategy."""

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from .schema import Strategy


def decimal(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("non-finite execution amount")
    return result


def round_price(price: Decimal, side: str, rules: list[dict[str, Any]]) -> Decimal:
    rules = sorted(rules, key=lambda item: decimal(item["low_edge"]))
    if not rules or decimal(rules[0]["low_edge"]) > price:
        raise ValueError("PRICE_RULE_UNAVAILABLE")
    for _ in range(len(rules) + 2):
        rule = next(item for item in reversed(rules) if decimal(item["low_edge"]) <= price)
        edge, tick = decimal(rule["low_edge"]), decimal(rule["increment"])
        if tick <= 0:
            raise ValueError("invalid increment")
        rounded = (
            edge
            + ((price - edge) / tick).to_integral_value(
                rounding=ROUND_CEILING if side == "BUY" else ROUND_FLOOR
            )
            * tick
        )
        next_rule = next(item for item in reversed(rules) if decimal(item["low_edge"]) <= rounded)
        if next_rule == rule:
            return rounded
        price = rounded
    raise ValueError("unstable price rule")


def order_type(attempts: list[dict[str, Any]], session: str) -> str | None:
    if session == "CLOSED":
        return None
    if any(item["order_type"] == "MKT" for item in attempts):
        return None
    if len(attempts) >= (2 if session == "RTH" else 3):
        return None
    return "MKT" if attempts and session == "RTH" else "LMT"


def price_and_quantity(
    *,
    side: str,
    kind: str,
    retry: bool,
    quote: dict[str, Any],
    rules: list[dict[str, Any]],
    strategy: Strategy,
    remaining_notional: Decimal | None,
    remaining_qty: int = 0,
) -> dict[str, Any]:
    reference = decimal(quote["ask" if side == "BUY" else "bid"])
    if reference <= 0:
        raise ValueError("QUOTE_UNAVAILABLE")
    tolerance = strategy.non_rth_retry_tolerance if retry else strategy.initial_tolerance
    cap = reference * (1 + tolerance if side == "BUY" else 1 - tolerance)
    limit = round_price(cap, side, rules) if kind == "LMT" else None
    sizing = limit if limit is not None else reference
    quantity = (
        int(max(Decimal(0), remaining_notional) // sizing)
        if remaining_notional is not None
        else remaining_qty
    )
    return {
        "quantity": quantity,
        "limit_price": str(limit) if limit is not None else None,
        "sizing_price": str(sizing),
        "raw_cap": str(cap) if limit is not None else None,
        "tolerance": str(tolerance) if limit is not None else None,
    }
