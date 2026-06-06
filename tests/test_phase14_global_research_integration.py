import pytest

from doxagent.models import AgentName, DocumentType, ResearchSection, ResultStatus
from doxagent.workflows import (
    BlackboardInitializationWorkflow,
    GlobalResearchAssembler,
    GlobalResearchInputs,
    GlobalResearchModuleRunner,
    WorkflowNode,
    WorkflowRunStatus,
)
from doxagent.workflows.errors import WorkflowContractError
from tests.test_phase13_real_workflow import StructuredInitializationRunner


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


def test_global_research_assembler_allows_agent_sections_without_evidence_refs() -> None:
    section = ResearchSection(
        text="Provider unavailable; data gap captured in unknowns.",
        summary="Provider unavailable.",
        evidence_refs=[],
        author_agent=AgentName.C1_FUNDAMENTAL_RESEARCH,
    )

    document = GlobalResearchAssembler().assemble_from_sections(
        "NVDA",
        fundamental_report=section,
        macro_report=section.model_copy(update={"author_agent": AgentName.C2_MACRO_RESEARCH}),
        industry_report=section.model_copy(
            update={"author_agent": AgentName.C3_INDUSTRY_RESEARCH}
        ),
        market_narrative_report=section.model_copy(
            update={"author_agent": AgentName.O1_EXPECTATION_OWNER}
        ),
        market_trace_report=section.model_copy(update={"author_agent": AgentName.O4_MARKET_TRACE}),
    )

    assert document.fundamental_report.evidence_refs == []


def test_initialization_workflow_builds_global_research_from_phase8_modules() -> None:
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=StructuredInitializationRunner(include_blockers=False),
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
    assert "industry" in result.checkpoint.metadata["global_research_downstream_context"]

    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    assert len(run.working_memory) == 5
    assert {entry.content_type for entry in run.working_memory} == {
        "global_research_agent_result",
    }
    global_objects = run.belief_state.documents[DocumentType.GLOBAL_RESEARCH]
    document = next(iter(global_objects.values()))["document"]
    assert "fundamental_report" in document
    assert "macro_report" in document
    assert "industry_report" in document
    assert "market_trace_report" in document
    assert "Pending O1/DoxAtlas" not in document["market_narrative_report"]["summary"]


def test_expectation_generation_receives_global_research_context() -> None:
    runner = StructuredInitializationRunner(include_blockers=False)
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=runner,
    )

    result = workflow.run("NVDA", stop_after=WorkflowNode.GENERATE_EXPECTATION_UNITS)

    assert result.status is WorkflowRunStatus.RUNNING
    o1_tasks = [
        task
        for task in runner.tasks
        if task.agent_name is AgentName.O1_EXPECTATION_OWNER
        and task.run_metadata.workflow_node == WorkflowNode.GENERATE_EXPECTATION_UNITS.value
    ]
    assert o1_tasks
    context = o1_tasks[0].input_context["global_research_context"]
    assert context["ticker"] == "NVDA"
    assert "fundamental_report" in context["sections"]
    assert "macro_report" in context["sections"]
    assert "industry_report" in context["sections"]
    assert "market_trace_report" in context["sections"]
    assert "market_narrative_report" not in context["sections"]
    assert o1_tasks[0].permissions.writable_targets == [DocumentType.EXPECTATION_UNIT.value]
    assert o1_tasks[0].input_context["required_tool_names"] == ["doxa_get_narrative_report"]


def test_build_global_research_tasks_use_draft_permissions_and_no_prior_sections() -> None:
    runner = StructuredInitializationRunner(include_blockers=False)
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=runner,
    )

    result = workflow.run("NVDA", stop_after=WorkflowNode.BUILD_GLOBAL_RESEARCH)

    assert result.status is WorkflowRunStatus.RUNNING
    build_tasks = [
        task
        for task in runner.tasks
        if task.run_metadata.workflow_node == WorkflowNode.BUILD_GLOBAL_RESEARCH.value
    ]
    assert build_tasks
    assert all(task.permissions.can_raise_objection is False for task in build_tasks)
    assert all(
        task.permissions.writable_targets == [DocumentType.GLOBAL_RESEARCH.value]
        for task in build_tasks
    )
    o1_tasks = [task for task in build_tasks if task.agent_name is AgentName.O1_EXPECTATION_OWNER]
    assert o1_tasks
    assert "prior_sections" not in o1_tasks[0].input_context


def test_known_events_task_only_allows_known_events_writes() -> None:
    runner = StructuredInitializationRunner(include_blockers=False)
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=runner,
    )

    result = workflow.run("NVDA", stop_after=WorkflowNode.GENERATE_KNOWN_EVENTS)

    assert result.status is WorkflowRunStatus.RUNNING
    known_event_tasks = [
        task
        for task in runner.tasks
        if task.run_metadata.workflow_node == WorkflowNode.GENERATE_KNOWN_EVENTS.value
    ]
    assert known_event_tasks
    assert known_event_tasks[0].permissions.writable_targets == [DocumentType.KNOWN_EVENTS.value]


def test_o2_monitoring_tasks_use_node_specific_write_targets() -> None:
    runner = StructuredInitializationRunner(include_blockers=False)
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=runner,
    )

    result = workflow.run("NVDA")

    assert result.status is WorkflowRunStatus.COMPLETED
    config_tasks = [
        task
        for task in runner.tasks
        if task.run_metadata.workflow_node == WorkflowNode.GENERATE_MONITORING_CONFIG.value
    ]
    policy_tasks = [
        task
        for task in runner.tasks
        if task.run_metadata.workflow_node == WorkflowNode.GENERATE_MONITORING_POLICY.value
    ]
    assert config_tasks
    assert policy_tasks
    assert config_tasks[0].permissions.writable_targets == [
        DocumentType.MONITORING_CONFIG.value
    ]
    assert policy_tasks[0].permissions.writable_targets == [
        DocumentType.MONITORING_POLICY.value
    ]


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
        runner=StructuredInitializationRunner(include_blockers=False),
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
