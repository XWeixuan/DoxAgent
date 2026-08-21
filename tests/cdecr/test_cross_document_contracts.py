from __future__ import annotations

import pytest
from pydantic import ValidationError

from cdecr.atomic_identity_contracts import (
    IdentityAxis,
    IdentityAxisAssessment,
    IdentityAxisVerdict,
)
from cdecr.contracts import (
    AtomicAction,
    AtomicSemanticRelation,
    ExternalRelationType,
    PackageExternalRelation,
)
from cdecr.cross_document_contracts import (
    AtomicAssignmentDecision,
    AtomicAssignmentRecord,
    AtomicCandidateAssessment,
    AtomicDecisionBatch,
    PackageAssignmentRecord,
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
                axis_assessments=[
                    IdentityAxisAssessment(
                        axis=IdentityAxis.REFERENT,
                        verdict=IdentityAxisVerdict.MATCH,
                    )
                ],
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
