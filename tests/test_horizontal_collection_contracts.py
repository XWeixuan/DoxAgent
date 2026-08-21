from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from doxagent.blackboard import BlackboardService
from doxagent.horizontal_collection import (
    CollectionMode,
    CollectionTargetStatus,
    HorizontalCollectionManifest,
    HorizontalCollectionManifestRepository,
    HorizontalCollectionTargetResult,
    ObjectRef,
    ObjectType,
    ProviderCapabilityStatus,
    ResolverStatus,
    SourceRole,
    StateParameterIdentity,
    StateValueCurrentKey,
    default_collection_target_registry,
    default_metric_registry,
)
from doxagent.horizontal_collection.generated_metric_catalog import GENERATED_METRIC_IDS
from doxagent.models import AgentName
from doxagent.settings import DoxAgentSettings
from doxagent.tools.factory import default_real_tool_registry

ROOT = Path(__file__).resolve().parents[1]


def test_generated_metric_catalog_matches_governing_plan() -> None:
    text = (ROOT / "dev_plan" / "workflow_v2" / "d1_horizontal_indicators_collection.md").read_text(
        encoding="utf-8"
    )
    scoped = text[text.index("# 六、C1 个股基本面固定清单") : text.index("# 十、采集结果 Manifest")]
    expected = set(re.findall(r"\b(?:fin|op|macro|ind|market)_[a-z0-9_]+\b", scoped))

    assert set(GENERATED_METRIC_IDS) == expected
    assert len(GENERATED_METRIC_IDS) == 304


def test_inventory_uses_reporting_currency_unit() -> None:
    metric = default_metric_registry().get("fin_inventory")

    assert metric.default_unit == "REPORTING_CURRENCY"
    assert metric.default_time_scope == "LATEST_REPORTED_BALANCE_SHEET_DATE"


def test_metric_and_target_registries_cover_every_fixed_required_metric() -> None:
    metrics = default_metric_registry()
    targets = default_collection_target_registry()

    assert len(metrics.all()) == 304
    # Thirty fixed canonical metrics plus one required primary-multiple target
    # that selects an entity-appropriate concrete metric at instantiation time.
    assert len(metrics.required()) == 30
    assert {item.metric_id for item in metrics.required()} <= {
        item.metric_id for item in targets.all()
    }
    primary_multiple = targets.get("o4_primary_forward_multiple")
    assert primary_multiple.metric_id is None
    assert "market_forward_pe" in primary_multiple.candidate_metric_ids
    assert {item.source_role for item in targets.for_metric("fin_revenue")} == {
        SourceRole.ACTUAL,
        SourceRole.MANAGEMENT,
        SourceRole.SELL_SIDE,
    }
    assert (
        targets.get("c2_macro_implied_policy_rate_12m").collection_mode
        is CollectionMode.PROGRAM
    )
    assert (
        targets.get("c2_macro_implied_policy_rate_12m").capability_status
        is ProviderCapabilityStatus.IMPLEMENTED
    )
    for target_id in ("o4_short_interest_pct_float", "o4_days_to_cover"):
        target = targets.get(target_id)
        assert target.collection_mode is CollectionMode.PROGRAM
        assert target.capability_status is ProviderCapabilityStatus.IMPLEMENTED
        assert target.tool_name == "yfinance.short_interest"
    short_change = targets.get("o4_short_interest_change")
    assert short_change.collection_mode is CollectionMode.UNAVAILABLE
    assert short_change.capability_status is ProviderCapabilityStatus.BLOCKED
    assert short_change.tool_name is None


def test_object_ref_includes_realization_factor_and_requires_locator() -> None:
    ref = ObjectRef(
        object_type=ObjectType.REALIZATION_FACTOR,
        canonical_object_id_candidate="factor_mu_supply",
        resolver_status=ResolverStatus.CANDIDATE,
    )
    assert ref.object_type is ObjectType.REALIZATION_FACTOR

    with pytest.raises(ValidationError):
        ObjectRef(object_type=ObjectType.METRIC, resolver_status=ResolverStatus.UNRESOLVED)


def test_manifest_is_target_level_and_rejects_outputs_for_empty_result() -> None:
    ref = ObjectRef(
        object_type=ObjectType.STATE_VALUE,
        provider_specific_id="sv_1",
        resolver_status=ResolverStatus.UNRESOLVED,
    )
    with pytest.raises(ValidationError):
        HorizontalCollectionTargetResult(
            collection_target_id="target_empty",
            status=CollectionTargetStatus.EMPTY,
            requested_items=1,
            output_refs=(ref,),
        )

    result = HorizontalCollectionTargetResult(
        collection_target_id="target_filled",
        status=CollectionTargetStatus.FILLED,
        requested_items=1,
        succeeded_items=1,
        output_refs=(ref,),
    )
    manifest = HorizontalCollectionManifest(
        run_id="run_1",
        ticker="MU",
        metric_registry_version="metrics-v1",
        target_registry_version="targets-v1",
        created_at=datetime.now(UTC),
        target_results=(result,),
    )
    assert manifest.target_results[0].collection_target_id == "target_filled"


def test_parameter_and_current_value_identity_are_explicit() -> None:
    parameter = StateParameterIdentity(entity_id="MU", metric_id="fin_revenue")
    current = StateValueCurrentKey(
        entity_id="MU",
        metric_id="fin_revenue",
        source_role=SourceRole.SELL_SIDE,
        time_scope="NEXT_QUARTER",
    )
    assert parameter.parameter_id == "param_mu_fin_revenue"
    assert current.model_dump() == {
        "entity_id": "MU",
        "metric_id": "fin_revenue",
        "source_role": SourceRole.SELL_SIDE,
        "time_scope": "NEXT_QUARTER",
    }


def test_manifest_persists_as_separate_run_audit_artifact() -> None:
    blackboard = BlackboardService()
    run = blackboard.start_run("MU", AgentName.SYSTEM)
    manifest = HorizontalCollectionManifest(
        run_id=run.run_id,
        ticker="MU",
        metric_registry_version="metrics-v1",
        target_registry_version="targets-v1",
        target_results=(),
    )
    repository = HorizontalCollectionManifestRepository(blackboard)

    entry = repository.save(manifest)

    assert entry.content_type == "horizontal_collection_manifest"
    assert repository.list_for_run(run.run_id) == (manifest,)
    assert blackboard.get_run(run.run_id).belief_state.documents == {}


def test_factory_registers_every_non_derived_horizontal_tool_with_descriptors() -> None:
    registry = default_real_tool_registry(DoxAgentSettings(_env_file=None))
    expected = {
        "sec.issuer_filings",
        "sec.company_financials",
        "sec.filing_content",
        "sec.material_contracts_projects",
        "sec.management_disclosures",
        "ibkr.contract_search",
        "ibkr.market_snapshot",
        "ibkr.market_history",
        "ibkr.trade_tape",
        "market.daily_ohlcv",
        "market.quote_snapshot",
        "market.trade_tape",
        "benzinga.management_guidance",
        "benzinga.analyst_events",
        "benzinga.market_signals",
        "fmp.sell_side_estimates",
        "fmp.valuation_snapshot",
        "twelvedata.sell_side_estimates",
        "twelvedata.daily_ohlcv",
        "yfinance.sell_side_consensus",
        "finnhub.company_peers",
        "finnhub.insider_transactions",
        "finnhub.company_news_events",
        "fred.activity_demand",
        "fred.inflation_labor",
        "fred.rates_credit_liquidity",
        "fred.commodities_fx",
        "bls.labor_inflation",
        "bls.industry_producer_prices",
        "bls.import_export_prices",
        "bea.national_accounts",
        "bea.industry_accounts",
        "census.manufacturing_orders",
        "eia.energy_prices",
        "eia.energy_supply_operations",
        "usaspending.award_search",
        "usaspending.award_detail",
        "sam.contract_opportunities",
        "regulations.rulemaking_records",
        "federal_register.documents",
        "congress.legislative_actions",
        "openfda.approval_milestones",
        "openfda.safety_actions",
        "ir.official_feed_discovery",
        "ir.official_updates",
    }
    assert expected <= set(registry.names())
    assert len(expected) == 45
    assert {
        "benzinga.short_interest",
        "benzinga.transcripts",
        "fmp.transcript_fallback",
        "finnhub.ownership_and_insiders",
    }.isdisjoint(registry.names())
    for tool_name in expected:
        descriptor = registry.describe(tool_name)
        assert descriptor is not None
        assert descriptor.business_purpose
        assert descriptor.input_fields
