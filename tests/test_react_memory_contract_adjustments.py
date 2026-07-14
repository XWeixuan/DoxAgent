from __future__ import annotations

import json
from pathlib import Path

import pytest

from doxagent.agents.runtime.memory import ObservationService, TaskMemoryRuntime
from doxagent.agents.runtime.memory.observations import ObservationPolicyRegistry
from doxagent.agents.runtime.memory.protocol import (
    memory_action_schema,
    read_observation_descriptor,
)
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


def test_read_observation_schema_and_memory_prompt_cover_catalog_aliases() -> None:
    descriptor = read_observation_descriptor()
    assert descriptor["input_schema"]["required"] == ["alias"]
    assert descriptor["input_schema"]["additionalProperties"] is False
    assert "complete catalog group" in descriptor["description"]
    prompt = (
        Path(__file__).parents[1] / "prompts" / "workflows" / "memory.md"
    ).read_text(encoding="utf-8")
    assert "group_catalog" in prompt
    assert "block_index" in prompt
    assert "include_children=false" in prompt
    assert '{"read_observation":{...}}' in prompt


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


def test_full_fresh_view_removes_repeated_envelopes_and_catalog_entries() -> None:
    output = {
        "provider": "alpha",
        "symbol": "INTC",
        "data": {
            f"Metric{index}": {
                "label": f"Metric {index}",
                "value": index,
                "description": "x" * 80,
            }
            for index in range(120)
        },
    }
    service = ObservationService()
    index = service.ingest(
        tool_call_id="tc_compact",
        step=1,
        input_payload={},
        result=_result("alpha.company_overview", output),
        declared_policy="inline",
        adapter="auto",
    )

    fresh = service.fresh_view("tc_compact")
    assert fresh is not None
    assert index.delivery_mode == "full"
    assert "blocks" not in fresh["outline"]
    assert "read_instruction" not in fresh
    assert all("context_envelope" not in block for block in fresh["loaded_blocks"])
    assert all("block_type" not in block for block in fresh["loaded_blocks"])
    assert service.reconstruct_output("tc_compact") == output

    original_chars = len(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    visible_chars = len(json.dumps(fresh, ensure_ascii=False, separators=(",", ":")))
    assert visible_chars <= max(original_chars + 1_024, int(original_chars * 1.25))


def test_payload_reconstruction_preserves_empty_containers() -> None:
    output = {
        "bindings": [],
        "recent_events": [],
        "metadata": {},
        "sources": [{"source": "fred", "config": {}}],
    }
    service = ObservationService()
    service.ingest(
        tool_call_id="tc_empty_containers",
        step=1,
        input_payload={},
        result=_result("monitoring.list_status", output),
        declared_policy="inline",
        adapter="auto",
    )

    assert service.reconstruct_output("tc_empty_containers") == output


def test_large_doxatlas_fresh_view_is_semantic_exact_and_not_inflated() -> None:
    output = {
        "provider": "doxatlas",
        "data": {
            "narratives": [
                {
                    "narrative_code": f"N{index}",
                    "summary": "n" * 180,
                    "events": [
                        {
                            "event_code": f"E{index}_{event}",
                            "propositions": [
                                {
                                    "proposition_code": f"P{index}_{event}_{position}",
                                    "text": "p" * 100,
                                }
                                for position in range(3)
                            ],
                        }
                        for event in range(3)
                    ],
                }
                for index in range(30)
            ],
            "media_results": [
                {"media_code": f"M{index}", "text": "m" * 400}
                for index in range(15)
            ],
            "social_results": [
                {"social_code": f"S{index}", "text": "s" * 240}
                for index in range(15)
            ],
            "sources": [
                {"source_code": f"D{index}", "text": "d" * 320}
                for index in range(15)
            ],
        },
    }
    service = ObservationService()
    service.ingest(
        tool_call_id="tc_doxatlas_large",
        step=1,
        input_payload={},
        result=_result("doxa_get_narrative_report", output),
        declared_policy="inline",
        adapter="doxatlas",
    )

    fresh = service.fresh_view("tc_doxatlas_large")
    assert fresh is not None
    assert service.reconstruct_output("tc_doxatlas_large") == output
    semantics = {
        block.get("kind")
        for block in fresh["loaded_blocks"]
        if block.get("kind")
    }
    assert {"N", "E", "P"} <= semantics
    catalog = fresh["outline"]["group_catalog"]
    catalog_paths = {item["path"].rsplit("/part_", 1)[0] for item in catalog}
    assert {
        "/doxatlas/narratives/media_details",
        "/doxatlas/narratives/social_details",
        "/doxatlas/narratives/source_details",
    } <= catalog_paths
    for item in catalog:
        loaded = service.read(item["alias"])
        assert len(loaded) == item["block_count"]
        assert sum(len(json.dumps(block.content, ensure_ascii=False)) for block in loaded)
    original_chars = len(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    visible_chars = len(json.dumps(fresh, ensure_ascii=False, separators=(",", ":")))
    assert visible_chars <= int(original_chars * 1.25)


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
    assert sec.delivery_mode == "full"

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
    assert oversized.delivery_mode == "indexed_threshold"
    assert len(oversized.selected_refs) < len(oversized.block_refs)
    assert "read_observation" in view["read_instruction"]
    assert "omitted_block_count" not in view["outline"]
    assert all("type" not in item for item in view["outline"]["block_index"])
    assert len(view["outline"]["block_index"]) == len(oversized.indexed_refs)
    assert service.reconstruct_output("tc_huge") == oversized_output


def test_profiled_financials_use_navigable_groups_and_group_alias_reads_once() -> None:
    output = {
        "provider": "alpha_vantage",
        "symbol": "INTC",
        "statements": {
            statement: {
                "symbol": "INTC",
                "quarterlyReports": [
                    {
                        "fiscalDateEnding": f"2025-{(index % 12) + 1:02d}-28",
                        "value": index,
                        "detail": "q" * 700,
                    }
                    for index in range(30)
                ],
                "annualReports": [
                    {
                        "fiscalDateEnding": str(2025 - index),
                        "value": index,
                        "detail": "a" * 700,
                    }
                    for index in range(10)
                ],
            }
            for statement in ("INCOME_STATEMENT", "BALANCE_SHEET", "CASH_FLOW")
        },
        "provider_errors": [],
        "source_coordinates": {"provider": "alpha", "endpoint": "query"},
    }
    runtime = TaskMemoryRuntime(agent_task())
    call_id = runtime.begin_tool_call(
        step=1,
        tool_name="alpha.financial_statements",
        input_payload={"ticker": "INTC"},
    )
    runtime.record_tool_result(
        step=1,
        tool_call_id=call_id,
        result=_result("alpha.financial_statements", output),
        input_payload={"ticker": "INTC"},
        warnings=[],
        descriptor=ToolDescriptor(
            name="alpha.financial_statements",
            description="financial statements",
            observation_policy="recomputable",
            observation_adapter="time_series",
        ),
    )
    index = runtime.observations.call_index(call_id)
    assert index is not None
    assert index.delivery_mode == "hybrid_profiled"
    fresh = runtime.observations.fresh_view(call_id)
    assert fresh is not None
    catalog = fresh["outline"]["group_catalog"]
    assert any(
        item["path"].startswith("/financials/income_statement/quarterly_history")
        for item in catalog
    )
    assert all("type" not in item for item in catalog)
    assert "omitted_block_count" not in fresh["outline"]
    content_refs = {
        block.ref
        for block in runtime.observations.block_store.blocks_for_call(call_id)
        if block.block_type != "outline"
    }
    catalog_refs = {ref for group in index.catalog_groups for ref in group.member_refs}
    assert content_refs == set(index.selected_refs) | catalog_refs | set(index.indexed_refs)

    runtime.record_action(2, {"reasoning_summary": "已读取首屏。"})
    group = catalog[0]
    assert runtime.read_observation(step=3, input_payload={"alias": group["alias"]})
    read_fresh = runtime.active_context()["fresh_observations"]
    assert len(read_fresh) == 1
    assert read_fresh[0]["observation_read"] == group["alias"]
    assert len(read_fresh[0]["loaded_blocks"]) == group["block_count"]


def test_unprofiled_over_50k_uses_complete_type_free_fallback_index() -> None:
    output = {
        "records": [
            {"rank": index, "text": f"record-{index}-" + "x" * 900}
            for index in range(120)
        ]
    }
    service = ObservationService()
    index = service.ingest(
        tool_call_id="tc_unprofiled_large",
        step=1,
        input_payload={},
        result=_result("provider.unprofiled", output),
        declared_policy="inline",
        adapter="auto",
    )

    fresh = service.fresh_view("tc_unprofiled_large")
    assert fresh is not None
    assert index.delivery_mode == "indexed_threshold"
    assert index.indexed_refs
    assert "group_catalog" not in fresh["outline"]
    assert len(fresh["outline"]["block_index"]) == len(index.indexed_refs)
    assert all(set(item) == {"alias", "path"} for item in fresh["outline"]["block_index"])
    assert "omitted_block_count" not in fresh["outline"]
    assert service.reconstruct_output("tc_unprofiled_large") == output


def test_large_sec_profile_keeps_research_core_and_groups_all_fact_pages() -> None:
    output = {
        "provider": "sec",
        "cik": "0000050863",
        "company": {"name": "Intel", "sic": "3674"},
        "recent_filings": [
            {"form": "10-K", "filing_date": "2026-02-01", "accession": "a"}
            for _ in range(20)
        ],
        "key_facts": [
            {
                "concept": f"Metric{index}",
                "label": f"Metric {index}",
                "latest_observations": [{"end": "2025-12-31", "value": index}],
            }
            for index in range(20)
        ],
        "fact_directory": {"page_count": 300, "concept_count": 300},
        "fact_pages": {
            f"page_{index:04d}": {
                "concept": f"Metric{index}",
                "description": "historical fact detail " + "x" * 420,
                "latest_observations": [{"end": "2025-12-31", "value": index}],
            }
            for index in range(300)
        },
        "facts_status": "available",
        "source_coordinates": {"provider": "sec", "endpoint": "companyfacts"},
    }
    service = ObservationService()
    index = service.ingest(
        tool_call_id="tc_sec_large",
        step=1,
        input_payload={"ticker": "INTC"},
        result=_result("sec.company_facts_and_filings", output),
        declared_policy="indexed",
        adapter="auto",
    )
    fresh = service.fresh_view("tc_sec_large")

    assert fresh is not None
    assert index.delivery_mode == "hybrid_profiled"
    visible_chars = len(json.dumps(fresh, ensure_ascii=False, separators=(",", ":")))
    assert 10_000 <= visible_chars < 50_000
    assert any(
        item["path"].startswith("/sec/company_facts/fact_pages")
        for item in fresh["outline"]["group_catalog"]
    )
    assert service.reconstruct_output("tc_sec_large") == output


@pytest.mark.parametrize(
    ("tool_name", "adapter", "output", "expected_path"),
    [
        (
            "alpha.earnings_events",
            "auto",
            {
                "provider": "alpha_vantage",
                "symbol": "INTC",
                "earnings": {
                    "EARNINGS_ESTIMATES": {
                        "estimates": [
                            {
                                "horizon": f"period-{index}",
                                "estimate": index,
                                "detail": "e" * 600,
                            }
                            for index in range(40)
                        ]
                    }
                },
                "provider_errors": [],
                "source_coordinates": {"provider": "alpha"},
            },
            "/earnings/estimates/later_periods",
        ),
        (
            "fred.series_observations",
            "time_series",
            {
                "provider": "fred",
                "series": {
                    series_id: {
                        "series_id": series_id,
                        "title": f"Series {series_id}",
                        "observations": [
                            {
                                "date": f"{2010 + index // 12}-{(index % 12) + 1:02d}-01",
                                "value": str(index),
                                "realtime_start": "2026-01-01",
                            }
                            for index in range(300)
                        ],
                    }
                    for series_id in ("DGS10", "UNRATE")
                },
                "failed_series": [],
                "source_coordinates": {"provider": "fred"},
            },
            "/macro/fred/dgs10/earlier_history",
        ),
        (
            "bea.nipa_data",
            "time_series",
            {
                "provider": "bea",
                "dataset": "NIPA",
                "data": {
                    "BEAAPI": {
                        "Results": {
                            "Data": [
                                {
                                    "TimePeriod": f"{2000 + index // 4}Q{index % 4 + 1}",
                                    "DataValue": str(index),
                                    "LineDescription": "Gross domestic product " + "b" * 120,
                                }
                                for index in range(300)
                            ]
                        }
                    }
                },
                "source_coordinates": {"provider": "bea"},
            },
            "/macro/bea/nipa/earlier_periods",
        ),
    ],
)
def test_profiled_time_series_and_earnings_keep_core_and_group_history(
    tool_name: str,
    adapter: str,
    output: dict,
    expected_path: str,
) -> None:
    service = ObservationService()
    index = service.ingest(
        tool_call_id="tc_profiled",
        step=1,
        input_payload={},
        result=_result(tool_name, output),
        declared_policy="recomputable",
        adapter=adapter,
    )
    fresh = service.fresh_view("tc_profiled")
    assert fresh is not None
    assert index.delivery_mode == "hybrid_profiled"
    assert any(
        item["path"].startswith(expected_path)
        for item in fresh["outline"]["group_catalog"]
    )
    assert len(json.dumps(fresh, ensure_ascii=False, separators=(",", ":"))) >= 10_000
    assert service.reconstruct_output("tc_profiled") == output


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
