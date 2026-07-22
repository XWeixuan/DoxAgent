from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from cdecr.contracts import (
    AssertionState,
    EventFamily,
    EventMention,
    LocalPackageHint,
    ParticipantRole,
    Predicate,
    TimePrecision,
)
from cdecr.single_document_contracts import (
    EventTimeDraft,
    EvidenceLocator,
    EvidenceText,
    GroundedMentionDraft,
    JudgeAction,
    JudgeDecisionRecord,
    MentionDraft,
    OpenAttributeDraft,
    ParticipantDraft,
)


def mention_draft() -> MentionDraft:
    return MentionDraft(
        evidence_locations=[
            EvidenceText(segment_id="text:0", text="Micron")
        ],
        canonical_proposition="Micron raised guidance.",
        source_claim=None,
        event_family=EventFamily.GUIDANCE_EXPECTATION,
        predicate=Predicate(raw="raised", normalized="raise_guidance"),
        participants=[
            ParticipantDraft(
                surface="Micron",
                role=ParticipantRole.SUBJECT,
            )
        ],
        locations=[],
        time=EventTimeDraft(
            event_start=date(2026, 6, 25),
            event_end=None,
            precision=TimePrecision.DAY,
        ),
        assertion_state=AssertionState.ACTUAL,
        quantities=[],
        open_attributes=[],
    )


def test_grounder_draft_forbids_retired_judge_routing_fields() -> None:
    with pytest.raises(ValidationError, match="needs_judge"):
        GroundedMentionDraft(
            draft_id="D1",
            source_candidate_ids=["C1"],
            mention=mention_draft(),
            needs_judge=True,
        )


@pytest.mark.parametrize(
    "action",
    [
        JudgeAction.ACCEPT,
        JudgeAction.REJECT,
        JudgeAction.SPLIT,
        JudgeAction.DUPLICATE,
        JudgeAction.MERGE_AS_ATTRIBUTE,
    ],
)
def test_all_judge_actions_have_closed_payload_semantics(action: JudgeAction) -> None:
    payload: dict[str, object] = {
        "decision_id": f"J-{action.value}",
        "target_draft_id": "D1",
        "action": action,
        "reason": "test",
    }
    if action is JudgeAction.SPLIT:
        payload["split_mentions"] = [mention_draft(), mention_draft()]
    if action is JudgeAction.DUPLICATE:
        payload["target_mention_id"] = "D2"
    if action is JudgeAction.MERGE_AS_ATTRIBUTE:
        payload["target_mention_id"] = "D2"
        payload["attribute"] = OpenAttributeDraft(
            key="reason",
            value="demand",
            evidence_location=EvidenceText(segment_id="text:0", text="Micron"),
        )
    assert JudgeDecisionRecord.model_validate(payload).action is action


def test_split_requires_two_replacements_and_duplicate_requires_target() -> None:
    with pytest.raises(ValidationError, match="at least two"):
        JudgeDecisionRecord(
            decision_id="J1",
            target_draft_id="D1",
            action=JudgeAction.SPLIT,
            reason="mixed",
            split_mentions=[mention_draft()],
        )
    with pytest.raises(ValidationError, match="target_mention_id"):
        JudgeDecisionRecord(
            decision_id="J2",
            target_draft_id="D1",
            action=JudgeAction.DUPLICATE,
            reason="duplicate",
        )


def test_intermediate_contracts_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError, match="extra"):
        EvidenceLocator.model_validate(
            {
                "segment_id": "text:0",
                "start_char": 0,
                "end_char": 6,
                "text": "Micron",
                "absolute_offset": 0,
            }
        )
    with pytest.raises(ValidationError, match="extra"):
        LocalPackageHint.model_validate(
            {
                "anchor": "Micron earnings release",
                "relation_to_anchor": "DISCLOSED_IN",
                "package_kind": "BOUNDED",
                "anchor_artifact_id": "old-field",
            }
        )


def test_final_event_mention_requires_evidence() -> None:
    draft = mention_draft()
    with pytest.raises(ValidationError, match="at least 1"):
        EventMention(
            mention_id="M1",
            message_id="MSG-1",
            evidence_spans=[],
            canonical_proposition=draft.canonical_proposition,
            source_claim=draft.source_claim,
            event_family=draft.event_family,
            predicate=draft.predicate,
            participants=[],
            locations=[],
            time=draft.time,
            assertion_state=draft.assertion_state,
            quantities=[],
            open_attributes=[],
        )
