from __future__ import annotations

import hashlib
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from cdecr.canonical_field_resolution import package_field_links_hash
from cdecr.contracts import (
    EventFamily,
    EventTime,
    LocalPackageHint,
    MembershipDecisionAction,
    MembershipRelation,
    PackageMembership,
    PackageMembershipDecision,
    PackageQualityState,
    PackageTimeRange,
    Participant,
    ParticipantRole,
    TimePrecision,
)
from cdecr.coreference_rules import (
    package_seed_for_event,
    singleton_atomic_event,
    singleton_package,
)
from cdecr.cross_document_contracts import (
    PackageMergePlan,
    PackagePairMergeDecision,
)
from cdecr.field_coreference_contracts import (
    CanonicalFieldLink,
    CanonicalFieldRegistryEntry,
    FieldLinkMethod,
    FieldNamespace,
)
from cdecr.package_engine import (
    PackageBoundaryGate,
    PackageProfileCompiler,
    atomic_surface_evidence,
    package_is_n13_repairable,
    package_retrieval_hash,
    package_retrieval_text,
    representative_package_members,
    request_local_source_ids,
)
from cdecr.registry import RegistryError, SQLiteCDECRRegistry, VersionConflict
from tests.cdecr.test_registry import mention, source


@pytest.fixture
def registry(tmp_path: Path) -> SQLiteCDECRRegistry:
    value = SQLiteCDECRRegistry(tmp_path / "package-v13.sqlite3")
    value.initialize()
    return value


def _save_event(registry: SQLiteCDECRRegistry, suffix: str):
    source_value = source().model_copy(
        update={
            "message_id": f"MESSAGE-{suffix}",
            "url": f"https://example.test/{suffix}",
        }
    )
    mention_value = mention().model_copy(
        update={
            "mention_id": f"MENTION-{suffix}",
            "message_id": source_value.message_id,
            "canonical_proposition": f"Micron event {suffix}.",
        }
    )
    registry.save_source(source_value, fingerprint=(suffix.lower()[0] * 64))
    registry.save_mention(mention_value)
    event = singleton_atomic_event(mention_value)
    registry.save_atomic_event(event)
    return source_value, mention_value, event


def _start_cross_run(
    registry: SQLiteCDECRRegistry,
    message_id: str,
    run_id: str,
) -> None:
    assert registry.start_cross_document_run(
        run_id=run_id,
        processing_key=f"key-{run_id}",
        message_id=message_id,
        engine_version="v13",
        prompt_version="v7",
        model_config={"m2": "fake", "m3": "fake"},
    )


def test_package_retrieval_hash_matches_exact_embedding_text() -> None:
    mention_value = mention()
    first = singleton_atomic_event(mention_value)
    seed = package_seed_for_event(first, [mention_value])
    package = singleton_package(first, seed)
    text = package_retrieval_text(package, [first])

    assert (
        package_retrieval_hash(package, [first]) == hashlib.sha256(text.encode("utf-8")).hexdigest()
    )

    second = first.model_copy(
        update={
            "event_id": "atomic:second",
            "canonical_proposition": "Micron also reported EPS.",
            "mention_ids": ["MENTION-2"],
            "representative_mention_ids": ["MENTION-2"],
        }
    )
    updated = PackageProfileCompiler().compile(package, [first, second])
    updated_hash = package_retrieval_hash(updated, representative_package_members([first, second]))
    assert updated_hash != package_retrieval_hash(package, [first])


def test_n12_representatives_prefer_task_relevant_member_without_growing_card() -> None:
    first = singleton_atomic_event(mention()).model_copy(update={"event_id": "atomic:a"})
    second = first.model_copy(
        update={
            "event_id": "atomic:z",
            "event_family": EventFamily.MARKET_MOVEMENT,
            "canonical_proposition": "Micron shares moved after the disclosure.",
            "mention_ids": ["MENTION-Z"],
            "representative_mention_ids": ["MENTION-Z"],
        }
    )
    preferred = second.model_copy(update={"event_id": "atomic:incoming"})

    selected = representative_package_members(
        [first, second],
        limit=1,
        preferred_events=[preferred],
    )

    assert [event.event_id for event in selected] == [second.event_id]


def test_package_boundary_gate_separates_quality_from_lifecycle() -> None:
    first = singleton_atomic_event(mention())
    reaction = first.model_copy(
        update={
            "event_id": "atomic:reaction",
            "event_family": EventFamily.MARKET_MOVEMENT,
            "canonical_proposition": "Micron shares rose.",
            "mention_ids": ["MENTION-REACTION"],
            "representative_mention_ids": ["MENTION-REACTION"],
        }
    )
    package = singleton_package(first, package_seed_for_event(first, [mention()])).model_copy(
        update={"member_event_ids": [first.event_id, reaction.event_id]}
    )

    finding = PackageBoundaryGate().evaluate(package, [first, reaction])

    assert package.status.value == "UNKNOWN"
    assert finding.quality_state is PackageQualityState.ACTIVE
    assert "MARKET_REACTION_REQUIRES_N12_REEVALUATION" in finding.reasons
    assert finding.severity == "REVIEW_REQUIRED"


def test_package_field_links_hash_tracks_hint_and_redirect_root(
    registry: SQLiteCDECRRegistry,
) -> None:
    source_value = source()
    mention_value = mention().model_copy(
        update={
            "local_package_hint": LocalPackageHint(
                anchor="Micron earnings release",
                relation_to_anchor=MembershipRelation.DISCLOSED_IN,
            )
        }
    )
    registry.save_source(source_value, fingerprint="a" * 64)
    registry.save_mention(mention_value)
    provisional = CanonicalFieldRegistryEntry(
        id="FIELD:PACKAGE:PROVISIONAL",
        namespace=FieldNamespace.PACKAGE_ANCHOR,
        canonical_text="Micron earnings release",
        aliases=["Micron earnings release"],
    )
    trusted = CanonicalFieldRegistryEntry(
        id="FIELD:PACKAGE:TRUSTED",
        namespace=FieldNamespace.PACKAGE_ANCHOR,
        canonical_text="Micron FY2026 earnings release",
        aliases=["Micron FY2026 earnings release"],
        external_id="ARTIFACT:MU:FY2026",
    )
    registry.create_field_registry_entry(provisional)
    registry.create_field_registry_entry(trusted)
    registry.save_field_link(
        CanonicalFieldLink(
            mention_id=mention_value.mention_id,
            field_path="local_package_hint.anchor",
            registry_id=provisional.id,
            method=FieldLinkMethod.INTERNAL_COREFERENCE,
        )
    )
    before = package_field_links_hash(registry, [mention_value], catalog_hash="catalog-v2")

    registry.save_field_redirect(provisional.id, trusted.id)
    after = package_field_links_hash(registry, [mention_value], catalog_hash="catalog-v2")

    assert before != after


def test_append_only_membership_projection_supports_add_move_remove(
    registry: SQLiteCDECRRegistry,
) -> None:
    _, mention_value, event = _save_event(registry, "A")
    first = singleton_package(event, package_seed_for_event(event, [mention_value]))
    second = first.model_copy(update={"package_id": "package:second"})
    registry.save_package(first)
    registry.save_package(second)

    registry.save_membership_decision(
        PackageMembershipDecision(
            decision_id="membership:add",
            action=MembershipDecisionAction.ADD,
            event_id=event.event_id,
            target_package_id=first.package_id,
            relation=MembershipRelation.DISCLOSED_IN,
            reason="initial placement",
        )
    )
    registry.save_membership_decision(
        PackageMembershipDecision(
            decision_id="membership:move",
            action=MembershipDecisionAction.MOVE,
            event_id=event.event_id,
            source_package_id=first.package_id,
            target_package_id=second.package_id,
            relation=MembershipRelation.DISCLOSED_IN,
            reason="corrected placement",
        )
    )
    assert registry.list_packages_for_event(event.event_id)[0].package_id == second.package_id
    registry.save_membership_decision(
        PackageMembershipDecision(
            decision_id="membership:remove",
            action=MembershipDecisionAction.REMOVE,
            event_id=event.event_id,
            source_package_id=second.package_id,
            reason="remove false member",
        )
    )

    assert registry.list_packages_for_event(event.event_id) == []
    assert [
        item.action for item in registry.list_membership_decisions(event_id=event.event_id)
    ] == [
        MembershipDecisionAction.ADD,
        MembershipDecisionAction.MOVE,
        MembershipDecisionAction.REMOVE,
    ]


def test_package_root_resolution_and_merge_plan_are_atomic(
    registry: SQLiteCDECRRegistry,
) -> None:
    first_source, first_mention, first_event = _save_event(registry, "A")
    _, second_mention, second_event = _save_event(registry, "B")
    first_package = singleton_package(
        first_event, package_seed_for_event(first_event, [first_mention])
    )
    second_package = singleton_package(
        second_event, package_seed_for_event(second_event, [second_mention])
    )
    registry.save_package(first_package)
    registry.save_package(second_package)
    registry.save_membership_decision(
        PackageMembershipDecision(
            decision_id="membership:first",
            action=MembershipDecisionAction.ADD,
            event_id=first_event.event_id,
            target_package_id=first_package.package_id,
            relation=MembershipRelation.DISCLOSED_IN,
            reason="initial",
        )
    )
    registry.save_membership_decision(
        PackageMembershipDecision(
            decision_id="membership:second",
            action=MembershipDecisionAction.ADD,
            event_id=second_event.event_id,
            target_package_id=second_package.package_id,
            relation=MembershipRelation.DISCLOSED_IN,
            reason="initial",
        )
    )
    run_id = "RUN-MERGE"
    _start_cross_run(registry, first_source.message_id, run_id)
    compiler = PackageProfileCompiler()
    merged = compiler.compile(first_package, [first_event, second_event]).model_copy(
        update={"version": first_package.version + 1}
    )
    decision = PackagePairMergeDecision(
        source_package_id=first_package.package_id,
        target_package_id=second_package.package_id,
        relation="SAME_PACKAGE",
        reason="same bounded disclosure",
    )
    plan = PackageMergePlan(
        plan_id="merge-plan:1",
        target_package_id=first_package.package_id,
        source_package_ids=[second_package.package_id],
        decision_ids=["merge-decision:1"],
        reason="same bounded disclosure",
    )
    text = package_retrieval_text(
        merged, representative_package_members([first_event, second_event])
    )
    registry.apply_package_merge_plan(
        plan=plan,
        decisions=[decision],
        merged_package=merged,
        run_id=run_id,
        embedding_model="fake-embedding",
        embedding_input_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        embedding_vector=[1.0, 0.0],
    )

    assert registry.resolve_package_root(second_package.package_id) == first_package.package_id
    assert registry.get_current_package(second_package.package_id) == merged
    assert len(registry.list_memberships_for_package(first_package.package_id)) == 2
    with pytest.raises((RegistryError, VersionConflict)):
        registry.apply_package_merge_plan(
            plan=plan.model_copy(update={"plan_id": "merge-plan:duplicate"}),
            decisions=[decision],
            merged_package=merged.model_copy(update={"version": merged.version + 1}),
            run_id=run_id,
            embedding_model="fake-embedding",
            embedding_input_hash="duplicate",
            embedding_vector=[1.0, 0.0],
        )
    assert registry.get_current_package(first_package.package_id) == merged
    with sqlite3.connect(registry.path) as connection:
        source_embeddings = connection.execute(
            """
            SELECT COUNT(*) FROM embeddings
            WHERE owner_kind = 'event_package' AND owner_id = ?
            """,
            (second_package.package_id,),
        ).fetchone()[0]
    assert source_embeddings == 0


def test_failed_merge_plan_leaves_no_partial_redirect(
    registry: SQLiteCDECRRegistry,
) -> None:
    first_source, first_mention, first_event = _save_event(registry, "C")
    _, second_mention, second_event = _save_event(registry, "D")
    first_package = singleton_package(
        first_event, package_seed_for_event(first_event, [first_mention])
    )
    second_package = singleton_package(
        second_event, package_seed_for_event(second_event, [second_mention])
    )
    registry.save_package(first_package)
    registry.save_package(second_package)
    _start_cross_run(registry, first_source.message_id, "RUN-ROLLBACK")
    invalid_merged = first_package.model_copy(
        update={
            "member_event_ids": [first_event.event_id, second_event.event_id],
            "version": first_package.version + 2,
        }
    )
    plan = PackageMergePlan(
        plan_id="merge-plan:rollback",
        target_package_id=first_package.package_id,
        source_package_ids=[second_package.package_id],
        decision_ids=["merge-decision:rollback"],
        reason="rollback probe",
    )
    decision = PackagePairMergeDecision(
        source_package_id=first_package.package_id,
        target_package_id=second_package.package_id,
        relation="SAME_PACKAGE",
        reason="rollback probe",
    )

    with pytest.raises(VersionConflict):
        registry.apply_package_merge_plan(
            plan=plan,
            decisions=[decision],
            merged_package=invalid_merged,
            run_id="RUN-ROLLBACK",
            embedding_model="fake",
            embedding_input_hash="hash",
            embedding_vector=[1.0],
        )

    assert registry.resolve_package_root(second_package.package_id) == second_package.package_id
    assert registry.get_current_package(first_package.package_id) == first_package


def test_v8_migration_rebuilds_profile_from_atomic_membership_and_field_root(
    registry: SQLiteCDECRRegistry,
) -> None:
    _, mention_value, event = _save_event(registry, "E")
    field = CanonicalFieldRegistryEntry(
        id="FIELD:PACKAGE:V8",
        namespace=FieldNamespace.PACKAGE_ANCHOR,
        canonical_text="Micron earnings",
        aliases=["Micron earnings"],
        external_id="ARTIFACT:MU:V8",
    )
    registry.create_field_registry_entry(field)
    registry.save_field_link(
        CanonicalFieldLink(
            mention_id=mention_value.mention_id,
            field_path="local_package_hint.anchor",
            registry_id=field.id,
            method=FieldLinkMethod.INTERNAL_COREFERENCE,
        )
    )
    package = singleton_package(event, package_seed_for_event(event, [mention_value])).model_copy(
        update={
            "canonical_title": "stale title",
            "canonical_summary": "stale summary",
            "package_anchor_ids": [],
        }
    )
    registry.save_package(package)
    registry.save_membership(
        PackageMembership(
            membership_id="legacy-membership:v8",
            event_id=event.event_id,
            package_id=package.package_id,
            relation=MembershipRelation.DISCLOSED_IN,
        )
    )
    registry.save_embedding(
        owner_kind="event_package",
        owner_id=package.package_id,
        model="legacy",
        input_hash="legacy",
        vector=[1.0],
    )
    with sqlite3.connect(registry.path) as connection:
        connection.execute("PRAGMA user_version=7")
        connection.commit()

    registry.initialize()

    rebuilt = registry.get_current_package(package.package_id)
    assert rebuilt is not None
    assert rebuilt.version == package.version + 1
    assert rebuilt.canonical_title == event.canonical_proposition
    assert rebuilt.package_anchor_ids == [field.id]
    assert rebuilt.anchor_artifact_id == field.external_id
    assert (
        registry.get_embedding(
            owner_kind="event_package",
            owner_id=package.package_id,
            model="legacy",
            input_hash="legacy",
        )
        is None
    )


def test_surface_evidence_preserves_raw_surfaces_and_deduplicates_sources(
    registry: SQLiteCDECRRegistry,
) -> None:
    source_value = source().model_copy(
        update={
            "message_id": "MESSAGE-SURFACE",
            "title": "Counterpoint Optimus update",
            "text": "Counterpoint, Micron management and Optimus were mentioned.",
            "url": "https://example.test/surface",
        }
    )
    registry.save_source(source_value, fingerprint="f" * 64)
    participants = [
        Participant(
            surface="Counterpoint",
            entity_id=None,
            role=ParticipantRole.ACTOR,
        ),
        Participant(
            surface="Micron management",
            entity_id=None,
            role=ParticipantRole.SUBJECT,
        ),
        Participant(
            surface="Optimus",
            entity_id=None,
            role=ParticipantRole.TARGET,
        ),
    ]
    mentions = []
    for index in range(1, 4):
        value = mention(
            mention_id=f"MENTION-SURFACE-{index}",
            message_id=source_value.message_id,
        ).model_copy(
            update={
                "canonical_proposition": f"Surface probe {index}.",
                "participants": participants,
            }
        )
        registry.save_mention(value)
        mentions.append(value)
    mappings = [
        ("Counterpoint", "Counterpoint Funds"),
        ("Micron management", "Carson Management"),
        ("Optimus", "NVIDIA Optimus"),
    ]
    for index, (raw, canonical) in enumerate(mappings):
        entry = CanonicalFieldRegistryEntry(
            id=f"FIELD:SURFACE:{index}",
            namespace=FieldNamespace.PARTICIPANT_COMPANY,
            canonical_text=canonical,
            aliases=[raw, canonical],
        )
        registry.create_field_registry_entry(entry)
        for mention_value in mentions:
            registry.save_field_link(
                CanonicalFieldLink(
                    mention_id=mention_value.mention_id,
                    field_path=f"participants[{index}]",
                    registry_id=entry.id,
                    method=FieldLinkMethod.INTERNAL_COREFERENCE,
                )
            )
    event = singleton_atomic_event(mentions[0]).model_copy(
        update={
            "mention_ids": [item.mention_id for item in mentions],
            "representative_mention_ids": [item.mention_id for item in mentions],
        }
    )
    registry.save_atomic_event(event)

    short_ids = request_local_source_ids(registry, [event])
    evidence, source_ids = atomic_surface_evidence(registry, event, source_short_ids=short_ids)
    payload = evidence.model_dump(mode="json", exclude_none=True)

    assert source_ids == ["s1"]
    assert short_ids == {source_value.message_id: "s1"}
    flattened = {
        surface: canonical_id
        for participant in evidence.participants or []
        for surface, canonical_id in zip(
            participant.surfaces, participant.canonical_ids, strict=True
        )
    }
    assert flattened == {
        "Counterpoint": "FIELD:SURFACE:0",
        "Micron management": "FIELD:SURFACE:1",
        "Optimus": "FIELD:SURFACE:2",
    }
    assert set(payload) == {"participants"}
    assert "canonical_proposition" not in str(payload)
    assert "message_id" not in str(payload)
    assert "url" not in str(payload)


def test_profile_rebuild_is_current_membership_function_and_clears_removed_fields(
    registry: SQLiteCDECRRegistry,
) -> None:
    _, first_mention, first_event = _save_event(registry, "PROFILE-A")
    second_source = source().model_copy(
        update={
            "message_id": "MESSAGE-PROFILE-B",
            "url": "https://example.test/profile-b",
        }
    )
    second_mention = mention(
        mention_id="MENTION-PROFILE-B",
        message_id=second_source.message_id,
    ).model_copy(
        update={
            "canonical_proposition": "Current surviving disclosure.",
            "time": EventTime(
                event_start=date(2026, 7, 1),
                precision=TimePrecision.DAY,
                reference_period_id="FY2026-Q4",
            ),
            "local_package_hint": LocalPackageHint(
                anchor="Current filing",
                relation_to_anchor=MembershipRelation.DISCLOSED_IN,
            ),
        }
    )
    registry.save_source(second_source, fingerprint="b" * 64)
    registry.save_mention(second_mention)
    second_event = singleton_atomic_event(second_mention)
    registry.save_atomic_event(second_event)
    old_anchor = CanonicalFieldRegistryEntry(
        id="FIELD:OLD:ARTIFACT",
        namespace=FieldNamespace.ARTIFACT_REPORT,
        canonical_text="Old report",
        aliases=["Old report"],
        external_id="ARTIFACT:OLD",
    )
    current_anchor = CanonicalFieldRegistryEntry(
        id="FIELD:CURRENT:ARTIFACT",
        namespace=FieldNamespace.ARTIFACT_FILING,
        canonical_text="Current filing",
        aliases=["Current filing"],
        external_id="ARTIFACT:CURRENT",
    )
    current_period = CanonicalFieldRegistryEntry(
        id="FIELD:CURRENT:PERIOD",
        namespace=FieldNamespace.FISCAL_PERIOD,
        canonical_text="FY2026 Q4",
        aliases=["FY2026-Q4"],
    )
    for entry in (old_anchor, current_anchor, current_period):
        registry.create_field_registry_entry(entry)
    registry.save_field_link(
        CanonicalFieldLink(
            mention_id=first_mention.mention_id,
            field_path="local_package_hint.anchor",
            registry_id=old_anchor.id,
            method=FieldLinkMethod.INTERNAL_COREFERENCE,
        )
    )
    registry.save_field_link(
        CanonicalFieldLink(
            mention_id=second_mention.mention_id,
            field_path="local_package_hint.anchor",
            registry_id=current_anchor.id,
            method=FieldLinkMethod.INTERNAL_COREFERENCE,
        )
    )
    registry.save_field_link(
        CanonicalFieldLink(
            mention_id=second_mention.mention_id,
            field_path="time.reference_period_id",
            registry_id=current_period.id,
            method=FieldLinkMethod.INTERNAL_COREFERENCE,
        )
    )
    root = singleton_package(
        first_event, package_seed_for_event(first_event, [first_mention])
    ).model_copy(
        update={
            "canonical_title": "Old stale title",
            "anchor_entities": ["STALE_ENTITY"],
            "package_anchor_ids": [old_anchor.id],
            "anchor_artifact_id": old_anchor.external_id,
            "anchor_period_id": "OLD_PERIOD",
        }
    )
    compiler = PackageProfileCompiler(registry)
    expanded = compiler.compile(root, [first_event, second_event])
    rebuilt = compiler.compile(expanded, [second_event])
    rebuilt_again = compiler.compile(rebuilt, [second_event])

    assert rebuilt.canonical_title == "Current surviving disclosure."
    assert rebuilt.package_anchor_ids == [current_anchor.id]
    assert rebuilt.anchor_artifact_id == current_anchor.external_id
    assert rebuilt.anchor_period_id == current_period.id
    assert rebuilt.time_range.start == date(2026, 7, 1)
    assert rebuilt.time_range.end is None
    assert first_event.event_id not in rebuilt.member_event_ids
    assert "STALE_ENTITY" not in rebuilt.anchor_entities
    assert old_anchor.id not in rebuilt.package_anchor_ids
    assert rebuilt_again == rebuilt


def test_boundary_legacy_heuristics_are_nonblocking_and_frozen_is_repairable() -> None:
    base = singleton_atomic_event(mention())
    families = [
        EventFamily.FINANCIAL_PERFORMANCE,
        EventFamily.GUIDANCE_EXPECTATION,
        EventFamily.TRANSACTION_CAPITAL,
        EventFamily.COMMERCIAL_OPERATION,
        EventFamily.PRODUCTION_SUPPLY,
    ]
    members = [
        base.model_copy(
            update={
                "event_id": f"EVENT-LARGE-{index}",
                "event_family": families[index % len(families)],
            }
        )
        for index in range(26)
    ]
    package = singleton_package(base, package_seed_for_event(base, [mention()])).model_copy(
        update={
            "member_event_ids": [item.event_id for item in members],
            "time_range": PackageTimeRange(start=date(2025, 1, 1), end=date(2026, 7, 1)),
        }
    )
    finding = PackageBoundaryGate().evaluate(package, members)

    assert finding.quality_state is PackageQualityState.ACTIVE
    assert finding.severity == "WARNING"
    assert "PACKAGE_MEMBER_COUNT_WARNING" in finding.reasons
    assert "PACKAGE_TIME_SPAN_WARNING" in finding.reasons
    assert "BOUNDED_IDENTITY_HETEROGENEITY" not in finding.reasons
    frozen = package.model_copy(update={"quality_state": PackageQualityState.FROZEN})
    assert package_is_n13_repairable(package)
    assert package_is_n13_repairable(frozen)


def test_boundary_anchor_severity_uses_resolved_trust(
    registry: SQLiteCDECRRegistry,
) -> None:
    event = singleton_atomic_event(mention())
    base = singleton_package(event, package_seed_for_event(event, [mention()]))
    provisional = []
    trusted = []
    for index in range(2):
        provisional_entry = CanonicalFieldRegistryEntry(
            id=f"FIELD:PROVISIONAL:{index}",
            namespace=FieldNamespace.PACKAGE_ANCHOR,
            canonical_text=f"Provisional {index}",
            aliases=[f"Provisional {index}"],
        )
        trusted_entry = provisional_entry.model_copy(
            update={
                "id": f"FIELD:TRUSTED:{index}",
                "canonical_text": f"Trusted {index}",
                "aliases": [f"Trusted {index}"],
                "external_id": f"ARTIFACT:{index}",
            }
        )
        registry.create_field_registry_entry(provisional_entry)
        registry.create_field_registry_entry(trusted_entry)
        provisional.append(provisional_entry.id)
        trusted.append(trusted_entry.id)
    review = PackageBoundaryGate(registry).evaluate(
        base.model_copy(update={"package_anchor_ids": provisional}),
        [event],
    )
    blocking = PackageBoundaryGate(registry).evaluate(
        base.model_copy(update={"package_anchor_ids": trusted}),
        [event],
    )
    recovered = PackageBoundaryGate(registry).evaluate(
        base.model_copy(update={"quality_state": PackageQualityState.FROZEN}),
        [event],
    )

    assert review.quality_state is PackageQualityState.ACTIVE
    assert review.severity == "REVIEW_REQUIRED"
    assert blocking.quality_state is PackageQualityState.QUARANTINED
    assert blocking.severity == "BLOCKING_CONFLICT"
    assert recovered.quality_state is PackageQualityState.ACTIVE
