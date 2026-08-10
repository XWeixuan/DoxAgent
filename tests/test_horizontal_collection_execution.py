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
