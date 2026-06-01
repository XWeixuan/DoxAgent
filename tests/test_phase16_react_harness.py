import threading
from typing import Any

from doxagent.agents import ModelGatewayAgentRunner
from doxagent.agents.runtime.react import ReActHarnessConfig
from doxagent.gateway import MockModelClient, ModelGateway
from doxagent.models import AgentPermissions, ResultStatus
from doxagent.tools import ToolClient, ToolDescriptor, ToolRegistry, ToolRequest, ToolResult
from doxagent.tools.mock import default_tool_registry
from tests.fixtures.phase1_contracts import agent_task


def runner_with_sequence(
    structured_sequence: list[dict[str, Any]],
    *,
    tool_registry: ToolRegistry | None = None,
    react_config: ReActHarnessConfig | None = None,
) -> ModelGatewayAgentRunner:
    return ModelGatewayAgentRunner(
        model_gateway=ModelGateway(MockModelClient(structured_sequence=structured_sequence)),
        tool_registry=tool_registry or default_tool_registry(),
        react_config=react_config,
        tool_mode="mock",
    )


def test_react_is_default_and_accepts_direct_structured_payload() -> None:
    task = agent_task()
    runner = runner_with_sequence([{"summary": "ok"}])

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["runtime"] == "react"
    assert result.payload["structured"] == {"summary": "ok"}
    assert result.payload["react_audit"]["entries"][0]["completion_reason"]


def test_react_unwraps_nested_react_protocol_action() -> None:
    task = agent_task()
    runner = runner_with_sequence(
        [
            {
                "react_protocol": {
                    "is_complete": True,
                    "completion_reason": "done",
                    "final_payload": {"summary": "ok"},
                    "tool_calls": [],
                    "delegations": [],
                }
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["structured"] == {"summary": "ok"}
    assert result.payload["react_audit"]["entries"][0]["completion_reason"] == "done"


def test_react_retries_once_after_non_json_model_text() -> None:
    task = agent_task()
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(
            MockModelClient(
                text_sequence=[
                    "I need to answer in JSON.",
                    '{"is_complete": true, "completion_reason": "fixed", '
                    '"final_payload": {"summary": "ok"}}',
                ]
            )
        ),
        tool_registry=default_tool_registry(),
        tool_mode="mock",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["structured"] == {"summary": "ok"}
    assert any(
        entry["kind"] == "model_format_error"
        for entry in result.payload["react_audit"]["entries"]
    )


def test_react_retries_after_no_progress_action() -> None:
    task = agent_task()
    runner = runner_with_sequence(
        [
            {"is_complete": False, "tool_calls": [], "delegations": []},
            {
                "is_complete": True,
                "completion_reason": "fixed",
                "final_payload": {"summary": "ok"},
            },
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert any(
        entry["kind"] == "react_no_progress"
        for entry in result.payload["react_audit"]["entries"]
    )


def test_react_normalizes_report_like_payload_to_research_section() -> None:
    task = agent_task().model_copy(
        update={"required_output_schema": "ResearchSection"},
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "drafted",
                "final_payload": {
                    "ticker": "NVDA",
                    "section_key": "fundamental_report",
                    "sections": [{"title": "Growth", "body": "Revenue is rising."}],
                    "unknowns": ["Balance sheet detail missing."],
                },
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["structured"]["author_agent"] == "O1"
    assert "Revenue is rising" in result.payload["structured"]["text"]
    assert "Balance sheet detail missing" in result.payload["structured"]["text"]


def test_react_normalizes_expectation_construction_payload_extras() -> None:
    task = agent_task().model_copy(
        update={"required_output_schema": "ExpectationConstructionResult"},
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "drafted",
                "final_payload": {
                    "expectations": [{"expectation_id": "exp_1", "name": "AI cycle"}],
                    "rationale": "Built from available research.",
                    "notes": [{"extra": "drop me"}],
                },
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    structured = result.payload["structured"]
    assert structured["proposed_patches"][0]["target"]["expectation_id"] == "exp_1"
    assert structured["evidence_refs"][0]["source_type"] == "agent_output"
    assert "notes" not in structured


def test_can_switch_to_single_shot_maf_path() -> None:
    task = agent_task().model_copy(
        update={"input_context": {"execution_mode": "single_shot"}},
        deep=True,
    )
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(MockModelClient(structured={"summary": "ok"})),
        tool_registry=default_tool_registry(),
        tool_mode="mock",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["runtime"] == "maf"
    assert result.payload["execution_mode"] == "single_shot"


def test_caller_planned_tool_execution_remains_available() -> None:
    task = agent_task().model_copy(
        update={
            "input_context": {
                "execution_mode": "caller_planned_tools",
                "tool_requests": [
                    {"tool_name": "doxatlas.query", "input": {"query": "AI demand"}}
                ],
            }
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
    assert result.payload["runtime"] == "maf"
    assert result.payload["execution_mode"] == "caller_planned_tools"
    assert result.tool_calls[0].tool_name == "doxatlas.query"


def test_react_blocks_fourth_call_to_same_tool() -> None:
    task = agent_task()
    tool_action = {
        "is_complete": False,
        "tool_calls": [{"tool_name": "doxatlas.query", "input": {"query": "AI demand"}}],
    }
    runner = runner_with_sequence(
        [
            tool_action,
            tool_action,
            tool_action,
            tool_action,
            {
                "is_complete": True,
                "completion_reason": "enough",
                "final_payload": {"summary": "done"},
            },
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.tool_calls[-1].status is ResultStatus.FAILED
    assert result.tool_calls[-1].output_summary is not None
    assert "tool_call_limit_exceeded" in result.tool_calls[-1].output_summary
    assert result.payload["react_audit"]["tool_counts"]["doxatlas.query"] == 3


def test_react_warns_on_similar_tool_query() -> None:
    task = agent_task()
    runner = runner_with_sequence(
        [
            {
                "is_complete": False,
                "tool_calls": [{"tool_name": "doxatlas.query", "input": {"query": "AI demand"}}],
            },
            {
                "is_complete": False,
                "tool_calls": [{"tool_name": "doxatlas.query", "input": {"query": "AI demand"}}],
            },
            {
                "is_complete": True,
                "completion_reason": "done",
                "final_payload": {"summary": "done"},
            },
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["react_audit"]["warnings"]


def test_react_permission_denial_is_a_tool_result_not_exception() -> None:
    task = agent_task()
    runner = runner_with_sequence(
        [
            {
                "is_complete": False,
                "tool_calls": [{"tool_name": "market_data.snapshot", "input": {}}],
            },
            {
                "is_complete": True,
                "completion_reason": "done",
                "final_payload": {"summary": "done"},
            },
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.tool_calls[0].tool_name == "market_data.snapshot"
    assert result.tool_calls[0].status is ResultStatus.FAILED


def test_react_required_tool_gap_is_audited_without_blocking_final_payload() -> None:
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "input_context": {
                **base_task.input_context,
                "required_tool_names": ["market_data.snapshot"],
            }
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": False,
                "tool_calls": [{"tool_name": "market_data.snapshot", "input": {}}],
            },
            {
                "is_complete": True,
                "completion_reason": "done with data gap",
                "final_payload": {"summary": "done", "unknowns": ["market snapshot unavailable"]},
            },
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.tool_calls[0].status is ResultStatus.FAILED
    assert any(
        entry["kind"] == "required_tool_gap"
        for entry in result.payload["react_audit"]["entries"]
    )


def test_react_delegation_requires_permission() -> None:
    task = agent_task().model_copy(
        update={
            "permissions": AgentPermissions(
                readable_context_scopes=["global_research"],
                writable_targets=["expectation_unit"],
                allowed_tools=["doxatlas.query"],
                can_delegate=False,
            )
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": False,
                "delegations": [
                    {
                        "target_agent": "A2",
                        "question": "check",
                        "context_summary": "ctx",
                    }
                ],
            },
            {
                "is_complete": True,
                "completion_reason": "done",
                "final_payload": {"summary": "done"},
            },
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    delegation_entries = [
        entry
        for entry in result.payload["react_audit"]["entries"]
        if entry["kind"] == "delegation_result"
    ]
    assert delegation_entries[0]["status"] == "failed"


def test_react_full_compaction_uses_model_summary() -> None:
    task = agent_task()
    runner = runner_with_sequence(
        [
            {
                "is_complete": False,
                "tool_calls": [
                    {"tool_name": "doxatlas.query", "input": {"query": "large context"}}
                ],
            },
            {"summary": {"data_retrieved": ["tool result"]}},
            {
                "is_complete": True,
                "completion_reason": "done",
                "final_payload": {"summary": "done"},
            },
        ],
        react_config=ReActHarnessConfig(compaction_token_threshold=1),
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["react_audit"]["compacted_summaries"]


class RendezvousToolClient(ToolClient):
    def __init__(self, name: str, own_event: threading.Event, peer_event: threading.Event) -> None:
        self.name = name
        self.own_event = own_event
        self.peer_event = peer_event

    def call(self, request: ToolRequest) -> ToolResult:
        self.own_event.set()
        saw_peer = self.peer_event.wait(timeout=0.5)
        return ToolResult(
            tool_name=self.name,
            status=ResultStatus.SUCCEEDED,
            output={"saw_parallel_peer": saw_peer, "input": request.input},
            output_summary=f"{self.name} saw_peer={saw_peer}",
        )


def test_react_executes_concurrent_safe_tools_in_parallel() -> None:
    first_started = threading.Event()
    second_started = threading.Event()
    registry = ToolRegistry()
    registry.register(
        "first.concurrent",
        RendezvousToolClient("first.concurrent", first_started, second_started),
        descriptor=ToolDescriptor(
            name="first.concurrent",
            description="first",
            concurrent_safe=True,
        ),
    )
    registry.register(
        "second.concurrent",
        RendezvousToolClient("second.concurrent", second_started, first_started),
        descriptor=ToolDescriptor(
            name="second.concurrent",
            description="second",
            concurrent_safe=True,
        ),
    )
    task = agent_task().model_copy(
        update={
            "permissions": AgentPermissions(
                readable_context_scopes=["global_research"],
                writable_targets=["expectation_unit"],
                allowed_tools=["first.concurrent", "second.concurrent"],
            )
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": False,
                "tool_calls": [
                    {"tool_name": "first.concurrent", "input": {"query": "a"}},
                    {"tool_name": "second.concurrent", "input": {"query": "b"}},
                ],
            },
            {
                "is_complete": True,
                "completion_reason": "done",
                "final_payload": {"summary": "done"},
            },
        ],
        tool_registry=registry,
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert all("saw_peer=True" in call.output_summary for call in result.tool_calls)
