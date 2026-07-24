from __future__ import annotations

import pytest
from pydantic import ValidationError

from cdecr.contracts import (
    AtomicAction,
    AtomicSemanticRelation,
    ExternalRelationType,
    MembershipRelation,
    PackageAssignmentRelation,
    PackageExternalRelation,
)
from cdecr.cross_document_contracts import (
    AtomicAssignmentDecision,
    AtomicAssignmentRecord,
    AtomicCandidateAssessment,
    AtomicDecisionBatch,
    PackageAssignmentDecision,
    PackageAssignmentRecord,
    PackageCandidateAssessment,
    PackageDecisionBatch,
    PackagePairDecision,
)


def atomic_decision() -> AtomicAssignmentDecision:
    return AtomicAssignmentDecision(
        mention_id="M1",
        action=AtomicAction.MERGE,
        merge_target_event_id="E1",
        candidate_assessments=[
            AtomicCandidateAssessment(
                candidate_event_id="E1",
                relation=AtomicSemanticRelation.SAME_EVENT,
                claim_conflict=False,
                identity_differences=[],
            )
        ],
        related_candidate_event_ids=[],
        possible_duplicate_atomic_ids=[],
    )


def test_atomic_batch_rejects_duplicate_mentions_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        AtomicDecisionBatch(decisions=[atomic_decision(), atomic_decision()])
    with pytest.raises(ValidationError):
        AtomicAssignmentDecision.model_validate(
            {**atomic_decision().model_dump(), "unexpected": True}
        )


def test_atomic_decision_requires_action_specific_target() -> None:
    with pytest.raises(ValidationError):
        AtomicAssignmentDecision(
            mention_id="M1",
            action=AtomicAction.MERGE,
            candidate_assessments=[],
            related_candidate_event_ids=[],
            possible_duplicate_atomic_ids=[],
        )
    with pytest.raises(ValidationError):
        AtomicAssignmentDecision(
            mention_id="M1",
            action=AtomicAction.CREATE_NEW,
            merge_target_event_id="E1",
            candidate_assessments=[],
            related_candidate_event_ids=[],
            possible_duplicate_atomic_ids=[],
        )


def test_package_decision_requires_relation_specific_detail() -> None:
    with pytest.raises(ValidationError):
        PackagePairDecision(
            event_id="E1",
            candidate_package_id="P1",
            relation=PackageAssignmentRelation.MEMBER,
        )
    member = PackagePairDecision(
        event_id="E1",
        candidate_package_id="P1",
        relation=PackageAssignmentRelation.MEMBER,
        membership_relation=MembershipRelation.DISCLOSED_IN,
    )
    assert member.membership_relation is MembershipRelation.DISCLOSED_IN
    external = PackagePairDecision(
        event_id="E1",
        candidate_package_id="P1",
        relation=PackageAssignmentRelation.EXTERNAL_RELATED,
        external_relation=ExternalRelationType.MARKET_REACTION_TO,
    )
    assert external.external_relation is ExternalRelationType.MARKET_REACTION_TO


def test_joint_package_decision_enforces_complete_member_ranking() -> None:
    member_one = PackageCandidateAssessment(
        candidate_package_id="P1",
        relation=PackageAssignmentRelation.MEMBER,
        membership_relation=MembershipRelation.DISCLOSED_IN,
        reason="same canonical artifact",
    )
    member_two = member_one.model_copy(update={"candidate_package_id": "P2"})
    external = PackageCandidateAssessment(
        candidate_package_id="P3",
        relation=PackageAssignmentRelation.EXTERNAL_RELATED,
        external_relation=ExternalRelationType.MARKET_REACTION_TO,
        reason="outside boundary",
    )
    decision = PackageAssignmentDecision(
        event_id="E1",
        candidate_assessments=[member_one, member_two, external],
        ranked_member_package_ids=["P2", "P1"],
        selected_member_package_id="P2",
        selection_reason="P2 has the trusted anchor",
    )
    assert decision.selected_member_package_id == "P2"
    assert PackageDecisionBatch(decisions=[decision]).decisions == [decision]

    invalid_updates = [
        {"ranked_member_package_ids": ["P1"]},
        {
            "ranked_member_package_ids": ["P1", "P2"],
            "selected_member_package_id": "P2",
        },
        {
            "candidate_assessments": [member_one, member_one, external],
            "ranked_member_package_ids": ["P1"],
            "selected_member_package_id": "P1",
        },
    ]
    for update in invalid_updates:
        with pytest.raises(ValidationError):
            PackageAssignmentDecision.model_validate(
                {**decision.model_dump(mode="json"), **update}
            )


def test_joint_package_decision_allows_zero_members_without_target() -> None:
    decision = PackageAssignmentDecision(
        event_id="E1",
        candidate_assessments=[
            PackageCandidateAssessment(
                candidate_package_id="P1",
                relation=PackageAssignmentRelation.NOT_RELATED,
                reason="different report",
            ),
            PackageCandidateAssessment(
                candidate_package_id="P2",
                relation=PackageAssignmentRelation.UNCERTAIN,
                reason="insufficient evidence",
            ),
        ],
        ranked_member_package_ids=[],
    )
    assert decision.selected_member_package_id is None
    with pytest.raises(ValidationError):
        PackageDecisionBatch(decisions=[decision, decision])


def test_assignments_must_have_resulting_objects_and_identity_version() -> None:
    with pytest.raises(ValidationError):
        AtomicAssignmentRecord(
            assignment_id="A1",
            run_id="R1",
            mention_id="M1",
            action=AtomicAction.CREATE_NEW,
            hard_conflicts=[],
            identity_differences=[],
            identity_processing_key="identity-v1",
            assignment_policy_version="policy-v2",
            reason="NO_CANDIDATE",
        )
    with pytest.raises(ValidationError):
        PackageAssignmentRecord(
            assignment_id="P1",
            run_id="R1",
            event_id="E1",
            action="CREATE_NEW_PACKAGE",
            reason="NO_CANDIDATE",
        )


def test_package_external_relation_round_trip_is_strict() -> None:
    relation = PackageExternalRelation(
        relation_id="R1",
        source_event_id="E1",
        target_package_id="P1",
        relation=ExternalRelationType.ANALYST_REACTION_TO,
    )
    assert PackageExternalRelation.model_validate_json(relation.model_dump_json()) == relation
    with pytest.raises(ValidationError):
        PackageExternalRelation.model_validate(
            {**relation.model_dump(), "target_event_id": "E2"}
        )
