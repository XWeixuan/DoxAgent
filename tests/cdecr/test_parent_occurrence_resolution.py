from unittest.mock import MagicMock

import pytest

from cdecr.parent_occurrence import ParentOccurrenceService
from cdecr.parent_occurrence_contracts import (
    ParentProposalCard,
    ParentResolutionBatch,
    ParentResolutionGroup,
)
from tests.cdecr.parent_occurrence_fixtures import registry


def _proposal(ref: str) -> ParentProposalCard:
    return ParentProposalCard(
        proposal_ref=ref,
        proposal_id=f"id-{ref}",
        supporting_proposal_ids=[f"id-{ref}"],
        scope="PARENT_OCCURRENCE",
        package_family="OTHER",
        label=ref,
        atomic_refs=[f"A-{ref}"],
        event_ids=[f"E-{ref}"],
        membership_by_event={f"E-{ref}": "COMPONENT_OF"},
        document_refs=[f"D-{ref}"],
    )


def test_resolution_is_a_set_partition_not_pair_decisions() -> None:
    proposals = [_proposal("P1"), _proposal("P2")]
    result = ParentResolutionBatch(
        groups=[
            ParentResolutionGroup(
                resolution_group_id="R1",
                proposal_refs=["P1", "P2"],
                existing_parent_refs=[],
                canonical_label="one parent",
            )
        ]
    )
    ParentOccurrenceService._validate_resolution(result, proposals, [])
    assert result.groups[0].proposal_refs == ["P1", "P2"]


def test_persisted_proposal_ignores_request_local_ref_but_not_business_changes(
    tmp_path,
) -> None:
    store = registry(tmp_path)
    proposal = _proposal("P1")

    first = store.save_parent_occurrence_proposals(
        run_id="scope-1", records=[proposal.model_dump(mode="json")]
    )
    replay = store.save_parent_occurrence_proposals(
        run_id="scope-1",
        records=[
            proposal.model_copy(update={"proposal_ref": "P99"}).model_dump(mode="json")
        ],
    )

    assert first["inserted"] == 1
    assert replay["inserted"] == 0
    with pytest.raises(Exception, match="immutable"):
        store.save_parent_occurrence_proposals(
            run_id="scope-1",
            records=[
                proposal.model_copy(update={"label": "changed"}).model_dump(mode="json")
            ],
        )


def test_resolution_does_not_treat_cues_as_schema_hard_negatives() -> None:
    proposals = [_proposal("P1"), _proposal("P2")]
    result = ParentResolutionBatch(
        groups=[
            ParentResolutionGroup(
                resolution_group_id="R1",
                proposal_refs=["P1", "P2"],
                canonical_label="invalid shared parent",
            ),
        ]
    )

    ParentOccurrenceService._validate_resolution(result, proposals, [])


def test_resolution_rejects_missing_proposal_coverage() -> None:
    proposals = [_proposal("P1"), _proposal("P2")]
    result = ParentResolutionBatch(
        groups=[
            ParentResolutionGroup(
                resolution_group_id="R1",
                proposal_refs=["P1"],
                canonical_label="incomplete partition",
            )
        ]
    )

    with pytest.raises(ValueError, match="cover every input proposal"):
        ParentOccurrenceService._validate_resolution(result, proposals, [])


def test_candidate_coverage_counts_proposals_with_neighbors_not_retained_edges() -> None:
    first = _proposal("P1").model_copy(
        update={"participants": ["COMPANY_MU"], "embedding": [1.0, 0.0]}
    )
    second = _proposal("P2").model_copy(
        update={"participants": ["COMPANY_MU"], "embedding": [0.0, 1.0]}
    )
    service = ParentOccurrenceService(registry=MagicMock())

    tasks, telemetry, _ = service._resolution_tasks([first, second], [])

    assert tasks
    assert telemetry["eligible_candidate_edge_count"] == 1
    assert telemetry["candidate_coverage_bps"] == 10000
    assert telemetry["no_qualified_neighbor_proposal_count"] == 0


def test_token_budget_cut_edges_are_forwarded_to_r2_ledger() -> None:
    proposals = [
        _proposal(f"P{index}").model_copy(
            update={
                "participants": ["COMPANY_MU"],
                "label": f"Micron shared occurrence {index} " + "detail " * 500,
                "embedding": [1.0, index / 100],
            }
        )
        for index in range(1, 7)
    ]
    service = ParentOccurrenceService(
        registry=MagicMock(), resolution_max_input_tokens=2_000
    )

    tasks, telemetry, ledger = service._resolution_tasks(proposals, [])

    assert len(tasks) > 1
    assert telemetry["token_budget_split_count"] > 0
    assert ledger


def test_duplicate_atomic_ownership_is_resolved_without_merging_parent_groups() -> None:
    first = _proposal("P1").model_copy(
        update={"event_ids": ["E1", "E2"], "atomic_refs": ["A1", "A2"]}
    )
    second = _proposal("P2").model_copy(
        update={"event_ids": ["E1", "E3"], "atomic_refs": ["A1b", "A3"]}
    )
    slice_type = __import__(
        "cdecr.parent_occurrence_contracts", fromlist=["AtomicDocumentSlice"]
    ).AtomicDocumentSlice
    slices = {
        ref: slice_type(
            slice_id=f"S-{ref}",
            atomic_ref=ref,
            event_id=event_id,
            document_ref="D",
            document_fingerprint="f" * 64,
            proposition="fact",
            event_family="OTHER",
            time={},
            parent_role="OTHER",
        )
        for ref, event_id in {"A1": "E1", "A1b": "E1", "A2": "E2", "A3": "E3"}.items()
    }

    result, resolved_count = ParentOccurrenceService._deduplicate_final_event_ownership(
        [first, second], [first, second], slices
    )

    assert resolved_count == 1
    assert [item.event_ids for item in result] == [["E1", "E2"], ["E3"]]


def test_finalization_has_no_atomic_boundary_split_method() -> None:
    proposal = _proposal("P1").model_copy(
        update={
            "event_ids": ["E1", "E2", "E3"],
            "atomic_refs": ["A1", "A2", "A3"],
            "membership_by_event": {
                "E1": "COMPONENT_OF",
                "E2": "COMPONENT_OF",
                "E3": "COMPONENT_OF",
            },
        }
    )
    slice_type = __import__(
        "cdecr.parent_occurrence_contracts", fromlist=["AtomicDocumentSlice"]
    ).AtomicDocumentSlice
    slices = {
        ref: slice_type(
            slice_id=f"S-{ref}",
            atomic_ref=ref,
            event_id=event_id,
            document_ref="D",
            document_fingerprint="f" * 64,
            proposition="fact",
            event_family="OTHER",
            time={},
            parent_role="OTHER",
        )
        for ref, event_id in {"A1": "E1", "A2": "E2", "A3": "E3"}.items()
    }
    assert not hasattr(ParentOccurrenceService, "_enforce_final_boundary_purity")
    result, duplicate_count = ParentOccurrenceService._deduplicate_final_event_ownership(
        [proposal], [proposal], slices
    )
    assert duplicate_count == 0
    assert result[0].event_ids == ["E1", "E2", "E3"]
