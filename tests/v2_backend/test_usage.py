from datetime import UTC, datetime
from decimal import Decimal

from doxagent.gateway.providers import _usage_from_mapping
from doxagent.v2_read.usage import cost


def test_cost_preserves_missing_cache_zero_and_fixed_day_night_prices():
    usage = {"input_tokens": 1_000_000, "output_tokens": 0, "cached_input_tokens": 0}
    before = datetime(2026, 9, 7, 23, 59, tzinfo=UTC)
    after = datetime(2026, 9, 8, 0, tzinfo=UTC)
    model = "deepseek-v4-flash-0731"
    assert Decimal(cost(model, after, usage)["value"]) == Decimal(3) / Decimal("6.8")
    assert Decimal(cost(model, before, usage)["value"]) == Decimal("1.5") / Decimal("6.8")
    assert cost(model, after, {**usage, "cached_input_tokens": None})["reason"] == (
        "CACHED_INPUT_MISSING"
    )
    assert cost(model, after, {**usage, "cached_input_tokens": True})["reason"] == "INVALID_USAGE"
    assert cost(model, after, {**usage, "total_tokens": 9})["reason"] == "INVALID_USAGE"
    assert cost(model, after, usage, channel="CODEX")["reason"] == "CODEX_SUBSCRIPTION_NOT_PRICED"
    result = _usage_from_mapping(
        {"input_tokens": 0, "output_tokens": 0, "input_tokens_details": {"cached_tokens": 0}}
    )
    assert result.input_tokens == result.output_tokens == result.cached_input_tokens == 0
