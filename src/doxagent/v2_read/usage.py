"""Shared invocation accounting. Missing receipts stay unknown, never zero."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from doxagent.api_v2.dto import available, missing

from .pricing import normalize, price


def accounting(model, provider, channel, started_at, usage):
    if provider == "bailian" and model == "qwen3.7-text-embedding":
        # An embedding endpoint has no generated text or prompt cache. These are
        # protocol-inapplicable zeros, not guesses about an incomplete chat response.
        usage = {**usage, "output_tokens": 0, "cached_input_tokens": 0}
    normalized = normalize(provider, usage, ttl=usage.get("cache_ttl"))
    money = price(
        model,
        provider,
        started_at,
        {**usage, **normalized},
        channel=channel,
        endpoint=usage.get("provider_endpoint"),
        region=usage.get("region"),
    )
    return normalized, money


def cost(model: str, at: datetime, usage: dict[str, Any], *, channel="API"):
    if channel == "CODEX":
        return missing("CODEX_SUBSCRIPTION_NOT_PRICED", "NOT_APPLICABLE")
    if channel != "API":
        raise ValueError("invalid usage channel")
    keys = ("input_tokens", "cached_input_tokens", "output_tokens")
    if any(usage.get(k) is not None and (type(usage[k]) is not int or usage[k] < 0) for k in keys):
        return missing("INVALID_USAGE", "UNAVAILABLE")
    i, c, o = (usage.get(k) for k in keys)
    if (
        i is not None
        and c is not None
        and c > i
        or usage.get("total_tokens") is not None
        and (
            type(usage["total_tokens"]) is not int
            or i is not None
            and o is not None
            and usage["total_tokens"] != i + o
        )
    ):
        return missing("INVALID_USAGE", "UNAVAILABLE")
    normalized, money = accounting(model, "bailian", channel, at, usage)
    value = money["total_cost_usd"]
    if value is not None:
        return available(format(value, "f"))
    reason = (
        "CACHED_INPUT_MISSING" if i is not None and o is not None and c is None else money["reason"]
    )
    return missing(
        reason or "NOT_RECORDED",
        "UNAVAILABLE" if reason == "UNKNOWN_MODEL_PRICE" else "NOT_RECORDED",
    )


def observations(model, provider, channel, started_at, usage):
    normalized, money = accounting(model, provider, channel, started_at, usage)
    return observation_values(normalized, money, channel)


def observation_values(normalized, money, channel):
    """Reduce an already frozen quote without repricing or losing reported charges."""
    values = {key: value for key, value in normalized.items() if key != "normalization_version"}
    values.update({key: value for key, value in money.items() if key.endswith("_cost_usd")})
    values["api_token_cost"] = money["total_cost_usd"]
    incoming, cached = normalized["input_tokens"], normalized["cached_input_tokens"]
    cache_inapplicable = money.get("usage_kind") == "EMBEDDING"
    pair_known = incoming is not None and cached is not None and not cache_inapplicable
    values.update(
        cache_ratio_input_tokens=incoming if pair_known else None,
        cache_ratio_read_tokens=cached if pair_known else None,
        requests=1,
        usage_known_attempts=int(normalized["total_tokens"] is not None),
        unpriced_requests=int(channel == "API" and money["total_cost_usd"] is None),
        reported_cost_attempts=int(money["cost_basis"] == "REPORTED"),
        estimated_cost_attempts=int(money["cost_basis"] == "ESTIMATED"),
        normalization_v1_attempts=1,
        cache_inapplicable_attempts=int(cache_inapplicable),
        unknown_model_price_attempts=int(
            channel == "API" and money["reason"] == "UNKNOWN_MODEL_PRICE"
        ),
    )
    counters = {
        "requests",
        "usage_known_attempts",
        "unpriced_requests",
        "reported_cost_attempts",
        "estimated_cost_attempts",
        "normalization_v1_attempts",
        "cache_inapplicable_attempts",
        "unknown_model_price_attempts",
    }
    for key, value in list(values.items()):
        if key not in counters:
            values[key + "_samples"] = int(value is not None)
    # Original-currency totals and field coverage; not an FX-converted invoice.
    currency = money["currency"]
    for code in ("CNY", "USD"):
        value = money["total_cost_usd"]
        values["cost_native_" + code.lower()] = (
            (
                money["reported_cost_native"]
                if money["cost_basis"] == "REPORTED"
                else money["calculated_cost_native"]
            )
            if currency == code and value is not None
            else None
        )
        values["cost_native_" + code.lower() + "_samples"] = int(
            currency == code and value is not None
        )
    return values


def fact_observations(data):
    money = data.get("accounting")
    if money:
        return observation_values(normalize(data["provider"], data["usage"]), money, data["scope"])
    started = (data.get("timing", {}).get("first_started_at") or {}).get("value")
    return observations(data["model"], data["provider"], data["scope"], started, data["usage"])
