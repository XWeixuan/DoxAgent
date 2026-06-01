import pytest

from doxagent.agents import MockAgentRunner, default_agent_registry
from doxagent.models import AgentName, DocumentType, ResultStatus
from doxagent.workflows import (
    BlackboardInitializationWorkflow,
    GlobalResearchAssembler,
    GlobalResearchInputs,
    GlobalResearchModuleRunner,
    InitializationMockResultFactory,
    WorkflowNode,
    WorkflowRunStatus,
)
from doxagent.workflows.errors import WorkflowContractError


def test_global_research_module_runner_calls_phase8_modules() -> None:
    inputs = GlobalResearchInputs(
        sector_or_theme="AI accelerators",
        universe=["NVDA", "AMD"],
        benchmarks=["SPY", "QQQ"],
        peers=["AMD"],
    )

    results = GlobalResearchModuleRunner().run("NVDA", inputs)

    assert [result.agent_name for result in results] == [
        AgentName.C1_FUNDAMENTAL_RESEARCH,
        AgentName.C2_MACRO_RESEARCH,
        AgentName.C3_INDUSTRY_RESEARCH,
        AgentName.O4_MARKET_TRACE,
    ]
    for result in results:
        assert result.status is ResultStatus.SUCCEEDED
        assert isinstance(result.payload["structured"], dict)
        assert result.payload["markdown_summary"]
        assert result.evidence_refs

    industry = next(
        result for result in results if result.agent_name is AgentName.C3_INDUSTRY_RESEARCH
    )
    assert industry.payload["structured"]["downstream_hints"]


def test_global_research_assembler_builds_document_from_module_results() -> None:
    inputs = GlobalResearchInputs().resolved("NVDA")
    results = GlobalResearchModuleRunner().run("NVDA", inputs)

    document = GlobalResearchAssembler().assemble("NVDA", inputs, results)

    assert document.document_type is DocumentType.GLOBAL_RESEARCH
    assert document.fundamental_report.author_agent is AgentName.C1_FUNDAMENTAL_RESEARCH
    assert document.macro_report.author_agent is AgentName.C2_MACRO_RESEARCH
    assert document.industry_report.author_agent is AgentName.C3_INDUSTRY_RESEARCH
    assert document.market_trace_report.author_agent is AgentName.O4_MARKET_TRACE
    assert document.market_narrative_report.author_agent is AgentName.O1_EXPECTATION_OWNER
    assert "Pending O1/DoxAtlas" in document.market_narrative_report.summary
    assert document.fundamental_report.evidence_refs
    assert document.macro_report.evidence_refs
    assert document.industry_report.evidence_refs
    assert document.market_trace_report.evidence_refs


def test_global_research_assembler_extracts_downstream_context() -> None:
    inputs = GlobalResearchInputs().resolved("NVDA")
    results = GlobalResearchModuleRunner().run("NVDA", inputs)

    context = GlobalResearchAssembler().downstream_context(results)

    assert context["fundamental"]["risks"]
    assert context["fundamental"]["catalysts"]
    assert context["macro"]["monitoring_dashboard"]
    assert context["industry"]["downstream_hints"]
    assert context["market_trace"]["relative_performance"]
    assert context["market_trace"]["technical_signals"]


def test_global_research_assembler_requires_all_modules() -> None:
    inputs = GlobalResearchInputs().resolved("NVDA")
    results = GlobalResearchModuleRunner().run("NVDA", inputs)

    with pytest.raises(WorkflowContractError, match="missing required agents"):
        GlobalResearchAssembler().assemble("NVDA", inputs, results[:-1])


def test_global_research_assembler_requires_evidence() -> None:
    inputs = GlobalResearchInputs().resolved("NVDA")
    results = GlobalResearchModuleRunner().run("NVDA", inputs)
    stripped = results[0].model_copy(update={"evidence_refs": []}, deep=True)

    with pytest.raises(WorkflowContractError, match="no evidence refs"):
        GlobalResearchAssembler().assemble("NVDA", inputs, [stripped, *results[1:]])


def test_initialization_workflow_builds_global_research_from_phase8_modules() -> None:
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=MockAgentRunner(
            default_agent_registry(),
            result_factory=InitializationMockResultFactory(include_blockers=True),
        ),
    )
    inputs = GlobalResearchInputs(
        sector_or_theme="AI accelerators",
        industry_angle="data-center demand",
        universe=["NVDA", "AMD"],
        peers=["AMD"],
    )

    result = workflow.run(
        "NVDA",
        research_inputs=inputs,
        stop_after=WorkflowNode.BUILD_GLOBAL_RESEARCH,
    )

    assert result.status is WorkflowRunStatus.RUNNING
    assert result.summary.stable_document_types == [DocumentType.GLOBAL_RESEARCH]
    assert result.summary.commit_count == 1
    assert result.checkpoint.metadata["research_inputs"]["sector_or_theme"] == "AI accelerators"
    assert result.checkpoint.metadata["global_research_downstream_context"]["industry"][
        "downstream_hints"
    ]

    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    assert len(run.working_memory) == 4
    assert {entry.content_type for entry in run.working_memory} == {
        "global_research_module_result",
    }
    global_objects = run.belief_state.documents[DocumentType.GLOBAL_RESEARCH]
    document = next(iter(global_objects.values()))["document"]
    assert "fundamental_report" in document
    assert "macro_report" in document
    assert "industry_report" in document
    assert "market_trace_report" in document
    assert "Pending O1/DoxAtlas" in document["market_narrative_report"]["summary"]


def test_global_research_inputs_round_trip_for_resume() -> None:
    inputs = GlobalResearchInputs(
        sector_or_theme="US data-center power",
        industry_angle="supply gap",
        universe=["VST", "CEG"],
        benchmarks=["SPY"],
        peers=["NRG"],
    )
    restored = GlobalResearchInputs.model_validate_json(inputs.model_dump_json())
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=MockAgentRunner(
            default_agent_registry(),
            result_factory=InitializationMockResultFactory(include_blockers=True),
        ),
    )

    result = workflow.run(
        "VST",
        research_inputs=restored,
        stop_after=WorkflowNode.BUILD_GLOBAL_RESEARCH,
    )
    resumed = workflow.resume(result.checkpoint)

    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    assert resumed.status is WorkflowRunStatus.COMPLETED
    assert [
        commit.patch.target.document_type for commit in run.commit_log
    ].count(DocumentType.GLOBAL_RESEARCH) == 1
