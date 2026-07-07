"""Configurable model pricing for model usage cost audit."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doxagent.model_usage.schema import JsonObject, ModelUsageEvent
from doxagent.settings import DoxAgentSettings

DEFAULT_PRICING_PATH = Path(__file__).with_name("default_pricing.json")


@dataclass(frozen=True)
class ModelPricingResult:
    cost_cny: float
    cost_usd: float
    input_cny_per_million: float
    output_cny_per_million: float
    discount_rate: float
    cny_usd_rate: float
    pricing_version: str
    pricing_source: str | None


class ModelPricingCatalog:
    """Resolve model usage events to CNY/USD costs using a JSON price table."""

    def __init__(
        self,
        config: JsonObject,
        *,
        discount_rate: float,
        cny_usd_rate: float,
    ) -> None:
        if discount_rate < 0:
            raise ValueError("discount_rate must be >= 0")
        if cny_usd_rate <= 0:
            raise ValueError("cny_usd_rate must be > 0")
        self.config = config
        self.discount_rate = discount_rate
        self.cny_usd_rate = cny_usd_rate
        self.version = str(config.get("version") or "unknown")
        source = config.get("source")
        self.source = str(source) if isinstance(source, str) and source.strip() else None
        entries = config.get("entries")
        self.entries = entries if isinstance(entries, list) else []

    @classmethod
    def from_settings(
        cls,
        settings: DoxAgentSettings | None = None,
    ) -> ModelPricingCatalog:
        resolved = settings or DoxAgentSettings()
        path = (
            Path(resolved.model_pricing_config_path)
            if resolved.model_pricing_config_path
            else DEFAULT_PRICING_PATH
        )
        return cls(
            _load_pricing_config(path),
            discount_rate=resolved.model_pricing_discount_rate,
            cny_usd_rate=resolved.model_pricing_cny_usd_rate,
        )

    def price(self, event: ModelUsageEvent) -> ModelPricingResult | None:
        entry = self._find_entry(event.provider, event.model)
        if entry is None:
            return None
        tier = _find_tier(entry, event.input_tokens)
        if tier is None:
            return None
        input_price = _float(tier.get("input_cny_per_million"))
        output_price = _float(tier.get("output_cny_per_million"))
        if input_price is None or output_price is None:
            return None
        cost_cny = (
            (event.input_tokens / 1_000_000 * input_price)
            + (event.output_tokens / 1_000_000 * output_price)
        ) * self.discount_rate
        cost_usd = cost_cny / self.cny_usd_rate
        return ModelPricingResult(
            cost_cny=cost_cny,
            cost_usd=cost_usd,
            input_cny_per_million=input_price,
            output_cny_per_million=output_price,
            discount_rate=self.discount_rate,
            cny_usd_rate=self.cny_usd_rate,
            pricing_version=self.version,
            pricing_source=self.source,
        )

    def _find_entry(self, provider: str, model: str) -> JsonObject | None:
        normalized_provider = provider.strip().lower()
        normalized_model = model.strip().lower()
        for entry in self.entries:
            if not isinstance(entry, dict):
                continue
            providers = entry.get("providers")
            provider_values = {
                str(item).strip().lower()
                for item in providers
                if isinstance(item, str) and item.strip()
            } if isinstance(providers, list) else set()
            if provider_values and normalized_provider not in provider_values:
                continue
            names = {str(entry.get("model") or "").strip().lower()}
            aliases = entry.get("aliases")
            if isinstance(aliases, list):
                names.update(
                    str(item).strip().lower()
                    for item in aliases
                    if isinstance(item, str) and item.strip()
                )
            if normalized_model in names:
                return dict(entry)
        return None


def _load_pricing_config(path: Path) -> JsonObject:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("model pricing config must be a JSON object.")
    return value


def _find_tier(entry: JsonObject, input_tokens: int) -> JsonObject | None:
    tiers = entry.get("tiers")
    if not isinstance(tiers, list):
        return None
    normalized_input = max(0, input_tokens)
    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        min_exclusive = _int(tier.get("min_input_tokens_exclusive")) or 0
        max_tokens = _int(tier.get("max_input_tokens"))
        if normalized_input == 0 and min_exclusive == 0:
            return dict(tier)
        if normalized_input <= min_exclusive:
            continue
        if max_tokens is None or normalized_input <= max_tokens:
            return dict(tier)
    return None


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
