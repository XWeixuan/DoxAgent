import asyncio
import json
import threading
from datetime import date, timedelta
from typing import Any

from doxagent.agents import ModelGatewayAgentRunner
from doxagent.agents.config import default_agent_registry
from doxagent.agents.runtime.react import ReActHarnessConfig, Scratchpad
from doxagent.gateway import (
    MockModelClient,
    ModelAuditSummary,
    ModelGateway,
    ModelRequest,
    ModelResponse,
    ProviderName,
)
from doxagent.models import AgentName, AgentPermissions, AgentTask, ResultStatus, TaskType
from doxagent.prompts import PromptAssembler, PromptInjector
from doxagent.prompts.assembler import CHINESE_OUTPUT_RULES, agent_visible_input_context
from doxagent.tools import (
    ToolClient,
    ToolDescriptor,
    ToolError,
    ToolRegistry,
    ToolRequest,
    ToolResult,
)
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


class TextAndStructuredModelClient:
    def __init__(self, responses: list[tuple[dict[str, Any], str]]) -> None:
        self.responses = list(responses)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        structured, text = self.responses.pop(0)
        return ModelResponse(
            structured=structured,
            text=text,
            audit=ModelAuditSummary(
                provider=ProviderName.MOCK,
                model=request.model,
                latency_seconds=0,
                metadata=request.metadata,
            ),
        )


class SlowModelClient:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        await asyncio.sleep(1.0)
        return ModelResponse(
            structured={"summary": "late"},
            audit=ModelAuditSummary(
                provider=ProviderName.MOCK,
                model=request.model,
                latency_seconds=1.0,
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


def runtime_o3_task() -> AgentTask:
    base_task = agent_task()
    return base_task.model_copy(
        update={
            "agent_name": AgentName.O3_TRADING_STRATEGY,
            "task_type": TaskType.RUNTIME_O3_JUDGMENT,
            "input_context": {
                "source_message": {
                    "source_message_id": "msg_o3_schema",
                    "ticker": "NVDA",
                    "source_type": "media",
                    "title": "Runtime O3 schema smoke",
                },
                "o3_runtime_budget": {
                    "target_seconds": 120,
                    "max_model_calls": 2,
                    "max_parallel_tool_call_batches": 1,
                },
            },
            "required_output_schema": "O3Result",
            "permissions": AgentPermissions(
                readable_context_scopes=["known_events", "monitoring_policy"],
                writable_targets=["known_events", "monitoring_policy"],
                allowed_tools=["tavily.search"],
                can_delegate=False,
                can_propose_patch=True,
            ),
        },
        deep=True,
    )


def test_react_scratchpad_plan_keeps_latest_update_only() -> None:
    scratchpad = Scratchpad(agent_task())

    scratchpad.record_action(1, {"plan_update": ["first plan"], "is_complete": False})
    scratchpad.record_action(2, {"plan_update": ["second plan"], "is_complete": False})
    scratchpad.record_action(3, {"is_complete": False})

    assert scratchpad.plan == ["second plan"]


def test_react_is_default_and_accepts_direct_structured_payload() -> None:
    task = agent_task()
    runner = runner_with_sequence([{"summary": "ok"}])

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["structured"] == {"summary": "ok"}
    assert result.payload["runtime"] == "react"
    assert result.payload["structured"] == {"summary": "ok"}
    assert result.payload["react_audit"]["entries"][0]["completion_reason"]


def test_react_model_requests_carry_configured_timeout() -> None:
    task = agent_task()
    client = RecordingModelClient([{"summary": "ok"}])
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(client),
        tool_registry=default_tool_registry(),
        react_config=ReActHarnessConfig(model_request_timeout_seconds=12.5),
        tool_mode="mock",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert client.requests[0].timeout_seconds == 12.5


def test_react_runtime_budget_in_task_context_overrides_steps_tools_and_timeout() -> None:
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "input_context": {
                **base_task.input_context,
                "react_runtime_budget": {
                    "max_steps": 1,
                    "max_tool_call_batches": 0,
                    "model_request_timeout_seconds": 1.5,
                },
            }
        },
        deep=True,
    )
    client = RecordingModelClient([{"summary": "ok"}])
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(client),
        tool_registry=default_tool_registry(),
        react_config=ReActHarnessConfig(max_steps=5, max_tool_call_batches=None),
        tool_mode="mock",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert client.requests[0].timeout_seconds == 1.5
    prompt = json.loads(client.requests[0].messages[-1].content)
    assert prompt["react_protocol"]["max_steps"] == 1
    assert prompt["react_protocol"]["max_tool_call_batches"] == 0


def test_react_enforces_outer_model_request_timeout() -> None:
    client = SlowModelClient()
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(client),
        tool_registry=default_tool_registry(),
        react_config=ReActHarnessConfig(model_request_timeout_seconds=0.01),
        tool_mode="mock",
    )

    result = runner.run(agent_task())

    assert result.status is ResultStatus.FAILED
    assert result.error is not None
    assert result.error.code == "model_gateway_error"
    assert result.error.retryable is True
    assert result.error.details["gateway_error"]["code"] == "model_request_timeout"


def test_react_known_events_agent_output_fallback_summary_is_chinese() -> None:
    task = agent_task().model_copy(
        update={
            "task_type": TaskType.GENERATE_KNOWN_EVENTS,
            "required_output_schema": "KnownEventsDocument",
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "document_id": "doc_known_events",
                "document_type": "known_events",
                "ticker": task.ticker,
                "events": [
                    {
                        "event_id": "event_1",
                        "description": "已确认的供应链事件。",
                        "expectation_id": "expectation_mu_001",
                    }
                ],
            }
        ]
    )

    result = runner.run(task)

    source = result.payload["structured"]["events"][0]["source"]
    assert source["summary"] == "供应商证据不可用，已保留模型输出溯源。"


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


def test_react_unwraps_fenced_nested_react_protocol_text() -> None:
    task = agent_task()
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(
            MockModelClient(
                text=(
                    "```json\n"
                    '{"react_protocol":{"is_complete":true,"completion_reason":"done",'
                    '"final_payload":{"summary":"ok"},"tool_calls":[],"delegations":[]}}\n'
                    "```"
                )
            )
        ),
        tool_registry=default_tool_registry(),
        tool_mode="mock",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["structured"] == {"summary": "ok"}


def test_react_unwraps_structured_text_nested_react_protocol_action() -> None:
    task = agent_task()
    client = RecordingModelClient(
        [
            {
                "text": json.dumps(
                    {
                        "react_protocol": {
                            "is_complete": False,
                            "tool_calls": [
                                {
                                    "tool_name": "doxatlas.query",
                                    "input": {"query": "AI demand narrative"},
                                }
                            ],
                            "delegations": [],
                        }
                    }
                )
            },
            {
                "is_complete": True,
                "completion_reason": "done after tool",
                "final_payload": {"summary": "ok"},
            },
        ]
    )
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(client),
        tool_registry=default_tool_registry(),
        tool_mode="mock",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert len(client.requests) == 2
    assert result.payload["structured"] == {"summary": "ok"}
    assert result.tool_calls[0].tool_name == "doxatlas.query"


def test_react_does_not_accept_incomplete_final_payload() -> None:
    task = agent_task()
    runner = runner_with_sequence(
        [
            {
                "is_complete": False,
                "final_payload": {"summary": "premature"},
                "tool_calls": [],
                "delegations": [],
            },
            {
                "is_complete": True,
                "completion_reason": "fixed",
                "final_payload": {"summary": "ok"},
            },
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["structured"] == {"summary": "ok"}
    assert any(
        entry["kind"] == "react_no_progress"
        for entry in result.payload["react_audit"]["entries"]
    )


def test_react_accepts_complete_final_payload_with_skill_call_metadata() -> None:
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.C1_FUNDAMENTAL_RESEARCH,
            "task_type": TaskType.GENERATE_GLOBAL_RESEARCH,
            "required_output_schema": "ResearchSection",
            "permissions": default_agent_registry()
            .get(AgentName.C1_FUNDAMENTAL_RESEARCH)
            .runtime.to_permissions(),
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "final with loaded skill reference",
                "skill_calls": [{"skill_id": "financial-statement", "reason": "already used"}],
                "final_payload": {
                    "text": "完整研究正文。",
                    "summary": "完整研究摘要。",
                    "evidence_refs": [],
                    "author_agent": "C1",
                    "reviewer_agents": [],
                },
            }
        ],
        react_config=ReActHarnessConfig(max_steps=1),
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["structured"]["summary"] == "完整研究摘要。"
    assert "financial-statement" in result.payload["react_audit"]["loaded_skill_ids"]


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
    assert CHINESE_OUTPUT_RULES[0] in first_request_text
    assert "JSON key" in first_request_text
    user_payload = json.loads(client.requests[0].messages[-1].content)
    assert "assembled_task_prompt" not in user_payload
    assert "task_spec" not in user_payload
    assert "rules" not in user_payload
    assert "runtime_output_schema" not in user_payload["task"]
    assert user_payload["task"]["input_context"] == {"document_ids": ["global-research-001"]}
    assert "context_snapshot" not in user_payload
    assert "plan" not in user_payload
    assert "recent_trajectory" not in user_payload
    assert "available_tools" in user_payload
    assert "available_skills" in user_payload
    assert user_payload["loaded_skills"] == []
    assert user_payload["tool_call_policy"]["required_tool_names"] == []
    assert "tool_requirements" not in user_payload["tool_call_policy"]


def test_react_prompt_includes_compact_doxatlas_contract_briefs() -> None:
    class NoopToolClient(ToolClient):
        def call(self, request: ToolRequest) -> ToolResult:
            return ToolResult(tool_name=request.tool_name, status=ResultStatus.SUCCEEDED)

    registry = ToolRegistry()
    registry.register(
        "doxa_query_propositions",
        NoopToolClient(),
        descriptor=ToolDescriptor(
            name="doxa_query_propositions",
            description="Read compact DoxAtlas propositions.",
            input_fields=["run_id", "narrative_code", "event_code"],
            business_purpose="Audit DoxAtlas proposition support.",
            contract_brief="Use event scope run_id+Nxx+Exx. Returns compact Pxx propositions.",
            concurrent_safe=True,
            compactable=True,
        ),
    )
    task = agent_task().model_copy(
        update={"permissions": AgentPermissions(allowed_tools=["doxa_query_propositions"])},
        deep=True,
    )
    client = RecordingModelClient([{"summary": "ok"}])
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(client),
        tool_registry=registry,
        tool_mode="mock",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    user_payload = json.loads(client.requests[0].messages[-1].content)
    tool = user_payload["available_tools"][0]
    assert tool["contract_brief"] == (
        "Use event scope run_id+Nxx+Exx. Returns compact Pxx propositions."
    )
    assert "concurrent_safe" not in tool
    assert "compactable" not in tool
    policy = user_payload["tool_call_policy"]["doxatlas_contract_brief"]
    assert "DoxAtlas uses scoped short ids" in policy
    assert "run_id+narrative_code+event_code" in policy
    assert "bare narrative_code" in policy
    assert "finalize with a data gap" in policy


def test_prompt_assembler_adds_chinese_output_rules_for_single_shot_paths() -> None:
    registry = default_agent_registry()
    task = agent_task()
    definition = registry.get(task.agent_name)
    injected = PromptInjector().inject(task, definition)
    assert injected.prompt_bundle is not None

    assembled = PromptAssembler().assemble(
        injected,
        definition,
        injected.prompt_bundle,
        context_snapshot=None,
        tool_results=[],
    )

    assert CHINESE_OUTPUT_RULES[0] in assembled.instructions
    assert "JSON key" not in assembled.user_prompt
    user_payload = json.loads(assembled.user_prompt)
    assert "rules" not in user_payload
    assert "context_snapshot" not in user_payload
    assert "tool_results" not in user_payload
    assert user_payload["task_summary"]["input_context"] == {
        "document_ids": ["global-research-001"]
    }


def test_agent_visible_input_context_omits_only_safe_empty_workflow_fields() -> None:
    visible = agent_visible_input_context(
        {
            "completed_nodes": [],
            "stable_document_types": [],
            "belief_state_summary": {},
            "pending_patch_ids": [],
            "pending_patches": [],
            "working_memory_summary": [],
            "unresolved_objections": [],
            "blocking_delegations": [],
            "evidence_refs": [],
            "document3_review_objections": [],
            "field_repair_task": {},
            "review_scope": [],
            "runtime_context": {"known_events": [], "monitoring_policies": []},
        }
    )

    for omitted_key in (
        "completed_nodes",
        "stable_document_types",
        "belief_state_summary",
        "pending_patch_ids",
        "pending_patches",
        "working_memory_summary",
        "unresolved_objections",
        "blocking_delegations",
        "evidence_refs",
    ):
        assert omitted_key not in visible
    assert visible["document3_review_objections"] == []
    assert visible["field_repair_task"] == {}
    assert visible["review_scope"] == []
    assert visible["runtime_context"]["known_events"] == []
    assert visible["runtime_context"]["monitoring_policies"] == []


def test_react_loads_external_skill_on_demand() -> None:
    client = RecordingModelClient(
        [
            {
                "is_complete": False,
                "skill_calls": [
                    {
                        "skill_id": "financial-statement",
                        "reason": "Need financial statement analysis standards.",
                    }
                ],
            },
            {
                "is_complete": True,
                "completion_reason": "done",
                "final_payload": {
                    "text": "Financial quality reviewed.",
                    "summary": "Financial quality reviewed.",
                    "evidence_refs": [],
                    "author_agent": "C1",
                    "reviewer_agents": [],
                },
            },
        ]
    )
    definition = default_agent_registry().get(AgentName.C1_FUNDAMENTAL_RESEARCH)
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.C1_FUNDAMENTAL_RESEARCH,
            "task_type": TaskType.GENERATE_GLOBAL_RESEARCH,
            "required_output_schema": "ResearchSection",
            "permissions": definition.runtime.to_permissions(),
            "run_metadata": agent_task().run_metadata.model_copy(
                update={"workflow_node": "BuildGlobalResearch"}
            ),
        },
        deep=True,
    )
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(client),
        tool_registry=default_tool_registry(),
        tool_mode="mock",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    first_payload = json.loads(client.requests[0].messages[-1].content)
    second_payload = json.loads(client.requests[1].messages[-1].content)
    assert [item["skill_id"] for item in first_payload["available_skills"]] == [
        "financial-statement",
        "valuation-model",
    ]
    assert "Financial Statement Analysis" not in json.dumps(
        first_payload["loaded_skills"],
        ensure_ascii=True,
    )
    assert second_payload["loaded_skills"][0]["skill_id"] == "financial-statement"
    assert "instructions" in second_payload["loaded_skills"][0]
    assert result.payload["skill_ids"] == ["financial-statement"]
    assert result.payload["external_skill_package_ids"] == ["financial-statement"]
    assert any(
        entry["kind"] == "skill_result" and entry["status"] == "loaded"
        for entry in result.payload["react_audit"]["entries"]
    )


def test_react_does_not_load_same_skill_twice() -> None:
    definition = default_agent_registry().get(AgentName.C1_FUNDAMENTAL_RESEARCH)
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.C1_FUNDAMENTAL_RESEARCH,
            "task_type": TaskType.GENERATE_GLOBAL_RESEARCH,
            "required_output_schema": "ResearchSection",
            "permissions": definition.runtime.to_permissions(),
            "run_metadata": agent_task().run_metadata.model_copy(
                update={"workflow_node": "BuildGlobalResearch"}
            ),
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {"is_complete": False, "skill_calls": [{"skill_id": "financial-statement"}]},
            {"is_complete": False, "skill_calls": [{"skill_id": "financial-statement"}]},
            {
                "is_complete": True,
                "completion_reason": "done",
                "final_payload": {
                    "text": "Done.",
                    "summary": "Done.",
                    "evidence_refs": [],
                    "author_agent": "C1",
                    "reviewer_agents": [],
                },
            },
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["skill_ids"] == ["financial-statement"]
    duplicate_entries = [
        entry
        for entry in result.payload["react_audit"]["entries"]
        if entry["kind"] == "skill_result" and entry["status"] == "duplicate"
    ]
    assert duplicate_entries


def test_react_rejects_unexposed_skill_call() -> None:
    definition = default_agent_registry().get(AgentName.C1_FUNDAMENTAL_RESEARCH)
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.C1_FUNDAMENTAL_RESEARCH,
            "task_type": TaskType.GENERATE_GLOBAL_RESEARCH,
            "required_output_schema": "ResearchSection",
            "permissions": definition.runtime.to_permissions(),
            "run_metadata": agent_task().run_metadata.model_copy(
                update={"workflow_node": "BuildGlobalResearch"}
            ),
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {"is_complete": False, "skill_calls": [{"skill_id": "macro-analysis"}]},
            {
                "is_complete": True,
                "completion_reason": "done",
                "final_payload": {
                    "text": "Done.",
                    "summary": "Done.",
                    "evidence_refs": [],
                    "author_agent": "C1",
                    "reviewer_agents": [],
                },
            },
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["skill_ids"] == []
    assert any(
        entry["kind"] == "skill_result" and entry["status"] == "rejected"
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


def test_react_uses_lightweight_contract_for_field_objection_resolution() -> None:
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "required_output_schema": "ExpectationConstructionResult",
            "input_context": {
                **base_task.input_context,
                "resolution_mode": "field_review_objection_resolution",
                "unresolved_objections": [
                    {
                        "objection_id": "obj_1",
                        "reason": "Reviewer raised a field concern.",
                    }
                ],
                "pending_patches": [],
            },
        },
        deep=True,
    )
    client = RecordingModelClient(
        [
            {
                "is_complete": True,
                "completion_reason": "resolved",
                "final_payload": {
                    "proposed_patches": [],
                    "evidence_refs": [],
                    "delegations": [],
                    "unknowns": [],
                    "rationale": "Resolved objection.",
                    "resolved_objection_ids": ["obj_1"],
                    "objection_resolutions": [
                        {
                            "objection_id": "obj_1",
                            "decision": "resolved",
                            "resolution_note": "Existing field is supported.",
                            "changed_paths": ["document.realized_facts"],
                            "evidence_refs": [],
                        }
                    ],
                },
            }
        ]
    )
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(client),
        tool_registry=default_tool_registry(),
        tool_mode="mock",
    )

    result = runner.run(task)

    prompt = json.loads(client.requests[0].messages[1].content)
    contract = prompt["output_contract"]["ExpectationConstructionResult"]
    contract_text = json.dumps(contract, ensure_ascii=False)
    assert result.status is ResultStatus.SUCCEEDED
    assert "objection-resolution task" in contract_text
    assert "Do not call tools" in contract_text
    assert "Do not generate 2 to 3 expectation patches" in contract_text
    assert "patch.after as a partial expectation_unit object" in contract_text
    assert "Never return unaffected expectation patches" in contract_text
    assert contract["final_payload"]["objection_resolutions"][0]["objection_id"]


def test_react_preserves_objection_resolution_changes_as_partial_after() -> None:
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "required_output_schema": "ExpectationConstructionResult",
            "input_context": {
                **base_task.input_context,
                "resolution_mode": "field_review_objection_resolution",
                "unresolved_objections": [{"objection_id": "obj_1"}],
            },
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "resolved",
                "final_payload": {
                    "proposed_patches": [
                        {
                            "patch_id": "patch_change_map",
                            "target": {
                                "document_type": "expectation_unit",
                                "ticker": "NVDA",
                                "expectation_id": "exp_1",
                                "field_path": "document",
                            },
                            "operation": "update",
                            "rationale": "Apply path-map corrections.",
                            "changes": {
                                "market_view.summary": "Source limitation noted.",
                                "document.realized_facts_summary": "Quarter labels corrected.",
                            },
                        }
                    ],
                    "evidence_refs": [],
                    "delegations": [],
                    "unknowns": [],
                    "rationale": "Accepted revision.",
                    "accepted_objection_ids": ["obj_1"],
                    "objection_resolutions": [
                        {
                            "objection_id": "obj_1",
                            "decision": "accepted",
                            "resolution_note": "Patch revised.",
                            "changed_paths": ["market_view.summary"],
                            "evidence_refs": [],
                        }
                    ],
                },
            }
        ]
    )

    result = runner.run(task)

    patch = result.payload["structured"]["proposed_patches"][0]
    assert patch["operation"] == "update"
    assert patch["after"] == {
        "market_view": {"summary": "Source limitation noted."},
        "realized_facts_summary": "Quarter labels corrected.",
    }


def test_react_preserves_objection_resolution_indexed_changes_as_partial_after() -> None:
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "required_output_schema": "ExpectationConstructionResult",
            "input_context": {
                **base_task.input_context,
                "resolution_mode": "field_review_objection_resolution",
                "unresolved_objections": [{"objection_id": "obj_1"}],
            },
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "resolved",
                "final_payload": {
                    "proposed_patches": [
                        {
                            "patch_id": "patch_indexed_changes",
                            "target": {
                                "document_type": "expectation_unit",
                                "ticker": "NVDA",
                                "expectation_id": "exp_1",
                                "field_path": "document",
                            },
                            "operation": "update",
                            "rationale": "Apply indexed monitoring corrections.",
                            "changes": {
                                "document.event_monitoring_direction.positive_events[0]": (
                                    "Q3 revenue threshold corrected."
                                ),
                                "document.event_monitoring_direction.negative_events[1]": (
                                    "Gross-margin downside threshold corrected."
                                ),
                            },
                        }
                    ],
                    "evidence_refs": [],
                    "delegations": [],
                    "unknowns": [],
                    "rationale": "Accepted revision.",
                    "accepted_objection_ids": ["obj_1"],
                    "objection_resolutions": [
                        {
                            "objection_id": "obj_1",
                            "decision": "accepted",
                            "resolution_note": "Patch revised.",
                            "changed_paths": [
                                "event_monitoring_direction.positive_events[0]",
                                "event_monitoring_direction.negative_events[1]",
                            ],
                            "evidence_refs": [],
                        }
                    ],
                },
            }
        ]
    )

    result = runner.run(task)

    patch = result.payload["structured"]["proposed_patches"][0]
    assert patch["operation"] == "update"
    assert patch["after"] == {
        "event_monitoring_direction": {
            "positive_events": ["Q3 revenue threshold corrected."],
            "negative_events": [None, "Gross-margin downside threshold corrected."],
        }
    }


def test_react_preserves_objection_resolution_flat_partial_fields_as_after() -> None:
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "required_output_schema": "ExpectationConstructionResult",
            "input_context": {
                **base_task.input_context,
                "resolution_mode": "field_review_objection_resolution",
                "unresolved_objections": [{"objection_id": "obj_1"}],
            },
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "resolved",
                "final_payload": {
                    "proposed_patches": [
                        {
                            "patch_id": "patch_flat_partial",
                            "target": {
                                "document_type": "expectation_unit",
                                "ticker": "NVDA",
                                "expectation_id": "exp_1",
                                "field_path": "document",
                            },
                            "operation": "update",
                            "rationale": "Apply flat field corrections.",
                            "event_monitoring_direction": {
                                "known_event_notice": "Q3 is current; Q4 is forward.",
                                "positive_events": ["Q4 guide improves"],
                                "negative_events": ["Q4 guide misses"],
                            },
                            "realized_facts": [
                                {
                                    "event_id": "event_1",
                                    "description": "Q2 date corrected.",
                                    "price_reaction": {
                                        "price_change": "removed",
                                        "price_pattern": "directional",
                                        "interpretation": "requires OHLCV",
                                    },
                                    "evidence_refs": [],
                                }
                            ],
                        }
                    ],
                    "evidence_refs": [],
                    "delegations": [],
                    "unknowns": [],
                    "rationale": "Accepted revision.",
                    "accepted_objection_ids": ["obj_1"],
                    "objection_resolutions": [
                        {
                            "objection_id": "obj_1",
                            "decision": "accepted",
                            "resolution_note": "Patch revised.",
                            "changed_paths": ["event_monitoring_direction"],
                            "evidence_refs": [],
                        }
                    ],
                },
            }
        ]
    )

    result = runner.run(task)

    patch = result.payload["structured"]["proposed_patches"][0]
    assert patch["operation"] == "update"
    assert patch["after"]["event_monitoring_direction"]["known_event_notice"] == (
        "Q3 is current; Q4 is forward."
    )
    assert patch["after"]["realized_facts"][0]["event_id"] == "event_1"
    assert "event_monitoring_direction" not in {
        key for key in patch if key != "after"
    }


def test_react_normalizes_expectation_shell_construction_without_patches() -> None:
    task = agent_task().model_copy(
        update={"required_output_schema": "ExpectationShellConstructionResult"},
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "drafted",
                "final_payload": {
                    "expectations": [
                        {
                            "expectation_id": "exp_1",
                            "name": "Commercial milestone execution",
                            "direction": "bullish",
                            "market_view": "Market focuses on execution milestones.",
                        }
                    ],
                    "rationale": "Built from DoxAtlas narrative evidence.",
                },
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    structured = result.payload["structured"]
    assert structured["shells"][0]["expectation_id"] == "exp_1"
    assert "proposed_patches" not in structured


def test_react_expectation_shell_fallback_produces_allowed_shell_count() -> None:
    task = agent_task().model_copy(
        update={
            "required_output_schema": "ExpectationShellConstructionResult",
            "input_context": {
                "global_research_context": {
                    "sections": {
                        "fundamental_report": {
                            "summary": "MU revenue and gross margin improved with HBM mix."
                        },
                        "industry_report": {
                            "summary": (
                                "AI servers drive HBM demand while memory supply remains cyclical."
                            )
                        },
                    }
                }
            },
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "fallback",
                "final_payload": {"rationale": "Use global research."},
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    shells = result.payload["structured"]["shells"]
    assert 1 <= len(shells) <= 3
    assert len({shell["expectation_name"] for shell in shells}) == len(shells)
    assert all(shell["expectation_id"] for shell in shells)


def test_react_normalizes_expectation_detail_to_single_patch() -> None:
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "required_output_schema": "ExpectationDetailResult",
            "input_context": {
                **base_task.input_context,
                "expectation_shell": {
                    "expectation_id": "exp_1",
                    "expectation_name": "Commercial milestone execution",
                    "direction": "bullish",
                    "why_it_matters": "It drives valuation.",
                    "market_view": {
                        "text": "Market focuses on execution milestones.",
                        "summary": "Execution milestones drive the view.",
                        "evidence_refs": [],
                        "author_agent": "O1",
                        "reviewer_agents": ["A1"],
                    },
                    "evidence_refs": [],
                    "unknowns": [],
                    "rationale": "Shell rationale.",
                },
            },
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "detailed",
                "final_payload": {
                    "realized_facts_summary": "Known facts are partially priced.",
                    "key_variables": ["Deployment cadence"],
                    "positive_events": ["Deployment milestone confirmed"],
                    "negative_events": ["Deployment delay"],
                    "rationale": "Detail completed.",
                },
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    patch = result.payload["structured"]["proposed_patches"][0]
    assert patch["target"]["expectation_id"] == "exp_1"
    assert patch["after"]["expectation_name"] == "Commercial milestone execution"
    assert patch["after"]["key_variables"][0]["name"] == "Deployment cadence"


def test_react_normalizes_event_monitoring_dict_items_to_strings() -> None:
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "required_output_schema": "ExpectationDetailResult",
            "input_context": {
                **base_task.input_context,
                "expectation_shell": {
                    "expectation_id": "exp_1",
                    "expectation_name": "Commercial milestone execution",
                    "direction": "bullish",
                    "why_it_matters": "It drives valuation.",
                    "market_view": {
                        "text": "Market focuses on execution milestones.",
                        "summary": "Execution milestones drive the view.",
                        "evidence_refs": [],
                        "author_agent": "O1",
                        "reviewer_agents": ["A1"],
                    },
                    "evidence_refs": [],
                    "unknowns": [],
                    "rationale": "Shell rationale.",
                },
            },
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "detailed",
                "final_payload": {
                    "realized_facts_summary": "Known facts are partially priced.",
                    "key_variables": ["Deployment cadence"],
                    "event_monitoring_direction": {
                        "known_event_notice": "Watch quarterly updates.",
                        "positive_events": [
                            {
                                "event": "HBM order accelerates",
                                "monitoring_signal": "customer order disclosure",
                                "impact": "supports demand durability",
                            }
                        ],
                        "negative_events": [
                            {
                                "event": "Deployment delayed",
                                "monitoring": "earnings call guidance",
                                "impact": "weakens execution thesis",
                            }
                        ],
                    },
                    "rationale": "Detail completed.",
                },
            }
        ]
    )

    result = runner.run(task)

    monitoring = result.payload["structured"]["proposed_patches"][0]["after"][
        "event_monitoring_direction"
    ]
    assert monitoring["positive_events"][0] == (
        "HBM order accelerates; customer order disclosure; supports demand durability"
    )
    assert not monitoring["negative_events"][0].startswith("{")


def test_react_retries_prompt_echo_instead_of_accepting_it_as_detail() -> None:
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "required_output_schema": "ExpectationDetailResult",
            "input_context": {
                **base_task.input_context,
                "expectation_shell": {
                    "expectation_id": "exp_echo",
                    "expectation_name": "Prompt echo recovery",
                    "direction": "bullish",
                    "why_it_matters": "It protects the detail boundary.",
                    "market_view": {
                        "text": "Market view.",
                        "summary": "Market view summary.",
                        "evidence_refs": [],
                        "author_agent": "O1",
                        "reviewer_agents": ["A1"],
                    },
                    "evidence_refs": [],
                    "unknowns": [],
                    "rationale": "Shell rationale.",
                },
            },
        },
        deep=True,
    )
    prompt_echo = {
        "react_protocol": {"max_steps": 5},
        "task": {"required_output_schema": "ExpectationDetailResult"},
        "tool_call_policy": {"available_tools_are_authoritative": True},
        "output_contract": {"ExpectationDetailResult": {"rules": []}},
    }
    valid_detail = {
        "realized_facts_summary": "Customer qualification is partly priced.",
        "realized_facts": ["Customer qualification was announced."],
        "key_variables": ["Deployment cadence"],
        "event_monitoring_direction": {
            "known_event_notice": "Watch customer disclosures.",
            "positive_events": ["Customer order conversion accelerates"],
            "negative_events": ["Customer deployment timeline slips"],
        },
        "rationale": "Detail completed.",
    }
    client = RecordingModelClient([prompt_echo, valid_detail])
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(client),
        tool_registry=default_tool_registry(),
        react_config=ReActHarnessConfig(max_steps=2),
        tool_mode="mock",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert len(client.requests) == 2
    patch = result.payload["structured"]["proposed_patches"][0]
    assert patch["target"]["expectation_id"] == "exp_echo"


def test_react_expectation_detail_rejects_summary_payload_without_detail_fields() -> None:
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "required_output_schema": "ExpectationDetailResult",
            "input_context": {
                **base_task.input_context,
                "global_research_context": {
                    "ticker": "ASTS",
                    "sections": {
                        "market_narrative_report": {
                            "summary": "Global research is available."
                        }
                    },
                },
                "expectation_shell": {
                    "expectation_id": "exp_summary_only",
                    "expectation_name": "Summary-only payload",
                    "direction": "bullish",
                    "why_it_matters": "It should not be synthesized from global fallback.",
                    "market_view": {
                        "text": "Market view.",
                        "summary": "Market view summary.",
                        "evidence_refs": [],
                        "author_agent": "O1",
                        "reviewer_agents": ["A1"],
                    },
                    "evidence_refs": [],
                    "unknowns": [],
                    "rationale": "Shell rationale.",
                },
            },
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "Only summarized retrieved data.",
                "final_payload": {
                    "data_retrieved": "Narrative report was retrieved.",
                    "current_work_state": "Need to generate ExpectationDetailResult next.",
                    "recommended_next_steps": ["Build realized_facts and key_variables."],
                },
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.FAILED
    assert result.error is not None
    assert result.error.code == "invalid_final_payload"
    assert "requires exactly one proposed_patches item" in result.error.message


def test_react_expectation_detail_patch_preserves_shell_identity() -> None:
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "required_output_schema": "ExpectationDetailResult",
            "input_context": {
                **base_task.input_context,
                "expectation_shell": {
                    "expectation_id": "exp_shell",
                    "expectation_name": "Shell thesis",
                    "direction": "bullish",
                    "why_it_matters": "Shell reason.",
                    "market_view": {
                        "text": "Shell market view.",
                        "summary": "Shell summary.",
                        "evidence_refs": [],
                        "author_agent": "O1",
                        "reviewer_agents": ["A1"],
                    },
                    "evidence_refs": [],
                    "unknowns": [],
                    "rationale": "Shell rationale.",
                },
            },
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "detailed",
                "final_payload": {
                    "proposed_patches": [
                        {
                            "target": {
                                "document_type": "expectation_unit",
                                "expectation_id": "wrong_id",
                                "field_path": "document",
                            },
                            "operation": "create",
                            "after": {
                                "expectation_id": "wrong_id",
                                "expectation_name": "Wrong thesis",
                                "direction": "bearish",
                                "why_it_matters": "Wrong reason.",
                                "realized_facts_summary": "Known facts are partially priced.",
                                "key_variables": ["Deployment cadence"],
                                "realized_facts": ["Customer qualification"],
                            },
                            "rationale": "Detail completed.",
                        }
                    ],
                    "rationale": "Detail completed.",
                },
            }
        ]
    )

    result = runner.run(task)

    patch = result.payload["structured"]["proposed_patches"][0]
    assert patch["target"]["expectation_id"] == "exp_shell"
    assert patch["target"]["document_id"] is None
    assert patch["after"]["expectation_id"] == "exp_shell"
    assert patch["after"]["expectation_name"] == "Shell thesis"
    assert patch["after"]["direction"] == "bullish"
    assert patch["after"]["why_it_matters"] == "Shell reason."
    assert patch["after"]["market_view"]["summary"] == "Shell summary."


def test_react_expectation_detail_carries_evidence_into_price_reaction_and_variables() -> None:
    evidence = {
        "evidence_id": "evidence_detail",
        "source_type": "external_report",
        "source_id": "source_detail",
        "title": "Detail evidence",
        "summary": "Evidence supporting the expectation detail.",
        "confidence": 0.8,
        "citation_scope": "expectation_detail",
    }
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "required_output_schema": "ExpectationDetailResult",
            "input_context": {
                **base_task.input_context,
                "expectation_shell": {
                    "expectation_id": "exp_evidence",
                    "expectation_name": "Evidence-backed thesis",
                    "direction": "bullish",
                    "why_it_matters": "It drives valuation.",
                    "market_view": {
                        "text": "Market view.",
                        "summary": "Market view summary.",
                        "evidence_refs": [evidence],
                        "author_agent": "O1",
                        "reviewer_agents": ["A1"],
                    },
                    "evidence_refs": [evidence],
                    "unknowns": [],
                    "rationale": "Shell rationale.",
                },
            },
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "detailed",
                "final_payload": {
                    "evidence_refs": [evidence],
                    "proposed_patches": [
                        {
                            "target": {
                                "document_type": "expectation_unit",
                                "expectation_id": "exp_evidence",
                                "field_path": "document",
                            },
                            "operation": "create",
                            "after": {
                                "expectation_id": "exp_evidence",
                                "expectation_name": "Evidence-backed thesis",
                                "direction": "bullish",
                                "why_it_matters": "It drives valuation.",
                                "market_view": {
                                    "text": "Market view.",
                                    "summary": "Market view summary.",
                                    "evidence_refs": [evidence],
                                    "author_agent": "O1",
                                    "reviewer_agents": ["A1"],
                                },
                                "realized_facts_summary": "Known facts are partially priced.",
                                "realized_facts": [
                                    {
                                        "event_id": "event_1",
                                        "description": {
                                            "fact": "Customer qualification",
                                            "when": "2026 Q1",
                                            "why_it_matters": "Confirms HBM demand.",
                                            "pricing_status": "partially priced",
                                        },
                                        "evidence_refs": [evidence],
                                        "price_reaction": {
                                            "price_change": "+2%",
                                            "price_pattern": "positive",
                                            "interpretation": "市场正面消化。",
                                        },
                                    },
                                    {
                                        "event_id": "event_2",
                                        "description": "Management guidepost is already public.",
                                        "price_reaction": {
                                            "price_change": "unknown",
                                            "price_pattern": "unknown",
                                            "interpretation": "Needs monitoring.",
                                        },
                                    },
                                ],
                                "key_variables": [
                                    {
                                        "variable_id": "var_1",
                                        "name": "Customer order cadence",
                                        "current_status": "需要继续跟踪",
                                        "certainty": "medium",
                                    }
                                ],
                            },
                            "rationale": "Detail completed.",
                        }
                    ],
                    "rationale": "Detail completed.",
                },
            }
        ]
    )

    result = runner.run(task)

    patch = result.payload["structured"]["proposed_patches"][0]
    fact = patch["after"]["realized_facts"][0]
    fallback_fact = patch["after"]["realized_facts"][1]
    variable = patch["after"]["key_variables"][0]
    assert fact["description"].startswith("fact: Customer qualification")
    assert "why_it_matters: Confirms HBM demand." in fact["description"]
    assert fact["evidence_refs"][0]["evidence_id"] == "evidence_detail"
    assert fact["price_reaction"]["evidence_refs"][0]["evidence_id"] == "evidence_detail"
    assert fallback_fact["evidence_refs"][0]["evidence_id"] == "evidence_detail"
    assert fallback_fact["price_reaction"]["evidence_refs"][0]["evidence_id"] == (
        "evidence_detail"
    )
    assert variable["evidence_refs"][0]["evidence_id"] == "evidence_detail"


def test_react_expectation_detail_recovers_arrays_from_invalid_text() -> None:
    evidence = {
        "evidence_id": "evidence_salvage",
        "source_type": "external_report",
        "source_id": "source_salvage",
        "title": "Salvage evidence",
        "summary": "Evidence embedded in the model response text.",
        "confidence": 0.8,
        "citation_scope": "expectation_detail",
    }
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "required_output_schema": "ExpectationDetailResult",
            "input_context": {
                **base_task.input_context,
                "expectation_shell": {
                    "expectation_id": "exp_salvage",
                    "expectation_name": "HBM supply conversion",
                    "direction": "bullish",
                    "why_it_matters": "It drives margin conversion.",
                    "market_view": {
                        "text": "Market view.",
                        "summary": "Market view summary.",
                        "evidence_refs": [evidence],
                        "author_agent": "O1",
                        "reviewer_agents": ["A1"],
                    },
                    "evidence_refs": [evidence],
                    "unknowns": [],
                    "rationale": "Shell rationale.",
                },
            },
        },
        deep=True,
    )
    structured = {
        "is_complete": True,
        "completion_reason": "detailed",
        "final_payload": {
            "evidence_refs": [evidence],
            "proposed_patches": [
                {
                    "target": {
                        "document_type": "expectation_unit",
                        "expectation_id": "exp_salvage",
                        "field_path": "document",
                    },
                    "operation": "create",
                    "after": {
                        "expectation_id": "exp_salvage",
                        "expectation_name": "HBM supply conversion",
                        "direction": "bullish",
                        "why_it_matters": "It drives margin conversion.",
                        "market_view": {
                            "text": "Market view.",
                            "summary": "Market view summary.",
                            "evidence_refs": [evidence],
                            "author_agent": "O1",
                            "reviewer_agents": ["A1"],
                        },
                        "realized_facts_summary": "No realized facts were available.",
                        "realized_facts": [],
                        "key_variables": [],
                    },
                    "rationale": "Detail completed.",
                    "evidence_refs": [evidence],
                }
            ],
            "rationale": "Detail completed.",
        },
    }
    text = """
    {
      "final_payload": {
        "realized_facts_summary": "Customer qualification is public; conversion is uncertain.",
        "realized_facts": [
          {
            "event_id": "event_customer",
            "fact": "Customer qualification was announced",
            "when": "2026 Q1",
            "why_it_matters": "Confirms HBM demand.",
            "pricing_assessment": "partially priced after the earnings call",
            "evidence_refs": [
              {
                "evidence_id": "evidence_salvage",
                "source_type": "external_report",
                "source_id": "source_salvage",
                "title": "Salvage evidence",
                "summary": "Evidence embedded in the model response text.",
                "confidence": 0.8,
                "citation_scope": "expectation_detail"
              }
            ]
          }
        ],
        "key_variables": [
          {
            "variable": "Customer order cadence",
            "relevance": "Monitor whether follow-on orders convert into revenue.",
            "certainty": "medium",
            "evidence_refs": [
              {
                "evidence_id": "evidence_salvage",
                "source_type": "external_report",
                "source_id": "source_salvage",
                "title": "Salvage evidence",
                "summary": "Evidence embedded in the model response text.",
                "confidence": 0.8,
                "citation_scope": "expectation_detail"
              }
            ]
          }
        ]
      trailing comma breaks the outer JSON
    """
    client = TextAndStructuredModelClient([(structured, text)])
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(client),
        tool_registry=default_tool_registry(),
        tool_mode="mock",
    )

    result = runner.run(task)

    patch = result.payload["structured"]["proposed_patches"][0]
    fact = patch["after"]["realized_facts"][0]
    variable = patch["after"]["key_variables"][0]
    assert result.status is ResultStatus.SUCCEEDED
    assert patch["after"]["realized_facts_summary"].startswith("Customer qualification")
    assert fact["description"].startswith("fact: Customer qualification")
    assert "pricing_assessment: partially priced" in fact["description"]
    assert fact["price_reaction"]["price_pattern"] == "described"
    assert fact["price_reaction"]["evidence_refs"][0]["evidence_id"] == "evidence_salvage"
    assert variable["name"] == "Customer order cadence"
    assert "follow-on orders" in variable["current_status"]
    assert variable["evidence_refs"][0]["evidence_id"] == "evidence_salvage"


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


def test_react_normalizes_expectation_detail_candidate_legacy_fields() -> None:
    evidence_ref = {
        "evidence_id": "evidence_detail",
        "source_type": "agent_output",
        "source_id": "react:test",
        "title": "Detail evidence",
        "summary": "Model output evidence.",
        "retrieval_metadata": {},
        "confidence": 0.8,
        "citation_scope": "expectation_unit",
    }
    shell = {
        "expectation_id": "exp_shell",
        "expectation_name": "Shell identity must be preserved",
        "direction": "bearish",
        "why_it_matters": "Shell why-it-matters must survive detail completion.",
        "market_view": {
            "text": "Shell market view.",
            "summary": "Shell summary.",
            "evidence_refs": [evidence_ref],
            "author_agent": AgentName.O1_EXPECTATION_OWNER.value,
            "reviewer_agents": ["A1"],
        },
    }
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "required_output_schema": "ExpectationDetailCandidateResult",
            "input_context": {
                **base_task.input_context,
                "expectation_shell": shell,
            },
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "completed",
                "final_payload": {
                    "candidate": {
                        "document_id": "doc_candidate",
                        "ticker": "WRONG",
                        "expectation_id": "wrong_id",
                        "expectation_name": "Wrong detail identity",
                        "direction": "bullish",
                        "realized_facts": [
                            {
                                "fact": "Customer qualification was announced.",
                                "price_reaction": "Shares rallied after the announcement.",
                                "evidence_refs": [evidence_ref],
                            }
                        ],
                        "realized_facts_summary": "Qualification is already partly priced.",
                        "key_variables": [
                            {
                                "variable": "HBM shipment cadence",
                                "status": "Qualification is complete; ramp timing remains key.",
                                "certainty": "medium",
                                "evidence_refs": [evidence_ref],
                            }
                        ],
                        "event_monitoring_direction": {
                            "known_event_notice": "Qualification is already known.",
                            "positive_events": ["Named customer confirms faster HBM ramp."],
                            "negative_events": ["Named customer delays HBM ramp."],
                        },
                    },
                    "evidence_refs": [evidence_ref],
                    "delegations": [],
                    "unknowns": [],
                    "rationale": "Complete detail candidate.",
                },
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    candidate = result.payload["structured"]["candidate"]
    assert candidate["ticker"] == task.ticker
    assert candidate["expectation_id"] == "exp_shell"
    assert candidate["expectation_name"] == "Shell identity must be preserved"
    assert candidate["direction"] == "bearish"
    assert candidate["created_at"]
    assert candidate["market_view"]["summary"] == "Shell summary."
    assert candidate["realized_facts"][0]["description"].startswith(
        "fact: Customer qualification"
    )
    assert candidate["realized_facts"][0]["price_reaction"]["price_pattern"] == "described"
    assert candidate["key_variables"][0]["name"] == "HBM shipment cadence"


def test_react_expectation_detail_candidate_contract_is_complete_document_not_patch() -> None:
    task = agent_task().model_copy(
        update={"required_output_schema": "ExpectationDetailCandidateResult"},
        deep=True,
    )
    client = RecordingModelClient(
        [
            {
                "is_complete": True,
                "completion_reason": "captured",
                "final_payload": {
                    "candidate": {
                        "document_id": "doc_candidate",
                        "document_type": "expectation_unit",
                        "ticker": task.ticker,
                        "created_at": "2026-06-12T00:00:00Z",
                        "updated_at": None,
                        "expectation_id": "exp_candidate",
                        "expectation_name": "Candidate contract",
                        "direction": "neutral",
                        "why_it_matters": "Contract capture.",
                        "market_view": {
                            "text": "Market view.",
                            "summary": "Market view.",
                            "evidence_refs": [],
                            "author_agent": "O1",
                            "reviewer_agents": [],
                        },
                        "realized_facts": [
                            {
                                "event_id": "event_contract",
                                "description": "Contract event.",
                                "price_reaction": {
                                    "price_change": "unknown",
                                    "price_pattern": "unknown",
                                    "interpretation": "No price evidence.",
                                    "evidence_refs": [],
                                },
                                "evidence_refs": [],
                            }
                        ],
                        "realized_facts_summary": "Contract summary.",
                        "key_variables": [
                            {
                                "variable_id": "var_contract",
                                "name": "Evidence coverage",
                                "current_status": "Unknown",
                                "certainty": "unknown",
                                "evidence_refs": [],
                            }
                        ],
                        "event_monitoring_direction": {
                            "known_event_notice": "No fixed known date.",
                            "positive_events": ["Positive trigger."],
                            "negative_events": ["Negative trigger."],
                        },
                    },
                    "evidence_refs": [],
                    "delegations": [],
                    "unknowns": [],
                    "rationale": "Contract capture.",
                },
            }
        ]
    )
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(client),
        tool_registry=default_tool_registry(),
        tool_mode="mock",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    prompt = json.loads(client.requests[0].messages[-1].content)
    contract = prompt["output_contract"]["ExpectationDetailCandidateResult"]
    contract_json = json.dumps(contract, ensure_ascii=False)
    assert "proposed_patches" not in contract["final_payload"]
    assert "patches" not in contract["final_payload"]
    assert "event_time" not in contract_json
    assert "document_type" in contract["final_payload"]["candidate"]
    assert "price_reaction" in contract_json
    assert "variable_id" in contract_json
    assert "event_monitoring_direction" in contract_json
    assert "partial updates" in " ".join(contract["rules"])


def test_react_rejects_list_wrapped_document2_resolution_revised_candidate() -> None:
    evidence_ref = {
        "evidence_id": "evidence_resolution",
        "source_type": "agent_output",
        "source_id": "react:resolution",
        "title": "Resolution evidence",
        "summary": "Review evidence for the accepted revision.",
        "retrieval_metadata": {},
        "confidence": 0.8,
        "citation_scope": "expectation_unit",
    }
    task = agent_task().model_copy(
        update={"required_output_schema": "Document2ResolutionPlan"},
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "resolved",
                "final_payload": {
                    "expectation_id": "exp_resolution",
                    "decision": "accepted",
                    "decisions": [
                        {
                            "objection_id": "obj_resolution",
                            "finding_id": None,
                            "decision": "accepted",
                            "resolution_note": "Accepted reviewer finding with evidence.",
                            "changed_paths": ["document.market_view"],
                            "evidence_refs": [evidence_ref],
                        }
                    ],
                    "revised_candidate": [
                        {
                            "expectation_id": "exp_resolution",
                            "expectation_name": "Revised HBM demand durability",
                            "direction": "bullish",
                            "why_it_matters": "HBM demand durability drives the setup.",
                            "market_view": {
                                "text": "Market now needs a narrower HBM demand claim.",
                                "summary": "HBM demand claim narrowed.",
                                "evidence_refs": [evidence_ref],
                            },
                            "realized_facts": [
                                {
                                    "fact": "HBM qualification remains the known catalyst.",
                                    "price_reaction": "Shares reflect part of the catalyst.",
                                    "evidence_refs": [evidence_ref],
                                }
                            ],
                            "realized_facts_summary": "Known HBM catalysts are partly priced.",
                            "key_variables": [
                                {
                                    "variable": "Customer ramp timing",
                                    "status": "Qualification known; ramp timing still key.",
                                    "evidence_refs": [evidence_ref],
                                }
                            ],
                            "event_monitoring_direction": {
                                "known_event_notice": "Qualification is already known.",
                                "positive_events": ["Customer confirms faster ramp."],
                                "negative_events": ["Customer delays ramp."],
                            },
                            "evidence_refs": [evidence_ref],
                        }
                    ],
                    "evidence_refs": [evidence_ref],
                    "evidence_requests": [],
                    "unresolved_reason": None,
                    "rationale": "Accepted revision plan.",
                },
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.FAILED
    assert result.error is not None
    assert "revised_candidate" in result.error.message


def test_react_document2_resolution_contract_is_plan_with_complete_revision_shape() -> None:
    task = agent_task().model_copy(
        update={"required_output_schema": "Document2ResolutionPlan"},
        deep=True,
    )
    client = RecordingModelClient(
        [
            {
                "is_complete": True,
                "completion_reason": "captured",
                "final_payload": {
                    "expectation_id": "exp_resolution",
                    "decision": "resolved",
                    "decisions": [
                        {
                            "objection_id": "obj_resolution",
                            "finding_id": None,
                            "decision": "resolved",
                            "resolution_note": "Existing evidence resolves this objection.",
                            "changed_paths": ["document.market_view"],
                            "evidence_refs": [],
                        }
                    ],
                    "target_finding_ids": [],
                    "revised_candidate": None,
                    "evidence_requests": [],
                    "unresolved_finding_ids": [],
                    "unresolved_reason": None,
                    "rationale": "Resolution plan.",
                },
            }
        ]
    )
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(client),
        tool_registry=default_tool_registry(),
        tool_mode="mock",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    prompt = json.loads(client.requests[0].messages[-1].content)
    contract = prompt["output_contract"]["Document2ResolutionPlan"]
    contract_json = json.dumps(contract, ensure_ascii=False)
    assert contract["final_payload"]["revised_candidate"] is None
    assert "proposed_patches" not in contract["final_payload"]
    assert "event_time" not in contract_json
    assert "revised_candidate_shape_when_needed" in contract
    assert "price_reaction" in contract_json
    assert "variable_id" in contract_json
    assert "event_monitoring_direction" in contract_json
    rules = " ".join(contract["rules"])
    assert "proposed_patches" in rules
    assert "list-wrapped" in rules
    assert "multiple revised candidates" in rules


def test_react_document2_field_repair_contract_limits_single_field_output() -> None:
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.O1_EXPECTATION_OWNER,
            "task_type": TaskType.REVIEW_EXPECTATION_FIELD,
            "required_output_schema": "Document2FieldRepairResult",
            "input_context": {
                "field_repair_task": {
                    "task_id": "d2repair_exp_market_view",
                    "expectation_id": "exp_ai_demand",
                    "field_family": "market_view",
                }
            },
        },
        deep=True,
    )
    client = RecordingModelClient(
        [
            {
                "is_complete": True,
                "completion_reason": "field repaired",
                "final_payload": {
                    "task_id": "d2repair_exp_market_view",
                    "expectation_id": "exp_ai_demand",
                    "field_family": "market_view",
                    "decision": "resolved",
                    "decisions": [],
                    "target_finding_ids": [],
                    "realized_facts": None,
                    "key_variables": None,
                    "event_monitoring_direction": None,
                    "market_view": None,
                    "revised_candidate": None,
                    "evidence_requests": [],
                    "unresolved_finding_ids": [],
                    "unresolved_reason": None,
                    "rationale": "No content revision needed.",
                },
            }
        ]
    )
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(client),
        tool_registry=default_tool_registry(),
        tool_mode="mock",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    prompt = json.loads(client.requests[0].messages[-1].content)
    contract = prompt["output_contract"]["Document2FieldRepairResult"]
    examples_json = json.dumps(contract["typed_field_examples"], ensure_ascii=False)
    assert contract["final_payload"]["revised_candidate"] is None
    assert "event_time" not in examples_json
    assert "price_reaction" in examples_json
    rules = " ".join(contract["rules"])
    assert "For field_family other than cross_field" in rules
    assert "JSON Patch operations" in rules
    assert "Do not include event_time" in rules


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


def test_react_normalizes_doxatlas_audit_payload_to_strict_schema() -> None:
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.A1_DOXATLAS_AUDIT,
            "task_type": TaskType.REVIEW_EXPECTATION_FIELD,
            "required_output_schema": "DoxAtlasAuditResult",
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "audited",
                "final_payload": {
                    "overall_status": "revise",
                    "summary": "期望壳缺少 proposition 证据，需要 O1 修订。",
                    "findings": [
                        "market_view is not supported by source ids",
                        {
                            "field": "direction",
                            "status": "needs_more_evidence",
                            "reason": "Direction is plausible but not directly traced.",
                        },
                    ],
                    "objections": [
                        {
                            "severity": "material",
                            "reason": "Missing proposition support should block promotion.",
                        }
                    ],
                    "text": "This report-like field must not survive normalization.",
                    "author_agent": "A1",
                },
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    structured = result.payload["structured"]
    assert structured["verdict"] == "needs_revision"
    assert structured["revision_required"] is True
    assert structured["findings"][0]["field_path"] == "document"
    assert structured["findings"][0]["status"] == "unsupported"
    assert structured["findings"][1]["field_path"] == "direction"
    assert structured["objections"][0]["severity"] == "blocking"
    assert "text" not in structured
    assert "summary" not in structured


def test_react_normalizes_expectation_field_review_report_payload_to_schema() -> None:
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.C1_FUNDAMENTAL_RESEARCH,
            "task_type": TaskType.REVIEW_EXPECTATION_FIELD,
            "required_output_schema": "ExpectationFieldReviewResult",
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "reviewed",
                "final_payload": {
                    "ticker": "ASTS",
                    "review_timestamp": "2026-06-12T10:00:00Z",
                    "overall_assessment": (
                        "Realized facts are plausible, but several fields need direct "
                        "filing or company-source citations before promotion."
                    ),
                    "patches_reviewed": [
                        {
                            "patch_id": "patch_1",
                            "expectation_id": "expectation_1",
                            "issues": [
                                "realized_facts lack direct SEC citation",
                                {
                                    "field_path": "key_variables.current_state",
                                    "status": "needs_more_evidence",
                                    "recommendation": "Replace placeholder variable names.",
                                },
                            ],
                        }
                    ],
                },
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    structured = result.payload["structured"]
    assert structured["rationale"].startswith("Realized facts are plausible")
    assert structured["findings"][0]["field_path"] == "expectation_1"
    assert structured["findings"][0]["status"] == "needs_more_evidence"
    assert structured["findings"][1]["field_path"] == "key_variables.current_state"
    assert "ticker" not in structured
    assert "patches_reviewed" not in structured


def test_react_accepts_direct_expectation_field_review_payload_with_delegations_key() -> None:
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.C3_INDUSTRY_RESEARCH,
            "task_type": TaskType.REVIEW_EXPECTATION_FIELD,
            "required_output_schema": "ExpectationFieldReviewResult",
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "ticker": "ASTS",
                "task_type": "ExpectationFieldReviewResult",
                "findings": [
                    {
                        "field_path": "key_variables.current_state",
                        "status": "needs_more_evidence",
                        "rationale": "Industry validation source is missing.",
                        "evidence_refs": [],
                    }
                ],
                "objections": [],
                "delegations": [],
                "unknowns": ["Need third-party launch cadence source."],
                "rationale": "C3 completed the field review.",
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["structured"]["findings"][0]["field_path"] == (
        "key_variables.current_state"
    )
    assert result.payload["react_audit"]["entries"][0]["completion_reason"] == (
        "model returned direct structured payload"
    )


def test_react_normalizes_a2_search_payload_to_delegated_retrieval_result() -> None:
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.A2_FACT_CHECK,
            "task_type": TaskType.DELEGATED_RETRIEVAL,
            "required_output_schema": "DelegatedRetrievalResult",
            "permissions": AgentPermissions(
                readable_context_scopes=["delegations"],
                writable_targets=[],
                allowed_tools=["anysearch.search"],
            ),
            "input_context": {
                "delegation": {
                    "delegation_id": "delegation_test",
                    "question": "Verify whether Apple published quarterly results.",
                }
            },
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": False,
                "tool_calls": [
                    {
                        "tool_name": "anysearch.search",
                        "input": {
                            "query": "Apple investor relations quarterly results",
                            "max_results": 3,
                            "domain": "finance",
                        },
                    }
                ],
            },
            {
                "is_complete": True,
                "completion_reason": "verified",
                "final_payload": {
                    "conclusion": "Public company sources support the delegated fact.",
                    "verdict": "confirmed",
                    "basis": "Search found relevant investor-relations evidence.",
                    "queries": ["Apple investor relations quarterly results"],
                    "uncertainties": ["Exact filing detail was not extracted."],
                    "text": "This free-text alias should be normalized, not preserved.",
                },
            },
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    structured = result.payload["structured"]
    assert structured["answer"] == "Public company sources support the delegated fact."
    assert structured["claim_verdict"] == "supported"
    assert structured["delegation_id"] == "delegation_test"
    assert structured["can_complete_delegation"] is True
    assert structured["source_refs"]
    assert structured["query_log"] == ["Apple investor relations quarterly results"]
    assert "text" not in structured


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


class FailingToolClient(ToolClient):
    def call(self, request: ToolRequest) -> ToolResult:
        return ToolResult(
            tool_name=request.tool_name,
            status=ResultStatus.FAILED,
            output_summary="provider_error: upstream failed",
            error=ToolError(
                code="provider_error",
                message="upstream failed",
                retryable=False,
            ),
        )


class SlowToolClient(ToolClient):
    def __init__(self) -> None:
        self.started = threading.Event()

    def call(self, request: ToolRequest) -> ToolResult:
        self.started.set()
        threading.Event().wait(1.0)
        return ToolResult(
            tool_name=request.tool_name,
            status=ResultStatus.SUCCEEDED,
            output_summary="late success",
        )


def test_react_success_filters_failed_tool_calls_to_audit_only() -> None:
    registry = ToolRegistry()
    registry.register(
        "failing.lookup",
        FailingToolClient(),
        descriptor=ToolDescriptor(name="failing.lookup", description="Always fails."),
    )
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "permissions": base_task.permissions.model_copy(
                update={"allowed_tools": ["failing.lookup"]}
            )
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": False,
                "tool_calls": [{"tool_name": "failing.lookup", "input": {"query": "x"}}],
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

    tool_entries = [
        entry
        for entry in result.payload["react_audit"]["entries"]
        if entry.get("kind") == "tool_result"
    ]
    assert result.status is ResultStatus.SUCCEEDED
    assert result.tool_calls == []
    assert tool_entries[0]["tool_name"] == "failing.lookup"
    assert tool_entries[0]["status"] == ResultStatus.FAILED.value
    assert tool_entries[0]["error"]["code"] == "provider_error"


def test_react_tool_call_timeout_returns_failed_tool_result() -> None:
    client = SlowToolClient()
    registry = ToolRegistry()
    registry.register(
        "slow.lookup",
        client,
        descriptor=ToolDescriptor(
            name="slow.lookup",
            description="Slow lookup.",
            concurrent_safe=False,
        ),
    )
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "permissions": base_task.permissions.model_copy(
                update={"allowed_tools": ["slow.lookup"]}
            )
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": False,
                "tool_calls": [{"tool_name": "slow.lookup", "input": {"query": "x"}}],
            },
            {
                "is_complete": True,
                "completion_reason": "done",
                "final_payload": {"summary": "done despite timeout"},
            },
        ],
        tool_registry=registry,
        react_config=ReActHarnessConfig(tool_call_timeout_seconds=0.01),
    )

    result = runner.run(task)

    tool_entries = [
        entry
        for entry in result.payload["react_audit"]["entries"]
        if entry.get("kind") == "tool_result"
    ]
    assert result.status is ResultStatus.SUCCEEDED
    assert client.started.is_set()
    assert result.tool_calls == []
    assert tool_entries[0]["tool_name"] == "slow.lookup"
    assert tool_entries[0]["status"] == ResultStatus.FAILED.value
    assert tool_entries[0]["error"]["code"] == "tool_call_timeout"
    assert tool_entries[0]["error"]["retryable"] is True


def test_react_allows_multiple_same_tool_calls_in_one_loop() -> None:
    task = agent_task()
    runner = runner_with_sequence(
        [
            {
                "is_complete": False,
                "tool_calls": [
                    {"tool_name": "doxatlas.query", "input": {"query": f"AI demand {index}"}}
                    for index in range(4)
                ],
            },
            {
                "is_complete": True,
                "completion_reason": "enough",
                "final_payload": {"summary": "done"},
            },
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert len(result.tool_calls) == 4
    assert all(call.status is ResultStatus.SUCCEEDED for call in result.tool_calls)
    assert result.payload["react_audit"]["tool_counts"]["doxatlas.query"] == 4
    assert "doxatlas.query" not in result.payload["react_audit"]["consecutive_tool_loop_counts"]


def test_react_blocks_fourth_consecutive_loop_to_same_tool() -> None:
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
    assert all(call.status is ResultStatus.SUCCEEDED for call in result.tool_calls)
    failed_tool_entries = [
        entry
        for entry in result.payload["react_audit"]["entries"]
        if entry.get("kind") == "tool_result" and entry.get("status") == ResultStatus.FAILED.value
    ]
    assert failed_tool_entries
    assert "tool_call_limit_exceeded" in failed_tool_entries[-1]["output_summary"]
    assert result.payload["react_audit"]["tool_counts"]["doxatlas.query"] == 3
    assert "doxatlas.query" not in result.payload["react_audit"]["consecutive_tool_loop_counts"]


def test_react_resets_same_tool_consecutive_loop_limit_after_non_tool_loop() -> None:
    task = agent_task()
    tool_action = {
        "is_complete": False,
        "tool_calls": [{"tool_name": "doxatlas.query", "input": {"query": "AI demand"}}],
    }
    runner = runner_with_sequence(
        [
            tool_action,
            tool_action,
            {
                "is_complete": False,
                "reasoning_summary": "pause tool use",
                "task_ledger_updates": [{"item": "review evidence", "status": "todo"}],
            },
            tool_action,
            tool_action,
            {
                "is_complete": True,
                "completion_reason": "enough",
                "final_payload": {"summary": "done"},
            },
        ],
        react_config=ReActHarnessConfig(max_steps=6),
    )

    result = runner.run(task)

    failed_tool_entries = [
        entry
        for entry in result.payload["react_audit"]["entries"]
        if entry.get("kind") == "tool_result" and entry.get("status") == ResultStatus.FAILED.value
    ]
    assert result.status is ResultStatus.SUCCEEDED
    assert not failed_tool_entries
    assert result.payload["react_audit"]["tool_counts"]["doxatlas.query"] == 4


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


def test_react_enforces_global_tool_call_batch_budget() -> None:
    task = agent_task()
    runner = runner_with_sequence(
        [
            {
                "is_complete": False,
                "tool_calls": [{"tool_name": "doxatlas.query", "input": {"query": "AI demand"}}],
            },
            {
                "is_complete": False,
                "tool_calls": [{"tool_name": "doxatlas.query", "input": {"query": "AI demand 2"}}],
            },
        ],
        react_config=ReActHarnessConfig(max_steps=3, max_tool_call_batches=1),
    )

    result = runner.run(task)

    assert result.status is ResultStatus.FAILED
    assert result.error is not None
    assert result.error.code == "tool_call_batch_limit_exceeded"
    assert result.error.details["max_tool_call_batches"] == 1


def test_react_lazily_validates_runtime_o3_result_schema() -> None:
    task = runtime_o3_task()
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "trade intent",
                "final_payload": {
                    "primary_action": "trading_record",
                    "confidence": "high",
                    "reasoning": "Missing trade_intent should fail runtime schema.",
                },
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.FAILED
    assert result.error is not None
    assert result.error.code == "invalid_final_payload"
    assert result.error.details["required_output_schema"] == "O3Result"
    assert "O3Result" in result.error.message
    assert "trade_intent" in result.error.message


def test_react_normalizes_runtime_o3_object_side_effects() -> None:
    task = runtime_o3_task()
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "known events update",
                "final_payload": {
                    "primary_action": "ingest_queue",
                    "side_effects": [
                        {
                            "type": "known_events_update",
                            "payload": {
                                "event_id": "KE_MU_1",
                                "core_fact": "Micron memory demand improved.",
                                "duplicate_detection_keys": ["memory demand"],
                            },
                        }
                    ],
                    "evidence_refs": ["source_message_id: std_test"],
                    "reasoning": "Preserve for later research and update Known Events.",
                },
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["structured"]["side_effects"] == ["known_events_update"]
    assert result.payload["structured"]["known_events_patch"]["event_id"] == "KE_MU_1"
    assert result.payload["structured"]["evidence_refs"] == [{"ref": "source_message_id: std_test"}]


def test_react_runtime_o3_prompt_includes_output_contract() -> None:
    task = runtime_o3_task()
    client = RecordingModelClient(
        [
            {
                "is_complete": True,
                "completion_reason": "queue",
                "final_payload": {
                    "primary_action": "ingest_queue",
                    "reasoning": "Needs later research consumption.",
                },
            }
        ]
    )
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(client),
        tool_registry=default_tool_registry(),
        tool_mode="mock",
    )

    result = runner.run(task)

    prompt = json.loads(client.requests[0].messages[-1].content)
    contract = prompt["output_contract"]["O3Result"]
    contract_text = json.dumps(contract, ensure_ascii=False)
    assert result.status is ResultStatus.SUCCEEDED
    assert "primary_action" in contract["final_payload"]
    assert "trading_record requires trade_intent" in contract_text
    assert "one parallel tool-call batch" in contract_text
    assert "Never call a broker" in contract_text


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
    assert result.tool_calls == []
    failed_tool_entries = [
        entry
        for entry in result.payload["react_audit"]["entries"]
        if entry.get("kind") == "tool_result" and entry.get("status") == ResultStatus.FAILED.value
    ]
    assert failed_tool_entries[0]["tool_name"] == "market_data.snapshot"
    assert failed_tool_entries[0]["error"]["code"] == "tool_not_allowed"


def test_react_recovers_research_section_from_max_steps_with_tool_evidence() -> None:
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "agent_name": AgentName.C1_FUNDAMENTAL_RESEARCH,
            "task_type": TaskType.GENERATE_GLOBAL_RESEARCH,
            "required_output_schema": "ResearchSection",
            "input_context": {"required_section_key": "market_trace_report"},
            "run_metadata": base_task.run_metadata.model_copy(
                update={"workflow_node": "BuildGlobalResearch"}
            ),
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": False,
                "tool_calls": [{"tool_name": "doxatlas.query", "input": {"query": "price"}}],
            }
        ],
        react_config=ReActHarnessConfig(max_steps=1),
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert "保守恢复段落" in result.payload["structured"]["text"]
    assert result.payload["structured"]["evidence_refs"]
    assert any(
        entry["kind"] == "max_steps_recovered"
        for entry in result.payload["react_audit"]["entries"]
    )


def test_react_recovers_doxatlas_audit_from_max_steps_as_review_gap() -> None:
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "agent_name": AgentName.A1_DOXATLAS_AUDIT,
            "task_type": TaskType.REVIEW_EXPECTATION_FIELD,
            "required_output_schema": "DoxAtlasAuditResult",
            "input_context": {
                "review_scope": ["market_view", "realized_facts"],
                "pending_expectation_patches": [{"patch_id": "patch-1"}],
            },
            "run_metadata": base_task.run_metadata.model_copy(
                update={"workflow_node": "ReviewExpectationFields"}
            ),
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": False,
                "tool_calls": [
                    {"tool_name": "doxatlas.query", "input": {"query": "MU narrative"}}
                ],
            }
        ],
        react_config=ReActHarnessConfig(max_steps=1),
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    structured = result.payload["structured"]
    assert structured["verdict"] == "needs_revision"
    assert structured["revision_required"] is True
    assert structured["findings"][0]["status"] == "needs_more_evidence"
    assert "max_steps" in structured["unknowns"][0]
    assert structured["objections"] == []
    assert any(
        entry["kind"] == "max_steps_recovered"
        and entry["schema"] == "DoxAtlasAuditResult"
        for entry in result.payload["react_audit"]["entries"]
    )


def test_react_recovers_review_gap_when_final_step_has_no_progress() -> None:
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "agent_name": AgentName.C1_FUNDAMENTAL_RESEARCH,
            "task_type": TaskType.REVIEW_EXPECTATION_FIELD,
            "required_output_schema": "ExpectationFieldReviewResult",
            "input_context": {
                "review_scope": ["realized_facts", "event_monitoring_direction"],
                "pending_expectation_patches": [{"patch_id": "patch-1"}],
            },
            "run_metadata": base_task.run_metadata.model_copy(
                update={"workflow_node": "ReviewExpectationFields"}
            ),
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": False,
                "tool_calls": [
                    {"tool_name": "doxatlas.query", "input": {"query": "MU guidance"}}
                ],
            },
            {
                "plan_update": "已找到部分证据，但仍在整理字段级结论。",
                "task_ledger_updates": [
                    {"item": "整理 realized_facts 审查结论", "status": "blocked"}
                ],
            },
        ],
        react_config=ReActHarnessConfig(max_steps=2),
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    structured = result.payload["structured"]
    assert structured["findings"][0]["status"] == "needs_more_evidence"
    assert structured["objections"] == []
    assert "max_steps" in structured["unknowns"][0]
    assert any(
        entry["kind"] == "react_no_progress"
        for entry in result.payload["react_audit"]["entries"]
    )
    assert any(
        entry["kind"] == "max_steps_recovered"
        and entry["schema"] == "ExpectationFieldReviewResult"
        for entry in result.payload["react_audit"]["entries"]
    )


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
    assert result.tool_calls == []
    assert any(
        entry.get("kind") == "tool_result"
        and entry.get("tool_name") == "market_data.snapshot"
        and entry.get("status") == ResultStatus.FAILED.value
        for entry in result.payload["react_audit"]["entries"]
    )
    assert any(
        entry["kind"] == "required_tool_gap"
        for entry in result.payload["react_audit"]["entries"]
    )


class HugeOutputToolClient(ToolClient):
    def call(self, request: ToolRequest) -> ToolResult:
        return ToolResult(
            tool_name=request.tool_name,
            status=ResultStatus.SUCCEEDED,
            output={"blob": "x" * 200_000},
            output_summary="huge output returned",
        )


class HugeOhlcvToolClient(ToolClient):
    def call(self, request: ToolRequest) -> ToolResult:
        rows = [
            {
                "datetime": (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                "open": 100 + index,
                "high": 101 + index,
                "low": 99 + index,
                "close": 100 + index,
                "volume": 1_000_000 + index,
            }
            for index in range(500)
        ]
        return ToolResult(
            tool_name=request.tool_name,
            status=ResultStatus.SUCCEEDED,
            output={
                "provider": "twelvedata",
                "symbol": "NVDA",
                "interval": "1day",
                "ohlcv": rows,
            },
            output_summary="daily OHLCV returned",
        )


def test_react_compacts_large_tool_observation_before_next_model_request() -> None:
    client = RecordingModelClient(
        [
            {
                "is_complete": False,
                "tool_calls": [{"tool_name": "huge.context", "input": {"query": "large"}}],
            },
            {
                "is_complete": True,
                "completion_reason": "done",
                "final_payload": {"summary": "done"},
            },
        ]
    )
    registry = ToolRegistry()
    registry.register(
        "huge.context",
        HugeOutputToolClient(),
        descriptor=ToolDescriptor(name="huge.context", description="huge context"),
    )
    task = agent_task().model_copy(
        update={
            "permissions": AgentPermissions(
                readable_context_scopes=["global_research"],
                writable_targets=["expectation_unit"],
                allowed_tools=["huge.context"],
            )
        },
        deep=True,
    )
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(client),
        tool_registry=registry,
        react_config=ReActHarnessConfig(max_steps=2, compaction_token_threshold=10_000_000),
        tool_mode="mock",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    second_payload = json.loads(client.requests[1].messages[-1].content)
    tool_entries = [
        entry
        for entry in second_payload["recent_trajectory"]
        if entry.get("kind") == "tool_result"
    ]
    assert tool_entries[0]["output"]["compacted"] is True
    assert tool_entries[0]["output"]["original_chars"] > 200_000
    assert "x" * 50_000 not in client.requests[1].messages[-1].content
    audit_tool_entries = [
        entry
        for entry in result.payload["react_audit"]["entries"]
        if entry.get("kind") == "tool_result"
    ]
    assert audit_tool_entries[0]["output_compacted"] is True


def test_react_preserves_daily_ohlcv_market_snapshot_across_compaction() -> None:
    client = RecordingModelClient(
        [
            {
                "is_complete": False,
                "tool_calls": [
                    {"tool_name": "twelvedata.daily_ohlcv", "input": {"symbol": "NVDA"}}
                ],
            },
            {
                "is_complete": True,
                "completion_reason": "done",
                "final_payload": {"summary": "done"},
            },
        ]
    )
    registry = ToolRegistry()
    registry.register(
        "twelvedata.daily_ohlcv",
        HugeOhlcvToolClient(),
        descriptor=ToolDescriptor(
            name="twelvedata.daily_ohlcv",
            description="daily OHLCV",
        ),
    )
    task = agent_task().model_copy(
        update={
            "permissions": AgentPermissions(
                readable_context_scopes=["global_research"],
                writable_targets=["expectation_unit"],
                allowed_tools=["twelvedata.daily_ohlcv"],
            )
        },
        deep=True,
    )
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(client),
        tool_registry=registry,
        react_config=ReActHarnessConfig(max_steps=2, compaction_token_threshold=10_000_000),
        tool_mode="mock",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    second_payload = json.loads(client.requests[1].messages[-1].content)
    snapshot = second_payload["market_evidence_snapshot"]["daily_ohlcv"][0]
    assert snapshot["symbol"] == "NVDA"
    assert snapshot["start_close"] == 100
    assert snapshot["end_close"] == 599
    tool_entries = [
        entry
        for entry in second_payload["recent_trajectory"]
        if entry.get("kind") == "tool_result"
    ]
    assert tool_entries[0]["output"]["tool_aware_compaction"] == "daily_ohlcv"
    assert tool_entries[0]["output"]["market_evidence_snapshot"]["symbol"] == "NVDA"
    assert len(tool_entries[0]["output"]["ohlcv_sample_rows"]) < 20
    assert result.payload["market_evidence_snapshot"]["daily_ohlcv"][0]["symbol"] == "NVDA"
    assert (
        result.payload["react_audit"]["market_evidence_snapshot"]["daily_ohlcv"][0]["symbol"]
        == "NVDA"
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
    assert all("saw_peer=True" in (call.output_summary or "") for call in result.tool_calls)
