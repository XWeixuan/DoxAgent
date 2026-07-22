from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from cdecr.contracts import (
    AssertionState,
    AtomicEvent,
    EventFamily,
    EventMention,
    EventPackage,
    EventTime,
    EvidenceSpan,
    Language,
    MembershipRelation,
    OpenIdentityFields,
    OpenIdentityProfile,
    PackageFamily,
    PackageKind,
    PackageMembership,
    PackageStatus,
    Participant,
    ParticipantRole,
    Predicate,
    SourceMessage,
    SourceType,
    TimePrecision,
)
from cdecr.registry import (
    DecisionAuditRecord,
    ImmutableRecordConflict,
    SQLiteCDECRRegistry,
    VersionConflict,
)


def source(message_id: str = "MSG-1") -> SourceMessage:
    return SourceMessage(
        message_id=message_id,
        source_type=SourceType.NEWS,
        title="Micron update",
        text="Micron raised guidance after stronger demand.",
        published_at=datetime(2026, 6, 25, 12, tzinfo=UTC),
        source_name="Wire",
        url="https://example.test/1",
        ticker_hints=["MU"],
        language=Language.EN,
    )


def mention(mention_id: str = "MENTION-1", message_id: str = "MSG-1") -> EventMention:
    return EventMention(
        mention_id=mention_id,
        message_id=message_id,
        evidence_spans=[EvidenceSpan(field="title", start_char=0, end_char=6, text="Micron")],
        canonical_proposition="Micron raised guidance.",
        source_claim=None,
        event_family=EventFamily.GUIDANCE_EXPECTATION,
        predicate=Predicate(raw="raised", normalized="raise_guidance"),
        participants=[
            Participant(surface="Micron", entity_id="COMPANY_MU", role=ParticipantRole.SUBJECT)
        ],
        locations=[],
        time=EventTime(event_start=date(2026, 6, 25), precision=TimePrecision.DAY),
        assertion_state=AssertionState.ACTUAL,
        quantities=[],
        open_attributes=[],
    )


def atomic(
    *, event_id: str = "EVENT-1", version: int = 1, mention_ids: list[str] | None = None
) -> AtomicEvent:
    ids = ["MENTION-1"] if mention_ids is None else mention_ids
    return AtomicEvent(
        event_id=event_id,
        canonical_proposition=f"Micron raised guidance v{version}.",
        event_family=EventFamily.GUIDANCE_EXPECTATION,
        identity_profile=OpenIdentityProfile(
            fields=OpenIdentityFields(
                normalized_predicate="raise guidance",
                principal_participant_ids=["COMPANY_MU"],
                event_time=EventTime(event_start=date(2026, 6, 25), precision=TimePrecision.DAY),
                location_or_asset_ids=[],
                assertion_state=AssertionState.ACTUAL,
            )
        ),
        time=EventTime(event_start=date(2026, 6, 25), precision=TimePrecision.DAY),
        assertion_state=AssertionState.ACTUAL,
        mention_ids=ids,
        representative_mention_ids=ids[:1],
        consensus_claims={},
        conflict_flags=[],
        version=version,
    )


def package(*, version: int = 1) -> EventPackage:
    return EventPackage(
        package_id="PACKAGE-1",
        package_kind=PackageKind.BOUNDED,
        package_family=PackageFamily.COMPANY_DISCLOSURE,
        canonical_title=f"Micron update v{version}",
        anchor_entities=["COMPANY_MU"],
        time_range={},
        member_event_ids=["EVENT-1"],
        canonical_summary="Micron disclosure.",
        status=PackageStatus.CLOSED,
        version=version,
    )


@pytest.fixture
def registry(tmp_path: Path) -> SQLiteCDECRRegistry:
    value = SQLiteCDECRRegistry(tmp_path / "cdecr.sqlite3")
    value.initialize()
    return value


def test_initialize_is_idempotent_and_survives_restart(registry: SQLiteCDECRRegistry) -> None:
    registry.initialize()
    assert registry.save_source(source(), fingerprint="a" * 64)
    restarted = SQLiteCDECRRegistry(registry.path)
    restarted.initialize()
    assert not restarted.save_source(source(), fingerprint="a" * 64)
    assert restarted.pragma_state()["user_version"] == 5
    assert restarted.pragma_state()["foreign_keys"] == 1
    assert str(restarted.pragma_state()["journal_mode"]).lower() == "wal"


def test_source_and_mention_are_immutable_and_idempotent(
    registry: SQLiteCDECRRegistry,
) -> None:
    registry.save_source(source(), fingerprint="a" * 64)
    assert registry.save_mention(mention())
    assert not registry.save_mention(mention())
    changed = mention().model_copy(update={"canonical_proposition": "Changed claim."})
    with pytest.raises(ImmutableRecordConflict):
        registry.save_mention(changed)
    with pytest.raises(ImmutableRecordConflict):
        registry.save_source(source().model_copy(update={"title": "Changed"}), fingerprint="b" * 64)


def test_foreign_keys_are_enforced(registry: SQLiteCDECRRegistry) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        registry.save_mention(mention(message_id="MISSING"))


def test_atomic_and_package_require_contiguous_versions(
    registry: SQLiteCDECRRegistry,
) -> None:
    registry.save_source(source(), fingerprint="a" * 64)
    registry.save_mention(mention())
    assert registry.save_atomic_event(atomic(version=1))
    assert not registry.save_atomic_event(atomic(version=1))
    with pytest.raises(VersionConflict):
        registry.save_atomic_event(atomic(version=3))
    assert registry.save_atomic_event(atomic(version=2))
    assert registry.save_package(package(version=1))
    assert registry.save_package(package(version=2))


def test_failed_atomic_fk_insert_rolls_back_head(registry: SQLiteCDECRRegistry) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        registry.save_atomic_event(atomic(event_id="EVENT-X", mention_ids=["MISSING"]))
    registry.save_source(source(), fingerprint="a" * 64)
    registry.save_mention(mention())
    assert registry.save_atomic_event(atomic(event_id="EVENT-X"))


def test_membership_requires_existing_event_and_package(
    registry: SQLiteCDECRRegistry,
) -> None:
    membership = PackageMembership(
        membership_id="MEMBER-1",
        event_id="EVENT-1",
        package_id="PACKAGE-1",
        relation=MembershipRelation.DISCLOSED_IN,
    )
    with pytest.raises(sqlite3.IntegrityError):
        registry.save_membership(membership)
    registry.save_source(source(), fingerprint="a" * 64)
    registry.save_mention(mention())
    registry.save_atomic_event(atomic())
    registry.save_package(package())
    assert registry.save_membership(membership)
    assert not registry.save_membership(membership)


def test_embedding_float32_round_trip_and_idempotency(
    registry: SQLiteCDECRRegistry,
) -> None:
    vector = [0.1, -0.25, 3.5]
    assert registry.save_embedding(
        owner_kind="mention",
        owner_id="MENTION-1",
        model="text-embedding-v4",
        input_hash="f" * 64,
        vector=vector,
        embedding_id="EMBED-1",
    )
    assert not registry.save_embedding(
        owner_kind="mention",
        owner_id="MENTION-1",
        model="text-embedding-v4",
        input_hash="f" * 64,
        vector=vector,
    )
    assert not registry.save_embedding(
        owner_kind="mention",
        owner_id="MENTION-1",
        model="text-embedding-v4",
        input_hash="f" * 64,
        vector=[0.10001, -0.24999, 3.50001],
    )
    restored = registry.get_embedding(
        owner_kind="mention",
        owner_id="MENTION-1",
        model="text-embedding-v4",
        input_hash="f" * 64,
    )
    assert restored is not None
    assert restored.dimension == 3
    assert restored.vector == pytest.approx(vector, rel=1e-6)


def test_decision_audit_is_append_only_and_idempotent(
    registry: SQLiteCDECRRegistry,
) -> None:
    record = DecisionAuditRecord(
        audit_id="AUDIT-1",
        decision_type="SOURCE_REJECTION",
        subject_id="ROW-1",
        payload={"reason_codes": ["missing_title"]},
    )
    assert registry.append_decision_audit(record)
    assert not registry.append_decision_audit(record)
    with pytest.raises(ImmutableRecordConflict):
        registry.append_decision_audit(
            record.model_copy(update={"payload": {"reason_codes": ["changed"]}})
        )
