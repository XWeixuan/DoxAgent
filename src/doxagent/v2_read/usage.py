"""Fixed contract pricing; missing provider usage never becomes an invented zero."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from doxagent.api_v2.dto import available, missing


def cost(
    model: str, at: datetime, usage: dict[str, Any], *, channel: str = "API"
) -> dict[str, Any]:
    if channel == "CODEX":
        return missing("CODEX_SUBSCRIPTION_NOT_PRICED", "NOT_APPLICABLE")
    if channel != "API":
        raise ValueError("invalid usage channel")
    if model == "qwen3.8-flash":
        prices = ("0.8", "0.1", "2.7")
    elif model == "deepseek-v4-flash-0731":
        if at.tzinfo is None:
            raise ValueError("pricing requires an aware invocation timestamp")
        hour = at.astimezone(ZoneInfo("Asia/Shanghai")).hour
        prices = ("3", "0.3", "9") if 8 <= hour < 22 else ("1.5", "0.15", "4.5")
    else:
        return missing("UNKNOWN_MODEL_PRICE", "UNAVAILABLE")
    observed = [usage.get(key) for key in ("input_tokens", "cached_input_tokens", "output_tokens")]
    if any(v is not None and (type(v) is not int or v < 0) for v in observed):
        return missing("INVALID_USAGE", "UNAVAILABLE")
    incoming, cached, outgoing = observed
    if incoming is None or outgoing is None:
        return missing("NOT_RECORDED")
    if cached is None:
        return missing("CACHED_INPUT_MISSING")
    if cached > incoming:
        return missing("INVALID_USAGE", "UNAVAILABLE")
    total = usage.get("total_tokens")
    if total is not None and (type(total) is not int or total != incoming + outgoing):
        return missing("INVALID_USAGE", "UNAVAILABLE")
    amounts = (incoming - cached, cached, outgoing)
    cny = sum((Decimal(p) * n for p, n in zip(prices, amounts, strict=True)), Decimal(0))
    return available(format(cny / Decimal(1_000_000) / Decimal("6.8"), "f"))


def observations(
    model: str, provider: str, channel: str, started_at: str | None, usage: dict[str, Any]
) -> dict[str, Any]:
    """Independent known components; missing input never erases observed output."""
    keys = ("input_tokens", "cached_input_tokens", "output_tokens")
    values = {key: usage.get(key) for key in keys}
    invalid = any(
        value is not None and (type(value) is not int or value < 0) for value in values.values()
    )
    if invalid:
        values = {key: None for key in keys}
    incoming, cached, outgoing = (values[key] for key in keys)
    invalid |= incoming is not None and cached is not None and cached > incoming
    total = incoming + outgoing if incoming is not None and outgoing is not None else None
    invalid |= usage.get("total_tokens") is not None and (
        type(usage["total_tokens"]) is not int
        or total is not None
        and usage["total_tokens"] != total
    )
    if invalid:
        values = {key: None for key in keys}
        incoming = cached = outgoing = total = None
    values["total_tokens"] = total
    for name in (
        "noncached_input_cost_usd",
        "cached_input_cost_usd",
        "input_cost_usd",
        "output_cost_usd",
        "total_cost_usd",
    ):
        values[name] = None
    if channel == "API" and provider == "bailian" and started_at and not invalid:
        at = datetime.fromisoformat(started_at)

        def component(i, c, o):
            return cost(
                model, at, {"input_tokens": i, "cached_input_tokens": c, "output_tokens": o}
            )["value"]

        if incoming is not None and cached is not None:
            values["noncached_input_cost_usd"] = component(incoming - cached, 0, 0)
            values["cached_input_cost_usd"] = component(cached, cached, 0)
            values["input_cost_usd"] = component(incoming, cached, 0)
        if outgoing is not None:
            values["output_cost_usd"] = component(0, 0, outgoing)
        if incoming is not None and cached is not None and outgoing is not None:
            values["total_cost_usd"] = component(incoming, cached, outgoing)
    values["api_token_cost"] = values["total_cost_usd"]
    values["requests"] = 1
    values["unpriced_requests"] = int(channel == "API" and values["total_cost_usd"] is None)
    values.update(
        {
            key + "_samples": int(amount is not None)
            for key, amount in list(values.items())
            if key not in {"requests", "unpriced_requests"}
        }
    )
    return values
