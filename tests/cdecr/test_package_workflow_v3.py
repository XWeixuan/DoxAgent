from __future__ import annotations

from cdecr.contracts import MembershipRelation, PackageFamily
from cdecr.models import _chat_json_schema_kwargs
from cdecr.package_global_clustering import PackageWorkflowV3Service
from cdecr.package_projection import project_frozen_partition_v3
from cdecr.package_v3_contracts import (
    PackageV3DescriptionOutput,
    PackageV3InitialClusteringOutput,
    PackageV3OccurrenceInput,
    PackageV3RollingClusteringOutput,
)
from cdecr.parent_occurrence_contracts import ParentProposalCard
from cdecr.ports import ResponsesModelRequest
from tests.cdecr.parent_occurrence_fixtures import ScriptedParentModels, registry
from tests.cdecr.test_registry import atomic, source


def _proposal(index: int) -> ParentProposalCard:
    event_id = f"EVENT-{index:03d}"
    return ParentProposalCard(
        proposal_ref=f"P{index}",
        proposal_id=f"proposal:{index:03d}",
        supporting_proposal_ids=[f"proposal:{index:03d}"],
        scope="PARENT_OCCURRENCE",
        package_family=PackageFamily.COMPANY_DISCLOSURE,
        label=f"Micron June update item {index}",
        atomic_refs=[f"A{index}"],
        event_ids=[event_id],
        membership_by_event={event_id: MembershipRelation.COMPONENT_OF},
        document_refs=["MSG-1"],
    )


def _start_run(store, *, run_id: str) -> None:
    store.start_cross_document_run(
        run_id=run_id,
        processing_key=run_id,
        message_id="MSG-1",
        engine_version="test-v3",
        prompt_version="test-v3",
        model_config={},
    )


def test_v3_responses_use_true_strict_json_schema_without_prompt_duplication() -> None:
    request = ResponsesModelRequest(
        input=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "payload"},
        ],
        json_schema=PackageV3InitialClusteringOutput.model_json_schema(),
        output_mode="json_schema",
        schema_name="package_v3_initial_clustering",
        strict=True,
        reasoning_effort="high",
        previous_response_id=None,
    )
    kwargs = _chat_json_schema_kwargs(
        model="deepseek-v4-flash-0731",
        request=request,
    )
    assert kwargs["messages"] == request.input
    assert kwargs["response_format"]["type"] == "json_schema"
    assert kwargs["response_format"]["json_schema"]["strict"] is True
    assert kwargs["reasoning_effort"] == "high"


def test_v3_uses_m3_low_for_clustering_and_m2_none_for_description() -> None:
    service = PackageWorkflowV3Service(registry=object())  # type: ignore[arg-type]
    cluster = service._model_request(
        prompt="cluster",
        payload={},
        schema=PackageV3InitialClusteringOutput.model_json_schema(),
        schema_name="cluster",
        stage="package_v3_initial_clustering",
        reasoning_effort=service.reasoning_effort,
    )
    description = service._model_request(
        prompt="description",
        payload={},
        schema=PackageV3DescriptionOutput.model_json_schema(),
        schema_name="description",
        stage="package_v3_description",
        reasoning_effort=service.description_reasoning_effort,
    )
    assert cluster.reasoning_effort == "low"
    assert description.reasoning_effort == "none"
    assert cluster.output_mode == description.output_mode == "json_object"
    assert cluster.strict is description.strict is False

    strict_service = PackageWorkflowV3Service(  # type: ignore[arg-type]
        registry=object(), strict_output=True
    )
    strict_request = strict_service._model_request(
        prompt="cluster",
        payload={},
        schema=PackageV3InitialClusteringOutput.model_json_schema(),
        schema_name="cluster",
        stage="package_v3_initial_clustering",
        reasoning_effort="high",
    )
    assert strict_request.output_mode == "json_schema"
    assert strict_request.strict is True


def test_v3_schemas_are_closed_objects() -> None:
    for output_type in (
        PackageV3InitialClusteringOutput,
        PackageV3RollingClusteringOutput,
        PackageV3DescriptionOutput,
    ):
        schema = output_type.model_json_schema()
        assert schema["additionalProperties"] is False
        for definition in schema.get("$defs", {}).values():
            assert definition["additionalProperties"] is False


def test_v3_occurrence_payload_contract_is_unchanged() -> None:
    assert set(PackageV3OccurrenceInput.model_fields) == {
        "occurrence_id",
        "parent_occurrence",
    }


def test_v3_canonical_schema_examples_are_present() -> None:
    initial_schema = PackageV3InitialClusteringOutput.model_json_schema()
    rolling_schema = PackageV3RollingClusteringOutput.model_json_schema()
    assert initial_schema["$defs"]["PackageV3InitialCluster"]["properties"]["canonical"][
        "examples"
    ] == ["Micron fiscal Q3 2026 earnings release"]
    assert rolling_schema["$defs"]["PackageV3NewMCP"]["properties"]["canonical"]["examples"] == [
        "Apple June 2026 device price increase"
    ]


def test_rolling_normalization_gives_keep_role_order_independent_priority() -> None:
    service = PackageWorkflowV3Service(registry=object())  # type: ignore[arg-type]
    base = {
        "existing_assignments": [],
        "new_mcps": [{"canonical": "new", "occurrence_ids": ["PO-1"]}],
    }
    forward = PackageV3RollingClusteringOutput.model_validate(
        {
            **base,
            "merges": [
                {"keep_mcp_id": "A", "merge_mcp_ids": ["B"]},
                {"keep_mcp_id": "B", "merge_mcp_ids": ["C"]},
            ],
        }
    )
    reverse = forward.model_copy(update={"merges": list(reversed(forward.merges))})

    action_logs: list[list[dict[str, object]]] = [[], []]
    normalized = [
        service._normalize_rolling(
            value,
            expected_occurrence_ids={"PO-1"},
            active_mcp_ids={"A", "B", "C"},
            labels={"PO-1": "new"},
            actions=action_logs[index],
        ).model_dump(mode="json")
        for index, value in enumerate((forward, reverse))
    ]

    assert normalized[0] == normalized[1]
    assert normalized[0]["merges"] == [{"keep_mcp_id": "B", "merge_mcp_ids": ["C"]}]
    assert all(
        any(item["code"] == "EMPTY_MERGE_DROPPED" for item in actions) for actions in action_logs
    )


def test_v3_201_occurrences_run_initial_then_rolling_and_replay_without_calls(
    tmp_path,
) -> None:
    store = registry(tmp_path)
    store.save_source(source("MSG-1"), fingerprint="1" * 64)
    _start_run(store, run_id="package-v3-201")
    proposals = [_proposal(index) for index in range(1, 202)]
    events = [
        atomic(event_id=f"EVENT-{index:03d}", mention_ids=[f"MENTION-{index:03d}"])
        for index in range(1, 202)
    ]
    models = ScriptedParentModels()
    service = PackageWorkflowV3Service(registry=store, batch_size=200)
    result = service.run(
        events=events,
        proposals=proposals,
        external_links=[],
        models=models,
        run_id="package-v3-201",
        registry_scope_id="scope-v3-201",
    )
    assert result.status == "FINALIZED"
    assert result.partition is not None
    assert result.partition.registry_version == 2
    assert len(result.partition.groups) == 1
    assert len(result.partition.groups[0].atomic_event_ids) == 201
    assert result.partition.groups[0].canonical == "Micron June update"
    assert [stage for _, stage in models.stages] == [
        "package_v3_initial_clustering",
        "package_v3_description",
        "package_v3_rolling_clustering",
        "package_v3_description",
    ]

    replay = service.run(
        events=events,
        proposals=proposals,
        external_links=[],
        models=object(),
        run_id="package-v3-201",
        registry_scope_id="scope-v3-201",
    )
    assert replay.status == "FINALIZED"
    assert replay.partition == result.partition


def test_v3_projection_has_one_membership_per_atomic(tmp_path) -> None:
    store = registry(tmp_path)
    store.save_source(source("MSG-1"), fingerprint="2" * 64)
    _start_run(store, run_id="package-v3-projection")
    proposals = [_proposal(1), _proposal(2)]
    events = [
        atomic(event_id="EVENT-001", mention_ids=["MENTION-001"]),
        atomic(event_id="EVENT-002", mention_ids=["MENTION-002"]),
    ]
    result = PackageWorkflowV3Service(registry=store).run(
        events=events,
        proposals=proposals,
        external_links=[],
        models=ScriptedParentModels(),
        run_id="package-v3-projection",
        registry_scope_id="scope-v3-projection",
    )
    assert result.partition is not None
    packages, memberships, assignments, external_relations, redirects = project_frozen_partition_v3(
        result.partition,
        events=events,
        existing_packages=[],
        run_id="package-v3-projection",
    )
    assert len(packages) == 1
    assert len(memberships) == len(assignments) == 2
    assert {item.event_id for item in memberships} == {"EVENT-001", "EVENT-002"}
    assert external_relations == []
    assert redirects == []
