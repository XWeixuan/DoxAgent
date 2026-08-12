"""Build the single, role-scoped horizontal input shown to a D1 agent."""

from __future__ import annotations

from typing import Any

from doxagent.horizontal_collection.registry import (
    default_collection_target_registry,
    default_metric_registry,
)
from doxagent.horizontal_collection.schema import (
    CollectionTargetDefinition,
    HorizontalCollectionBundle,
)

_ROLE_PREFIXES = {
    "c1": ("c1_",),
    "c2": ("c2_",),
    "c3": ("c3_",),
    "o4_b": ("o4_",),
    "o4_a": ("o4_", "c1_"),
}

_OPTIONAL_METRICS = {
    "c1": (
        "fin_revenue",
        "fin_gross_margin",
        "fin_operating_margin",
        "fin_diluted_eps",
        "fin_free_cash_flow",
        "fin_capex",
        "fin_cash",
        "fin_total_debt",
        "fin_net_debt",
        "fin_inventory",
        "fin_deferred_revenue",
        "fin_stock_based_compensation",
        "fin_working_capital",
        "fin_liquidity",
        "op_customer_count",
        "op_retention_rate",
    ),
    "c2": (
        "macro_real_gdp_growth",
        "macro_unemployment_rate",
        "macro_core_pce_inflation",
        "macro_effective_policy_rate",
        "macro_implied_policy_rate_12m",
        "macro_us_10y_yield",
        "macro_high_yield_oas",
        "macro_financial_conditions",
        "macro_broad_usd",
        "macro_vix",
        "macro_bank_lending_standards",
        "macro_corporate_borrowing_cost",
        "macro_inflation_expectation_5y",
        "macro_money_supply_growth",
    ),
    "c3": (
        "ind_market_size",
        "ind_market_growth",
        "ind_end_demand_growth",
        "ind_order_growth",
        "ind_order_backlog",
        "ind_book_to_bill",
        "ind_inventory_level",
        "ind_inventory_days",
        "ind_capacity_growth",
        "ind_capacity_utilization",
        "ind_supply_growth",
        "ind_average_selling_price",
        "ind_input_cost",
        "ind_market_share_change",
        "ind_competitor_market_share",
        "ind_customer_concentration",
        "ind_supplier_concentration",
        "ind_commercial_launch_stage",
        "ind_production_ramp_stage",
    ),
    "o4_b": (
        "market_share_price",
        "market_cap",
        "market_enterprise_value",
        "market_price_history",
        "market_daily_ohlcv",
        "market_return_1d",
        "market_return_1m",
        "market_return_3m",
        "market_return_1y",
        "market_relative_return",
        "market_realized_volatility",
        "market_atm_iv_30d",
        "market_next_event_implied_move",
        "market_put_skew_30d",
        "market_short_interest_pct_float",
        "market_days_to_cover",
        "market_short_interest_change",
    ),
    "o4_a": (
        "market_share_price",
        "market_cap",
        "market_enterprise_value",
        "market_primary_forward_multiple",
        "market_primary_multiple_percentile",
        "market_peer_premium",
        "market_atm_iv_30d",
        "market_next_event_implied_move",
        "fin_revenue",
        "fin_gross_margin",
        "fin_operating_margin",
        "fin_free_cash_flow",
        "ind_market_growth",
        "ind_order_growth",
        "ind_inventory_level",
        "ind_capacity_utilization",
        "ind_average_selling_price",
        "ind_market_share_change",
    ),
}


def render_horizontal_context(
    bundle: HorizontalCollectionBundle,
    *,
    role: str | None = None,
    target_prefix: str | None = None,
) -> dict[str, Any]:
    """Return one small input object; never expose the full registry to an agent."""

    resolved_role = role or _role_from_prefix(target_prefix)
    prefixes = _ROLE_PREFIXES[resolved_role]
    metrics = default_metric_registry()
    targets = default_collection_target_registry()

    def target_definition(target_id: str) -> CollectionTargetDefinition | None:
        try:
            return targets.get(target_id)
        except KeyError:
            return None

    program_values: list[dict[str, Any]] = []
    for value in bundle.state_values:
        if not value.collection_target_id.startswith(prefixes):
            continue
        definition = _metric(metrics, value.metric_id)
        program_values.append(
            {
                "metric_key": value.metric_id,
                "meaning": _meaning(definition, value.metric_id, value.source_role.value),
                "value": value.value,
                "unit": value.unit,
                "as_of": value.as_of.isoformat(),
                "source_refs": [item.model_dump(mode="json") for item in value.source_refs],
            }
        )

    target_status: list[dict[str, str]] = []
    for result in bundle.manifest.target_results:
        if not result.collection_target_id.startswith(prefixes):
            continue
        target = target_definition(result.collection_target_id)
        metric_key = (
            target.metric_id
            if target and target.metric_id
            else (
                target.candidate_metric_ids[0] if target and target.candidate_metric_ids else None
            )
        ) or _metric_key_from_target(result.collection_target_id, prefixes)
        definition = _metric(metrics, metric_key)
        target_status.append(
            {
                "metric_key": metric_key,
                "meaning": _meaning(definition, metric_key),
                "status": result.status.value,
            }
        )

    optional_metrics: list[dict[str, str]] = []
    for metric_key in _OPTIONAL_METRICS[resolved_role]:
        definition = _metric(metrics, metric_key)
        optional_metrics.append(
            {
                "metric_key": metric_key,
                "meaning": _meaning(definition, metric_key),
                "value_format": _value_format(definition),
                "unit": getattr(definition, "default_unit", None) or "DOMAIN_SPECIFIC",
            }
        )
    return {
        "schema_version": "d1-horizontal-agent-input-v1",
        "program_values": program_values,
        "target_status": target_status,
        "optional_metrics": optional_metrics,
        "candidate_format": {
            "metric_key": "string",
            "meaning": "string",
            "value": "string | number | boolean",
            "unit": "string | null",
            "as_of": "ISO date/datetime | null",
            "source_aliases": ["O#"],
            "method": "string",
            "confidence": "high | medium | low",
        },
        "freeform_metrics_allowed": True,
        "instructions": [
            "Use program_values when available.",
            "EMPTY, FAILED, and UNAVAILABLE mean unknown, never zero.",
            "Optional metrics are a guide, not an exhaustive list.",
            "Research only metrics relevant to the current report.",
            "You may submit a useful metric not listed in optional_metrics.",
            "For an unlisted metric, create a clear custom_<snake_case> metric_key "
            "and provide meaning.",
            "Do not overwrite a governed program value.",
        ],
    }


def _role_from_prefix(prefix: str | None) -> str:
    mapping = {"c1_": "c1", "c2_": "c2", "c3_": "c3", "o4_": "o4_b"}
    if prefix not in mapping:
        raise ValueError("role or a known target_prefix is required")
    return mapping[prefix]


def _metric(registry: Any, metric_key: str) -> Any | None:
    try:
        return registry.get(metric_key)
    except KeyError:
        return None


def _meaning(definition: Any | None, metric_key: str, source_role: str | None = None) -> str:
    base = getattr(definition, "definition", None) or metric_key.replace("_", " ")
    return f"{base} ({source_role})" if source_role else base


def _value_format(definition: Any | None) -> str:
    value_type = getattr(getattr(definition, "value_type", None), "value", "NUMBER")
    return "number" if value_type == "NUMBER" else value_type.lower()


def _metric_key_from_target(target_id: str, prefixes: tuple[str, ...]) -> str:
    for prefix in prefixes:
        if target_id.startswith(prefix):
            return target_id.removeprefix(prefix)
    return target_id
