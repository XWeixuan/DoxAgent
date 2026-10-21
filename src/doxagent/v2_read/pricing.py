"""Small, receipt-first cost ledger. No I/O beyond loading versioned tariffs once."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

RULES = json.loads(Path(__file__).with_name("pricing_rules.json").read_text(encoding="utf-8"))
VERSION = RULES["version"]
FX = Decimal(RULES["usd_cny"])
MILLION = Decimal(1_000_000)


def count(value):
    return value if type(value) is int and value >= 0 else None


def amount(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except (InvalidOperation, ValueError):
        return None


def normalize(provider: str, usage: dict, *, ttl: str | None = None) -> dict:
    """Normalized input is total input, including reads and writes on every channel."""
    raw = usage.get("provider_usage") or {}
    if not isinstance(raw, dict):
        raw = {}
    native = raw.get("usage", raw)
    native = native if isinstance(native, dict) else {}
    vertex = provider == "vertex" and not usage.get("normalization_version")
    result = {
        key: count(usage.get(key))
        for key in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "cache_write_input_tokens",
            "cache_write_5m_input_tokens",
            "cache_write_1h_input_tokens",
            "reasoning_tokens",
        )
    }
    if vertex:
        uncached = count(native.get("input_tokens"))
        read = count(native.get("cache_read_input_tokens"))
        write = count(native.get("cache_creation_input_tokens"))
        result.update(
            cached_input_tokens=read,
            cache_write_input_tokens=write,
            output_tokens=count(native.get("output_tokens")),
        )
        result["input_tokens"] = (
            sum((uncached, read, write))
            if all(v is not None for v in (uncached, read, write))
            else None
        )
        creation = native.get("cache_creation") or {}
        result["cache_write_5m_input_tokens"] = count(creation.get("ephemeral_5m_input_tokens"))
        result["cache_write_1h_input_tokens"] = count(creation.get("ephemeral_1h_input_tokens"))
    else:
        if result["input_tokens"] is None:
            result["input_tokens"] = count(native.get("prompt_tokens"))
        if result["output_tokens"] is None:
            result["output_tokens"] = count(native.get("completion_tokens"))
        details = native.get("prompt_tokens_details") or native.get("input_tokens_details") or {}
        if result["cached_input_tokens"] is None:
            result["cached_input_tokens"] = count(details.get("cached_tokens"))
        if result["cache_write_input_tokens"] is None:
            result["cache_write_input_tokens"] = count(details.get("cache_write_tokens"))
        if result["reasoning_tokens"] is None:
            result["reasoning_tokens"] = count(
                (native.get("completion_tokens_details") or {}).get("reasoning_tokens")
            )
    if raw.get("output_complete") is False:
        result["output_tokens"] = None
    write = result["cache_write_input_tokens"]
    if write is not None and all(
        result[k] is None for k in ("cache_write_5m_input_tokens", "cache_write_1h_input_tokens")
    ):
        if write == 0 or ttl in {"5m", "1h"}:
            result["cache_write_5m_input_tokens"] = write if ttl == "5m" else 0
            result["cache_write_1h_input_tokens"] = write if ttl == "1h" else 0
    write5, write1 = result["cache_write_5m_input_tokens"], result["cache_write_1h_input_tokens"]
    if write is not None and write5 is not None and write1 is not None and write5 + write1 != write:
        result["cache_write_5m_input_tokens"] = None
        result["cache_write_1h_input_tokens"] = None
    incoming, read = result["input_tokens"], result["cached_input_tokens"]
    cached_write = write if provider in {"vertex", "openrouter"} else 0
    uncached = (
        incoming - read - cached_write
        if all(v is not None for v in (incoming, read, cached_write))
        else None
    )
    if uncached is not None and uncached < 0:
        result["cached_input_tokens"] = None
        result["cache_write_input_tokens"] = None
        uncached = None
    result["input_tokens_uncached"] = uncached
    outgoing = result["output_tokens"]
    result["total_tokens"] = (
        incoming + outgoing if incoming is not None and outgoing is not None else None
    )
    if (
        usage.get("total_tokens") is not None
        and result["total_tokens"] is not None
        and count(usage["total_tokens"]) != result["total_tokens"]
    ):
        result.update(
            input_tokens=None,
            output_tokens=None,
            cached_input_tokens=None,
            total_tokens=None,
            input_tokens_uncached=None,
        )
    result["normalization_version"] = VERSION
    return result


def price(
    model: str,
    provider: str,
    at: str | datetime | None,
    usage: dict,
    *,
    channel="API",
    endpoint=None,
    region=None,
) -> dict:
    """Known components survive missing siblings; selected total is never a partial sum."""
    empty = {
        k: None
        for k in (
            "noncached_input_cost_usd",
            "cached_input_cost_usd",
            "cache_write_cost_usd",
            "input_cost_usd",
            "output_cost_usd",
            "total_cost_usd",
        )
    }
    result = {
        **empty,
        "price_version": VERSION,
        "fx_version": RULES["fx_version"],
        "fx_rate": str(FX),
        "cost_basis": None,
        "reason": "UNKNOWN_MODEL_PRICE",
        "price_rule_id": f"{provider}:{model}:{region or endpoint or 'unspecified'}:{VERSION}",
        "price_source": RULES["sources"][
            "haiku"
            if provider in {"vertex", "openrouter"}
            else "glm"
            if model == "ZHIPU/GLM-5.3-FlashX"
            else "bailian"
        ],
        "tariff_as_of": "2026-10-21",
        "reported_cost_native": None,
        "calculated_cost_native": None,
        "currency": None,
    }
    if channel != "API":
        return {**result, "reason": "CODEX_SUBSCRIPTION_NOT_PRICED"}
    rule, multiplier = None, Decimal(1)
    if provider == "bailian":
        rule = RULES["bailian"].get(model)
        if rule and region and region not in {"cn-beijing", "beijing"}:
            rule = None
        if (
            rule
            and endpoint
            and any(part in endpoint for part in ("dashscope-intl", "dashscope-us"))
        ):
            rule = None
        if rule and "offpeak_multiplier" in rule:
            timestamp = datetime.fromisoformat(at) if isinstance(at, str) else at
            if timestamp is None or timestamp.tzinfo is None:
                rule = None
                result["reason"] = "NOT_RECORDED"
            elif not 8 <= timestamp.astimezone(ZoneInfo("Asia/Shanghai")).hour < 22:
                multiplier = Decimal(rule["offpeak_multiplier"])
    elif provider in {"vertex", "openrouter"} and model in {
        "claude-haiku-5-5",
        "anthropic/claude-haiku-5.5",
    }:
        incoming = usage.get("input_tokens")
        if incoming is not None:
            rule = RULES["haiku"][
                "long" if incoming >= RULES["haiku"]["long_input_from"] else "short"
            ]
            if provider == "vertex":
                if not endpoint and not region:
                    rule = None
                elif (region or endpoint) != "global" and "/locations/global/" not in (
                    endpoint or ""
                ):
                    multiplier = Decimal(RULES["haiku"]["non_global_multiplier"])
    if provider == "openrouter" and endpoint != "google-vertex/global":
        rule = None  # A missing billed total does not prove which upstream tariff applied.
    if rule:
        currency = rule["currency"]
        result["usage_kind"] = rule.get("kind", "GENERATION")
        if result["usage_kind"] == "EMBEDDING":
            result["price_source"] = RULES["sources"]["embedding"]
        divisor = MILLION * (FX if currency == "CNY" else 1)

        def component(tokens, key):
            return (
                Decimal(tokens) * Decimal(rule[key]) * multiplier / divisor
                if tokens is not None
                else None
            )

        result["currency"] = currency
        result["noncached_input_cost_usd"] = component(usage.get("input_tokens_uncached"), "input")
        result["cached_input_cost_usd"] = component(usage.get("cached_input_tokens"), "read")
        result["output_cost_usd"] = component(usage.get("output_tokens"), "output")
        write = Decimal(0)
        if "write_5m" in rule:
            writes = [
                component(usage.get("cache_write_5m_input_tokens"), "write_5m"),
                component(usage.get("cache_write_1h_input_tokens"), "write_1h"),
            ]
            write = sum(writes) if all(v is not None for v in writes) else None
            result["cache_write_cost_usd"] = write
        inputs = [result["noncached_input_cost_usd"], result["cached_input_cost_usd"], write]
        if all(v is not None for v in inputs):
            result["input_cost_usd"] = sum(inputs)
        if result["input_cost_usd"] is not None and result["output_cost_usd"] is not None:
            pairs = [
                (usage["input_tokens_uncached"], "input"),
                (usage["cached_input_tokens"], "read"),
                (usage["output_tokens"], "output"),
            ]
            if "write_5m" in rule:
                pairs.extend(
                    (
                        (usage["cache_write_5m_input_tokens"], "write_5m"),
                        (usage["cache_write_1h_input_tokens"], "write_1h"),
                    )
                )
            result["calculated_cost_native"] = (
                sum((Decimal(n) * Decimal(rule[key]) * multiplier for n, key in pairs), Decimal(0))
                / MILLION
            )
            result["total_cost_usd"] = result["calculated_cost_native"] / (
                FX if currency == "CNY" else 1
            )
            result.update(cost_basis="ESTIMATED", reason=None)
        else:
            result["reason"] = "NOT_RECORDED"
    # The OpenRouter cost is the charged total, not an extra fee on top of cost_details.
    raw = usage.get("provider_usage") or {}
    native = raw.get("usage", raw) if isinstance(raw, dict) else {}
    reported = amount(native.get("cost")) if provider == "openrouter" else None
    if reported is not None:
        result.update(
            total_cost_usd=reported,
            reported_cost_native=reported,
            currency="USD",
            cost_basis="REPORTED",
            reason=None,
        )
        # A tariff estimate is not a reported breakdown if the billed total differs.
        calculated = result["calculated_cost_native"]
        if calculated is None or abs(calculated - reported) > Decimal("0.000000001"):
            for key in empty:
                if key != "total_cost_usd":
                    result[key] = None
    return result
