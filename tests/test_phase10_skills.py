import pytest

from doxagent.adapters import (
    FundamentalBriefAgentModule,
    IndustryResearchAgentModule,
    MacroContextAgentModule,
)
from doxagent.agents import MarketTraceAgentModule, MockAgentRunner, default_agent_registry
from doxagent.blackboard import BlackboardService
from doxagent.context import ContextBuilder
from doxagent.models import AgentName, TaskType
from doxagent.skills import UnknownSkillError, default_skill_registry
from doxagent.skills.injection import SkillInjector
from tests.fixtures.phase1_contracts import agent_task


def test_default_skill_registry_contains_migrated_external_skills() -> None:
    registry = default_skill_registry()
    expected = {
        "macro-analysis",
        "global-macro",
        "credit-analysis",
        "yfinance",
        "commodity-analysis",
        "seasonal",
        "asset-allocation",
        "risk-analysis",
        "hedging-strategy",
        "strategy-generate",
        "financial-statement",
        "fundamental-filter",
        "valuation-model",
        "earnings-forecast",
        "web-reader",
        "report-generate",
        "market-researcher",
        "sector-overview",
        "competitive-analysis",
        "comps-analysis",
        "idea-generation",
        "note-writer",
        "ohlcv-orchestration",
        "quote-context",
        "relative-performance",
        "technical-signal-analysis",
        "market-data-quality",
    }

    assert expected.issubset(set(registry.ids()))
    skill = registry.get("sector-overview")
    restored = skill.model_validate_json(skill.model_dump_json())
    assert restored.skill_id == "sector-overview"
    assert restored.source_kind.value == "financial_services"


def test_skill_registry_unknown_and_deep_copy_behavior() -> None:
    registry = default_skill_registry()

    with pytest.raises(UnknownSkillError):
        registry.get("missing-skill")

    copy = registry.get("macro-analysis")
    copy.content.output_requirements.append("local mutation")

    assert "local mutation" not in registry.get("macro-analysis").content.output_requirements


def test_skill_injector_selects_agent_defaults_and_explicit_registered_skills() -> None:
    registry = default_skill_registry()
    agent_registry = default_agent_registry()
    definition = agent_registry.get(AgentName.C2_MACRO_RESEARCH)
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.C2_MACRO_RESEARCH,
            "task_type": TaskType.GENERATE_GLOBAL_RESEARCH,
            "input_context": {"skill_ids": ["doxagent-source-discipline"]},
            "required_output_schema": "ResearchSection",
            "permissions": definition.runtime.to_permissions(),
        },
        deep=True,
    )

    injected = SkillInjector(registry).inject(task, definition)

    assert task.skill_bundle is None
    assert injected.skill_bundle is not None
    assert "macro-analysis" in injected.skill_bundle.skill_ids
    assert "asset-allocation" in injected.skill_bundle.skill_ids
    assert "doxagent-source-discipline" in injected.skill_bundle.skill_ids
    assert len(injected.skill_bundle.skill_ids) < len(registry.ids())


def test_unknown_explicit_skill_request_fails() -> None:
    definition = default_agent_registry().get(AgentName.C1_FUNDAMENTAL_RESEARCH)
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.C1_FUNDAMENTAL_RESEARCH,
            "task_type": TaskType.GENERATE_GLOBAL_RESEARCH,
            "input_context": {"skill_ids": ["not-registered"]},
            "required_output_schema": "ResearchSection",
            "permissions": definition.runtime.to_permissions(),
        },
        deep=True,
    )

    with pytest.raises(UnknownSkillError):
        SkillInjector().inject(task, definition)


def test_mock_runner_and_context_expose_injected_skill_summary() -> None:
    registry = default_agent_registry()
    definition = registry.get(AgentName.O4_MARKET_TRACE)
    base = agent_task()
    task = base.model_copy(
        update={
            "agent_name": AgentName.O4_MARKET_TRACE,
            "task_type": TaskType.GENERATE_GLOBAL_RESEARCH,
            "required_output_schema": "MarketTraceResult",
            "permissions": definition.runtime.to_permissions(),
        },
        deep=True,
    )

    result = MockAgentRunner(registry).run(task)

    assert "ohlcv-orchestration" in result.payload["skill_ids"]
    assert result.payload["skill_versions"]["quote-context"]

    service = BlackboardService()
    run = service.start_run("NVDA", AgentName.SYSTEM)
    injected_task = SkillInjector().inject(
        task.model_copy(
            update={"run_metadata": task.run_metadata.model_copy(update={"run_id": run.run_id})},
            deep=True,
        ),
        definition,
    )
    snapshot = ContextBuilder(service).build(injected_task, run.run_id)
    snapshot_skill_ids = [skill.skill_id for skill in snapshot.skill_summaries]
    assert snapshot_skill_ids == injected_task.skill_bundle.skill_ids


def test_external_adapter_outputs_include_skill_versions() -> None:
    macro = MacroContextAgentModule().run(goal="US equity allocation", timeframe="1-3 months")
    macro_output = macro.payload["structured"]["agent_outputs"][0]
    assert macro_output["skills"]
    assert macro_output["skill_versions"]["macro-analysis"]

    fundamental = FundamentalBriefAgentModule().run(target="AAPL", market="US")
    fundamental_output = fundamental.payload["structured"]["agent_outputs"][0]
    assert fundamental_output["skill_versions"]["financial-statement"]

    industry = IndustryResearchAgentModule().run(
        sector_or_theme="US data-center power",
        angle="supply gap",
        universe=["VST", "CEG"],
    )
    industry_output = industry.payload["structured"]["agent_outputs"][0]
    assert industry_output["skill_versions"]["market-researcher"]

    trace = MarketTraceAgentModule().run(ticker="AAPL")
    assert "ohlcv-orchestration" in trace.payload["metadata"]["skill_ids"]
    assert trace.payload["metadata"]["skill_versions"]["market-data-quality"]
