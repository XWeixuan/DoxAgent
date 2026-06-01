from doxagent.agents import AgentRunner
from doxagent.models import (
    AgentError,
    AgentName,
    AgentResult,
    AgentTask,
    DocumentType,
    ResultStatus,
    ToolCallSummary,
)
from doxagent.workflows import (
    BlackboardInitializationWorkflow,
    InitializationMockResultFactory,
    WorkflowNode,
    WorkflowRunStatus,
)


class StructuredInitializationRunner(AgentRunner):
    def __init__(self, *, include_blockers: bool = True) -> None:
        self.factory = InitializationMockResultFactory(include_blockers=include_blockers)
        self.calls: list[tuple[AgentName, str | None]] = []

    def run(self, task: AgentTask) -> AgentResult:
        self.calls.append((task.agent_name, task.run_metadata.workflow_node))
        node = task.run_metadata.workflow_node
        if node == WorkflowNode.BUILD_GLOBAL_RESEARCH.value:
            if task.agent_name == AgentName.C1_FUNDAMENTAL_RESEARCH:
                return self._structured(task, self.factory(task))
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.SUCCEEDED,
                payload={"structured": {"payload": {"section_agent": task.agent_name.value}}},
            )
        return self._structured(task, self.factory(task))

    def _structured(self, task: AgentTask, direct: AgentResult) -> AgentResult:
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=direct.status,
            payload={
                "runtime": "maf",
                "structured": {
                    "payload": direct.payload,
                    "proposed_patches": [
                        patch.model_dump(mode="json") for patch in direct.proposed_patches
                    ],
                    "evidence_refs": [
                        evidence.model_dump(mode="json") for evidence in direct.evidence_refs
                    ],
                    "objections": [
                        objection.model_dump(mode="json") for objection in direct.objections
                    ],
                    "delegations": [
                        delegation.model_dump(mode="json") for delegation in direct.delegations
                    ],
                    "tool_calls": [
                        tool_call.model_dump(mode="json") for tool_call in direct.tool_calls
                    ],
                },
                "skill_versions": {"doxagent-source-discipline": "1.0.0"},
                "model_audit": {"provider": "mock", "model": "fake"},
            },
        )


def test_agent_runner_workflow_uses_module_integration_for_global_research() -> None:
    runner = StructuredInitializationRunner()
    workflow = BlackboardInitializationWorkflow(
        runner=runner,
        execution_mode="agent_runner",
    )

    result = workflow.run("NVDA", stop_after=WorkflowNode.BUILD_GLOBAL_RESEARCH)

    assert result.status is WorkflowRunStatus.RUNNING
    assert result.summary.stable_document_types == [DocumentType.GLOBAL_RESEARCH]
    assert result.summary.commit_count == 1
    assert runner.calls == []
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    assert len(run.working_memory) == 4
    assert {entry.author_agent for entry in run.working_memory} == {
        AgentName.C1_FUNDAMENTAL_RESEARCH,
        AgentName.C2_MACRO_RESEARCH,
        AgentName.C3_INDUSTRY_RESEARCH,
        AgentName.O4_MARKET_TRACE,
    }


def test_agent_runner_workflow_completes_with_structured_agent_result_json() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=StructuredInitializationRunner(),
        execution_mode="agent_runner",
    )

    result = workflow.run("NVDA")

    assert result.status is WorkflowRunStatus.COMPLETED
    assert result.summary.stable_document_types == [
        DocumentType.GLOBAL_RESEARCH,
        DocumentType.EXPECTATION_UNIT,
        DocumentType.KNOWN_EVENTS,
        DocumentType.MONITORING_CONFIG,
        DocumentType.MONITORING_POLICY,
    ]
    assert result.summary.commit_count == 5
    assert result.checkpoint.metadata["execution_mode"] == "agent_runner"
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    assert set(run.belief_state.documents) == {
        DocumentType.GLOBAL_RESEARCH,
        DocumentType.EXPECTATION_UNIT,
        DocumentType.KNOWN_EVENTS,
        DocumentType.MONITORING_CONFIG,
        DocumentType.MONITORING_POLICY,
    }


def test_agent_runner_structured_schema_invalid_blocks_without_stable_state() -> None:
    class InvalidStructuredRunner(AgentRunner):
        def run(self, task: AgentTask) -> AgentResult:
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.SUCCEEDED,
                payload={"structured": ["bad"]},
            )

    workflow = BlackboardInitializationWorkflow(
        runner=InvalidStructuredRunner(),
        execution_mode="agent_runner",
    )

    result = workflow.run("NVDA")

    assert result.status is WorkflowRunStatus.BLOCKED
    assert result.error is not None
    assert "structured output" in result.error
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    assert set(run.belief_state.documents) == {DocumentType.GLOBAL_RESEARCH}
    assert len(run.commit_log) == 1


def test_agent_runner_required_tool_failure_blocks_and_preserves_tool_summary() -> None:
    class RequiredToolFailureRunner(AgentRunner):
        def run(self, task: AgentTask) -> AgentResult:
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.FAILED,
                tool_calls=[
                    ToolCallSummary(
                        tool_name="doxatlas.query",
                        status=ResultStatus.FAILED,
                        input_summary="required workflow evidence",
                        output_summary="tool failed",
                    ),
                ],
                error=AgentError(
                    code="required_tool_failed",
                    message="Required tool failed.",
                    retryable=False,
                ),
            )

    workflow = BlackboardInitializationWorkflow(
        runner=RequiredToolFailureRunner(),
        execution_mode="agent_runner",
    )

    result = workflow.run("NVDA")

    assert result.status is WorkflowRunStatus.BLOCKED
    assert result.error is not None
    assert "Required tool failed" in result.error
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    failed_memory = next(entry for entry in run.working_memory if entry.payload["tool_calls"])
    assert failed_memory.payload["tool_calls"][0]["status"] == "failed"
    assert set(run.belief_state.documents) == {DocumentType.GLOBAL_RESEARCH}


def test_agent_runner_resume_latest_after_manual_blocker_resolution() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=StructuredInitializationRunner(include_blockers=True),
        execution_mode="agent_runner",
        auto_resolve_blockers=False,
    )
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

    resumed = workflow.resume_latest(blocked.checkpoint.run_id)

    assert resumed.status is WorkflowRunStatus.COMPLETED
    assert resumed.summary.commit_count == 5
    assert resumed.summary.unresolved_objection_count == 0
    assert resumed.summary.blocking_delegation_count == 0


def test_agent_runner_partial_retry_does_not_duplicate_completed_global_commit() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=StructuredInitializationRunner(),
        execution_mode="agent_runner",
    )
    partial = workflow.run("NVDA", stop_after=WorkflowNode.BUILD_GLOBAL_RESEARCH)

    resumed = workflow.resume(partial.checkpoint)
    run = workflow.blackboard.get_run(partial.checkpoint.run_id)

    assert resumed.status is WorkflowRunStatus.COMPLETED
    assert len(run.commit_log) == 5
    assert [
        commit.patch.target.document_type for commit in run.commit_log
    ].count(DocumentType.GLOBAL_RESEARCH) == 1
