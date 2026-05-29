from doxagent.agents import MockAgentRunner, default_agent_registry
from doxagent.models import AgentName, DocumentType, ResultStatus
from doxagent.workflows import (
    INITIALIZATION_NODES,
    BlackboardInitializationWorkflow,
    InitializationMockResultFactory,
    WorkflowCheckpoint,
    WorkflowNode,
    WorkflowRunStatus,
)


def test_initialization_workflow_runs_mock_ticker_to_completion() -> None:
    workflow = BlackboardInitializationWorkflow()

    result = workflow.run("NVDA")

    assert result.status is WorkflowRunStatus.COMPLETED
    assert result.checkpoint.completed_nodes == list(INITIALIZATION_NODES)
    assert result.summary.stable_document_types == [
        DocumentType.GLOBAL_RESEARCH,
        DocumentType.EXPECTATION_UNIT,
        DocumentType.KNOWN_EVENTS,
        DocumentType.MONITORING_CONFIG,
        DocumentType.MONITORING_POLICY,
    ]
    assert result.summary.commit_count == 5
    assert result.summary.working_memory_count >= 5

    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    assert set(run.belief_state.documents) == {
        DocumentType.GLOBAL_RESEARCH,
        DocumentType.EXPECTATION_UNIT,
        DocumentType.KNOWN_EVENTS,
        DocumentType.MONITORING_CONFIG,
        DocumentType.MONITORING_POLICY,
    }
    assert len(run.commit_log) == 5
    assert run.working_memory
    assert run.objections[0].is_unresolved is False
    assert run.delegations[0].is_blocking is False


def test_initialization_workflow_enforces_document_order() -> None:
    workflow = BlackboardInitializationWorkflow()
    partial = workflow.run("NVDA", stop_after=WorkflowNode.START_TICKER_INITIALIZATION)
    bad_checkpoint = partial.checkpoint.model_copy(
        update={"next_node": WorkflowNode.GENERATE_KNOWN_EVENTS},
        deep=True,
    )

    result = workflow.resume(bad_checkpoint)

    assert result.status is WorkflowRunStatus.BLOCKED
    assert result.error is not None
    assert "global_research" in result.error
    assert workflow.blackboard.get_run(partial.checkpoint.run_id).commit_log == []


def test_o2_registry_permissions_cover_config_and_policy_documents() -> None:
    definition = default_agent_registry().get(AgentName.O2_MONITORING_CONFIG)
    permissions = definition.runtime.to_permissions()

    assert DocumentType.MONITORING_CONFIG.value in permissions.writable_targets
    assert DocumentType.MONITORING_POLICY.value in permissions.writable_targets


def test_blockers_stop_expectation_promotion_without_commit() -> None:
    workflow = BlackboardInitializationWorkflow(auto_resolve_blockers=False)

    result = workflow.run("NVDA")

    assert result.status is WorkflowRunStatus.BLOCKED
    assert result.checkpoint.next_node is WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE
    assert result.summary.stable_document_types == [DocumentType.GLOBAL_RESEARCH]
    assert result.summary.commit_count == 1
    assert result.summary.unresolved_objection_count == 1
    assert result.summary.blocking_delegation_count == 1

    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    assert set(run.belief_state.documents) == {DocumentType.GLOBAL_RESEARCH}
    assert len(run.commit_log) == 1


def test_blocked_checkpoint_can_resume_after_manual_resolution() -> None:
    workflow = BlackboardInitializationWorkflow(auto_resolve_blockers=False)
    blocked = workflow.run("NVDA")
    run = workflow.blackboard.get_run(blocked.checkpoint.run_id)

    workflow.blackboard.resolve_objection(
        blocked.checkpoint.run_id,
        run.objections[0].objection_id,
        "Manual review resolved the objection.",
    )
    workflow.blackboard.complete_delegation(
        blocked.checkpoint.run_id,
        run.delegations[0].delegation_id,
        "Manual fact-check completed.",
    )

    resumed = workflow.resume(blocked.checkpoint)

    assert resumed.status is WorkflowRunStatus.COMPLETED
    assert resumed.summary.commit_count == 5
    assert resumed.summary.unresolved_objection_count == 0
    assert resumed.summary.blocking_delegation_count == 0


def test_checkpoint_round_trips_and_resumes_in_same_process() -> None:
    workflow = BlackboardInitializationWorkflow()
    partial = workflow.run("NVDA", stop_after=WorkflowNode.GENERATE_EXPECTATION_UNITS)

    restored = WorkflowCheckpoint.model_validate_json(partial.checkpoint.model_dump_json())
    resumed = workflow.resume(restored)

    assert resumed.status is WorkflowRunStatus.COMPLETED
    assert resumed.checkpoint.completed_nodes == list(INITIALIZATION_NODES)
    assert resumed.summary.commit_count == 5


def test_mock_agent_runner_factory_mode_preserves_result_contract() -> None:
    runner = MockAgentRunner(
        default_agent_registry(),
        result_factory=InitializationMockResultFactory(include_blockers=False),
    )
    workflow = BlackboardInitializationWorkflow(runner=runner)

    result = workflow.run("NVDA", stop_after=WorkflowNode.BUILD_GLOBAL_RESEARCH)

    assert result.status is WorkflowRunStatus.RUNNING
    assert runner.calls == 1
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    assert run.working_memory[0].payload["status"] == ResultStatus.SUCCEEDED.value
