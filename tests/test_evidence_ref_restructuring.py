from __future__ import annotations

from pathlib import Path

import pytest

from doxagent.agents.runtime.memory import (
    InMemoryObservationArchive,
    ObservationAliasRegistry,
    ObservationService,
    TaskMemoryState,
)
from doxagent.annotations import (
    InMemoryAnnotationStore,
    TextAnnotationProcessor,
    render_time_tags,
)
from doxagent.models import (
    AgentResult,
    BlackboardPatch,
    Delegation,
    Objection,
    ResearchSection,
    ResultStatus,
    WorkingMemoryEntry,
)
from doxagent.tools import ToolResult
from doxagent.workflows.initialization import BlackboardInitializationWorkflow

ROOT = Path(__file__).resolve().parents[1]


class _FailingStore:
    def save_citations(self, records: list[object]) -> None:
        raise RuntimeError("storage unavailable")

    def save_times(self, records: list[object]) -> None:
        raise RuntimeError("storage unavailable")


def _observations() -> tuple[ObservationService, ToolResult, str, str]:
    service = ObservationService()
    result = ToolResult(
        tool_name="search",
        status=ResultStatus.SUCCEEDED,
        output={
            "results": [
                {
                    "title": "Quarterly release",
                    "url": "https://example.test/release",
                    "published_at": "2026-07-01",
                    "text": "Revenue increased in the second quarter.",
                }
            ]
        },
        output_summary="one source",
        raw={"complete": True},
    )
    service.ingest(
        tool_call_id="tc_internal_1",
        step=1,
        input_payload={"query": "quarterly release"},
        result=result,
        declared_policy="indexed",
        adapter="search_results",
    )
    block = service.block_store.records()[0]
    alias = service.aliases.alias_for(block.block_id)
    assert alias is not None
    return service, result, block.block_id, alias


def test_legacy_provenance_is_absent_from_cross_system_contracts() -> None:
    legacy = {
        "evidence_refs",
        "resolution_evidence_refs",
        "required_evidence",
        "source",
    }
    for model in (
        AgentResult,
        ToolResult,
        BlackboardPatch,
        Objection,
        Delegation,
        WorkingMemoryEntry,
        ResearchSection,
    ):
        assert legacy.isdisjoint(model.model_fields), model.__name__
    assert not hasattr(ToolResult, "to_evidence_ref")


def test_task_local_alias_is_stable_and_hides_internal_identifiers() -> None:
    service, _, block_id, alias = _observations()
    assert alias == "O1"
    assert service.aliases.register(block_id) == alias
    assert service.aliases.resolve(alias) == block_id

    view = service.fresh_view("tc_internal_1")
    assert view is not None
    rendered = repr(view)
    assert "tc_internal_1" not in repr(view["loaded_blocks"])
    assert "obs_tc" not in rendered
    assert view["loaded_blocks"][0]["alias"] == "O1"
    assert "locator" not in view["loaded_blocks"][0]
    assert "block_id" not in view["loaded_blocks"][0]


def test_retain_read_synthesis_and_invalid_alias_share_one_registry() -> None:
    service, _, block_id, alias = _observations()
    memory = TaskMemoryState()
    warnings = memory.apply_action(
        step=1,
        action={
            "synthesis_update": [f"ADD: Revenue increased.【cite:{alias}】"],
            "retain_observations": [
                {"alias": alias, "note": "quarter", "reason": "supports revenue"},
                {"alias": "O99", "note": "bad", "reason": "invalid"},
            ],
        },
        observations=service,
    )
    assert service.read(alias)[0].block_id == block_id
    assert memory.synthesis["S1"].observation_block_ids == (block_id,)
    assert block_id in memory.retained
    assert any("O99" in warning for warning in warnings)


def test_recursive_annotation_resolves_citations_and_independent_times() -> None:
    service, _, block_id, alias = _observations()
    store = InMemoryAnnotationStore()
    batch = TextAnnotationProcessor(store).process(
        run_id="run_1",
        task_id="task_1",
        result_id="result_1",
        payload={
            "section": {
                "text": (
                    "Revenue increased in 2026-Q2."
                    f"【cite:{alias}】【occurred_at:2026-Q2】"
                    "【published_at:2026-07-01】"
                )
            },
            "analysis": "Structural demand remains strong without a dated event.",
        },
        aliases=service.aliases,
    )
    plain = batch.plain_payload["section"]["text"]
    assert plain == "Revenue increased in 2026-Q2."
    assert batch.processed_texts[0].raw_tagged_text != plain
    assert batch.citations[0].observation_block_id == block_id
    assert {item.occurred_at for item in batch.times} == {"2026-Q2", None}
    assert {item.published_at for item in batch.times} == {None, "2026-07-01"}
    assert batch.metrics.citation_resolution_rate == 1.0
    assert batch.metrics.time_validity_rate == 1.0
    rerendered = render_time_tags(plain, store.times_for_text(plain))
    assert "【occurred_at:2026-Q2】" in rerendered
    assert "【published_at:2026-07-01】" in rerendered
    assert "【cite:" not in rerendered


def test_annotation_failures_are_non_blocking_and_disabled_mode_is_identity() -> None:
    aliases = ObservationAliasRegistry()
    payload = {
        "text": "A dated statement.【cite:O9】【occurred_at:not-a-time】"
    }
    batch = TextAnnotationProcessor(_FailingStore()).process(
        run_id="run_1",
        task_id="task_1",
        result_id="result_1",
        payload=payload,
        aliases=aliases,
    )
    assert batch.plain_payload == {"text": "A dated statement."}
    assert batch.citations == []
    assert batch.times == []
    assert batch.metrics.invalid_alias_count == 1
    assert batch.metrics.invalid_time_count == 1
    assert any("annotation_persistence_failed" in warning for warning in batch.warnings)

    disabled = TextAnnotationProcessor(enabled=False).process(
        run_id="run_1",
        task_id="task_1",
        result_id="result_2",
        payload=payload,
        aliases=aliases,
    )
    assert disabled.plain_payload == payload
    assert disabled.citations == []
    assert disabled.times == []


def test_audit_chain_reaches_raw_tool_result_without_source_object() -> None:
    service, result, block_id, _ = _observations()
    archive = InMemoryObservationArchive()
    archive.save_task(run_id="run_1", task_id="task_1", observations=service)
    block = archive.get_block("run_1", "task_1", block_id)
    assert block is not None
    raw = archive.get_raw_result("run_1", "task_1", block.tool_call_id)
    assert raw is not None
    assert raw.result == result
    assert raw.result.output["results"][0]["url"] == "https://example.test/release"


def test_new_persistence_and_runtime_do_not_write_legacy_evidence_table() -> None:
    migration = (ROOT / "supabase/migrations/202607120001_observation_annotations.sql").read_text(
        encoding="utf-8"
    )
    assert "raw_tool_results" in migration
    assert "observation_blocks" in migration
    assert "citation_annotations" in migration
    assert "time_annotations" in migration
    repository = (ROOT / "src/doxagent/blackboard/postgres_repository.py").read_text(
        encoding="utf-8"
    )
    assert "insert into doxagent.evidence_refs" not in repository.lower()


def test_complete_research_workflow_has_no_annotation_driven_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOXAGENT_STORAGE_MODE", "memory")
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    result = workflow.run("NVDA")
    assert result.error is None
    assert result.status.value == "completed"
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    serialized = repr(run.belief_state.model_dump(mode="json"))
    assert "evidence_refs" not in serialized
    assert "EvidenceRef" not in serialized
