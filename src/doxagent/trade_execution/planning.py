"""Pure asset mapping and integer plans; no model or broker calls."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TradingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    environment: Literal["PAPER", "LIVE"] = "PAPER"
    cash_short_substitute_enabled: bool = False
    inverse_single_stock_etf: str | None = None
    hedge_enabled: bool = False
    hedge_ratio: Decimal = Field(default=Decimal("1"), gt=0)
    margin_index_etf: str | None = None
    cash_positive_index_etf: str | None = None
    cash_inverse_index_etf: str | None = None
    trailing_profit_enabled: bool = False
    banned_semantic_dates: tuple[date, ...] = ()

    @field_validator(
        "inverse_single_stock_etf",
        "margin_index_etf",
        "cash_positive_index_etf",
        "cash_inverse_index_etf",
    )
    @classmethod
    def ticker(cls, value):
        import re

        if value is None:
            return None
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", value):
            raise ValueError("invalid ETF ticker")
        return value

    @model_validator(mode="after")
    def substitute_fields(self):
        if self.cash_short_substitute_enabled and not self.inverse_single_stock_etf:
            raise ValueError("CASH_SHORT_SUBSTITUTE_MISSING")
        return self

    def validate_account(self, account_type):
        if self.hedge_enabled:
            fields = (
                [self.margin_index_etf]
                if account_type == "MARGIN"
                else [self.cash_positive_index_etf, self.cash_inverse_index_etf]
            )
            if not all(fields):
                raise ValueError("HEDGE_ASSET_MISSING")


def assets(ticker: str, direction: str, account_type: str, config: TradingConfig) -> list[dict]:
    config.validate_account(account_type)
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("invalid signal direction")
    if direction == "SHORT" and account_type == "CASH":
        if not config.cash_short_substitute_enabled or not config.inverse_single_stock_etf:
            raise ValueError("CASH_SHORT_SUBSTITUTE_DISABLED")
        main_ticker, side = config.inverse_single_stock_etf, "BUY"
    else:
        main_ticker, side = ticker, "BUY" if direction == "LONG" else "SELL"
    legs = [{"role": "MAIN", "traded_ticker": main_ticker, "side": side}]
    if config.hedge_enabled:
        if account_type == "MARGIN":
            hedge_ticker, hedge_side = (
                config.margin_index_etf,
                "SELL" if direction == "LONG" else "BUY",
            )
        else:
            hedge_ticker = (
                config.cash_inverse_index_etf
                if direction == "LONG"
                else config.cash_positive_index_etf
            )
            hedge_side = "BUY"
        if main_ticker == hedge_ticker:
            raise ValueError("MAIN_HEDGE_ASSET_CONFLICT")
        legs.append({"role": "HEDGE", "traded_ticker": hedge_ticker, "side": hedge_side})
    return legs


ALLOCATION_POLICY_VERSION = 2
HEDGE_TOLERANCE = Decimal("0.10")


class AllocationError(ValueError):
    """A planning rejection with bounded, persistable input/candidate evidence."""

    def __init__(self, reason, **diagnostic):
        super().__init__(reason)
        self.diagnostic = {"reason": reason, **diagnostic}


def ratio_bounds(ratio, policy_version=ALLOCATION_POLICY_VERSION):
    if policy_version < 2:
        return ratio * Decimal(".9"), ratio * Decimal("1.1")
    return max(Decimal(0), ratio - HEDGE_TOLERANCE), ratio + HEDGE_TOLERANCE


def _closest(low, high, target):
    candidates = {
        max(low, min(high, int(target))),
        max(low, min(high, int(target.to_integral_value(rounding=ROUND_CEILING)))),
    }
    return min(candidates, key=lambda q: (abs(Decimal(q) - target), q))


def integer_pair(
    budget: Decimal,
    ratio: Decimal,
    main_price: Decimal,
    hedge_price: Decimal,
    *,
    policy_version=ALLOCATION_POLICY_VERSION,
):
    if min(budget, ratio, main_price, hedge_price) <= 0:
        raise AllocationError("NO_VALID_INTEGER_COMBINATION", subreason="NON_POSITIVE_INPUT")
    main_cap = budget / (1 + ratio)
    lower, upper = ratio_bounds(ratio, policy_version)
    diagnostic = dict(
        nominal_budget_usd=str(budget),
        main_cap=str(main_cap),
        main_price=str(main_price),
        hedge_price=str(hedge_price),
        target_ratio=str(ratio),
        ratio_lower=str(lower),
        ratio_upper=str(upper),
        main_quantity_max=int(main_cap // main_price),
    )
    # The shared remainder grows as MAIN shrinks: a jump based on a fixed
    # hedge cap can skip the largest feasible MAIN (e.g. 1400/.4/100/222).
    for qm in range(diagnostic["main_quantity_max"], 0, -1):
        main = qm * main_price
        hedge_room = budget - (main if policy_version >= 2 else main_cap)
        low = max(1, int((lower * main / hedge_price).to_integral_value(rounding=ROUND_CEILING)))
        high = min(
            int(hedge_room // hedge_price),
            int((upper * main / hedge_price).to_integral_value(rounding=ROUND_FLOOR)),
        )
        diagnostic.update(
            candidate_main_quantity=qm, hedge_quantity_low=low, hedge_quantity_high=high
        )
        if low <= high:
            qh = _closest(low, high, ratio * main / hedge_price)
            plan = dict(main_quantity=qm, hedge_quantity=qh, main_cap=str(main_cap))
            if policy_version < 2:
                plan["hedge_cap"] = str(budget - main_cap)
            else:
                plan.update(
                    allocation_policy_version=2,
                    hedge_tolerance_pp=10,
                    nominal_budget_usd=str(budget),
                    hedge_theoretical_usd=str(budget - main_cap),
                    main_notional_usd=str(main),
                    hedge_notional_usd=str(qh * hedge_price),
                    **{
                        k: v
                        for k, v in diagnostic.items()
                        if k not in {"main_cap", "nominal_budget_usd"}
                    },
                    actual_ratio=str(qh * hedge_price / main),
                )
            return plan
    subreason = (
        "MAIN_BELOW_ONE_SHARE"
        if not diagnostic["main_quantity_max"]
        else "TWO_LEGS_UNAFFORDABLE"
        if main_price + hedge_price > budget
        else "INTEGER_RATIO_GAP"
    )
    raise AllocationError("NO_VALID_INTEGER_COMBINATION", subreason=subreason, **diagnostic)


def hedge_quantity(
    main_notional: Decimal,
    cap: Decimal,
    ratio: Decimal,
    price: Decimal,
    spent=Decimal(0),
    *,
    policy_version=ALLOCATION_POLICY_VERSION,
):
    lower, upper = ratio_bounds(ratio, policy_version)
    lower_amount, upper_amount = lower * main_notional, upper * main_notional
    diagnostic = dict(
        main_notional_usd=str(main_notional),
        hedge_spent_usd=str(spent),
        hedge_price=str(price),
        hedge_available_usd=str(cap - spent),
        target_ratio=str(ratio),
        ratio_lower=str(lower),
        ratio_upper=str(upper),
        actual_ratio=str(spent / main_notional) if main_notional else None,
    )
    if policy_version >= 2:
        if main_notional <= 0:
            raise AllocationError(
                "HEDGE_UNREPRESENTABLE_AFTER_PARTIAL", subreason="MAIN_NOT_FILLED", **diagnostic
            )
        if spent > upper_amount:
            raise AllocationError("HEDGE_ABOVE_RATIO_LIMIT", **diagnostic)
        if spent > 0 and lower_amount <= spent <= upper_amount:
            return 0
    target = (main_notional * ratio - spent) / price
    low = max(
        0 if spent and policy_version < 2 else 1,
        int(((lower_amount - spent) / price).to_integral_value(rounding=ROUND_CEILING)),
    )
    high = min(
        int((cap - spent) // price),
        int(((upper_amount - spent) / price).to_integral_value(rounding=ROUND_FLOOR)),
    )
    if low > high:
        raise AllocationError(
            "HEDGE_UNREPRESENTABLE_AFTER_PARTIAL",
            subreason="NO_VALID_INCREMENT",
            hedge_quantity_low=low,
            hedge_quantity_high=high,
            **diagnostic,
        )
    return _closest(low, high, target)
