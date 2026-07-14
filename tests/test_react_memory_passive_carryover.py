from __future__ import annotations

import json
from pathlib import Path

import pytest

from doxagent.agents import ModelGatewayAgentRunner
from doxagent.agents.config import default_agent_registry
from doxagent.agents.runtime.memory import (
    ContextBudgetConfig,
    TaskMemoryRuntime,
    measure_context_budget,
    passive_observation_budget,
)
from doxagent.agents.runtime.react import ReActAgentHarness, ReActHarnessConfig
from doxagent.gateway import (
    MessageRole,
    MockModelClient,
    ModelAuditSummary,
    ModelGateway,
    ModelRequest,
    ModelResponse,
    ProviderName,
)
from doxagent.models import AgentName, AgentPermissions, ResultStatus, TaskType
from doxagent.prompts import PromptInjector
from doxagent.prompts.registry import default_prompt_registry
from doxagent.tools import (
    ToolClient,
    ToolDescriptor,
    ToolRegistry,
    ToolRequest,
    ToolResult,
)
from tests.fixtures.phase1_contracts import agent_task

ROOT = Path(__file__).resolve().parents[1]


def _record_observations(runtime: TaskMemoryRuntime, count: int = 4) -> list[str]:
    aliases: list[str] = []
    descriptor = ToolDescriptor(
        name="source.test",
        description="test source",
        observation_policy="inline",
        observation_adapter="json",
    )
    for index in range(count):
        call_id = runtime.begin_tool_call(
            step=1,
            tool_name="source.test",
            input_payload={"index": index},
        )
        runtime.record_tool_result(
            step=1,
            tool_call_id=call_id,
            result=ToolResult(
                tool_name="source.test",
                status=ResultStatus.SUCCEEDED,
                output={"index": index, "text": f"exact block {index} " + "x" * 300},
            ),
            input_payload={"index": index},
            warnings=[],
            descriptor=descriptor,
        )
        block = runtime.observations.block_store.blocks_for_call(call_id)[0]
        alias = runtime.observations.aliases.alias_for(block.block_id)
        assert alias is not None
        aliases.append(alias)
    return aliases


def test_context_budget_keeps_115k_micro_and_uses_192k_full_threshold() -> None:
    config = ContextBudgetConfig()

    at_micro = measure_context_budget(
        system_prompt="s" * 4,
        user_prompt="u" * ((115_200 - 1) * 4),
        active_context={},
        available_tools=[],
        config=config,
        mode="normal",
    )
    above_micro = measure_context_budget(
        system_prompt="s" * 4,
        user_prompt="u" * (115_200 * 4),
        active_context={},
        available_tools=[],
        config=config,
        mode="normal",
    )
    between_thresholds = measure_context_budget(
        system_prompt="s" * 4,
        user_prompt="u" * ((150_000 - 1) * 4),
        active_context={},
        available_tools=[],
        config=config,
        mode="micro",
    )
    at_full = measure_context_budget(
        system_prompt="s" * 4,
        user_prompt="u" * ((192_000 - 1) * 4),
        active_context={},
        available_tools=[],
        config=config,
        mode="micro",
    )

    assert at_micro["available_input_tokens"] == 192_000
    assert "reserved_output_tokens" not in at_micro
    assert "safety_reserve_tokens" not in at_micro
    assert at_micro["over_micro_threshold"] is False
    assert above_micro["projected_input_tokens"] == 115_201
    assert above_micro["over_micro_threshold"] is True
    assert above_micro["over_full_threshold"] is False
    assert between_thresholds["projected_input_tokens"] == 150_000
    assert between_thresholds["over_full_threshold"] is False
    assert between_thresholds["over_hard_budget"] is False
    assert at_micro["micro_threshold_tokens"] == 115_200
    assert at_full["projected_input_tokens"] == 192_000
    assert at_full["full_compaction_threshold_tokens"] == 192_000
    assert at_full["over_full_threshold"] is True
    assert at_full["over_hard_budget"] is False


def test_successful_full_compaction_consumes_fresh_and_blocks_only_next_loop() -> None:
    runtime = TaskMemoryRuntime(agent_task())
    _record_observations(runtime, count=1)
    assert runtime.active_context()["fresh_observations"]

    runtime.apply_full_compaction(
        {"compaction_reasoning_summary": "已消化本轮 Fresh Observation。"},
        step=2,
        before={"projected_input_tokens": 192_000},
    )

    assert runtime.active_context()["fresh_observations"] == []
    assert runtime.full_compaction_blocked_for_step(3) is True
    assert runtime.full_compaction_blocked_for_step(4) is False
    consumed = [
        event
        for event in runtime.event_log.audit()
        if event["kind"] == "fresh_observations_consumed"
    ]
    assert consumed[-1]["source"] == "full_compaction"


def test_failed_full_compaction_keeps_fresh_but_still_blocks_next_loop() -> None:
    runtime = TaskMemoryRuntime(agent_task())
    _record_observations(runtime, count=1)

    runtime.record_full_compaction_failure("provider unavailable", step=2)

    assert runtime.active_context()["fresh_observations"]
    assert runtime.full_compaction_blocked_for_step(3) is True
    assert runtime.audit()["full_compaction"] == {
        "attempts": 1,
        "last_attempt_step": 2,
    }


def test_harness_drops_compacted_fresh_and_suppresses_next_loop_full() -> None:
    class HugeNarrativeClient(ToolClient):
        def call(self, request: ToolRequest) -> ToolResult:
            return ToolResult(
                tool_name=request.tool_name,
                status=ResultStatus.SUCCEEDED,
                output={
                    "data": {
                        "narratives": [
                            {
                                "narrative_id": "N1",
                                "summary": "x" * 900_000,
                            }
                        ]
                    },
                    "provider": "test",
                },
                output_summary="huge narrative",
            )

    class RecordingClient:
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []
            self.responses = [
                {
                    "is_complete": False,
                    "tool_calls": [
                        {"tool_name": "doxa_get_narrative_report", "input": {}}
                    ],
                },
                {
                    "compaction_reasoning_summary": "已消化 Fresh Observation。",
                    "plan_update": ["继续验证"],
                },
                {
                    "is_complete": False,
                    "tool_calls": [
                        {"tool_name": "doxa_get_narrative_report", "input": {}}
                    ],
                },
                {
                    "is_complete": True,
                    "completion_reason": "done",
                    "final_payload": {"summary": "done"},
                },
            ]

        async def complete(self, request: ModelRequest) -> ModelResponse:
            self.requests.append(request)
            return ModelResponse(
                structured=self.responses.pop(0),
                audit=ModelAuditSummary(
                    provider=ProviderName.MOCK,
                    model=request.model,
                    latency_seconds=0,
                    metadata=request.metadata,
                ),
            )

    registry = ToolRegistry()
    registry.register(
        "doxa_get_narrative_report",
        HugeNarrativeClient(),
        descriptor=ToolDescriptor(
            name="doxa_get_narrative_report",
            description="large narrative",
            observation_policy="indexed",
            observation_adapter="doxatlas",
        ),
    )
    client = RecordingClient()
    task = agent_task().model_copy(
        update={
            "permissions": AgentPermissions(
                readable_context_scopes=["global_research"],
                writable_targets=["expectation_unit"],
                allowed_tools=["doxa_get_narrative_report"],
            )
        },
        deep=True,
    )
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(client),
        tool_registry=registry,
        react_config=ReActHarnessConfig(
            max_steps=3,
            max_full_compaction_retries=0,
        ),
        tool_mode="mock",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    compaction_requests = [
        request
        for request in client.requests
        if request.metadata.get("react_compaction") == "true"
    ]
    assert len(compaction_requests) == 1
    second_loop_payload = json.loads(client.requests[2].messages[-1].content)
    third_loop_payload = json.loads(client.requests[3].messages[-1].content)
    assert second_loop_payload["task_memory"]["fresh_observations"] == []
    assert third_loop_payload["task_memory"]["fresh_observations"]
    event_kinds = [
        event["kind"] for event in result.payload["react_audit"]["event_log"]
    ]
    assert "full_compaction_suppressed" in event_kinds


@pytest.mark.parametrize(
    ("other_tokens", "expected"),
    [(60_000, 36_000), (80_000, 16_000), (96_000, 0), (120_000, 0), (0, 64_000)],
)
def test_passive_budget_formula(other_tokens: int, expected: int) -> None:
    assert passive_observation_budget(other_tokens) == expected


def test_passive_carryover_prioritizes_action_then_reasoning_and_excludes_obtained() -> None:
    runtime = TaskMemoryRuntime(agent_task())
    o1, o2, o3, o4 = _record_observations(runtime)

    runtime.record_action(
        2,
        {
            "reasoning_summary": f"先参考【cite:{o1}】，再看【cite:{o2}】，最后回到【cite:{o1}】。",
            "final_payload": {"text": f"正文仍使用【cite:{o2}】后以【cite:{o1}】收束。"},
            "retain_observations": [
                {"alias": o4, "note": "长期材料", "reason": "显式 obtain"}
            ],
        },
        reasoning_content=(
            f"provider reasoning 先用【cite:{o3}】，再用【cite:{o4}】，"
            f"最后再次使用【cite:{o3}】。"
        ),
    )

    assert runtime.passive_candidate_aliases == [o1, o2, o3]
    runtime.set_passive_budget_tokens(64_000)
    context = runtime.active_context()
    assert [item["alias"] for item in context["passive_observation_carryover"]] == [
        o1,
        o2,
        o3,
    ]
    assert context["retained_observations"][0]["alias"] == o4


def test_passive_carryover_lasts_one_loop_unless_referenced_again() -> None:
    runtime = TaskMemoryRuntime(agent_task())
    o1, o2, *_ = _record_observations(runtime)
    runtime.record_action(2, {"reasoning_summary": f"使用【cite:{o1}】。"})
    assert runtime.passive_candidate_aliases == [o1]

    runtime.record_action(3, {"reasoning_summary": "本轮没有引用任何 Observation。"})
    assert runtime.passive_candidate_aliases == []

    runtime.record_action(4, {"reasoning_summary": f"再次使用【cite:{o2}】。"})
    assert runtime.passive_candidate_aliases == [o2]
    runtime.record_action(
        5,
        {
            "reasoning_summary": f"获得并继续使用【cite:{o2}】。",
            "retain_observations": [
                {"alias": o2, "note": "正式保留", "reason": "后续写作需要"}
            ],
        },
    )
    assert runtime.passive_candidate_aliases == []
    runtime.set_passive_budget_tokens(0)
    context = runtime.active_context()
    assert context["passive_observation_carryover"] == []
    assert context["retained_observations"][0]["alias"] == o2
    assert "original_block" in context["retained_observations"][0]


def test_passive_budget_loads_only_complete_blocks_without_truncation() -> None:
    runtime = TaskMemoryRuntime(agent_task())
    o1, o2, *_ = _record_observations(runtime)
    runtime.record_action(
        2,
        {"reasoning_summary": f"先【cite:{o2}】后【cite:{o1}】。"},
    )

    runtime.set_passive_budget_tokens(1)
    assert runtime.active_context()["passive_observation_carryover"] == []

    runtime.set_passive_budget_tokens(64_000)
    loaded = runtime.active_context()["passive_observation_carryover"]
    assert [item["alias"] for item in loaded] == [o1, o2]
    for item in loaded:
        block = runtime.observations.read(item["alias"])[0]
        assert item["content"] == block.content


def _task_for(
    agent_name: AgentName,
    task_type: TaskType,
    workflow_node: str,
):
    base = agent_task()
    return base.model_copy(
        update={
            "agent_name": agent_name,
            "task_type": task_type,
            "run_metadata": base.run_metadata.model_copy(
                update={"workflow_node": workflow_node}
            ),
        },
        deep=True,
    )


def test_memory_prompt_layering_is_registry_driven() -> None:
    registry = default_agent_registry()
    injector = PromptInjector()
    for agent_name in registry.names():
        definition = registry.get(agent_name)
        if definition.runtime.execution_mode != "react":
            continue
        task = _task_for(agent_name, definition.task_types[0], agent_name.value)
        ids = injector.inject(task, definition).prompt_bundle.prompt_block_ids
        assert "workflow.memory" in ids

    research_task = _task_for(
        AgentName.C1_FUNDAMENTAL_RESEARCH,
        TaskType.GENERATE_GLOBAL_RESEARCH,
        "BuildGlobalResearch",
    )
    review_task = _task_for(
        AgentName.C1_FUNDAMENTAL_RESEARCH,
        TaskType.REVIEW_EXPECTATION_FIELD,
        "ReviewExpectationFields",
    )
    repair_task = _task_for(
        AgentName.O1_EXPECTATION_OWNER,
        TaskType.REVIEW_EXPECTATION_FIELD,
        "ResolveObjectionsAndDelegations",
    )
    resolve_task = _task_for(
        AgentName.O2_MONITORING_CONFIG,
        TaskType.RESOLVE_MONITORING_CONFIG,
        "ResolveMonitoringConfig",
    )
    o3_task = _task_for(
        AgentName.O3_TRADING_STRATEGY,
        TaskType.RUNTIME_O3_JUDGMENT,
        "O3",
    )

    research_ids = injector.inject(
        research_task, registry.get(research_task.agent_name)
    ).prompt_bundle.prompt_block_ids
    assert "workflow.memory" in research_ids
    assert "workflow.research_memory" in research_ids
    assert "workflow.observation-annotations" in research_ids

    for task in (review_task, repair_task, resolve_task, o3_task):
        ids = injector.inject(task, registry.get(task.agent_name)).prompt_bundle.prompt_block_ids
        assert "workflow.memory" in ids
        assert "workflow.research_memory" not in ids


def test_full_compaction_uses_manual_registry_prompt_and_preserves_user_payload() -> None:
    registry = default_prompt_registry()
    full_compaction = registry.get("workflow.full_compaction")
    automatically_selected = registry.find_prompt_blocks(
        AgentName.C1_FUNDAMENTAL_RESEARCH,
        TaskType.GENERATE_GLOBAL_RESEARCH,
        "BuildGlobalResearch",
    )
    assert full_compaction.manual_only is True
    assert "workflow.full_compaction" not in {
        item.resource_id for item in automatically_selected
    }

    harness = ReActAgentHarness(
        model_gateway=ModelGateway(MockModelClient(structured={})),
        tool_registry=None,
        provider=ProviderName.MOCK,
        model="mock-model",
        tool_mode="mock",
        prompt_registry=registry,
    )
    task = agent_task()
    messages = harness._full_compaction_messages(
        task=task,
        micro_report={"projected_input_tokens": 128_000},
        micro_context={"fresh_observations": [{"alias": "O1"}]},
    )

    assert len(messages) == 2
    assert messages[0].role is MessageRole.SYSTEM
    assert messages[0].content == full_compaction.body
    assert messages[1].role is MessageRole.USER
    user_payload = json.loads(messages[1].content)
    assert user_payload["task"] == {
        "task_id": task.task_id,
        "task_type": task.task_type.value,
        "required_output_schema": task.required_output_schema,
    }
    assert user_payload["full_compaction_reason"] == {
        "projected_input_tokens": 128_000
    }
    assert user_payload["active_context"] == {
        "fresh_observations": [{"alias": "O1"}]
    }
    assert user_payload["protected_fresh_observation_count"] == 1
    assert "maintenance_action_schema" in user_payload
    assert "language_rules" in user_payload


def test_evidence_ref_usage_file_is_the_only_prompt_source_for_citation_syntax() -> None:
    evidence = ROOT / "prompts/workflows/evidence_ref_usage.md"
    memory = ROOT / "prompts/workflows/memory.md"
    prompt_sources = [
        *ROOT.joinpath("prompts").rglob("*.md"),
        *ROOT.joinpath("src/doxagent/prompts").rglob("*.py"),
        ROOT / "src/doxagent/agents/runtime/react.py",
    ]
    offenders = [
        path
        for path in prompt_sources
        if path not in {evidence, memory} and "【cite:" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
    assert "【cite:O1】" in evidence.read_text(encoding="utf-8")
    memory_text = memory.read_text(encoding="utf-8")
    assert '"synthesis_update":["ADD：结论【cite:O1】"]' in memory_text
    assert "Citation 的含义和使用规则仅以独立加载的" in memory_text


def test_passive_audit_never_persists_block_payloads() -> None:
    runtime = TaskMemoryRuntime(agent_task())
    o1, *_ = _record_observations(runtime)
    runtime.record_action(2, {"reasoning_summary": f"使用【cite:{o1}】。"})
    runtime.set_passive_budget_tokens(64_000)

    persisted = runtime.persisted_audit()
    rendered = json.dumps(persisted, ensure_ascii=False)
    assert persisted["passive_observation_carryover"]["candidate_count"] == 1
    assert "exact block" not in rendered
