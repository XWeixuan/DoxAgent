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
from doxagent.prompts import PromptAssembler, PromptInjector, UnknownPromptResourceError
from doxagent.prompts.registry import default_prompt_registry
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
    assert "doxagent-source-discipline" not in registry.ids()
    skill = registry.get("sector-overview")
    restored = skill.model_validate_json(skill.model_dump_json())
    assert restored.skill_id == "sector-overview"
    assert restored.source_kind.value == "financial_services"


def test_skill_allowed_tools_are_runtime_subsets_and_not_legacy_mock_tools() -> None:
    skill_registry = default_skill_registry()
    agent_registry = default_agent_registry()
    legacy_tools = {
        "external_research.mock",
        "market_data.quote",
        "market_data.ohlcv",
        "market_data.multiple_quotes",
        "doxatlas.query",
        "doxatlas.source_lookup",
    }

    for skill_id in skill_registry.ids():
        skill = skill_registry.get(skill_id)
        assert legacy_tools.isdisjoint(skill.allowed_tools)
        for agent_name in skill.applicable_agents:
            allowed = set(agent_registry.get(agent_name).runtime.allowed_tools)
            assert set(skill.allowed_tools).issubset(allowed)


def test_prompt_external_package_allowed_tools_are_runtime_subsets() -> None:
    prompt_registry = default_prompt_registry()
    agent_registry = default_agent_registry()
    legacy_tools = {
        "external_research.mock",
        "market_data.quote",
        "market_data.ohlcv",
        "market_data.multiple_quotes",
        "doxatlas.query",
        "doxatlas.source_lookup",
    }

    for package in prompt_registry.external_packages():
        assert legacy_tools.isdisjoint(package.allowed_tools)
        for agent_name in package.applicable_agents:
            allowed = set(agent_registry.get(agent_name).runtime.allowed_tools)
            assert set(package.allowed_tools).issubset(allowed)


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
            "input_context": {"external_skill_package_ids": ["risk-analysis"]},
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
    assert "doxagent-source-discipline" not in injected.skill_bundle.skill_ids
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
    assert "agent.o4" in result.payload["prompt_block_ids"]
    assert "doxagent-source-discipline" in result.payload["internal_task_skill_ids"]

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


def test_prompt_registry_distinguishes_prompt_internal_and_external_resources() -> None:
    registry = default_prompt_registry()

    system = registry.get("system.doxagent_core")
    internal = registry.get("expectation-construction")
    external = registry.get("macro-analysis")

    assert system.kind.value == "prompt_block"
    assert internal.kind.value == "internal_task_skill"
    assert external.kind.value == "external_skill_package"
    assert registry.get("agent.o1").model_validate_json(registry.get("agent.o1").model_dump_json())


def test_prompt_injector_selects_o1_internal_sop_without_external_packages() -> None:
    agent_registry = default_agent_registry()
    definition = agent_registry.get(AgentName.O1_EXPECTATION_OWNER)
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.O1_EXPECTATION_OWNER,
            "task_type": TaskType.GENERATE_EXPECTATION_UNIT,
            "required_output_schema": "ExpectationConstructionResult",
            "permissions": definition.runtime.to_permissions(),
        },
        deep=True,
    )

    injected = PromptInjector().inject(task, definition)

    assert injected.prompt_bundle is not None
    assert "agent.o1" in injected.prompt_bundle.prompt_block_ids
    assert "expectation-construction" in injected.prompt_bundle.internal_task_skill_ids
    assert "macro-analysis" not in injected.prompt_bundle.external_skill_package_ids


def test_prompt_injector_rejects_unknown_explicit_external_package() -> None:
    definition = default_agent_registry().get(AgentName.C2_MACRO_RESEARCH)
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.C2_MACRO_RESEARCH,
            "task_type": TaskType.GENERATE_GLOBAL_RESEARCH,
            "input_context": {"external_skill_package_ids": ["not-registered"]},
            "permissions": definition.runtime.to_permissions(),
        },
        deep=True,
    )

    with pytest.raises(UnknownPromptResourceError):
        PromptInjector().inject(task, definition)


def test_prompt_assembler_does_not_embed_full_agent_task_dump() -> None:
    definition = default_agent_registry().get(AgentName.C2_MACRO_RESEARCH)
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.C2_MACRO_RESEARCH,
            "task_type": TaskType.GENERATE_GLOBAL_RESEARCH,
            "permissions": definition.runtime.to_permissions(),
        },
        deep=True,
    )
    injected = PromptInjector().inject(task, definition)

    assembled = PromptAssembler().assemble(
        injected,
        definition,
        injected.prompt_bundle,
        None,
        [],
    )

    assert "System / Agent Prompt Blocks" in assembled.instructions
    assert "External Skill Packages" in assembled.instructions
    assert '"task_summary"' in assembled.user_prompt
    assert '"skill_bundle"' not in assembled.user_prompt


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
