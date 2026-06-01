from doxagent.agents import MockAgentRunner, default_agent_registry
from doxagent.audit import AuditQueryService, build_run_debug_report
from doxagent.blackboard import BlackboardService, PatchValidationError
from doxagent.models import (
    AgentError,
    AgentName,
    AgentResult,
    AgentTask,
    BlackboardPatch,
    DocumentType,
    ResultStatus,
)
from doxagent.workflows import (
    BlackboardInitializationWorkflow,
    InitializationMockResultFactory,
    WorkflowNode,
    WorkflowRunStatus,
)
from tests.fixtures.phase1_contracts import patch


def completed_workflow() -> BlackboardInitializationWorkflow:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    workflow.run("NVDA")
    return workflow


def test_commit_log_query_and_field_trace_link_patch_agent_evidence_and_commit() -> None:
    workflow = completed_workflow()
    run = workflow.blackboard.repository.snapshot()
    blackboard_run = next(iter(run.values()))
    audit = AuditQueryService.for_run(blackboard_run)

    commits = audit.list_commit_log(document_type=DocumentType.EXPECTATION_UNIT)

    assert len(commits) == 1
    commit = commits[0]
    assert commit.patch_id
    assert commit.author_agent is AgentName.O1_EXPECTATION_OWNER
    assert commit.trigger_reason == "Promote reviewed expectation unit."
    assert commit.evidence_ids

    trace = audit.trace_field(
        DocumentType.EXPECTATION_UNIT,
        "exp_mock_core",
        "document",
    )
    assert trace is not None
    assert trace.commit_id == commit.commit_id
    assert trace.patch_id == commit.patch_id
    assert trace.author_agent is AgentName.O1_EXPECTATION_OWNER
    assert trace.evidence_ids == commit.evidence_ids
    assert trace.value["expectation_id"] == "exp_mock_core"


def test_audit_reports_only_unresolved_objections_and_blocking_delegations() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock", auto_resolve_blockers=False)
    result = workflow.run("NVDA")
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    audit = AuditQueryService.for_run(run)

    unresolved = audit.list_unresolved_objections()
    blocking = audit.list_blocking_delegations()

    assert len(unresolved) == 1
    assert unresolved[0].document_type is DocumentType.EXPECTATION_UNIT
    assert unresolved[0].field_path == "document"
    assert len(blocking) == 1
    assert blocking[0].target_agent is AgentName.A2_FACT_CHECK

    workflow.blackboard.resolve_objection(
        result.checkpoint.run_id,
        unresolved[0].objection_id,
        "Resolved.",
    )
    workflow.blackboard.complete_delegation(
        result.checkpoint.run_id,
        blocking[0].delegation_id,
        "Completed.",
    )
    audit_after = AuditQueryService.for_run(workflow.blackboard.get_run(result.checkpoint.run_id))
    assert audit_after.list_unresolved_objections() == []
    assert audit_after.list_blocking_delegations() == []


def test_run_debug_report_summarizes_business_audit_boundary_and_blockers() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock", auto_resolve_blockers=False)
    result = workflow.run("NVDA")
    run = workflow.blackboard.get_run(result.checkpoint.run_id)

    report = build_run_debug_report(run, result.checkpoint)

    assert report.workflow_status == "blocked"
    assert report.next_node == WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE.value
    assert report.belief_document_types == [DocumentType.GLOBAL_RESEARCH]
    assert report.commit_count == 1
    assert report.unresolved_objection_count == 1
    assert report.blocking_delegation_count == 1
    assert "LangSmith/model tracing is observational metadata" in report.audit_boundary_note


def test_missing_evidence_patch_failure_does_not_mutate_belief_state_or_commit_log() -> None:
    service = BlackboardService()
    run = service.start_run("NVDA", AgentName.SYSTEM)
    candidate = patch().model_copy(update={"evidence_refs": []}, deep=True)

    try:
        service.submit_patch(
            run.run_id,
            candidate,
            permissions=default_agent_registry()
            .get(AgentName.O1_EXPECTATION_OWNER)
            .runtime.to_permissions(),
            trigger_reason="Missing evidence should fail.",
        )
    except PatchValidationError:
        pass
    else:
        raise AssertionError("missing evidence patch should fail")

    after = service.get_run(run.run_id)
    assert after.belief_state.documents == {}
    assert after.commit_log == []


def test_bad_agent_result_enters_blocked_state_without_polluting_stable_state() -> None:
    def bad_result(task: AgentTask) -> AgentResult:
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=ResultStatus.FAILED,
            error=AgentError(
                code="schema_invalid",
                message="Mock schema validation failed.",
                retryable=False,
            ),
        )

    runner = MockAgentRunner(default_agent_registry(), result_factory=bad_result)
    workflow = BlackboardInitializationWorkflow(runner=runner, execution_mode="mock")

    result = workflow.run("NVDA")

    assert result.status is WorkflowRunStatus.BLOCKED
    assert result.error is not None
    assert "agent result failed" in result.error
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    assert run.belief_state.documents == {}
    assert run.commit_log == []


def test_patch_without_evidence_enters_blocked_state_without_extra_commit() -> None:
    factory = InitializationMockResultFactory(include_blockers=False)

    def no_evidence_result(task: AgentTask) -> AgentResult:
        result = factory(task)
        stripped_patches: list[BlackboardPatch] = [
            patch.model_copy(update={"evidence_refs": []}, deep=True)
            for patch in result.proposed_patches
        ]
        return result.model_copy(update={"proposed_patches": stripped_patches}, deep=True)

    runner = MockAgentRunner(default_agent_registry(), result_factory=no_evidence_result)
    workflow = BlackboardInitializationWorkflow(runner=runner, execution_mode="mock")

    result = workflow.run("NVDA")

    assert result.status is WorkflowRunStatus.BLOCKED
    assert result.error is not None
    assert "without evidence" in result.error
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    assert run.belief_state.documents == {}
    assert run.commit_log == []


def test_partial_retry_from_checkpoint_does_not_duplicate_completed_commits() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    partial = workflow.run("NVDA", stop_after=WorkflowNode.BUILD_GLOBAL_RESEARCH)
    before = workflow.blackboard.get_run(partial.checkpoint.run_id)
    assert len(before.commit_log) == 1

    resumed = workflow.resume(partial.checkpoint)

    after = workflow.blackboard.get_run(partial.checkpoint.run_id)
    assert resumed.status is WorkflowRunStatus.COMPLETED
    assert len(after.commit_log) == 5
    assert [
        commit.patch.target.document_type for commit in after.commit_log
    ].count(DocumentType.GLOBAL_RESEARCH) == 1


def test_dependency_violation_has_debug_report_and_preserves_existing_state() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    partial = workflow.run("NVDA", stop_after=WorkflowNode.START_TICKER_INITIALIZATION)
    bad_checkpoint = partial.checkpoint.model_copy(
        update={"next_node": WorkflowNode.GENERATE_MONITORING_CONFIG},
        deep=True,
    )

    result = workflow.resume(bad_checkpoint)
    run = workflow.blackboard.get_run(partial.checkpoint.run_id)
    report = build_run_debug_report(run, result.checkpoint)

    assert result.status is WorkflowRunStatus.BLOCKED
    assert result.error is not None
    assert "Missing required documents" in result.error
    assert run.belief_state.documents == {}
    assert run.commit_log == []
    assert report.workflow_status == "blocked"
    assert "workflow_blocked" in report.residual_risks
