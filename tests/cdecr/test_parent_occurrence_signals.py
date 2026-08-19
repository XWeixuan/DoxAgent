from cdecr.contracts import OpenAttribute, Participant, ParticipantRole
from cdecr.parent_occurrence_signals import (
    MergeGuardStatus,
    ParentBoundarySignature,
    ParentRole,
    compile_parent_signatures,
    proposal_merge_guard,
)
from tests.cdecr.test_registry import atomic, mention


def _signature(**updates) -> ParentBoundarySignature:
    values = {
        "role": ParentRole.DISCLOSURE,
        "issuer_ids": ("COMPANY_MU",),
        "period_ids": ("FY2026-Q3",),
    }
    values.update(updates)
    return ParentBoundarySignature(**values)


def test_missing_cue_does_not_trigger_merge_review() -> None:
    left = _signature(artifact_ids=("release-1",))
    right = _signature(artifact_ids=())

    assert proposal_merge_guard(left, right).status is MergeGuardStatus.PASS


def test_explicit_role_and_artifact_boundaries_request_proposal_review() -> None:
    disclosure = _signature(artifact_ids=("release-1",))
    reaction = _signature(role=ParentRole.MARKET_EPISODE, market_scope=("close",))
    other_release = _signature(artifact_ids=("release-2",))

    assert proposal_merge_guard(disclosure, reaction).status is MergeGuardStatus.REVIEW
    assert proposal_merge_guard(disclosure, other_release).status is MergeGuardStatus.REVIEW


def test_surface_participant_and_fiscal_text_do_not_become_trusted_identity() -> None:
    current_mention = mention().model_copy(
        update={
            "participants": [
                Participant(surface="Management", entity_id=None, role=ParticipantRole.SUBJECT)
            ],
            "open_attributes": [OpenAttribute(key="report", value="fiscal Q3")],
        }
    )
    current_event = atomic()

    signatures, _ = compile_parent_signatures(
        [current_event],
        mentions_by_id={current_mention.mention_id: current_mention},
        field_links_by_mention={},
        field_entries_by_id={},
    )

    signature = signatures[current_event.event_id]
    assert "Management" not in signature.issuer_ids
    assert "fiscal Q3" not in signature.artifact_ids
