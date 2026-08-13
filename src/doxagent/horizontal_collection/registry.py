"""Versioned Metric and Collection Target registries for Document 1."""

from __future__ import annotations

from collections.abc import Iterable

from doxagent.horizontal_collection.generated_metric_catalog import GENERATED_METRIC_IDS
from doxagent.horizontal_collection.schema import (
    CollectionMode,
    CollectionTargetDefinition,
    EntityScope,
    MetricDefinition,
    MetricRequirement,
    MetricValueType,
    OutputPolicy,
    ProviderCapabilityStatus,
    SourceRole,
)

METRIC_REGISTRY_VERSION = "d1-horizontal-metrics-v1"
TARGET_REGISTRY_VERSION = "d1-horizontal-targets-v1"

REQUIRED_METRIC_IDS = frozenset(
    {
        "fin_revenue",
        "fin_gross_margin",
        "fin_operating_margin",
        "fin_diluted_eps",
        "fin_free_cash_flow",
        "fin_capex",
        "fin_cash",
        "fin_total_debt",
        "fin_net_debt",
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
        "market_share_price",
        "market_cap",
        "market_enterprise_value",
        "market_primary_multiple_percentile",
        "market_peer_premium",
        "market_atm_iv_30d",
        "market_next_event_implied_move",
        "market_put_skew_30d",
        "market_short_interest_pct_float",
        "market_days_to_cover",
        "market_short_interest_change",
    }
)

_REQUIRED_OVERRIDES: dict[str, tuple[MetricValueType, str, str]] = {
    "fin_revenue": (MetricValueType.NUMBER, "REPORTING_CURRENCY", "MULTI_PERIOD"),
    "fin_gross_margin": (MetricValueType.NUMBER, "PERCENTAGE", "MULTI_PERIOD"),
    "fin_operating_margin": (MetricValueType.NUMBER, "PERCENTAGE", "MULTI_PERIOD"),
    "fin_diluted_eps": (MetricValueType.NUMBER, "CURRENCY_PER_SHARE", "MULTI_PERIOD"),
    "fin_free_cash_flow": (
        MetricValueType.NUMBER,
        "REPORTING_CURRENCY",
        "TRAILING_TWELVE_MONTHS",
    ),
    "fin_capex": (MetricValueType.NUMBER, "REPORTING_CURRENCY", "MULTI_PERIOD"),
    "fin_cash": (
        MetricValueType.NUMBER,
        "REPORTING_CURRENCY",
        "LATEST_REPORTED_BALANCE_SHEET_DATE",
    ),
    "fin_total_debt": (
        MetricValueType.NUMBER,
        "REPORTING_CURRENCY",
        "LATEST_REPORTED_BALANCE_SHEET_DATE",
    ),
    "fin_net_debt": (
        MetricValueType.NUMBER,
        "REPORTING_CURRENCY",
        "LATEST_REPORTED_BALANCE_SHEET_DATE",
    ),
    "fin_inventory": (
        MetricValueType.NUMBER,
        "REPORTING_CURRENCY",
        "LATEST_REPORTED_BALANCE_SHEET_DATE",
    ),
    "macro_real_gdp_growth": (
        MetricValueType.NUMBER,
        "PERCENTAGE",
        "LATEST_REPORTED_QUARTER_QOQ_SAAR",
    ),
    "macro_unemployment_rate": (
        MetricValueType.NUMBER,
        "PERCENTAGE",
        "LATEST_REPORTED_MONTH",
    ),
    "macro_core_pce_inflation": (
        MetricValueType.NUMBER,
        "PERCENTAGE",
        "LATEST_REPORTED_MONTH_YOY",
    ),
    "macro_effective_policy_rate": (
        MetricValueType.NUMBER,
        "PERCENTAGE",
        "CURRENT",
    ),
    "macro_implied_policy_rate_12m": (
        MetricValueType.NUMBER,
        "PERCENTAGE",
        "TWELVE_MONTHS_FORWARD",
    ),
    "macro_us_10y_yield": (MetricValueType.NUMBER, "PERCENTAGE", "CURRENT"),
    "macro_high_yield_oas": (MetricValueType.NUMBER, "PERCENTAGE", "CURRENT"),
    "macro_financial_conditions": (MetricValueType.NUMBER, "INDEX", "CURRENT"),
    "macro_broad_usd": (MetricValueType.NUMBER, "INDEX", "CURRENT"),
    "macro_vix": (MetricValueType.NUMBER, "INDEX", "CURRENT"),
    "market_share_price": (
        MetricValueType.NUMBER,
        "TRADING_CURRENCY",
        "MARKET_SNAPSHOT_TIME",
    ),
    "market_cap": (MetricValueType.NUMBER, "TRADING_CURRENCY", "MARKET_SNAPSHOT_TIME"),
    "market_enterprise_value": (
        MetricValueType.NUMBER,
        "TRADING_CURRENCY",
        "MARKET_SNAPSHOT_TIME",
    ),
    "market_primary_forward_multiple": (
        MetricValueType.NUMBER,
        "MULTIPLE",
        "NEXT_TWELVE_MONTHS",
    ),
    "market_primary_multiple_percentile": (
        MetricValueType.NUMBER,
        "PERCENTILE",
        "TRAILING_FIVE_YEARS",
    ),
    "market_peer_premium": (MetricValueType.NUMBER, "PERCENTAGE", "MARKET_SNAPSHOT_TIME"),
    "market_atm_iv_30d": (MetricValueType.NUMBER, "PERCENTAGE", "MARKET_SNAPSHOT_TIME"),
    "market_next_event_implied_move": (
        MetricValueType.NUMBER,
        "PERCENTAGE",
        "NEXT_SCHEDULED_EVENT",
    ),
    "market_put_skew_30d": (
        MetricValueType.NUMBER,
        "VOLATILITY_POINTS",
        "MARKET_SNAPSHOT_TIME",
    ),
    "market_short_interest_pct_float": (
        MetricValueType.NUMBER,
        "PERCENTAGE",
        "LATEST_PUBLISHED_SETTLEMENT_DATE",
    ),
    "market_days_to_cover": (
        MetricValueType.NUMBER,
        "DAYS",
        "LATEST_PUBLISHED_SETTLEMENT_DATE",
    ),
    "market_short_interest_change": (
        MetricValueType.NUMBER,
        "PERCENTAGE",
        "LATEST_TWO_PUBLISHED_SETTLEMENT_DATES",
    ),
}


class MetricRegistry:
    def __init__(self, definitions: Iterable[MetricDefinition]) -> None:
        materialized = tuple(definitions)
        self.version = METRIC_REGISTRY_VERSION
        self._definitions = {item.metric_id: item for item in materialized}
        if len(self._definitions) != len(materialized):
            raise ValueError("Metric Registry contains duplicate metric_id values.")

    def get(self, metric_id: str) -> MetricDefinition:
        return self._definitions[metric_id]

    def all(self) -> tuple[MetricDefinition, ...]:
        return tuple(self._definitions[key] for key in sorted(self._definitions))

    def required(self) -> tuple[MetricDefinition, ...]:
        return tuple(item for item in self.all() if item.requirement is MetricRequirement.REQUIRED)


class CollectionTargetRegistry:
    def __init__(self, targets: Iterable[CollectionTargetDefinition]) -> None:
        materialized = tuple(targets)
        self.version = TARGET_REGISTRY_VERSION
        self._targets = {item.collection_target_id: item for item in materialized}
        if len(self._targets) != len(materialized):
            raise ValueError("Collection Target Registry contains duplicate target ids.")

    def get(self, collection_target_id: str) -> CollectionTargetDefinition:
        return self._targets[collection_target_id]

    def all(self) -> tuple[CollectionTargetDefinition, ...]:
        return tuple(self._targets[key] for key in sorted(self._targets))

    def for_metric(self, metric_id: str) -> tuple[CollectionTargetDefinition, ...]:
        return tuple(
            item
            for item in self.all()
            if item.metric_id == metric_id or metric_id in item.candidate_metric_ids
        )


def default_metric_registry() -> MetricRegistry:
    return MetricRegistry(_definition(metric_id) for metric_id in GENERATED_METRIC_IDS)


def default_collection_target_registry() -> CollectionTargetRegistry:
    return CollectionTargetRegistry(_fixed_targets())


def _definition(metric_id: str) -> MetricDefinition:
    requirement = (
        MetricRequirement.REQUIRED
        if metric_id in REQUIRED_METRIC_IDS
        else MetricRequirement.OPTIONAL
    )
    value_type: MetricValueType
    unit: str | None
    time_scope: str
    if metric_id in _REQUIRED_OVERRIDES:
        value_type, unit, time_scope = _REQUIRED_OVERRIDES[metric_id]
    else:
        value_type = _infer_value_type(metric_id)
        unit = _infer_unit(metric_id, value_type)
        time_scope = _infer_time_scope(metric_id)
    words = metric_id.replace("_", " ")
    domain = metric_id.split("_", 1)[0]
    domain_label = {
        "fin": "issuer financial",
        "op": "issuer operating",
        "macro": "macro and financial environment",
        "ind": "industry and supply-chain",
        "market": "market and positioning",
    }[domain]
    return MetricDefinition(
        metric_id=metric_id,
        standard_name=words,
        definition=f"Canonical {domain_label} indicator: {words}.",
        requirement=requirement,
        value_type=value_type,
        default_unit=unit,
        default_time_scope=time_scope,
        aliases=(words, words.upper()),
    )


def _infer_value_type(metric_id: str) -> MetricValueType:
    if metric_id.endswith("_stage"):
        return MetricValueType.STAGE
    if metric_id.endswith(("_direction", "_outlook", "_availability", "_status")):
        return MetricValueType.DIRECTION
    if metric_id.endswith(("_history", "_series", "_daily_ohlcv")):
        return MetricValueType.SERIES
    return MetricValueType.NUMBER


def _infer_unit(metric_id: str, value_type: MetricValueType) -> str | None:
    if value_type in {MetricValueType.STAGE, MetricValueType.DIRECTION}:
        return None
    if any(
        token in metric_id
        for token in (
            "margin",
            "rate",
            "growth",
            "share",
            "yield",
            "utilization",
            "volatility",
            "premium",
            "ownership",
            "turnover",
            "discount",
        )
    ):
        return "PERCENTAGE"
    if metric_id.endswith(("_price", "_cost", "_revenue", "_income", "_capex")):
        return "CURRENCY"
    if metric_id.endswith(("_volume", "_count", "_capacity", "_inventory")):
        return "COUNT_OR_DOMAIN_UNIT"
    return "DOMAIN_SPECIFIC"


def _infer_time_scope(metric_id: str) -> str:
    if metric_id.startswith("macro_"):
        return "LATEST_AVAILABLE_PERIOD"
    if metric_id.startswith("market_"):
        return "MARKET_SNAPSHOT_TIME"
    return "MULTI_PERIOD"


def _fixed_targets() -> tuple[CollectionTargetDefinition, ...]:
    targets: list[CollectionTargetDefinition] = []

    def add(
        target_id: str,
        metric_id: str,
        role: SourceRole,
        time_scope: str,
        scope: EntityScope,
        mode: CollectionMode,
        *,
        provider: str | None = None,
        tool: str | None = None,
        output: OutputPolicy = OutputPolicy.STATE_VALUE,
        capability: ProviderCapabilityStatus = ProviderCapabilityStatus.PRODUCTION_READY,
        method_id: str | None = None,
    ) -> None:
        targets.append(
            CollectionTargetDefinition(
                collection_target_id=target_id,
                metric_id=metric_id,
                requirement=MetricRequirement.REQUIRED,
                source_role=role,
                time_scope=time_scope,
                entity_scope=scope,
                collection_mode=mode,
                provider=provider,
                tool_name=tool,
                method_id=method_id,
                output_policy=output,
                capability_status=capability,
            )
        )

    # C1: source-role and period targets are independent routes.
    add(
        "c1_fin_revenue_actual_latest_q",
        "fin_revenue",
        SourceRole.ACTUAL,
        "LATEST_REPORTED_QUARTER",
        EntityScope.ISSUER,
        CollectionMode.PROGRAM,
        provider="SEC",
        tool="sec.company_financials",
    )
    add(
        "c1_fin_revenue_management_next_q",
        "fin_revenue",
        SourceRole.MANAGEMENT,
        "NEXT_QUARTER",
        EntityScope.ISSUER,
        CollectionMode.AGENT,
        capability=ProviderCapabilityStatus.DOCUMENTED,
    )
    add(
        "c1_fin_revenue_sell_side_next_q",
        "fin_revenue",
        SourceRole.SELL_SIDE,
        "NEXT_QUARTER",
        EntityScope.ISSUER,
        CollectionMode.PROGRAM,
        provider="FMP",
        tool="fmp.sell_side_estimates",
    )
    add(
        "c1_fin_gross_margin_actual_latest_q",
        "fin_gross_margin",
        SourceRole.ACTUAL,
        "LATEST_REPORTED_QUARTER",
        EntityScope.ISSUER,
        CollectionMode.UNAVAILABLE,
        method_id="gaap_gross_margin_v1",
        capability=ProviderCapabilityStatus.BLOCKED,
    )
    add(
        "c1_fin_gross_margin_management",
        "fin_gross_margin",
        SourceRole.MANAGEMENT,
        "NEXT_GUIDED_PERIOD",
        EntityScope.ISSUER,
        CollectionMode.AGENT,
        capability=ProviderCapabilityStatus.DOCUMENTED,
    )
    add(
        "c1_fin_operating_margin_actual_latest_q",
        "fin_operating_margin",
        SourceRole.ACTUAL,
        "LATEST_REPORTED_QUARTER",
        EntityScope.ISSUER,
        CollectionMode.UNAVAILABLE,
        method_id="gaap_operating_margin_v1",
        capability=ProviderCapabilityStatus.BLOCKED,
    )
    add(
        "c1_fin_operating_margin_management",
        "fin_operating_margin",
        SourceRole.MANAGEMENT,
        "NEXT_GUIDED_PERIOD",
        EntityScope.ISSUER,
        CollectionMode.AGENT,
        capability=ProviderCapabilityStatus.DOCUMENTED,
    )
    add(
        "c1_fin_diluted_eps_actual_latest_q",
        "fin_diluted_eps",
        SourceRole.ACTUAL,
        "LATEST_REPORTED_QUARTER",
        EntityScope.ISSUER,
        CollectionMode.PROGRAM,
        provider="SEC",
        tool="sec.company_financials",
    )
    add(
        "c1_fin_diluted_eps_management",
        "fin_diluted_eps",
        SourceRole.MANAGEMENT,
        "NEXT_GUIDED_PERIOD",
        EntityScope.ISSUER,
        CollectionMode.AGENT,
        capability=ProviderCapabilityStatus.DOCUMENTED,
    )
    add(
        "c1_fin_diluted_eps_sell_side_next_q",
        "fin_diluted_eps",
        SourceRole.SELL_SIDE,
        "NEXT_QUARTER",
        EntityScope.ISSUER,
        CollectionMode.PROGRAM,
        provider="FMP",
        tool="fmp.sell_side_estimates",
    )
    add(
        "c1_fin_free_cash_flow_ttm",
        "fin_free_cash_flow",
        SourceRole.ACTUAL,
        "TRAILING_TWELVE_MONTHS",
        EntityScope.ISSUER,
        CollectionMode.UNAVAILABLE,
        method_id="standard_fcf_v1",
        capability=ProviderCapabilityStatus.BLOCKED,
    )
    add(
        "c1_fin_capex_actual_ttm",
        "fin_capex",
        SourceRole.ACTUAL,
        "TRAILING_TWELVE_MONTHS",
        EntityScope.ISSUER,
        CollectionMode.PROGRAM,
        provider="SEC",
        tool="sec.company_financials",
    )
    add(
        "c1_fin_capex_management_fy",
        "fin_capex",
        SourceRole.MANAGEMENT,
        "CURRENT_FISCAL_YEAR",
        EntityScope.ISSUER,
        CollectionMode.AGENT,
        capability=ProviderCapabilityStatus.DOCUMENTED,
    )
    add(
        "c1_fin_cash_actual",
        "fin_cash",
        SourceRole.ACTUAL,
        "LATEST_REPORTED_BALANCE_SHEET_DATE",
        EntityScope.ISSUER,
        CollectionMode.PROGRAM,
        provider="SEC",
        tool="sec.company_financials",
    )
    add(
        "c1_fin_total_debt_actual",
        "fin_total_debt",
        SourceRole.ACTUAL,
        "LATEST_REPORTED_BALANCE_SHEET_DATE",
        EntityScope.ISSUER,
        CollectionMode.PROGRAM,
        provider="SEC",
        tool="sec.company_financials",
    )
    add(
        "c1_fin_net_debt_actual",
        "fin_net_debt",
        SourceRole.ACTUAL,
        "LATEST_REPORTED_BALANCE_SHEET_DATE",
        EntityScope.ISSUER,
        CollectionMode.UNAVAILABLE,
        method_id="net_debt_v1",
        capability=ProviderCapabilityStatus.BLOCKED,
    )

    # C2: raw/official observations only; provider-side transformations are explicit args.
    macro_routes = (
        (
            "macro_real_gdp_growth",
            "bea.national_accounts",
            "BEA",
            "LATEST_REPORTED_QUARTER_QOQ_SAAR",
        ),
        ("macro_unemployment_rate", "fred.inflation_labor", "FRED", "LATEST_REPORTED_MONTH"),
        ("macro_core_pce_inflation", "fred.inflation_labor", "FRED", "LATEST_REPORTED_MONTH_YOY"),
        ("macro_effective_policy_rate", "fred.rates_credit_liquidity", "FRED", "CURRENT"),
        ("macro_us_10y_yield", "fred.rates_credit_liquidity", "FRED", "CURRENT"),
        ("macro_high_yield_oas", "fred.rates_credit_liquidity", "FRED", "CURRENT"),
        ("macro_financial_conditions", "fred.rates_credit_liquidity", "FRED", "CURRENT"),
        ("macro_broad_usd", "fred.commodities_fx", "FRED", "CURRENT"),
        ("macro_vix", "fred.rates_credit_liquidity", "FRED", "CURRENT"),
    )
    for metric_id, tool, provider, time_scope in macro_routes:
        add(
            f"c2_{metric_id}",
            metric_id,
            SourceRole.ACTUAL,
            time_scope,
            EntityScope.US_MACRO,
            CollectionMode.PROGRAM,
            provider=provider,
            tool=tool,
        )
    add(
        "c2_macro_implied_policy_rate_12m",
        "macro_implied_policy_rate_12m",
        SourceRole.MARKET_IMPLIED,
        "TWELVE_MONTHS_FORWARD",
        EntityScope.US_MACRO,
        CollectionMode.UNAVAILABLE,
        method_id="fed_funds_curve_12m_v1",
        capability=ProviderCapabilityStatus.BLOCKED,
    )

    # O4: direct provider fields only; governed calculations remain unavailable this round.
    add(
        "o4_price_snapshot",
        "market_share_price",
        SourceRole.MARKET_IMPLIED,
        "MARKET_SNAPSHOT_TIME",
        EntityScope.SECURITY,
        CollectionMode.PROGRAM,
        provider="IBKR-first market route",
        tool="market.quote_snapshot",
    )
    add(
        "o4_market_cap",
        "market_cap",
        SourceRole.MARKET_IMPLIED,
        "MARKET_SNAPSHOT_TIME",
        EntityScope.SECURITY,
        CollectionMode.PROGRAM,
        provider="FMP",
        tool="fmp.valuation_snapshot",
    )
    add(
        "o4_enterprise_value",
        "market_enterprise_value",
        SourceRole.MARKET_IMPLIED,
        "MARKET_SNAPSHOT_TIME",
        EntityScope.SECURITY,
        CollectionMode.PROGRAM,
        provider="FMP",
        tool="fmp.valuation_snapshot",
    )
    targets.append(
        CollectionTargetDefinition(
            collection_target_id="o4_primary_forward_multiple",
            candidate_metric_ids=(
                "market_forward_pe",
                "market_forward_ev_to_ebitda",
                "market_forward_ev_to_sales",
                "market_forward_price_to_sales",
            ),
            requirement=MetricRequirement.REQUIRED,
            source_role=SourceRole.MARKET_IMPLIED,
            time_scope="NEXT_TWELVE_MONTHS",
            entity_scope=EntityScope.SECURITY,
            collection_mode=CollectionMode.PROGRAM,
            provider="FMP",
            tool_name="fmp.valuation_snapshot",
            output_policy=OutputPolicy.STATE_VALUE,
            capability_status=ProviderCapabilityStatus.IMPLEMENTED,
        )
    )
    for target_id, metric_id, method_id in (
        (
            "o4_primary_multiple_percentile",
            "market_primary_multiple_percentile",
            "historical_forward_percentile_v1",
        ),
        ("o4_peer_premium", "market_peer_premium", "peer_relative_valuation_v1"),
        ("o4_atm_iv_30d", "market_atm_iv_30d", "option_iv_30d_v1"),
        ("o4_next_event_implied_move", "market_next_event_implied_move", "event_straddle_move_v1"),
        ("o4_put_skew_30d", "market_put_skew_30d", "put_skew_30d_v1"),
    ):
        add(
            target_id,
            metric_id,
            SourceRole.MARKET_IMPLIED,
            "MARKET_SNAPSHOT_TIME",
            EntityScope.SECURITY,
            CollectionMode.UNAVAILABLE,
            method_id=method_id,
            capability=ProviderCapabilityStatus.BLOCKED,
        )
    add(
        "o4_short_interest_pct_float",
        "market_short_interest_pct_float",
        SourceRole.MARKET_IMPLIED,
        "LATEST_PUBLISHED_SETTLEMENT_DATE",
        EntityScope.SECURITY,
        CollectionMode.UNAVAILABLE,
        capability=ProviderCapabilityStatus.BLOCKED,
        method_id="benzinga_short_interest_entitlement_required",
    )
    add(
        "o4_days_to_cover",
        "market_days_to_cover",
        SourceRole.MARKET_IMPLIED,
        "LATEST_PUBLISHED_SETTLEMENT_DATE",
        EntityScope.SECURITY,
        CollectionMode.UNAVAILABLE,
        capability=ProviderCapabilityStatus.BLOCKED,
        method_id="benzinga_short_interest_entitlement_required",
    )
    add(
        "o4_short_interest_change",
        "market_short_interest_change",
        SourceRole.MARKET_IMPLIED,
        "LATEST_TWO_PUBLISHED_SETTLEMENT_DATES",
        EntityScope.SECURITY,
        CollectionMode.UNAVAILABLE,
        capability=ProviderCapabilityStatus.BLOCKED,
        method_id="benzinga_short_interest_entitlement_required",
        output=OutputPolicy.OBSERVATION_ONLY,
    )
    return tuple(targets)
