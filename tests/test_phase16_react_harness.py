import threading
from typing import Any

from doxagent.agents import ModelGatewayAgentRunner
from doxagent.agents.config import default_agent_registry
from doxagent.agents.runtime.react import ReActHarnessConfig
from doxagent.gateway import (
    MockModelClient,
    ModelAuditSummary,
    ModelGateway,
    ModelRequest,
    ModelResponse,
    ProviderName,
)
from doxagent.models import AgentPermissions, ResultStatus
from doxagent.prompts import PromptAssembler, PromptInjector
from doxagent.prompts.assembler import CHINESE_OUTPUT_RULES
from doxagent.tools import ToolClient, ToolDescriptor, ToolRegistry, ToolRequest, ToolResult
from doxagent.tools.mock import default_tool_registry
from tests.fixtures.phase1_contracts import agent_task


class RecordingModelClient:
    def __init__(self, structured_sequence: list[dict[str, Any]]) -> None:
        self.structured_sequence = list(structured_sequence)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        structured = self.structured_sequence.pop(0)
        return ModelResponse(
            structured=structured,
            audit=ModelAuditSummary(
                provider=ProviderName.MOCK,
                model=request.model,
                latency_seconds=0,
                metadata=request.metadata,
            ),
        )


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
    assert result.payload["structured"] == {"summary": "ok"}
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


def test_react_requests_include_chinese_output_rules_and_step_metadata() -> None:
    client = RecordingModelClient(
        [
            {"is_complete": False, "tool_calls": [], "delegations": []},
            {
                "is_complete": True,
                "completion_reason": "done",
                "final_payload": {"summary": "ok"},
            },
        ]
    )
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(client),
        tool_registry=default_tool_registry(),
        tool_mode="mock",
    )

    result = runner.run(agent_task())

    assert result.status is ResultStatus.SUCCEEDED
    assert [request.metadata["react_step"] for request in client.requests] == ["1", "2"]
    first_request_text = "\n".join(message.content for message in client.requests[0].messages)
    assert "所有人类可读文本内容必须使用简体中文" in first_request_text
    assert CHINESE_OUTPUT_RULES[0] in first_request_text
    assert "JSON key" in first_request_text


def test_prompt_assembler_adds_chinese_output_rules_for_single_shot_paths() -> None:
    registry = default_agent_registry()
    task = agent_task()
    definition = registry.get(task.agent_name)
    injected = PromptInjector().inject(task, definition)

    assembled = PromptAssembler().assemble(
        injected,
        definition,
        injected.prompt_bundle,
        context_snapshot=None,
        tool_results=[],
    )

    assert "所有人类可读文本内容必须使用简体中文" in assembled.instructions
    assert CHINESE_OUTPUT_RULES[0] in assembled.instructions
    assert "JSON key" in assembled.user_prompt


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


def test_react_filters_invalid_research_section_evidence_refs() -> None:
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
                    "text": "ASTS deployment update.",
                    "summary": "Deployment update.",
                    "evidence_refs": ["BlueBird 7 mission"],
                    "author_agent": "narrative_engine",
                    "reviewer_agents": ["analyst"],
                },
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["structured"]["evidence_refs"] == []
    assert result.payload["structured"]["author_agent"] == "O1"
    assert result.payload["structured"]["reviewer_agents"] == []


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


def test_react_synthesizes_expectation_patch_from_global_research_context() -> None:
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "required_output_schema": "ExpectationConstructionResult",
            "input_context": {
                **base_task.input_context,
                "global_research_context": {
                    "ticker": "ASTS",
                    "sections": {
                        "market_narrative_report": {
                            "summary": "Commercial deployment milestones drive market debate.",
                            "text": "ASTS direct-to-device deployment remains the key variable.",
                        }
                    },
                },
            },
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "drafted",
                "final_payload": {"rationale": "Use global research context."},
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    patch = result.payload["structured"]["proposed_patches"][0]
    assert patch["target"]["document_type"] == "expectation_unit"
    assert patch["after"]["expectation_name"].endswith("commercialization milestone execution")


def test_react_normalizes_expectation_patch_target_to_document_id() -> None:
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
                    "proposed_patches": [
                        {
                            "target": {
                                "document_type": "expectation_unit",
                                "ticker": "WRONG",
                                "expectation_id": "target_id",
                                "field_path": "document",
                            },
                            "operation": "create",
                            "after": {
                                "id": "after_id",
                                "name": "Milestone execution",
                                "description": "Milestone execution matters.",
                                "key_variables": ["Launch cadence"],
                                "realized_facts": ["BlueBird launch announced"],
                            },
                            "rationale": "test",
                        }
                    ],
                    "rationale": "test",
                },
            }
        ]
    )

    result = runner.run(task)

    patch = result.payload["structured"]["proposed_patches"][0]
    assert patch["target"]["ticker"] == task.ticker
    assert patch["target"]["expectation_id"] == patch["after"]["expectation_id"]
    assert patch["after"]["key_variables"][0]["name"] == "Launch cadence"
    assert patch["after"]["realized_facts"][0]["description"] == "BlueBird launch announced"


def test_react_normalizes_output_delegations_for_expectation_construction() -> None:
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
                    "expectations": [{"expectation_id": "exp_1", "name": "Milestone"}],
                    "delegations": [
                        {
                            "target_agent": "A2",
                            "task_type": "data_retrieval",
                            "question": "Fetch OHLCV data.",
                            "context_summary": "Need market data.",
                        }
                    ],
                    "rationale": "test",
                },
            }
        ]
    )

    result = runner.run(task)

    delegation = result.payload["structured"]["delegations"][0]
    assert delegation["requester_agent"] == "O1"
    assert delegation["target_agent"] == "A2"
    assert delegation["required_evidence"] == ["market_data"]
    assert delegation["blocking_scope"]["ticker"] == task.ticker


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
