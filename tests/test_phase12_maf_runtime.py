import pytest

pytest.skip("retired EvidenceRef MAF contract", allow_module_level=True)

from doxagent.agents import MafAgentAdapter, ModelGatewayAgentRunner
from doxagent.blackboard import BlackboardService
from doxagent.context import ContextBuilder
from doxagent.gateway import GatewayError, MockModelClient, ModelGateway, ProviderName
from doxagent.models import AgentName, ResultStatus
from doxagent.tools import default_tool_registry
from tests.fixtures.phase1_contracts import TICKER, agent_task


def runner_with_mock_response(
    *,
    text: str = '{"summary":"ok"}',
    structured: object | None = None,
    failures: list[GatewayError] | None = None,
) -> ModelGatewayAgentRunner:
    return ModelGatewayAgentRunner(
        model_gateway=ModelGateway(
            MockModelClient(text=text, structured=structured, failures=failures),
        ),
        tool_mode="disabled",
    )


def test_maf_runner_returns_succeeded_agent_result_from_fake_gateway() -> None:
    task = agent_task().model_copy(
        update={"input_context": {"execution_mode": "single_shot"}},
        deep=True,
    )
    runner = runner_with_mock_response(structured={"summary": "ok"})

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.agent_name is AgentName.O1_EXPECTATION_OWNER
    assert result.payload["runtime"] == "maf"
    assert result.payload["structured"] == {"summary": "ok"}
    assert result.payload["skill_ids"] == []
    assert "agent.o1" in result.payload["prompt_block_ids"]
    assert "expectation-construction" in result.payload["internal_task_skill_ids"]
    assert result.payload["external_skill_package_ids"] == []
    assert result.payload["tool_mode"] == "disabled"
    assert result.proposed_patches == []
    assert result.objections == []
    assert result.delegations == []


def test_maf_runner_rejects_non_object_structured_output() -> None:
    task = agent_task().model_copy(
        update={"input_context": {"execution_mode": "single_shot"}},
        deep=True,
    )
    result = runner_with_mock_response(text='["not-an-object"]').run(task)

    assert result.status is ResultStatus.FAILED
    assert result.error is not None
    assert result.error.code == "model_gateway_error"
    assert result.error.details["gateway_error"]["code"] == "invalid_json"


def test_maf_runner_maps_gateway_error_to_agent_error() -> None:
    task = agent_task().model_copy(
        update={"input_context": {"execution_mode": "single_shot"}},
        deep=True,
    )
    result = runner_with_mock_response(
        failures=[
            GatewayError(
                code="provider_down",
                message="Provider unavailable.",
                retryable=False,
                provider=ProviderName.MOCK,
            ),
        ],
    ).run(task)

    assert result.status is ResultStatus.FAILED
    assert result.error is not None
    assert result.error.code == "model_gateway_error"
    assert result.error.details["gateway_error"]["code"] == "provider_down"


def test_maf_agent_adapter_no_longer_returns_placeholder_error() -> None:
    task = agent_task().model_copy(
        update={"input_context": {"execution_mode": "single_shot"}},
        deep=True,
    )
    result = MafAgentAdapter(
        runner=runner_with_mock_response(structured={"summary": "adapter-ok"}),
    ).run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.error is None
    assert result.payload["structured"]["summary"] == "adapter-ok"


def test_maf_runner_records_workflow_memory_assembly_audit() -> None:
    service = BlackboardService()
    run = service.start_run(TICKER, AgentName.SYSTEM)
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "input_context": {
                **base_task.input_context,
                "execution_mode": "single_shot",
            },
            "run_metadata": base_task.run_metadata.model_copy(update={"run_id": run.run_id}),
        },
        deep=True,
    )
    service.add_working_memory_entry(
        run.run_id,
        author_agent=AgentName.O1_EXPECTATION_OWNER,
        content_type="agent_note",
        payload={"summary": "draft only"},
    )
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(MockModelClient(structured={"summary": "ok"})),
        context_builder=ContextBuilder(service),
        tool_mode="disabled",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    context = result.payload["context_assembly_audit"]
    assert context["run_id"] == run.run_id
    assert context["policy_id"] == "compat.generate_expectation_document.v1"
    assert context["missing_document_types"] == ["global_research"]
    assert "commit_log" not in context


def test_maf_runner_records_mock_tool_success_without_blackboard_write() -> None:
    service = BlackboardService()
    run = service.start_run(TICKER, AgentName.SYSTEM)
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "input_context": {
                **base_task.input_context,
                "execution_mode": "caller_planned_tools",
                "tool_requests": [{"tool_name": "doxatlas.query", "input": {"query": "AI"}}],
            },
            "run_metadata": base_task.run_metadata.model_copy(update={"run_id": run.run_id}),
        },
        deep=True,
    )
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(MockModelClient(structured={"summary": "ok"})),
        tool_registry=default_tool_registry(),
        tool_mode="mock",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.tool_calls[0].tool_name == "doxatlas.query"
    assert result.tool_calls[0].status is ResultStatus.SUCCEEDED
    assert result.evidence_refs
    assert service.get_run(run.run_id).belief_state.documents == {}


def test_maf_runner_required_tool_failure_returns_failed_result() -> None:
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "input_context": {
                **base_task.input_context,
                "execution_mode": "caller_planned_tools",
                "tool_requests": [{"tool_name": "market_data.snapshot"}],
                "required_tool_names": ["market_data.snapshot"],
            },
        },
        deep=True,
    )
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(MockModelClient(structured={"summary": "should-not-run"})),
        tool_registry=default_tool_registry(),
        tool_mode="mock",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.FAILED
    assert result.error is not None
    assert result.error.code == "required_tool_failed"
    assert result.tool_calls[0].status is ResultStatus.FAILED
