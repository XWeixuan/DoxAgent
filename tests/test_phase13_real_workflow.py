import os

import pytest

from doxagent.agents import AgentRunner, default_agent_registry, default_real_agent_runner
from doxagent.gateway import ProviderName
from doxagent.models import (
    AgentError,
    AgentName,
    AgentResult,
    AgentTask,
    DocumentType,
    EvidenceRef,
    EvidenceSourceType,
    ResearchSection,
    ResultStatus,
    ToolCallSummary,
    new_id,
)
from doxagent.settings import DoxAgentSettings
from doxagent.tools import default_real_tool_registry
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
            return self._research_section(task)
        return self._structured(task, self.factory(task))

    def _research_section(self, task: AgentTask) -> AgentResult:
        evidence = EvidenceRef(
            evidence_id=new_id("evidence"),
            source_type=EvidenceSourceType.AGENT_OUTPUT,
            source_id=f"test:{task.agent_name.value}",
            title=f"{task.agent_name.value} research evidence",
            summary="Structured test research evidence.",
            confidence=0.8,
            citation_scope="test.global_research",
        )
        section = ResearchSection(
            text=f"{task.ticker} {task.agent_name.value} research text.",
            summary=f"{task.ticker} {task.agent_name.value} research summary.",
            evidence_refs=[evidence],
            author_agent=task.agent_name,
            reviewer_agents=[AgentName.O1_EXPECTATION_OWNER],
        )
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=ResultStatus.SUCCEEDED,
            payload={"structured": section.model_dump(mode="json")},
            evidence_refs=[evidence],
        )

    def _structured(self, task: AgentTask, direct: AgentResult) -> AgentResult:
        if task.required_output_schema == "ExpectationConstructionResult":
            structured = {
                "proposed_patches": [
                    patch.model_dump(mode="json") for patch in direct.proposed_patches
                ],
                "evidence_refs": [
                    evidence.model_dump(mode="json") for evidence in direct.evidence_refs
                ],
                "delegations": [
                    delegation.model_dump(mode="json") for delegation in direct.delegations
                ],
                "unknowns": [],
                "rationale": "Structured expectation construction test output.",
            }
        elif task.required_output_schema == "DoxAtlasAuditResult":
            structured = {
                "findings": [],
                "evidence_refs": [
                    evidence.model_dump(mode="json") for evidence in direct.evidence_refs
                ],
                "objections": [
                    objection.model_dump(mode="json") for objection in direct.objections
                ],
                "delegations": [
                    delegation.model_dump(mode="json") for delegation in direct.delegations
                ],
                "unknowns": [],
                "rationale": "Structured audit test output.",
            }
        elif task.required_output_schema == "ExpectationFieldReviewResult":
            structured = {
                "findings": [],
                "evidence_refs": [
                    evidence.model_dump(mode="json") for evidence in direct.evidence_refs
                ],
                "objections": [],
                "delegations": [],
                "unknowns": [],
                "rationale": "Structured expectation-field review test output.",
            }
        elif task.required_output_schema == "DelegatedRetrievalResult":
            structured = direct.payload
        else:
            structured = {
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
            }
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=direct.status,
            payload={
                "runtime": "maf",
                "structured": structured,
                "skill_versions": {"doxagent-source-discipline": "1.0.0"},
                "model_audit": {"provider": "mock", "model": "fake"},
            },
        )


def test_agent_runner_default_uses_bailian_and_real_tools() -> None:
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        settings=DoxAgentSettings(dashscope_api_key="test-key"),
    )

    assert workflow.runner.default_provider is ProviderName.BAILIAN
    assert workflow.runner.default_model == "qwen3.6-flash"
    assert workflow.runner.tool_mode == "real"
    assert "doxa_get_narrative_report" in workflow.runner.tool_registry.names()


def test_default_real_runner_applies_langsmith_env_and_wraps_bailian(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_wrap_provider_client(
        provider: ProviderName,
        client: object,
        **kwargs: object,
    ) -> object:
        calls.append({"provider": provider, **kwargs})
        return client

    for key in (
        "LANGSMITH_TRACING",
        "LANGSMITH_ENDPOINT",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("doxagent.agents.runner.wrap_provider_client", fake_wrap_provider_client)

    runner = default_real_agent_runner(
        settings=DoxAgentSettings(
            dashscope_api_key="test-key",
            langsmith_tracing=True,
            langsmith_endpoint="https://api.smith.langchain.com",
            langsmith_api_key="ls-test",
            langsmith_project="DoxAgent",
        )
    )

    assert runner.default_provider is ProviderName.BAILIAN
    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGSMITH_ENDPOINT"] == "https://api.smith.langchain.com"
    assert os.environ["LANGSMITH_API_KEY"] == "ls-test"
    assert os.environ["LANGSMITH_PROJECT"] == "DoxAgent"
    assert calls[0]["provider"] is ProviderName.BAILIAN
    assert calls[0]["tracing_enabled"] is True
    assert calls[0]["tracing_extra"] == {
        "metadata": {
            "runtime": "doxagent",
            "provider": "bailian",
            "model": "qwen3.6-flash",
        }
    }


def test_default_agent_allowed_tools_exist_in_real_registry() -> None:
    registry = default_agent_registry()
    real_tools = set(default_real_tool_registry(DoxAgentSettings(dashscope_api_key="x")).names())

    missing = {
        f"{agent.value}:{tool_name}"
        for agent in registry.names()
        for tool_name in registry.get(agent).runtime.allowed_tools
        if tool_name not in real_tools
    }

    assert missing == set()


def test_expectation_patch_count_requires_one_to_three() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    task = AgentTask.model_validate(
        {
            "task_id": "task_count",
            "ticker": "NVDA",
            "agent_name": AgentName.O1_EXPECTATION_OWNER,
            "task_type": "generate_expectation_unit",
            "input_context": {},
            "required_output_schema": "ExpectationConstructionResult",
            "permissions": default_agent_registry()
            .get(AgentName.O1_EXPECTATION_OWNER)
            .runtime.to_permissions(),
            "run_metadata": {
                "run_id": "run_count",
                "ticker": "NVDA",
                "workflow_node": "GenerateExpectationUnits",
                "created_at": "2026-06-01T00:00:00Z",
            },
        }
    )
    one_patch = InitializationMockResultFactory(include_blockers=False)(task)

    workflow._validate_expectation_patches("NVDA", one_patch)

    empty = one_patch.model_copy(update={"proposed_patches": []}, deep=True)
    with pytest.raises(Exception, match="no expectation patches"):
        workflow._validate_expectation_patches("NVDA", empty)

    too_many = one_patch.model_copy(
        update={"proposed_patches": one_patch.proposed_patches * 4},
        deep=True,
    )
    with pytest.raises(Exception, match="too many expectations"):
        workflow._validate_expectation_patches("NVDA", too_many)


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
    assert runner.calls == [
        (AgentName.C1_FUNDAMENTAL_RESEARCH, WorkflowNode.BUILD_GLOBAL_RESEARCH.value),
        (AgentName.C2_MACRO_RESEARCH, WorkflowNode.BUILD_GLOBAL_RESEARCH.value),
        (AgentName.C3_INDUSTRY_RESEARCH, WorkflowNode.BUILD_GLOBAL_RESEARCH.value),
        (AgentName.O4_MARKET_TRACE, WorkflowNode.BUILD_GLOBAL_RESEARCH.value),
        (AgentName.O1_EXPECTATION_OWNER, WorkflowNode.BUILD_GLOBAL_RESEARCH.value),
    ]
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    assert len(run.working_memory) == 5
    assert {entry.author_agent for entry in run.working_memory} == {
        AgentName.C1_FUNDAMENTAL_RESEARCH,
        AgentName.C2_MACRO_RESEARCH,
        AgentName.C3_INDUSTRY_RESEARCH,
        AgentName.O4_MARKET_TRACE,
        AgentName.O1_EXPECTATION_OWNER,
    }


def test_agent_runner_workflow_completes_with_structured_agent_result_json() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=StructuredInitializationRunner(include_blockers=False),
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
    assert "structured output" in result.error or "schema validation" in result.error
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    assert set(run.belief_state.documents) == set()
    assert len(run.commit_log) == 0


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
    assert set(run.belief_state.documents) == set()


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
        runner=StructuredInitializationRunner(include_blockers=False),
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
