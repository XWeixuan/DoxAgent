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
    AtomicAssignmentRecord,
    AtomicDecisionBatch,
    AtomicPairDecision,
    PackagePairDecision,
)


def atomic_pair() -> AtomicPairDecision:
    return AtomicPairDecision(
        mention_id="M1",
        candidate_event_id="E1",
        relation=AtomicSemanticRelation.SAME_EVENT,
        claim_conflict=False,
        identity_conflicts=[],
    )


def test_atomic_batch_rejects_duplicate_pairs_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        AtomicDecisionBatch(decisions=[atomic_pair(), atomic_pair()])
    with pytest.raises(ValidationError):
        AtomicPairDecision.model_validate({**atomic_pair().model_dump(), "unexpected": True})


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


def test_hold_cannot_claim_an_atomic_assignment() -> None:
    with pytest.raises(ValidationError):
        AtomicAssignmentRecord(
            assignment_id="A1",
            run_id="R1",
            mention_id="M1",
            resulting_event_id="E1",
            action=AtomicAction.HOLD,
            hard_conflicts=[],
            identity_conflicts=[],
            reason="UNCERTAIN",
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
        PackageExternalRelation.model_validate({**relation.model_dump(), "target_event_id": "E2"})
