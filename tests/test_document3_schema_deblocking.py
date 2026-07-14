from __future__ import annotations

import pytest

from doxagent.agents import ModelGatewayAgentRunner
from doxagent.agents.runtime.react import _final_payload_schema_error
from doxagent.gateway import MockModelClient, ModelGateway
from doxagent.models import (
    AgentName,
    AgentResult,
    KnownEventsDocument,
    MonitoringConfigDocument,
    MonitoringPolicyDocument,
    ResultStatus,
    TaskType,
)
from doxagent.tools.mock import default_tool_registry
from doxagent.workflows.errors import WorkflowContractError
from doxagent.workflows.initialization import BlackboardInitializationWorkflow
from doxagent.workflows.schema import WorkflowCheckpoint, WorkflowNode
from tests.fixtures.phase1_contracts import agent_task


class _StaticDocument3Runner:
    def run(self, task):
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=ResultStatus.SUCCEEDED,
            payload={
                "runtime": "react",
                "structured": {
                    "events": [
                        {
                            "event_id": "ke_001",
                            "event_time": "Q3 FY2026",
                            "description": "INTC product milestone.",
                        },
                        "bad record",
                    ]
                },
            },
        )


class _RejectingEarlyValidator:
    def validate(self, payload, expected_schema):
        raise AssertionError(
            f"early validation must not run for {expected_schema}: {payload}"
        )


@pytest.mark.parametrize(
    "schema_name",
    ["KnownEventsDocument", "MonitoringConfigDocument", "MonitoringPolicyDocument"],
)
def test_react_defers_document3_object_schema_validation(schema_name: str) -> None:
    assert _final_payload_schema_error({}, schema_name) is None
    assert _final_payload_schema_error({"model_shape": "not-yet-normalized"}, schema_name) is None


def test_react_keeps_non_document3_schema_validation_strict() -> None:
    error = _final_payload_schema_error({"text": "missing required fields"}, "ResearchSection")
    assert error is not None
    assert "schema validation" in error


def test_react_returns_raw_document3_payload_without_invalid_final_payload() -> None:
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "task_type": TaskType.GENERATE_KNOWN_EVENTS,
            "required_output_schema": "KnownEventsDocument",
        },
        deep=True,
    )
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(
            MockModelClient(
                structured_sequence=[
                    {
                        "is_complete": True,
                        "completion_reason": "raw document ready",
                        "final_payload": {
                            "events": [
                                {
                                    "event_id": "ke_001",
                                    "event_time": "Q3 FY2026",
                                    "description": "INTC product milestone.",
                                }
                            ]
                        },
                    }
                ]
            )
        ),
        tool_registry=default_tool_registry(),
        tool_mode="mock",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.error is None
    assert result.payload["structured"]["events"][0]["event_time"] == "Q3 FY2026"


def test_document3_dispatch_defers_validation_until_workflow_normalizer() -> None:
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=_StaticDocument3Runner(),
        output_validator=_RejectingEarlyValidator(),
    )
    run = workflow.blackboard.start_run("INTC", AgentName.SYSTEM)
    checkpoint = WorkflowCheckpoint(
        run_id=run.run_id,
        ticker="INTC",
        next_node=WorkflowNode.GENERATE_KNOWN_EVENTS,
    )

    raw_result = workflow._run_agent(
        checkpoint,
        WorkflowNode.GENERATE_KNOWN_EVENTS,
        AgentName.O1_EXPECTATION_OWNER,
        TaskType.GENERATE_KNOWN_EVENTS,
        "KnownEventsDocument",
        workflow_watchdog=False,
    )
    normalized = workflow._ensure_document_patch_result(
        checkpoint, WorkflowNode.GENERATE_KNOWN_EVENTS, raw_result
    )

    assert raw_result.status is ResultStatus.SUCCEEDED
    assert normalized.status is ResultStatus.PARTIAL
    KnownEventsDocument.model_validate(normalized.proposed_patches[0].after)


def _result(agent_name: AgentName, structured: dict) -> AgentResult:
    return AgentResult(
        task_id="task_document3_normalization",
        agent_name=agent_name,
        status=ResultStatus.SUCCEEDED,
        payload={"runtime": "react", "structured": structured},
    )


def test_known_events_normalizer_isolates_records_and_normalizes_period_time() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    checkpoint = WorkflowCheckpoint(
        run_id="run_document3_known_events",
        ticker="INTC",
        next_node=WorkflowNode.GENERATE_KNOWN_EVENTS,
    )
    result = _result(
        AgentName.O1_EXPECTATION_OWNER,
        {
            "events": [
                "invalid record",
                {
                    "event_id": "ke_001",
                    "event_time": "Q3 FY2026",
                    "description": "INTC Q3 product milestone.",
                },
                {
                    "event_id": "ke_002",
                    "event_time": "after regulatory approval",
                    "description": "INTC event with no determinable date.",
                },
            ]
        },
    )

    normalized = workflow._ensure_document_patch_result(
        checkpoint, WorkflowNode.GENERATE_KNOWN_EVENTS, result
    )

    assert normalized.status is ResultStatus.PARTIAL
    assert normalized.payload["normalization_warnings"][0]["location"] == "events[0]"
    document = KnownEventsDocument.model_validate(normalized.proposed_patches[0].after)
    assert document.events[0].event_time is not None
    assert document.events[0].event_time.isoformat().startswith("2026-07-01")
    assert document.events[0].event_window == "Q3 FY2026"
    assert document.events[1].event_time is None
    assert document.events[1].event_window == "after regulatory approval"
    workflow._validate_patch_contract(
        normalized.proposed_patches[0], WorkflowNode.GENERATE_KNOWN_EVENTS
    )
    workflow._validate_agent_success(normalized, WorkflowNode.GENERATE_KNOWN_EVENTS)


def test_monitoring_config_normalizer_falls_back_to_valid_disabled_document() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    checkpoint = WorkflowCheckpoint(
        run_id="run_document3_config",
        ticker="INTC",
        next_node=WorkflowNode.GENERATE_MONITORING_CONFIG,
    )
    result = _result(
        AgentName.O2_MONITORING_CONFIG,
        {"monitoring_items": [{"tool_input": "not an object"}]},
    )

    normalized = workflow._ensure_document_patch_result(
        checkpoint, WorkflowNode.GENERATE_MONITORING_CONFIG, result
    )

    assert normalized.status is ResultStatus.PARTIAL
    document = MonitoringConfigDocument.model_validate(normalized.proposed_patches[0].after)
    assert document.monitoring_items[0].item_id == "mi_fallback_001"
    assert document.monitoring_items[0].tool_input["enabled"] is False
    workflow._validate_patch_contract(
        normalized.proposed_patches[0], WorkflowNode.GENERATE_MONITORING_CONFIG
    )
    workflow._validate_agent_success(normalized, WorkflowNode.GENERATE_MONITORING_CONFIG)


def test_monitoring_policy_normalizer_falls_back_to_valid_non_trading_document() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    checkpoint = WorkflowCheckpoint(
        run_id="run_document3_policy",
        ticker="INTC",
        next_node=WorkflowNode.GENERATE_MONITORING_POLICY,
    )
    result = _result(AgentName.O4_MARKET_TRACE, {"policies": [42]})

    normalized = workflow._ensure_document_patch_result(
        checkpoint, WorkflowNode.GENERATE_MONITORING_POLICY, result
    )

    assert normalized.status is ResultStatus.PARTIAL
    document = MonitoringPolicyDocument.model_validate(normalized.proposed_patches[0].after)
    assert document.policies[0].policy_type == "escalate"
    assert document.direct_trade_rules == []
    assert document.no_action_rationale
    workflow._validate_patch_contract(
        normalized.proposed_patches[0], WorkflowNode.GENERATE_MONITORING_POLICY
    )
    workflow._validate_agent_success(normalized, WorkflowNode.GENERATE_MONITORING_POLICY)


def test_partial_result_remains_blocking_outside_document3() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    result = AgentResult(
        task_id="task_non_document3",
        agent_name=AgentName.C1_FUNDAMENTAL_RESEARCH,
        status=ResultStatus.PARTIAL,
    )

    with pytest.raises(WorkflowContractError):
        workflow._validate_agent_success(
            result, WorkflowNode.BUILD_GLOBAL_RESEARCH, require_patches=False
        )
