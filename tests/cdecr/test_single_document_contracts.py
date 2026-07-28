from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from cdecr.contracts import (
    AssertionState,
    EventFamily,
    EventMention,
    EventTime,
    EvidenceRecord,
    EvidenceRecordStatus,
    LocalPackageHint,
    ParticipantRole,
    Predicate,
    Quantity,
    QuantityRole,
    TimePrecision,
)
from cdecr.single_document_contracts import (
    EventTimeDraft,
    EvidenceLocator,
    EvidenceText,
    GroundedMentionDraft,
    GrounderModelOutput,
    JudgeAction,
    JudgeCommandOutput,
    JudgeDecisionRecord,
    JudgeMentionChanges,
    JudgeMentionDraft,
    MentionDraft,
    OpenAttributeDraft,
    ParticipantDraft,
    QuantityDraft,
    normalize_event_time_semantics,
    validate_event_time_semantics,
)


def mention_draft() -> MentionDraft:
    return MentionDraft(
        evidence_locations=[EvidenceText(segment_id="text:0", text="Micron")],
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


def test_grounder_candidate_disposition_contract_is_closed() -> None:
    output = GrounderModelOutput.model_validate(
        {
            "drafts": [
                {
                    "source_candidate_ids": ["c1"],
                    "mention": mention_draft().model_dump(mode="json"),
                }
            ],
            "rejected_candidates": [
                {"id": "c2", "code": "BACKGROUND"},
                {"id": "c3", "code": "NOT_INDEPENDENT"},
            ],
            "issue_flags": [],
        }
    )
    assert [item.id for item in output.rejected_candidates] == ["c2", "c3"]
    with pytest.raises(ValidationError, match="literal_error"):
        GrounderModelOutput.model_validate(
            {
                "drafts": [],
                "rejected_candidates": [{"id": "c1", "code": "GENERIC_OPINION"}],
                "issue_flags": [],
            }
        )


def test_quantity_roles_require_exactly_one_primary_in_new_draft() -> None:
    primary = QuantityDraft(
        metric_id="revenue",
        value=50,
        unit="USD_BILLION",
        raw_text="$50 billion",
        role=QuantityRole.PRIMARY,
    )
    comparison = QuantityDraft(
        metric_id="revenue",
        value=42.95,
        unit="USD_BILLION",
        raw_text="$42.95 billion consensus",
        role=QuantityRole.COMPARISON,
    )
    valid = mention_draft().model_copy(
        update={"quantities": [primary, comparison]}
    )
    assert [item.role for item in valid.quantities] == [
        QuantityRole.PRIMARY,
        QuantityRole.COMPARISON,
    ]
    with pytest.raises(ValidationError, match="exactly one PRIMARY"):
        MentionDraft.model_validate(
            {
                **mention_draft().model_dump(mode="json"),
                "quantities": [
                    comparison.model_dump(mode="json"),
                    comparison.model_dump(mode="json"),
                ],
            }
        )


def test_legacy_mention_quantities_assign_first_primary_and_rest_supporting() -> None:
    draft = mention_draft()
    legacy_quantities = [
        Quantity(metric_id="revenue", value=50, unit="USD", raw_text="$50B"),
        Quantity(metric_id="eps", value=31, unit="USD", raw_text="$31"),
    ]
    mention = EventMention(
        mention_id="M-LEGACY",
        message_id="MSG-1",
        evidence_records=[],
        evidence_spans=[],
        canonical_proposition=draft.canonical_proposition,
        source_claim=draft.source_claim,
        event_family=draft.event_family,
        predicate=draft.predicate,
        participants=[],
        locations=[],
        time=EventTime(**draft.time.model_dump()),
        assertion_state=draft.assertion_state,
        quantities=legacy_quantities,
        open_attributes=[],
    )
    assert [item.role for item in mention.quantities] == [
        QuantityRole.PRIMARY,
        QuantityRole.SUPPORTING,
    ]


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


def test_judge_model_contract_groups_actions_and_excludes_persistence_fields() -> None:
    judge_mention = JudgeMentionDraft.model_validate(
        mention_draft().model_dump(mode="json", exclude={"local_package_hint"})
    )
    output = JudgeCommandOutput.model_validate(
        {
            "accepted": [{"id": "d1", "reason": "supported"}],
            "rejected": [{"id": "d2", "reason": "background"}],
            "split": [
                {
                    "id": "d3",
                    "reason": "two events",
                    "mentions": [judge_mention, judge_mention],
                }
            ],
            "duplicates": [{"id": "d4", "reason": "same event", "keep_id": "d1"}],
            "attribute_merges": [
                {
                    "id": "d5",
                    "reason": "modifier",
                    "keep_id": "d1",
                    "attribute": {
                        "key": "reason",
                        "value": "demand",
                        "evidence_location": {
                            "segment_id": "text:0",
                            "text": "Micron",
                        },
                    },
                }
            ],
        }
    )
    assert output.accepted[0].changes is None
    serialized = json.dumps(JudgeCommandOutput.model_json_schema(), sort_keys=True)
    assert "source_candidate_ids" not in serialized
    assert "local_package_hint" not in serialized
    assert "target_mention_id" not in serialized
    assert "revised_mention" not in serialized


def test_judge_accept_changes_are_nonempty_field_replacements() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        JudgeMentionChanges.model_validate({})
    with pytest.raises(ValidationError, match="event_family cannot be replaced with null"):
        JudgeMentionChanges.model_validate({"event_family": None})
    changes = JudgeMentionChanges.model_validate({"source_claim": None})
    assert changes.model_fields_set == {"source_claim"}


def test_event_time_without_bounds_requires_unknown_precision() -> None:
    invalid = EventTimeDraft(
        event_start=None,
        event_end=None,
        precision=TimePrecision.DAY,
        reference_period_id="FY2026 Q4",
    )
    with pytest.raises(ValueError, match="UNKNOWN"):
        validate_event_time_semantics(invalid)
    value = EventTimeDraft(
        event_start=None,
        event_end=None,
        precision=TimePrecision.UNKNOWN,
        reference_period_id="FY2026 Q4",
    )
    validate_event_time_semantics(value)
    assert value.reference_period_id == "FY2026 Q4"


def test_event_time_normalization_only_changes_unbounded_precision() -> None:
    unbounded = EventTimeDraft(
        event_start=None,
        event_end=None,
        precision=TimePrecision.DAY,
        reference_period_id="FY2026 Q4",
    )
    normalized, audit = normalize_event_time_semantics(unbounded)
    assert normalized.precision is TimePrecision.UNKNOWN
    assert normalized.reference_period_id == "FY2026 Q4"
    assert audit == {
        "precision_before": "DAY",
        "precision_after": "UNKNOWN",
        "reason_code": "NO_EVENT_BOUNDS",
    }

    bounded = EventTimeDraft(
        event_start=date(2026, 6, 25),
        event_end=None,
        precision=TimePrecision.DAY,
    )
    unchanged, audit = normalize_event_time_semantics(bounded)
    assert unchanged == bounded
    assert audit is None


def test_event_time_end_before_start_remains_a_hard_failure() -> None:
    with pytest.raises(ValidationError, match="event_end"):
        EventTimeDraft(
            event_start=date(2026, 6, 26),
            event_end=date(2026, 6, 25),
            precision=TimePrecision.INTERVAL,
        )


def test_event_time_rejects_mixed_timezone_awareness_cleanly() -> None:
    with pytest.raises(ValidationError, match="compatible timezone awareness"):
        EventTime(
            event_start=datetime(2026, 6, 25, tzinfo=UTC),
            event_end=datetime(2026, 6, 26),
            precision=TimePrecision.INTERVAL,
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


def test_final_event_mention_allows_raw_unlocated_evidence() -> None:
    draft = mention_draft()
    mention = EventMention(
        mention_id="M1",
        message_id="MSG-1",
        evidence_records=[
            EvidenceRecord(
                segment_id="text:0",
                text="unlocated quote",
                status=EvidenceRecordStatus.TEXT_NOT_FOUND,
                error_code="evidence_text_not_found",
            )
        ],
        evidence_spans=[],
        canonical_proposition=draft.canonical_proposition,
        source_claim=draft.source_claim,
        event_family=draft.event_family,
        predicate=draft.predicate,
        participants=[],
        locations=[],
        time=EventTime(**draft.time.model_dump()),
        assertion_state=draft.assertion_state,
        quantities=[],
        open_attributes=[],
    )
    assert mention.evidence_spans == []
    assert mention.evidence_records[0].status is EvidenceRecordStatus.TEXT_NOT_FOUND
