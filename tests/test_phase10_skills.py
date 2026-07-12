from pathlib import Path

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
from doxagent.prompts import (
    PromptAssembler,
    PromptInjector,
    UnknownPromptResourceError,
    lint_prompt_resources,
)
from doxagent.prompts.registry import default_prompt_registry
from doxagent.skills import UnknownSkillError, default_skill_registry
from doxagent.skills.injection import SkillInjector
from doxagent.workflow_memory import WorkflowMemoryCompiler
from tests.fixtures.phase1_contracts import agent_task

PROMPT_ROOT = Path(__file__).resolve().parents[1] / "prompts"


def test_default_skill_registry_contains_migrated_external_skills() -> None:
    registry = default_skill_registry()
    expected = {
        "macro-analysis",
        "financial-statement",
        "valuation-model",
        "sector-overview",
        "competitive-analysis",
        "ohlcv-orchestration",
        "quote-context",
        "relative-performance",
        "technical-signal-analysis",
        "market-data-quality",
    }

    assert set(registry.ids()) == expected
    assert "doxagent-source-discipline" not in registry.ids()
    skill = registry.get("sector-overview")
    restored = skill.model_validate_json(skill.model_dump_json())
    assert restored.skill_id == "sector-overview"
    assert restored.source_kind.value == "financial_services"
    assert restored.allowed_tools == []
    assert restored.content.output_requirements == []


def test_external_skills_are_plain_text_without_runtime_constraints() -> None:
    skill_registry = default_skill_registry()

    for skill_id in skill_registry.ids():
        skill = skill_registry.get(skill_id)
        assert skill.allowed_tools == []
        assert skill.content.output_requirements == []
        assert skill.content.guardrails == []
        assert skill.content.prompt_fragment


def test_prompt_external_package_legacy_metadata_is_loader_only() -> None:
    prompt_registry = default_prompt_registry()

    for package in prompt_registry.external_packages():
        assert package.body
        assert package.kind.value == "external_skill_package"


def test_skill_front_matter_does_not_carry_runtime_constraints() -> None:
    forbidden = ("allowed_tools", "output_requirements", "guardrails")
    for directory in ("internal_task_skills", "external_skill_packages"):
        for path in (PROMPT_ROOT / directory).glob("*.md"):
            raw = path.read_text(encoding="utf-8")
            front_matter = raw.split("+++\n", 2)[1]
            for key in forbidden:
                assert f"{key} =" not in front_matter


def test_prompt_resource_lint_passes_for_repo_prompts() -> None:
    issues = lint_prompt_resources(PROMPT_ROOT)

    assert issues == []


def test_skill_registry_unknown_and_deep_copy_behavior() -> None:
    registry = default_skill_registry()

    with pytest.raises(UnknownSkillError):
        registry.get("missing-skill")

    copy = registry.get("macro-analysis")
    copy.content.output_requirements.append("local mutation")

    assert "local mutation" not in registry.get("macro-analysis").content.output_requirements


def test_skill_injector_selects_only_runtime_loaded_skills() -> None:
    registry = default_skill_registry()
    agent_registry = default_agent_registry()
    definition = agent_registry.get(AgentName.C2_MACRO_RESEARCH)
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.C2_MACRO_RESEARCH,
            "task_type": TaskType.GENERATE_GLOBAL_RESEARCH,
            "input_context": {"loaded_skill_ids": ["macro-analysis"]},
            "required_output_schema": "ResearchSection",
            "permissions": definition.runtime.to_permissions(),
        },
        deep=True,
    )

    injected = SkillInjector(registry).inject(task, definition)

    assert task.skill_bundle is None
    assert injected.skill_bundle is not None
    assert injected.skill_bundle.skill_ids == ["macro-analysis"]


def test_unknown_explicit_skill_request_fails() -> None:
    definition = default_agent_registry().get(AgentName.C1_FUNDAMENTAL_RESEARCH)
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.C1_FUNDAMENTAL_RESEARCH,
            "task_type": TaskType.GENERATE_GLOBAL_RESEARCH,
            "input_context": {"loaded_skill_ids": ["not-registered"]},
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

    assert result.payload["skill_ids"] == []
    assert result.payload["skill_versions"] == {}
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
    assert snapshot_skill_ids == []
    assert injected_task.skill_bundle.skill_ids == []


def test_prompt_registry_distinguishes_prompt_internal_and_external_resources() -> None:
    registry = default_prompt_registry()

    system = registry.get("system.doxagent_core")
    internal = registry.get("expectation-construction")
    external = registry.get("macro-analysis")

    assert system.kind.value == "prompt_block"
    assert internal.kind.value == "internal_task_skill"
    assert external.kind.value == "external_skill_package"
    assert registry.get("agent.o1").model_validate_json(registry.get("agent.o1").model_dump_json())


def test_document3_prompt_resources_load_and_replace_generic_agent_prompts() -> None:
    registry = default_prompt_registry()
    agent_registry = default_agent_registry()

    known_events_skill = registry.get("known-events")
    known_events_review = registry.get("known-events-review")
    o1_document3_prompt = registry.get("agent.o1.document3_known_events")
    o4_document3_prompt = registry.get("agent.o4.document3_monitoring_policy")

    assert "`source_note`:" in known_events_skill.body
    assert "`【cite:O#】`" in known_events_skill.body
    assert "EvidenceRef" not in known_events_skill.body
    assert known_events_review.manual_only is True
    assert known_events_review.workflow_nodes == []
    assert o1_document3_prompt.replaces_prompt_blocks == ["agent.o1"]
    assert o4_document3_prompt.replaces_prompt_blocks == ["agent.o4"]
    assert "document-level `no_action_rationale`" in registry.get("monitoring-policy").body

    o1_definition = agent_registry.get(AgentName.O1_EXPECTATION_OWNER)
    o1_task = agent_task().model_copy(
        update={
            "agent_name": AgentName.O1_EXPECTATION_OWNER,
            "task_type": TaskType.GENERATE_KNOWN_EVENTS,
            "required_output_schema": "KnownEventsDocument",
            "permissions": o1_definition.runtime.to_permissions(),
            "run_metadata": agent_task().run_metadata.model_copy(
                update={"workflow_node": "GenerateKnownEvents"}
            ),
        },
        deep=True,
    )
    o1_injected = PromptInjector().inject(o1_task, o1_definition)
    assert "agent.o1.document3_known_events" in o1_injected.prompt_bundle.prompt_block_ids
    assert "agent.o1" not in o1_injected.prompt_bundle.prompt_block_ids
    assert "known-events" in o1_injected.prompt_bundle.internal_task_skill_ids
    assert "known-events-review" not in o1_injected.prompt_bundle.internal_task_skill_ids

    o4_definition = agent_registry.get(AgentName.O4_MARKET_TRACE)
    o4_task = agent_task().model_copy(
        update={
            "agent_name": AgentName.O4_MARKET_TRACE,
            "task_type": TaskType.GENERATE_MONITORING_POLICY,
            "required_output_schema": "MonitoringPolicyDocument",
            "permissions": o4_definition.runtime.to_permissions(),
            "run_metadata": agent_task().run_metadata.model_copy(
                update={"workflow_node": "GenerateMonitoringPolicy"}
            ),
        },
        deep=True,
    )
    o4_injected = PromptInjector().inject(o4_task, o4_definition)
    assert "agent.o4.document3_monitoring_policy" in o4_injected.prompt_bundle.prompt_block_ids
    assert "agent.o4" not in o4_injected.prompt_bundle.prompt_block_ids
    assert "monitoring-policy" in o4_injected.prompt_bundle.internal_task_skill_ids
    assert "ticker_price_tracking" not in o4_injected.prompt_bundle.internal_task_skill_ids


def test_document3_review_skills_auto_inject_only_for_existing_review_nodes() -> None:
    agent_registry = default_agent_registry()

    c1_definition = agent_registry.get(AgentName.C1_FUNDAMENTAL_RESEARCH)
    c1_task = agent_task().model_copy(
        update={
            "agent_name": AgentName.C1_FUNDAMENTAL_RESEARCH,
            "task_type": TaskType.REVIEW_MONITORING_CONFIG,
            "required_output_schema": "ExpectationFieldReviewResult",
            "permissions": c1_definition.runtime.to_permissions(),
            "run_metadata": agent_task().run_metadata.model_copy(
                update={"workflow_node": "ReviewMonitoringConfig"}
            ),
        },
        deep=True,
    )
    c1_injected = PromptInjector().inject(c1_task, c1_definition)
    assert "monitoring-config-review" in c1_injected.prompt_bundle.internal_task_skill_ids

    c3_definition = agent_registry.get(AgentName.C3_INDUSTRY_RESEARCH)
    c3_task = c1_task.model_copy(
        update={
            "agent_name": AgentName.C3_INDUSTRY_RESEARCH,
            "permissions": c3_definition.runtime.to_permissions(),
        },
        deep=True,
    )
    c3_injected = PromptInjector().inject(c3_task, c3_definition)
    assert "monitoring-config-review" in c3_injected.prompt_bundle.internal_task_skill_ids

    o2_definition = agent_registry.get(AgentName.O2_MONITORING_CONFIG)
    o2_task = agent_task().model_copy(
        update={
            "agent_name": AgentName.O2_MONITORING_CONFIG,
            "task_type": TaskType.REVIEW_MONITORING_POLICY,
            "required_output_schema": "ExpectationFieldReviewResult",
            "permissions": o2_definition.runtime.to_permissions(),
            "run_metadata": agent_task().run_metadata.model_copy(
                update={"workflow_node": "ReviewMonitoringPolicy"}
            ),
        },
        deep=True,
    )
    o2_injected = PromptInjector().inject(o2_task, o2_definition)
    assert "monitoring-policy-review" in o2_injected.prompt_bundle.internal_task_skill_ids

    a1_definition = agent_registry.get(AgentName.A1_DOXATLAS_AUDIT)
    future_known_events_review_task = agent_task().model_copy(
        update={
            "agent_name": AgentName.A1_DOXATLAS_AUDIT,
            "task_type": TaskType.REVIEW_EXPECTATION_FIELD,
            "required_output_schema": "DoxAtlasAuditResult",
            "permissions": a1_definition.runtime.to_permissions(),
            "run_metadata": agent_task().run_metadata.model_copy(
                update={"workflow_node": "ReviewKnownEvents"}
            ),
        },
        deep=True,
    )
    a1_injected = PromptInjector().inject(
        future_known_events_review_task,
        a1_definition,
    )
    assert "known-events-review" not in a1_injected.prompt_bundle.internal_task_skill_ids


def test_c1_c3_task_text_moved_to_internal_task_skills() -> None:
    registry = default_prompt_registry()

    c1_prompt = registry.get("agent.c1")
    c3_prompt = registry.get("agent.c3")
    fundamental = registry.get("fundamental-research")
    industry = registry.get("industry-research")

    assert "## Task" not in c1_prompt.body
    assert "## Task" not in c3_prompt.body
    assert "Use load_skill(\"financial-statement\")" in fundamental.body
    assert "Use load_skill(\"valuation-model\")" in fundamental.body
    assert "Invoke `sector-overview` skill" in industry.body
    assert "Invoke `competitive-analysis` skill" in industry.body


def test_prompt_injector_selects_o1_internal_sop_without_external_packages() -> None:
    agent_registry = default_agent_registry()
    definition = agent_registry.get(AgentName.O1_EXPECTATION_OWNER)
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.O1_EXPECTATION_OWNER,
            "task_type": TaskType.GENERATE_EXPECTATION_UNIT,
            "required_output_schema": "ExpectationShellConstructionResult",
            "permissions": definition.runtime.to_permissions(),
            "run_metadata": agent_task().run_metadata.model_copy(
                update={"workflow_node": "GenerateExpectationConstruction"}
            ),
        },
        deep=True,
    )

    injected = PromptInjector().inject(task, definition)

    assert injected.prompt_bundle is not None
    assert "agent.o1" in injected.prompt_bundle.prompt_block_ids
    assert "expectation-construction" in injected.prompt_bundle.internal_task_skill_ids
    assert "macro-analysis" not in injected.prompt_bundle.external_skill_package_ids

    detail_task = task.model_copy(
        update={
            "task_type": TaskType.GENERATE_EXPECTATION_DETAIL,
            "required_output_schema": "ExpectationDetailResult",
            "run_metadata": task.run_metadata.model_copy(
                update={"workflow_node": "GenerateExpectationDetails"}
            ),
        },
        deep=True,
    )
    detail_injected = PromptInjector().inject(detail_task, definition)
    assert "expectation-detail" in detail_injected.prompt_bundle.internal_task_skill_ids
    assert "expectation-construction" not in detail_injected.prompt_bundle.internal_task_skill_ids

    resolve_task = task.model_copy(
        update={
            "run_metadata": task.run_metadata.model_copy(
                update={"workflow_node": "ResolveExpectationConstruction"}
            )
        },
        deep=True,
    )
    resolve_injected = PromptInjector().inject(resolve_task, definition)
    assert "expectation-construction" in resolve_injected.prompt_bundle.internal_task_skill_ids

    field_resolve_task = task.model_copy(
        update={
            "task_type": TaskType.REVIEW_EXPECTATION_FIELD,
            "input_context": {"internal_task_skill_ids": ["document2-field-repair"]},
            "required_output_schema": "Document2FieldRepairResult",
            "run_metadata": task.run_metadata.model_copy(
                update={"workflow_node": "ResolveObjectionsAndDelegations"}
            ),
        },
        deep=True,
    )
    field_resolve_injected = PromptInjector().inject(field_resolve_task, definition)
    assert "document2-field-repair" in (
        field_resolve_injected.prompt_bundle.internal_task_skill_ids
    )
    assert "document2-resolution-plan" not in (
        field_resolve_injected.prompt_bundle.internal_task_skill_ids
    )
    assert "expectation-construction" not in (
        field_resolve_injected.prompt_bundle.internal_task_skill_ids
    )
    legacy_resolve_task = field_resolve_task.model_copy(
        update={
            "input_context": {"internal_task_skill_ids": ["document2-resolution-plan"]},
            "required_output_schema": "Document2ResolutionPlan",
        },
        deep=True,
    )
    legacy_resolve_injected = PromptInjector().inject(legacy_resolve_task, definition)
    assert "document2-resolution-plan" in (
        legacy_resolve_injected.prompt_bundle.internal_task_skill_ids
    )
    assert "document2-field-repair" not in (
        legacy_resolve_injected.prompt_bundle.internal_task_skill_ids
    )

    narrative_task = task.model_copy(
        update={
            "task_type": TaskType.GENERATE_GLOBAL_NARRATIVE_REPORT,
            "required_output_schema": "ResearchSection",
            "run_metadata": task.run_metadata.model_copy(
                update={"workflow_node": "GenerateGlobalNarrativeReport"}
            ),
        },
        deep=True,
    )
    narrative_injected = PromptInjector().inject(narrative_task, definition)
    assert "global_narrative_report" in narrative_injected.prompt_bundle.internal_task_skill_ids

    resolver_skill = default_prompt_registry().get("document2-resolution-plan")
    assert resolver_skill.manual_only is True
    assert "Document2ResolutionPlan" in resolver_skill.body
    assert "proposed_patches" in resolver_skill.body
    assert "list-wrapped" in resolver_skill.body


def test_prompt_injector_selects_a1_node_specific_internal_skills() -> None:
    definition = default_agent_registry().get(AgentName.A1_DOXATLAS_AUDIT)
    base_task = agent_task().model_copy(
        update={
            "agent_name": AgentName.A1_DOXATLAS_AUDIT,
            "task_type": TaskType.REVIEW_EXPECTATION_FIELD,
            "required_output_schema": "DoxAtlasAuditResult",
            "permissions": definition.runtime.to_permissions(),
        },
        deep=True,
    )

    construction_task = base_task.model_copy(
        update={
            "run_metadata": base_task.run_metadata.model_copy(
                update={"workflow_node": "ReviewExpectationConstruction"}
            )
        },
        deep=True,
    )
    construction_injected = PromptInjector().inject(construction_task, definition)

    field_task = base_task.model_copy(
        update={
            "run_metadata": base_task.run_metadata.model_copy(
                update={"workflow_node": "ReviewExpectationFields"}
            )
        },
        deep=True,
    )
    field_injected = PromptInjector().inject(field_task, definition)

    prompt_registry = default_prompt_registry()
    assert "doxatlas-audit" not in prompt_registry.ids()
    assert "a1-expectation-construction-audit" in (
        construction_injected.prompt_bundle.internal_task_skill_ids
    )
    assert "a1-expectation-field-audit" not in (
        construction_injected.prompt_bundle.internal_task_skill_ids
    )
    assert "a1-expectation-field-audit" in field_injected.prompt_bundle.internal_task_skill_ids
    assert "a1-expectation-construction-audit" not in (
        field_injected.prompt_bundle.internal_task_skill_ids
    )


def test_prompt_injector_keeps_a2_method_in_agent_prompt_without_internal_skill() -> None:
    definition = default_agent_registry().get(AgentName.A2_FACT_CHECK)
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.A2_FACT_CHECK,
            "task_type": TaskType.DELEGATED_RETRIEVAL,
            "required_output_schema": "DelegatedRetrievalResult",
            "permissions": definition.runtime.to_permissions(),
            "run_metadata": agent_task().run_metadata.model_copy(
                update={"workflow_node": "ResolveObjectionsAndDelegations"}
            ),
        },
        deep=True,
    )

    injected = PromptInjector().inject(task, definition)
    prompt_registry = default_prompt_registry()
    a2_prompt = prompt_registry.get("agent.a2")

    assert "tavily-retrieval-fact-check" not in prompt_registry.ids()
    assert "agent.a2" in injected.prompt_bundle.prompt_block_ids
    assert injected.prompt_bundle.internal_task_skill_ids == []
    assert "anysearch.search" in a2_prompt.body
    assert "Do not dump the whole delegated prompt into a search box." in a2_prompt.body


def test_prompt_injector_selects_global_research_internal_skills_for_c1_c3() -> None:
    agent_registry = default_agent_registry()

    c1_definition = agent_registry.get(AgentName.C1_FUNDAMENTAL_RESEARCH)
    c1_task = agent_task().model_copy(
        update={
            "agent_name": AgentName.C1_FUNDAMENTAL_RESEARCH,
            "task_type": TaskType.GENERATE_GLOBAL_RESEARCH,
            "required_output_schema": "ResearchSection",
            "permissions": c1_definition.runtime.to_permissions(),
            "run_metadata": agent_task().run_metadata.model_copy(
                update={"workflow_node": "BuildGlobalResearch"}
            ),
        },
        deep=True,
    )
    c1_injected = PromptInjector().inject(c1_task, c1_definition)

    assert "fundamental-research" in c1_injected.prompt_bundle.internal_task_skill_ids
    assert c1_injected.prompt_bundle.external_skill_package_ids == []
    c1_skill = default_prompt_registry().get("fundamental-research")
    assert "Document 1" in c1_skill.body
    assert "current market attention" in c1_skill.body

    c3_definition = agent_registry.get(AgentName.C3_INDUSTRY_RESEARCH)
    c3_task = c1_task.model_copy(
        update={
            "agent_name": AgentName.C3_INDUSTRY_RESEARCH,
            "permissions": c3_definition.runtime.to_permissions(),
        },
        deep=True,
    )
    c3_injected = PromptInjector().inject(c3_task, c3_definition)

    assert "industry-research" in c3_injected.prompt_bundle.internal_task_skill_ids
    assert c3_injected.prompt_bundle.external_skill_package_ids == []

    o4_definition = agent_registry.get(AgentName.O4_MARKET_TRACE)
    o4_task = c1_task.model_copy(
        update={
            "agent_name": AgentName.O4_MARKET_TRACE,
            "permissions": o4_definition.runtime.to_permissions(),
        },
        deep=True,
    )
    o4_injected = PromptInjector().inject(o4_task, o4_definition)
    assert "ticker_price_tracking" in o4_injected.prompt_bundle.internal_task_skill_ids
    o4_skill = default_prompt_registry().get("ticker_price_tracking")
    assert "recent price and flow reaction first" in o4_skill.body


def test_c2_exposes_macro_analysis_not_global_macro() -> None:
    definition = default_agent_registry().get(AgentName.C2_MACRO_RESEARCH)

    assert definition.runtime.default_external_skill_package_ids == ["macro-analysis"]
    assert "global-macro" not in default_skill_registry().ids()
    assert "load_skill(\"macro-analysis\")" in default_prompt_registry().get("agent.c2").body
    assert "recent macro developments" in default_prompt_registry().get("agent.c2").body


def test_prompt_injector_rejects_unknown_loaded_external_package() -> None:
    definition = default_agent_registry().get(AgentName.C2_MACRO_RESEARCH)
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.C2_MACRO_RESEARCH,
            "task_type": TaskType.GENERATE_GLOBAL_RESEARCH,
            "input_context": {"loaded_external_skill_package_ids": ["not-registered"]},
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
        WorkflowMemoryCompiler().compile(injected),
        [],
    )

    assert "System / Agent Prompt Blocks" in assembled.instructions
    assert "External Skill Packages" not in assembled.instructions
    assert '"task_contract"' in assembled.user_prompt
    assert '"workflow_memory"' in assembled.user_prompt
    assert '"input_context"' not in assembled.user_prompt
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
    assert industry_output["skill_versions"]["sector-overview"]

    trace = MarketTraceAgentModule().run(ticker="AAPL")
    assert "ohlcv-orchestration" in trace.payload["metadata"]["skill_ids"]
    assert trace.payload["metadata"]["skill_versions"]["market-data-quality"]
