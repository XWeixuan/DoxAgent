import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

pytest.skip("retired pre-alias Observation ref suite", allow_module_level=True)

from doxagent.agents.config import default_agent_registry
from doxagent.agents.runtime.memory import (
    ContextBudgetConfig,
    ObservationService,
    TaskEventLog,
    TaskMemoryRuntime,
    measure_context_budget,
)
from doxagent.blackboard.postgres_repository import _AGENT_CONTEXT_PAYLOAD_SQL
from doxagent.models import AgentName, ResultStatus, TaskType
from doxagent.prompts import PromptInjector
from doxagent.tools import ToolDescriptor, ToolResult
from tests.fixtures.phase1_contracts import agent_task

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "react_memory" / "tool_result_profiles.json"


def _profiles() -> dict[str, dict[str, Any]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _result(tool_name: str, output: dict[str, Any]) -> ToolResult:
    result = ToolResult(
        tool_name=tool_name,
        status=ResultStatus.SUCCEEDED,
        output=deepcopy(output),
        output_summary=f"{tool_name} returned",
    )
    return result.model_copy(
        update={"evidence_refs": [result.to_evidence_ref(source_id=f"source:{tool_name}")]},
        deep=True,
    )


def _profile_descriptor(tool_name: str) -> ToolDescriptor:
    profile = _profiles()[tool_name]
    return ToolDescriptor(
        name=tool_name,
        description=f"{tool_name} profile",
        observation_policy=profile["policy"],
        observation_adapter=profile["adapter"],
    )


def test_task_event_log_is_append_only_and_does_not_expose_mutable_payloads() -> None:
    log = TaskEventLog()
    source = {"nested": {"value": 1}}

    log.append("tool_request", source, step=1)
    source["nested"]["value"] = 99
    exposed = log.events()[0]
    exposed.payload["nested"]["value"] = 42

    assert log.audit()[0]["nested"]["value"] == 1
    assert log.audit()[0]["sequence"] == 1
    assert len(log) == 1


@pytest.mark.parametrize(
    ("tool_name", "expected_locator"),
    [
        ("tavily.search", "/results/0"),
        ("alpha.financial_statements", "/quarterlyReports/rows/0-1"),
        ("twelvedata.daily_ohlcv", "rows/2026-07-08..2026-07-09"),
        (
            "doxa_get_narrative_report",
            "narrative/N1/event/E1/proposition/P1",
        ),
    ],
)
def test_profiled_tool_results_produce_stable_exact_observation_refs(
    tool_name: str,
    expected_locator: str,
) -> None:
    profile = _profiles()[tool_name]
    service = ObservationService()

    index = service.ingest(
        tool_call_id="tc1",
        step=1,
        input_payload={"ticker": "SAMPLE"},
        result=_result(tool_name, profile["output"]),
        declared_policy=profile["policy"],
        adapter=profile["adapter"],
    )

    expected_ref = f"obs_tc1::{expected_locator}"
    assert expected_ref in index.block_refs
    block = service.block_store.get_by_ref(expected_ref)
    assert block is not None
    assert block.agent_view()["content"] == block.content
    assert block.context_envelope["evidence_ref_ids"]

    second_service = ObservationService()
    second_index = second_service.ingest(
        tool_call_id="tc1",
        step=1,
        input_payload={"ticker": "SAMPLE"},
        result=_result(tool_name, profile["output"]),
        declared_policy=profile["policy"],
        adapter=profile["adapter"],
    )
    second_block = second_service.block_store.get_by_ref(expected_ref)
    assert second_block is not None
    assert second_index.block_refs == index.block_refs
    assert second_block.block_id == block.block_id
    assert second_block.content_hash == block.content_hash


def test_recomputable_table_block_repeats_columns_period_unit_and_currency() -> None:
    profile = _profiles()["alpha.financial_statements"]
    service = ObservationService()
    service.ingest(
        tool_call_id="tc7",
        step=2,
        input_payload={},
        result=_result("alpha.financial_statements", profile["output"]),
        declared_policy="recomputable",
        adapter="table",
    )

    block = service.block_store.get_by_ref(
        "obs_tc7::/quarterlyReports/rows/0-1"
    )

    assert block is not None
    assert block.content["columns"] == [
        "fiscalDateEnding",
        "grossProfit",
        "totalRevenue",
    ]
    assert block.context_envelope["currency"] == "USD"


def test_doxatlas_scoped_result_preserves_native_hierarchy_codes() -> None:
    service = ObservationService()
    output = {
        "run_id": "run_sample",
        "narrative_code": "N2",
        "event_code": "E3",
        "propositions": [
            {"proposition_code": "P4", "text": "Desensitized proposition."}
        ],
    }

    index = service.ingest(
        tool_call_id="tc9",
        step=1,
        input_payload={},
        result=_result("doxa_query_propositions", output),
        declared_policy="indexed",
        adapter="doxatlas",
    )

    ref = "obs_tc9::narrative/N2/event/E3/proposition/P4"
    assert ref in index.block_refs
    block = service.block_store.get_by_ref(ref)
    assert block is not None
    assert block.content == output["propositions"][0]


def test_memory_updates_are_incremental_soft_validated_and_materialized() -> None:
    profile = _profiles()["tavily.search"]
    runtime = TaskMemoryRuntime(agent_task())
    call_id = runtime.begin_tool_call(
        step=1,
        tool_name="tavily.search",
        input_payload={"query": "sample"},
    )
    runtime.record_tool_result(
        step=1,
        tool_call_id=call_id,
        result=_result("tavily.search", profile["output"]),
        input_payload={"query": "sample"},
        warnings=[],
        descriptor=_profile_descriptor("tavily.search"),
    )
    ref = "obs_tc1::/results/0"

    runtime.record_action(
        2,
        {
            "reasoning_summary": "新材料形成了可复用判断。",
            "plan_update": ["验证剩余缺口"],
            "synthesis_update": [f"ADD：需求判断得到来源支持 {ref}"],
            "research_update": ["OPEN：是否存在反例？"],
            "retain_observations": [
                {"ref": ref, "note": "来源 A", "reason": "支撑 S1"},
                {"ref": "obs_tc999::/missing", "note": "bad", "reason": "bad"},
            ],
        },
    )

    view = runtime.active_context()
    assert view["working_synthesis"][0]["id"] == "S1"
    assert view["research_agenda"][0]["id"] == "Q1"
    assert view["current_plan"] == ["验证剩余缺口"]
    assert view["recent_reasoning_summary"] == [
        {"step": 2, "content": "新材料形成了可复用判断。"}
    ]
    retained = view["retained_observations"][0]
    assert retained["ref"] == ref
    assert retained["original_block"]["content"]["title"] == "Example result A"
    assert any("拒绝无效 observation ref" in warning for warning in runtime.warnings)

    runtime.record_action(
        3,
        {
            "synthesis_update": [f"REVISE S1：判断经反例检查后仍成立 {ref}"],
            "research_update": ["RESOLVE Q1"],
            "plan_update": ["完成输出"],
            "reasoning_summary": "反例检查完成。",
        },
    )
    assert runtime.memory.audit()["working_synthesis"][0]["id"] == "S1"
    assert runtime.memory.audit()["research_agenda"] == []


def test_fresh_observation_is_shown_once_unless_retained_or_read_again() -> None:
    profile = _profiles()["tavily.search"]
    runtime = TaskMemoryRuntime(agent_task())
    call_id = runtime.begin_tool_call(
        step=1,
        tool_name="tavily.search",
        input_payload={"query": "sample"},
    )
    runtime.record_tool_result(
        step=1,
        tool_call_id=call_id,
        result=_result("tavily.search", profile["output"]),
        input_payload={"query": "sample"},
        warnings=[],
        descriptor=_profile_descriptor("tavily.search"),
    )

    assert runtime.active_context()["fresh_observations"]
    runtime.record_action(2, {"reasoning_summary": "已处理。"})
    assert runtime.active_context()["fresh_observations"] == []

    assert runtime.read_observation(
        step=2,
        input_payload={"ref": "obs_tc1::/results/1"},
    )
    fresh = runtime.active_context()["fresh_observations"]
    assert fresh[0]["loaded_blocks"][0]["content"]["title"] == "Example result B"


def test_full_compaction_changes_only_materialized_memory_and_load_state() -> None:
    profile = _profiles()["tavily.search"]
    runtime = TaskMemoryRuntime(agent_task())
    call_id = runtime.begin_tool_call(
        step=1,
        tool_name="tavily.search",
        input_payload={"query": "sample"},
    )
    runtime.record_tool_result(
        step=1,
        tool_call_id=call_id,
        result=_result("tavily.search", profile["output"]),
        input_payload={"query": "sample"},
        warnings=[],
        descriptor=_profile_descriptor("tavily.search"),
    )
    ref = "obs_tc1::/results/0"
    runtime.record_action(
        2,
        {
            "synthesis_update": [f"ADD：判断 {ref}"],
            "retain_observations": [
                {"ref": ref, "note": "source", "reason": "final evidence"}
            ],
        },
    )
    raw_before = deepcopy(runtime.observations.raw_store.audit())
    block_before = runtime.observations.block_store.get_by_ref(ref)
    assert block_before is not None

    runtime.apply_full_compaction(
        {
            "compaction_reasoning_summary": "卸载当前无需持续显示的原文。",
            "retained_observation_update": [
                {"ref": ref, "action": "INDEX_ONLY", "reason": "可按需重载"}
            ],
        },
        before={"projected_input_tokens": 1000},
    )

    assert runtime.observations.raw_store.audit() == raw_before
    block_after = runtime.observations.block_store.get_by_ref(ref)
    assert block_after == block_before
    assert runtime.memory.retained[ref].load_state == "index_only"
    assert "original_block" not in runtime.active_context()["retained_observations"][0]
    assert runtime.reload_final_observations() == [ref]
    assert runtime.memory.retained[ref].load_state == "loaded"


def test_context_budget_uses_actual_prompt_without_output_or_safety_reserves() -> None:
    config = ContextBudgetConfig(
        model_context_window=1_000,
        micro_maintenance_ratio=0.75,
        full_compaction_ratio=0.85,
    )

    report = measure_context_budget(
        system_prompt="s" * 400,
        user_prompt="u" * 2_400,
        active_context={"memory": "m" * 1_000},
        available_tools=[{"name": "tool"}],
        config=config,
        mode="normal",
    )

    assert report["available_input_tokens"] == 1_000
    assert report["projected_input_tokens"] == 700
    assert report["over_micro_threshold"] is False
    assert report["over_full_threshold"] is False
    assert report["over_hard_budget"] is False


def test_memory_prompt_is_injected_only_for_react_agents() -> None:
    registry = default_agent_registry()
    injector = PromptInjector()
    react_task = agent_task()
    single_shot_task = react_task.model_copy(
        update={
            "agent_name": AgentName.W1_RUNTIME_NOVELTY,
            "task_type": TaskType.RUNTIME_W1_NOVELTY,
        },
        deep=True,
    )

    react_injected = injector.inject(react_task, registry.get(react_task.agent_name))
    single_shot_injected = injector.inject(
        single_shot_task,
        registry.get(single_shot_task.agent_name),
    )

    assert react_injected.prompt_bundle is not None
    assert "workflow.memory" in react_injected.prompt_bundle.prompt_block_ids
    assert single_shot_injected.prompt_bundle is not None
    assert "workflow.memory" not in single_shot_injected.prompt_bundle.prompt_block_ids


def test_large_nested_provider_payload_is_recursively_chunked_without_giant_blocks() -> None:
    concepts = {
        f"Metric{index}": {
            "label": f"Metric {index}",
            "units": {
                "USD": [
                    {
                        "fy": 2025,
                        "fp": "FY",
                        "val": index * 1_000 + row,
                        "filed": f"2026-02-{row + 1:02d}",
                        "frame": f"CY2025-{row}",
                    }
                    for row in range(12)
                ]
            },
        }
        for index in range(80)
    }
    output = {
        "provider": "sec",
        "companyfacts": {"facts": {"us-gaap": concepts}},
    }
    service = ObservationService()

    index = service.ingest(
        tool_call_id="tc_nested",
        step=1,
        input_payload={"ticker": "SAMPLE"},
        result=_result("sec.company_facts_and_filings", output),
        declared_policy="indexed",
        adapter="auto",
    )

    blocks = service.block_store.blocks_for_call("tc_nested")
    block_sizes = [
        len(json.dumps(block.content, ensure_ascii=False, sort_keys=True))
        for block in blocks
    ]
    ref = "obs_tc_nested::/companyfacts/facts/us-gaap/Metric0"
    assert len(index.block_refs) > 48
    assert max(block_sizes) <= 1_200
    assert service.read(ref)[0].content == concepts["Metric0"]
    assert service.raw_store.get("tc_nested").result.output == output  # type: ignore[union-attr]
    outline = index.outline(service.block_store)
    assert outline["listed_block_count"] == 24
    assert outline["omitted_block_count"] > 0


def test_long_text_blocks_preserve_exact_source_substrings_and_whitespace() -> None:
    source = ("first paragraph\n\n" + "second  paragraph with spacing\n" * 120).rstrip()
    service = ObservationService()

    service.ingest(
        tool_call_id="tc_text",
        step=1,
        input_payload={"urls": ["https://example.com"]},
        result=_result("tavily.extract", {"extract": {"raw_content": source}}),
        declared_policy="indexed",
        adapter="auto",
    )

    text_blocks = [
        block
        for block in service.block_store.blocks_for_call("tc_text")
        if block.block_type == "text"
    ]
    assert text_blocks
    assert all(block.content in source for block in text_blocks)
    assert all(
        len(json.dumps(block.content, ensure_ascii=False, separators=(",", ":"))) <= 1_200
        for block in text_blocks
    )


def test_persisted_audit_keeps_hashes_and_counts_without_raw_tool_payload() -> None:
    runtime = TaskMemoryRuntime(agent_task())
    call_id = runtime.begin_tool_call(
        step=1,
        tool_name="large.lookup",
        input_payload={"query": "sample"},
    )
    huge = "x" * 200_000
    runtime.record_tool_result(
        step=1,
        tool_call_id=call_id,
        result=_result("large.lookup", {"blob": huge}),
        input_payload={"query": "sample"},
        warnings=[],
        descriptor=ToolDescriptor(
            name="large.lookup",
            description="large lookup",
            observation_policy="indexed",
            observation_adapter="auto",
        ),
    )

    full = runtime.audit()
    persisted = runtime.persisted_audit()
    rendered = json.dumps(persisted, ensure_ascii=False)
    raw_metadata = persisted["observation_data"]["raw_tool_results"][call_id]

    assert full["observation_data"]["raw_tool_results"][call_id]["tool_result"][
        "output"
    ]["blob"] == huge
    assert huge not in rendered
    assert len(rendered) < 20_000
    assert raw_metadata["output_chars"] > 200_000
    assert len(raw_metadata["output_sha256"]) == 64
    assert "tool_result" not in raw_metadata
    assert persisted["audit_projection"] == "persistence_safe.v1"


def test_memory_has_no_database_client_and_context_query_strips_heavy_audits() -> None:
    memory_dir = Path(__file__).parents[1] / "src" / "doxagent" / "agents" / "runtime" / "memory"
    sources = "\n".join(path.read_text(encoding="utf-8") for path in memory_dir.glob("*.py"))

    assert "import supabase" not in sources
    assert "import psycopg" not in sources
    assert "doxagent.blackboard" not in sources
    assert "{payload,react_audit}" in _AGENT_CONTEXT_PAYLOAD_SQL
    assert "{payload,model_audits}" in _AGENT_CONTEXT_PAYLOAD_SQL
