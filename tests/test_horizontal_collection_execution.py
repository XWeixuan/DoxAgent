from __future__ import annotations

from doxagent.horizontal_collection.collector import HorizontalCollector
from doxagent.horizontal_collection.compiler import HorizontalStateCompiler
from doxagent.horizontal_collection.registry import CollectionTargetRegistry, MetricRegistry
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
from doxagent.models import ResultStatus
from doxagent.tools.registry import ToolRegistry
from doxagent.tools.schema import ToolResult


class _ValueClient:
    def call(self, request):
        return ToolResult(
            tool_name=request.tool_name,
            status=ResultStatus.SUCCEEDED,
            output={
                "fin_revenue": 42.0,
                "as_of": "2026-06-30T00:00:00Z",
                "source_url": "https://example.com/filing",
            },
        )


class _SecFinancialClient:
    def call(self, request):
        return ToolResult(
            tool_name=request.tool_name,
            status=ResultStatus.SUCCEEDED,
            output={
                "provider": "sec",
                "key_facts": [
                    {
                        "taxonomy": "us-gaap",
                        "concept": "RevenueFromContractWithCustomerExcludingAssessedTax",
                        "latest_observations": [
                            {
                                "unit": "USD",
                                "observation": {
                                    "start": "2025-09-01",
                                    "end": "2026-05-28",
                                    "filed": "2026-06-25",
                                    "accn": "0000000000-26-000003",
                                    "form": "10-Q",
                                    "fy": 2026,
                                    "fp": "Q3",
                                    "val": 320.0,
                                },
                            },
                            {
                                "unit": "USD",
                                "observation": {
                                    "start": "2026-02-27",
                                    "end": "2026-05-28",
                                    "filed": "2026-06-25",
                                    "accn": "0000000000-26-000003",
                                    "form": "10-Q",
                                    "fy": 2026,
                                    "fp": "Q3",
                                    "frame": "CY2026Q2",
                                    "val": 120.0,
                                },
                            },
                            {
                                "unit": "USD",
                                "observation": {
                                    "start": "2025-11-29",
                                    "end": "2026-02-26",
                                    "filed": "2026-03-26",
                                    "accn": "0000000000-26-000002",
                                    "form": "10-Q",
                                    "fy": 2026,
                                    "fp": "Q2",
                                    "frame": "CY2026Q1",
                                    "val": 100.0,
                                },
                            },
                        ],
                    }
                ],
            },
        )


class _MarketQuoteClient:
    def call(self, request):
        return ToolResult(
            tool_name=request.tool_name,
            status=ResultStatus.SUCCEEDED,
            output={
                "price": 953.78,
                "as_of": "2026-08-20T15:13:15Z",
                "price_kind": "quote_snapshot",
                "quote_timestamp": "2026-08-20T15:13:15Z",
                "session": "provider_reported",
                "requested_fields": ["last", "close"],
                "provider_routing": {
                    "selected_tool": "ibkr.market_snapshot",
                    "fallback_used": False,
                },
            },
        )


class _MarketCloseFallbackClient:
    def call(self, request):
        return ToolResult(
            tool_name=request.tool_name,
            status=ResultStatus.PARTIAL,
            output={
                "price": 937.11,
                "as_of": "2026-08-19",
                "price_kind": "daily_close_fallback",
                "quote_timestamp": None,
                "bar_close_date": "2026-08-19",
                "session": "daily_bar",
                "resolved_fields": ["daily_close"],
                "data_quality_flags": ["stale_daily_close_fallback"],
                "provider_routing": {
                    "selected_tool": "market.daily_ohlcv",
                    "fallback_used": True,
                },
            },
        )


def test_program_collector_and_compiler_promote_only_governed_values() -> None:
    metrics = MetricRegistry(
        [
            MetricDefinition(
                metric_id="fin_revenue",
                standard_name="revenue",
                definition="issuer revenue",
                requirement=MetricRequirement.REQUIRED,
                value_type=MetricValueType.NUMBER,
                default_unit="USD",
                default_time_scope="LATEST_REPORTED_QUARTER",
            )
        ]
    )
    targets = CollectionTargetRegistry(
        [
            CollectionTargetDefinition(
                collection_target_id="c1_revenue",
                metric_id="fin_revenue",
                requirement=MetricRequirement.REQUIRED,
                source_role=SourceRole.ACTUAL,
                time_scope="LATEST_REPORTED_QUARTER",
                entity_scope=EntityScope.ISSUER,
                collection_mode=CollectionMode.PROGRAM,
                provider="test",
                tool_name="test.value",
                output_policy=OutputPolicy.STATE_VALUE,
                capability_status=ProviderCapabilityStatus.PRODUCTION_READY,
            )
        ]
    )
    tools = ToolRegistry()
    tools.register("test.value", _ValueClient())
    manifest, observations = HorizontalCollector(
        tools=tools,
        metrics=metrics,
        targets=targets,
    ).collect(run_id="run-1", ticker="NVDA")
    bundle = HorizontalStateCompiler(metrics=metrics, targets=targets).compile(
        ticker="NVDA",
        manifest=manifest,
        observations=observations,
    )
    assert manifest.target_results[0].status.value == "FILLED"
    assert bundle.state_values[0].value == 42.0
    assert bundle.state_values[0].parameter_id == "param_nvda_fin_revenue"


def test_sec_financial_collection_selects_one_quarter_and_preserves_period_identity() -> None:
    metrics = MetricRegistry(
        [
            MetricDefinition(
                metric_id="fin_revenue",
                standard_name="revenue",
                definition="issuer revenue",
                requirement=MetricRequirement.REQUIRED,
                value_type=MetricValueType.NUMBER,
                default_unit="USD",
                default_time_scope="LATEST_REPORTED_QUARTER",
            )
        ]
    )
    targets = CollectionTargetRegistry(
        [
            CollectionTargetDefinition(
                collection_target_id="c1_fin_revenue_actual_latest_q",
                metric_id="fin_revenue",
                requirement=MetricRequirement.REQUIRED,
                source_role=SourceRole.ACTUAL,
                time_scope="LATEST_REPORTED_QUARTER",
                entity_scope=EntityScope.ISSUER,
                collection_mode=CollectionMode.PROGRAM,
                provider="SEC",
                tool_name="sec.company_financials",
                output_policy=OutputPolicy.STATE_VALUE,
                capability_status=ProviderCapabilityStatus.PRODUCTION_READY,
            )
        ]
    )
    tools = ToolRegistry()
    tools.register("sec.company_financials", _SecFinancialClient())

    manifest, observations = HorizontalCollector(
        tools=tools,
        metrics=metrics,
        targets=targets,
    ).collect(run_id="run-sec-period", ticker="MU")
    bundle = HorizontalStateCompiler(metrics=metrics, targets=targets).compile(
        ticker="MU",
        manifest=manifest,
        observations=observations,
    )

    assert manifest.target_results[0].status.value == "FILLED"
    assert len(observations) == 1
    observation = observations[0]
    assert observation.value == 120.0
    assert observation.as_of.date().isoformat() == "2026-05-28"
    assert observation.period_start and observation.period_start.date().isoformat() == "2026-02-27"
    assert observation.period_end and observation.period_end.date().isoformat() == "2026-05-28"
    assert observation.accession == "0000000000-26-000003"
    assert observation.source_concept == ("RevenueFromContractWithCustomerExcludingAssessedTax")
    assert observation.fiscal_year == "2026"
    assert observation.fiscal_period == "Q3"
    state_value = bundle.state_values[0]
    assert state_value.period_end == observation.period_end
    assert state_value.retrieved_at == observation.retrieved_at
    assert state_value.accession == observation.accession


def test_sec_record_selection_distinguishes_instant_and_twelve_month_scopes() -> None:
    instant_records = [
        {
            "concept": "CashAndCashEquivalentsAtCarryingValue",
            "value": 90,
            "row": {"end": "2026-02-26", "filed": "2026-03-26", "val": 90},
        },
        {
            "concept": "CashAndCashEquivalentsAtCarryingValue",
            "value": 110,
            "row": {"end": "2026-05-28", "filed": "2026-06-25", "val": 110},
        },
    ]
    duration_records = [
        {
            "concept": "PaymentsToAcquirePropertyPlantAndEquipment",
            "value": 210,
            "row": {
                "start": "2025-09-01",
                "end": "2026-05-28",
                "filed": "2026-06-25",
                "val": 210,
            },
        },
        {
            "concept": "PaymentsToAcquirePropertyPlantAndEquipment",
            "value": 280,
            "row": {
                "start": "2024-08-30",
                "end": "2025-08-28",
                "filed": "2025-10-10",
                "val": 280,
            },
        },
    ]

    instant = HorizontalCollector._select_sec_record(  # noqa: SLF001
        "LATEST_REPORTED_BALANCE_SHEET_DATE", instant_records
    )
    twelve_month = HorizontalCollector._select_sec_record(  # noqa: SLF001
        "TRAILING_TWELVE_MONTHS", duration_records
    )

    assert instant and instant["value"] == 110
    assert twelve_month and twelve_month["value"] == 280


def test_market_quote_collection_preserves_snapshot_contract_metadata() -> None:
    metrics = MetricRegistry(
        [
            MetricDefinition(
                metric_id="market_share_price",
                standard_name="share price",
                definition="market price",
                requirement=MetricRequirement.REQUIRED,
                value_type=MetricValueType.NUMBER,
                default_unit="USD",
                default_time_scope="MARKET_SNAPSHOT_TIME",
            )
        ]
    )
    targets = CollectionTargetRegistry(
        [
            CollectionTargetDefinition(
                collection_target_id="c5_price_snapshot",
                metric_id="market_share_price",
                requirement=MetricRequirement.REQUIRED,
                source_role=SourceRole.MARKET_IMPLIED,
                time_scope="MARKET_SNAPSHOT_TIME",
                entity_scope=EntityScope.SECURITY,
                collection_mode=CollectionMode.PROGRAM,
                provider="IBKR-first market route",
                tool_name="market.quote_snapshot",
                output_policy=OutputPolicy.STATE_VALUE,
                capability_status=ProviderCapabilityStatus.PRODUCTION_READY,
            )
        ]
    )
    tools = ToolRegistry()
    tools.register("market.quote_snapshot", _MarketQuoteClient())

    manifest, observations = HorizontalCollector(
        tools=tools,
        metrics=metrics,
        targets=targets,
    ).collect(run_id="run-market-contract", ticker="MU")
    bundle = HorizontalStateCompiler(metrics=metrics, targets=targets).compile(
        ticker="MU",
        manifest=manifest,
        observations=observations,
    )

    assert manifest.target_results[0].status.value == "FILLED"
    observation = observations[0]
    assert observation.value == 953.78
    assert observation.observation_metadata == {
        "price_kind": "quote_snapshot",
        "quote_timestamp": "2026-08-20T15:13:15Z",
        "session": "provider_reported",
        "selected_provider_tool": "ibkr.market_snapshot",
        "fallback_used": False,
        "requested_fields": ["last", "close"],
    }
    assert bundle.state_values[0].observation_metadata == observation.observation_metadata


def test_market_close_fallback_keeps_bar_date_separate_from_retrieval_time() -> None:
    metrics = MetricRegistry(
        [
            MetricDefinition(
                metric_id="market_share_price",
                standard_name="share price",
                definition="market price",
                requirement=MetricRequirement.REQUIRED,
                value_type=MetricValueType.NUMBER,
                default_unit="USD",
                default_time_scope="MARKET_SNAPSHOT_TIME",
            )
        ]
    )
    targets = CollectionTargetRegistry(
        [
            CollectionTargetDefinition(
                collection_target_id="c5_price_snapshot",
                metric_id="market_share_price",
                requirement=MetricRequirement.REQUIRED,
                source_role=SourceRole.MARKET_IMPLIED,
                time_scope="MARKET_SNAPSHOT_TIME",
                entity_scope=EntityScope.SECURITY,
                collection_mode=CollectionMode.PROGRAM,
                provider="IBKR-first market route",
                tool_name="market.quote_snapshot",
                output_policy=OutputPolicy.STATE_VALUE,
                capability_status=ProviderCapabilityStatus.PRODUCTION_READY,
            )
        ]
    )
    tools = ToolRegistry()
    tools.register("market.quote_snapshot", _MarketCloseFallbackClient())

    manifest, observations = HorizontalCollector(
        tools=tools,
        metrics=metrics,
        targets=targets,
    ).collect(run_id="run-market-fallback", ticker="MU")

    assert manifest.target_results[0].status.value == "PARTIAL"
    observation = observations[0]
    assert observation.value == 937.11
    assert observation.as_of.date().isoformat() == "2026-08-19"
    assert observation.period_end == observation.as_of
    assert observation.retrieved_at > observation.as_of
    assert observation.observation_metadata["price_kind"] == "daily_close_fallback"
    assert observation.observation_metadata["bar_close_date"] == "2026-08-19"
    assert observation.observation_metadata["fallback_used"] is True
    assert "stale_daily_close_fallback" in observation.quality_flags
    assert "provider_partial" in observation.quality_flags
