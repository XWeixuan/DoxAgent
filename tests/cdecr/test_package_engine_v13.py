from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from cdecr.canonical_field_resolution import package_field_links_hash
from cdecr.contracts import (
    EventFamily,
    LocalPackageHint,
    MembershipDecisionAction,
    MembershipRelation,
    PackageMembership,
    PackageMembershipDecision,
    PackageQualityState,
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
    package_retrieval_hash,
    package_retrieval_text,
    representative_package_members,
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

    assert package_retrieval_hash(package, [first]) == hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()

    second = first.model_copy(
        update={
            "event_id": "atomic:second",
            "canonical_proposition": "Micron also reported EPS.",
            "mention_ids": ["MENTION-2"],
            "representative_mention_ids": ["MENTION-2"],
        }
    )
    updated = PackageProfileCompiler().compile(package, [first, second])
    updated_hash = package_retrieval_hash(
        updated, representative_package_members([first, second])
    )
    assert updated_hash != package_retrieval_hash(package, [first])


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
    assert finding.quality_state is PackageQualityState.FROZEN
    assert "REACTION_EVENT_INSIDE_PACKAGE_BOUNDARY" in finding.reasons


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
    before = package_field_links_hash(
        registry, [mention_value], catalog_hash="catalog-v2"
    )

    registry.save_field_redirect(provisional.id, trusted.id)
    after = package_field_links_hash(
        registry, [mention_value], catalog_hash="catalog-v2"
    )

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
    merged = compiler.compile(
        first_package, [first_event, second_event]
    ).model_copy(update={"version": first_package.version + 1})
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
    package = singleton_package(
        event, package_seed_for_event(event, [mention_value])
    ).model_copy(
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
    assert registry.get_embedding(
        owner_kind="event_package",
        owner_id=package.package_id,
        model="legacy",
        input_hash="legacy",
    ) is None
