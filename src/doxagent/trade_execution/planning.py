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


def integer_pair(budget: Decimal, ratio: Decimal, main_price: Decimal, hedge_price: Decimal):
    if min(budget, ratio, main_price, hedge_price) <= 0:
        raise ValueError("invalid plan input")
    main_cap = budget / (1 + ratio)
    hedge_cap = budget - main_cap
    qm = int(main_cap // main_price)
    while qm >= 1:
        target = ratio * qm * main_price / hedge_price
        low = max(1, int((target * Decimal("0.9")).to_integral_value(rounding=ROUND_CEILING)))
        high = min(
            int(hedge_cap // hedge_price),
            int((target * Decimal("1.1")).to_integral_value(rounding=ROUND_FLOOR)),
        )
        if low <= high:
            candidates = {
                max(low, min(high, int(target))),
                max(low, min(high, int(target.to_integral_value(rounding=ROUND_CEILING)))),
            }
            qh = min(candidates, key=lambda q: (abs(Decimal(q) - target), q))
            return {
                "main_quantity": qm,
                "hedge_quantity": qh,
                "main_cap": str(main_cap),
                "hedge_cap": str(hedge_cap),
            }
        if high < 1:
            break
        qm = min(
            qm - 1,
            int(
                (high * hedge_price / (Decimal("0.9") * ratio * main_price)).to_integral_value(
                    rounding=ROUND_FLOOR
                )
            ),
        )
    raise ValueError("NO_VALID_INTEGER_COMBINATION")


def hedge_quantity(
    main_notional: Decimal, cap: Decimal, ratio: Decimal, price: Decimal, spent=Decimal(0)
):
    target = (main_notional * ratio - spent) / price
    low = max(
        0 if spent else 1,
        int(
            ((main_notional * ratio * Decimal(".9") - spent) / price).to_integral_value(
                rounding=ROUND_CEILING
            )
        ),
    )
    high = min(
        int((cap - spent) // price),
        int(
            ((main_notional * ratio * Decimal("1.1") - spent) / price).to_integral_value(
                rounding=ROUND_FLOOR
            )
        ),
    )
    if low > high:
        raise ValueError("HEDGE_UNREPRESENTABLE_AFTER_PARTIAL")
    candidates = {
        max(low, min(high, int(target))),
        max(low, min(high, int(target.to_integral_value(rounding=ROUND_CEILING)))),
    }
    return min(candidates, key=lambda q: (abs(Decimal(q) - target), q))
