import json

import pytest

from cdecr.parent_occurrence import ParentOccurrenceService, _ResolvedParent
from cdecr.parent_occurrence_contracts import (
    ParentProposalCard,
    ParentResolutionBatch,
    ParentResolutionGroup,
)
from tests.cdecr.parent_occurrence_fixtures import registry
from tests.cdecr.test_registry import atomic, source


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


def test_reconcile_rejects_two_parent_groups_that_share_an_atomic() -> None:
    first = _proposal("P1")
    second = _proposal("P2").model_copy(
        update={"event_ids": [first.event_ids[0], "E-P2"]}
    )
    result = ParentResolutionBatch(
        groups=[
            ParentResolutionGroup(
                resolution_group_id="R1",
                proposal_refs=["P1"],
                canonical_label="first parent",
            ),
            ParentResolutionGroup(
                resolution_group_id="R2",
                proposal_refs=["P2"],
                canonical_label="second parent",
            ),
        ]
    )

    with pytest.raises(ValueError, match="remaining-events"):
        ParentOccurrenceService._validate_resolution(
            result,
            [first, second],
            [],
            require_disjoint_events=True,
        )


def test_reconcile_can_assign_overlap_once_and_split_a_large_proposal() -> None:
    first = _proposal("P1").model_copy(
        update={
            "event_ids": ["E1", "E2"],
            "membership_by_event": {"E1": "COMPONENT_OF", "E2": "COMPONENT_OF"},
        }
    )
    second = _proposal("P2").model_copy(
        update={
            "event_ids": ["E1", "E3"],
            "membership_by_event": {"E1": "COMPONENT_OF", "E3": "COMPONENT_OF"},
        }
    )
    result = ParentResolutionBatch(
        groups=[
            ParentResolutionGroup(
                resolution_group_id="R1",
                proposal_refs=["P1", "P2"],
                includes_remaining_events=True,
                canonical_label="shared disclosure",
            ),
            ParentResolutionGroup(
                resolution_group_id="R2",
                proposal_refs=[],
                event_refs=["A3"],
                canonical_label="separate reaction",
            ),
        ]
    )

    ParentOccurrenceService._validate_resolution(
        result,
        [first, second],
        [],
        require_disjoint_events=True,
        review_event_id_by_ref={"A1": "E1", "A2": "E2", "A3": "E3"},
    )
    reduced = ParentOccurrenceService._reduce_proposals(
        [
            _ResolvedParent(
                group_id="R1",
                proposal_ids=["id-P1", "id-P2"],
                existing_package_ids=[],
                canonical_label="shared disclosure",
                event_ids=["E1", "E2"],
            ),
            _ResolvedParent(
                group_id="R2",
                proposal_ids=[],
                existing_package_ids=[],
                canonical_label="separate reaction",
                event_ids=["E3"],
            ),
        ],
        [first, second],
    )
    assert [item.event_ids for item in reduced] == [["E1", "E2"], ["E3"]]
    assert set(reduced[0].membership_by_event) == {"E1", "E2"}
    assert set(reduced[1].membership_by_event) == {"E3"}


def test_reconcile_discards_a_review_group_left_with_no_atomic_members() -> None:
    proposal = _proposal("P1")
    reduced = ParentOccurrenceService._reduce_proposals(
        [
            _ResolvedParent(
                group_id="empty",
                proposal_ids=[proposal.proposal_id],
                existing_package_ids=[],
                canonical_label="empty overlap residue",
                event_ids=[],
            ),
            _ResolvedParent(
                group_id="winner",
                proposal_ids=[proposal.proposal_id],
                existing_package_ids=[],
                canonical_label="winning parent",
                event_ids=proposal.event_ids,
            ),
        ],
        [proposal],
    )

    assert len(reduced) == 1
    assert reduced[0].label == "winning parent"


def test_reconcile_payload_exposes_compact_atomic_facts(tmp_path) -> None:
    class CaptureModels:
        model_m1 = "unused"

        def __init__(self) -> None:
            self.payload = None

        def typed_many(self, *, requests, validators, **_kwargs):
            self.payload = json.loads(requests[0].user_prompt)
            value = ParentResolutionBatch(
                groups=[
                    ParentResolutionGroup(
                        resolution_group_id="R1",
                        proposal_refs=["P1", "P2"],
                        includes_remaining_events=True,
                        canonical_label="reviewed parent",
                    )
                ]
            )
            validators[0](value)
            return [value]

    store = registry(tmp_path)
    store.save_source(source("MSG-1"), fingerprint="a" * 64)
    store.start_cross_document_run(
        run_id="review-run",
        processing_key="review-run",
        message_id="MSG-1",
        engine_version="test",
        prompt_version="test",
        model_config={},
    )
    proposal = _proposal("P1").model_copy(update={"embedding": [1.0, 0.0]})
    second_proposal = _proposal("P2").model_copy(update={"embedding": [1.0, 0.0]})
    event = atomic(event_id="E-P1", mention_ids=["MENTION-1"])
    second_event = atomic(event_id="E-P2", mention_ids=["MENTION-2"])
    models = CaptureModels()

    resolved, failures, _ = ParentOccurrenceService(registry=store)._resolve_wave(
        [proposal, second_proposal],
        [],
        models=models,
        run_id="review-run",
        checkpoint_scope_id="review-run",
        stage="parent_reconcile",
        review=True,
        review_events={event.event_id: event, second_event.event_id: second_event},
    )

    assert failures == []
    assert resolved[0].event_ids == [event.event_id, second_event.event_id]
    assert models.payload["atomic_events"] == [
        {
            "event_ref": "E1",
            "fact": event.canonical_proposition,
            "family": event.event_family.value,
            "assertion": event.assertion_state.value,
            "period": event.time.reference_period_id,
            "participants": ["COMPANY_MU"],
            "objects": [],
            "artifacts": [],
            "metrics": [],
        },
        {
            "event_ref": "E2",
            "fact": second_event.canonical_proposition,
            "family": second_event.event_family.value,
            "assertion": second_event.assertion_state.value,
            "period": second_event.time.reference_period_id,
            "participants": ["COMPANY_MU"],
            "objects": [],
            "artifacts": [],
            "metrics": [],
        },
    ]
