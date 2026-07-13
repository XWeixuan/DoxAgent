from __future__ import annotations

import json

import pytest

from doxagent.agents.runtime.memory import ObservationService, TaskMemoryRuntime
from doxagent.agents.runtime.memory.observations import ObservationPolicyRegistry
from doxagent.agents.runtime.memory.protocol import memory_action_schema
from doxagent.models import ResultStatus
from doxagent.tools import ToolDescriptor, ToolResult
from doxagent.workflows.initialization import BlackboardInitializationWorkflow
from doxagent.workflows.schema import WorkflowNode
from tests.fixtures.phase1_contracts import agent_task


def _result(tool_name: str, output: dict) -> ToolResult:
    return ToolResult(
        tool_name=tool_name,
        status=ResultStatus.SUCCEEDED,
        output=output,
        output_summary="test result",
    )


def test_action_example_uses_standard_read_observation_and_alias_note_only() -> None:
    schema = memory_action_schema()
    assert schema == {
        "synthesis_update": ["ADD：结论【cite:O1】"],
        "research_update": ["OPEN：待研究问题"],
        "retain_observations": [{"alias": "O1", "note": "材料内容"}],
        "tool_calls": [
            {
                "tool_name": "read_observation",
                "input": {
                    "alias": "O1",
                    "include_parent": False,
                    "include_children": False,
                },
            }
        ],
    }
    assert "read_observation" not in schema


def test_retain_without_reason_and_legacy_extra_reason_are_both_accepted() -> None:
    runtime = TaskMemoryRuntime(agent_task())
    descriptor = ToolDescriptor(
        name="source.test",
        description="test source",
        observation_policy="inline",
        observation_adapter="json",
    )
    call_id = runtime.begin_tool_call(step=1, tool_name="source.test", input_payload={})
    runtime.record_tool_result(
        step=1,
        tool_call_id=call_id,
        result=_result("source.test", {"first": "a", "second": "b"}),
        input_payload={},
        warnings=[],
        descriptor=descriptor,
    )
    aliases = [
        runtime.observations.aliases.alias_for(block.block_id)
        for block in runtime.observations.block_store.blocks_for_call(call_id)
    ]
    aliases = [alias for alias in aliases if alias]
    runtime.record_action(
        2,
        {
            "retain_observations": [
                {"alias": aliases[0], "note": "first"},
                {"alias": aliases[1], "note": "second", "reason": "legacy ignored"},
            ]
        },
    )
    audit = runtime.memory.audit()["retained_observations"]
    assert len(audit) == 2
    assert all("reason" not in item for item in audit)
    assert not any("缺少" in warning for warning in runtime.warnings)


def test_standard_and_batch_read_observation_load_exact_aliases() -> None:
    runtime = TaskMemoryRuntime(agent_task())
    descriptor = ToolDescriptor(
        name="source.test",
        description="test source",
        observation_policy="inline",
        observation_adapter="json",
    )
    call_id = runtime.begin_tool_call(step=1, tool_name="source.test", input_payload={})
    runtime.record_tool_result(
        step=1,
        tool_call_id=call_id,
        result=_result("source.test", {"first": "a", "second": "b"}),
        input_payload={},
        warnings=[],
        descriptor=descriptor,
    )
    aliases = [
        runtime.observations.aliases.alias_for(block.block_id)
        for block in runtime.observations.block_store.blocks_for_call(call_id)
        if block.block_type != "outline"
    ]
    assert all(aliases)
    assert runtime.read_observation(
        step=2,
        input_payload={
            "alias": aliases[0],
            "include_parent": False,
            "include_children": False,
        },
    )
    for alias in aliases[1:]:
        assert runtime.read_observation(
            step=2,
            input_payload={
                "alias": alias,
                "include_parent": False,
                "include_children": False,
            },
        )
    assert runtime.fresh_read_refs == aliases
    runtime.record_action(
        3,
        {
            "tool_calls": [
                {
                    "tool_name": "read_observation",
                    "input": {
                        "alias": aliases[0],
                        "include_parent": False,
                        "include_children": False,
                    },
                }
            ],
            "reasoning_summary": "继续读取材料，但本句未引用 Observation。",
        },
    )
    assert runtime.passive_candidate_aliases == []


def test_large_payload_is_chunked_full_and_reconstructable_without_auto_indexing() -> None:
    output = {
        "company": {"ticker": "INTC", "description": "x" * 5_000},
        "rows": [
            {"date": f"2026-07-{day:02d}", "value": day, "note": "n" * 120}
            for day in range(1, 13)
        ],
    }
    assert ObservationPolicyRegistry().resolve(None, output) == "inline"
    service = ObservationService()
    index = service.ingest(
        tool_call_id="tc_full",
        step=1,
        input_payload={},
        result=_result("provider.full", output),
        declared_policy="indexed",
        adapter="auto",
    )
    fresh = service.fresh_view("tc_full")
    assert fresh is not None
    assert index.delivery_mode == "full"
    assert len(fresh["loaded_blocks"]) == len(index.selected_refs)
    assert index.selected_refs == tuple(
        block.ref
        for block in service.block_store.blocks_for_call("tc_full")
        if block.block_type != "outline"
    )
    assert service.reconstruct_output("tc_full") == output
    assert max(
        len(json.dumps(block.content, ensure_ascii=False, separators=(",", ":")))
        for block in service.block_store.blocks_for_call("tc_full")
        if block.block_type != "outline"
    ) <= 1_200


def test_doxatlas_unwraps_data_and_preserves_nepmsd_semantics_and_payload() -> None:
    output = {
        "status": "ok",
        "data": {
            "narratives": [
                {
                    "narrative_code": "N1",
                    "events": [
                        {
                            "event_code": "E1",
                            "propositions": [
                                {"proposition_code": "P1", "text": "fact"}
                            ],
                        }
                    ],
                }
            ],
            "media_results": [{"media_code": "M1", "text": "media"}],
            "social_results": [{"social_code": "S1", "text": "social"}],
            "sources": [{"source_code": "D1", "text": "source"}],
        },
    }
    service = ObservationService()
    index = service.ingest(
        tool_call_id="tc_doxatlas",
        step=1,
        input_payload={},
        result=_result("doxa_get_narrative_report", output),
        declared_policy="inline",
        adapter="doxatlas",
    )
    semantics = {
        block.context_envelope.get("doxatlas_semantic")
        for block in service.block_store.blocks_for_call("tc_doxatlas")
    }
    assert {"N", "E", "P", "M", "S", "D"} <= semantics
    assert index.delivery_mode == "full"
    assert service.reconstruct_output("tc_doxatlas") == output


def test_sec_and_over_128k_results_are_explicitly_paged() -> None:
    service = ObservationService()
    sec = service.ingest(
        tool_call_id="tc_sec",
        step=1,
        input_payload={},
        result=_result("sec.company_facts_and_filings", {"facts": {"Revenue": [1, 2]}}),
        declared_policy="indexed",
        adapter="auto",
    )
    assert sec.delivery_mode == "indexed_sec"

    oversized_output = {"text": "x" * (128_000 * 4 + 1)}
    oversized = service.ingest(
        tool_call_id="tc_huge",
        step=1,
        input_payload={},
        result=_result("provider.huge", oversized_output),
        declared_policy="inline",
        adapter="auto",
    )
    view = service.fresh_view("tc_huge")
    assert view is not None
    assert oversized.delivery_mode == "paged_oversized"
    assert len(oversized.selected_refs) < len(oversized.block_refs)
    assert "explicitly paged" in view["read_instruction"]
    assert service.reconstruct_output("tc_huge") == oversized_output


@pytest.mark.parametrize(
    "node",
    [
        WorkflowNode.BUILD_GLOBAL_RESEARCH,
        WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT,
        WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION,
        WorkflowNode.RESOLVE_EXPECTATION_CONSTRUCTION,
        WorkflowNode.GENERATE_EXPECTATION_DETAILS,
        WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
    ],
)
def test_document12_default_five_step_nodes_are_raised_to_ten(node: WorkflowNode) -> None:
    workflow = object.__new__(BlackboardInitializationWorkflow)
    assert workflow._with_document12_react_budget({}, node) == {
        "react_runtime_budget": {"max_steps": 10}
    }
    assert workflow._with_document12_react_budget(
        {"react_runtime_budget": {"max_steps": 5, "max_tool_call_batches": 1}},
        node,
    )["react_runtime_budget"] == {"max_steps": 10, "max_tool_call_batches": 1}


def test_review_document3_other_and_nonfive_budgets_are_unchanged() -> None:
    workflow = object.__new__(BlackboardInitializationWorkflow)
    for node in (
        WorkflowNode.REVIEW_EXPECTATION_CONSTRUCTION,
        WorkflowNode.REVIEW_EXPECTATION_FIELDS,
        WorkflowNode.GENERATE_KNOWN_EVENTS,
        WorkflowNode.GENERATE_MONITORING_CONFIG,
        WorkflowNode.GENERATE_MONITORING_POLICY,
    ):
        assert workflow._with_document12_react_budget({}, node) == {}
    assert workflow._with_document12_react_budget(
        {"react_runtime_budget": {"max_steps": 1}},
        WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
    )["react_runtime_budget"]["max_steps"] == 1
