"""Frozen-snapshot N12 Wave A/B planning and deterministic Package reduction."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import TYPE_CHECKING

from cdecr.bulk_epoch.indexes import MultiKeyBoundedIndex
from cdecr.bulk_epoch.writer import BulkWriter
from cdecr.contracts import (
    AtomicEvent,
    EventMention,
    EventPackage,
    PackageAction,
    PackageAssignmentRelation,
    PackageQualityState,
)
from cdecr.coreference_rules import package_hard_conflicts, stable_id
from cdecr.cross_document_contracts import (
    PackageAssignmentDecision,
    PackageAssignmentRecord,
    PackageCandidate,
    PackageSeed,
    RecallRoute,
)
from cdecr.package_engine import PackageProfileCompiler, canonical_package_members
from cdecr.ports import DecisionAuditRecord

if TYPE_CHECKING:
    from cdecr.cross_document import CrossDocumentEngine, _AuditedModels


def _wave_b_candidates(
    *,
    engine: CrossDocumentEngine,
    event: AtomicEvent,
    seed: PackageSeed,
    provisional: dict[str, EventPackage],
    events: dict[str, AtomicEvent],
    seeds: dict[str, PackageSeed],
    scheduler_ids: Sequence[str],
) -> list[PackageCandidate]:
    ranked: list[PackageCandidate] = []
    for owner_event_id in scheduler_ids:
        package = provisional[owner_event_id]
        if owner_event_id == event.event_id:
            continue
        owner_seed = seeds[owner_event_id]
        routes: set[RecallRoute] = set()
        if seed.anchor_artifact_id and seed.anchor_artifact_id == owner_seed.anchor_artifact_id:
            routes.add(RecallRoute.CANONICAL_ARTIFACT)
        if set(seed.package_anchor_ids).intersection(owner_seed.package_anchor_ids):
            routes.add(RecallRoute.PACKAGE_ANCHOR)
        if engine._events_share_parent_context(event, events[owner_event_id]):
            routes.add(RecallRoute.PARENT_CONTEXT)
        if (
            seed.package_kind is owner_seed.package_kind
            and seed.package_family is owner_seed.package_family
        ):
            routes.add(RecallRoute.PACKAGE_KIND_FAMILY)
        if set(seed.anchor_entities).intersection(owner_seed.anchor_entities):
            routes.add(RecallRoute.CORE_ENTITY)
        if seed.anchor_period_id and seed.anchor_period_id == owner_seed.anchor_period_id:
            routes.add(RecallRoute.TIME_WINDOW)
        strong_parent = bool(
            routes.intersection(
                {
                    RecallRoute.CANONICAL_ARTIFACT,
                    RecallRoute.PACKAGE_ANCHOR,
                    RecallRoute.PARENT_CONTEXT,
                }
            )
        )
        compatible_context = {
            RecallRoute.PACKAGE_KIND_FAMILY,
            RecallRoute.CORE_ENTITY,
            RecallRoute.TIME_WINDOW,
        }.issubset(routes)
        if not strong_parent and not compatible_context:
            continue
        conflicts = package_hard_conflicts(seed, package)
        ranked.append(
            PackageCandidate(
                package=package,
                recall_routes=sorted(routes, key=str),
                recall_score=min(1.0, 0.35 + 0.12 * len(routes)),
                hard_conflicts=conflicts,
            )
        )
    ranked.sort(
        key=lambda item: (
            bool(item.hard_conflicts),
            -item.recall_score,
            item.package.package_id,
        )
    )
    return ranked[:6]


def _seed_index_keys(seed: PackageSeed) -> list[str]:
    kind_family = f"kf:{seed.package_kind.value}:{seed.package_family.value}"
    keys = [kind_family]
    if seed.anchor_artifact_id:
        keys.append(f"artifact:{seed.anchor_artifact_id}")
    keys.extend(f"anchor:{value}" for value in seed.package_anchor_ids)
    keys.extend(f"entity:{kind_family}:{value}" for value in seed.anchor_entities)
    if seed.anchor_period_id:
        keys.append(f"period:{kind_family}:{seed.anchor_period_id}")
        keys.extend(
            f"entity-period:{value}:{seed.anchor_period_id}"
            for value in seed.anchor_entities
        )
    return keys


def _constrained_components(
    event_ids: Sequence[str],
    decisions: dict[str, PackageAssignmentDecision],
    owner_by_package: dict[str, str],
) -> list[list[str]]:
    parent = {event_id: event_id for event_id in event_ids}
    members = {event_id: {event_id} for event_id in event_ids}
    explicit_not: set[frozenset[str]] = set()
    same_edges: list[tuple[str, str]] = []

    def root(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    for event_id, decision in decisions.items():
        for assessment in decision.candidate_assessments:
            owner = owner_by_package.get(assessment.candidate_package_id)
            if owner is None or owner == event_id:
                continue
            pair = frozenset((event_id, owner))
            if assessment.relation is PackageAssignmentRelation.MEMBER:
                same_edges.append((event_id, owner))
            elif assessment.relation is PackageAssignmentRelation.NOT_RELATED:
                explicit_not.add(pair)

    for left, right in sorted(same_edges):
        left_root, right_root = root(left), root(right)
        if left_root == right_root:
            continue
        if any(
            frozenset((left_member, right_member)) in explicit_not
            for left_member in members[left_root]
            for right_member in members[right_root]
        ):
            continue
        target, source = sorted((left_root, right_root))
        parent[source] = target
        members[target].update(members.pop(source))
    return [sorted(group) for _, group in sorted(members.items())]


def assign_packages_epoch(
    *,
    engine: CrossDocumentEngine,
    events: list[AtomicEvent],
    mentions: list[EventMention],
    models: _AuditedModels,
    run_id: str,
    candidate_counts: dict[str, int],
    base_packages: Sequence[EventPackage],
    writer: BulkWriter,
) -> tuple[list[EventPackage], list[PackageAssignmentRecord], dict[str, object]]:
    """Run N12 against one immutable history snapshot, then cluster new seeds in Wave B."""

    compiler = PackageProfileCompiler(engine.registry)
    event_map = {event.event_id: event for event in events}
    mention_map = {mention.mention_id: mention for mention in mentions}
    active_packages = {
        package.package_id: package
        for package in base_packages
        if package.quality_state is PackageQualityState.ACTIVE
    }
    package_embeddings = engine._sync_package_embeddings(list(active_packages.values()), models)
    atomic_embeddings = {
        item.owner_id: item.vector
        for item in engine.registry.list_latest_embeddings(
            owner_kind="atomic_event", model=engine.model_m1, limit=100000
        )
    }
    seeds: dict[str, PackageSeed] = {}
    keys: dict[str, tuple[str, str, str]] = {}
    wave_a_candidates: dict[str, list[PackageCandidate]] = {}
    for event in sorted(events, key=lambda item: item.event_id):
        event_mentions = [
            mention
            for mention_id in event.mention_ids
            if (mention := mention_map.get(mention_id) or engine.registry.get_mention(mention_id))
            is not None
        ]
        seed, assignment_key, seed_hash, links_hash = engine._package_seed_and_key(
            event, event_mentions
        )
        seeds[event.event_id] = seed
        keys[event.event_id] = (assignment_key, seed_hash, links_hash)
        wave_a_candidates[event.event_id] = engine._package_candidates_v13(
            event,
            seed,
            active_packages=active_packages,
            package_embeddings=package_embeddings,
            atomic_embeddings=atomic_embeddings,
            atomic_vector=atomic_embeddings.get(event.event_id),
            existing_packages=engine.registry.list_packages_for_event(event.event_id),
            run_id=run_id,
            candidate_counts=candidate_counts,
        )
    wave_a_unresolved = {
        event_id: [candidate for candidate in values if not candidate.hard_conflicts]
        for event_id, values in wave_a_candidates.items()
        if any(not candidate.hard_conflicts for candidate in values)
    }
    wave_a = engine._joint_package_decisions(
        unresolved=wave_a_unresolved,
        events=event_map,
        seeds=seeds,
        models=models,
    )
    selected_history = {
        event_id: decision.selected_member_package_id
        for event_id, decision in wave_a.items()
        if decision.selected_member_package_id is not None
    }
    unmatched_ids = [event_id for event_id in sorted(event_map) if event_id not in selected_history]
    provisional = {
        event_id: compiler.compile_singleton(event_map[event_id], seeds[event_id])
        for event_id in unmatched_ids
    }
    wave_b_index = MultiKeyBoundedIndex()
    for event_id in unmatched_ids:
        wave_b_index.add(event_id, _seed_index_keys(seeds[event_id]))
    wave_b_candidates = {
        event_id: _wave_b_candidates(
            engine=engine,
            event=event_map[event_id],
            seed=seeds[event_id],
            provisional=provisional,
            events=event_map,
            seeds=seeds,
            scheduler_ids=wave_b_index.query(
                _seed_index_keys(seeds[event_id]),
                limit=24,
                exclude=event_id,
            ),
        )
        for event_id in unmatched_ids
    }
    wave_b_unresolved = {
        event_id: [candidate for candidate in values if not candidate.hard_conflicts]
        for event_id, values in wave_b_candidates.items()
        if any(not candidate.hard_conflicts for candidate in values)
    }
    wave_b = engine._joint_package_decisions(
        unresolved=wave_b_unresolved,
        events=event_map,
        seeds=seeds,
        models=models,
    )
    owner_by_package = {package.package_id: event_id for event_id, package in provisional.items()}
    new_components = _constrained_components(unmatched_ids, wave_b, owner_by_package)

    history_members: dict[str, list[str]] = defaultdict(list)
    for event_id, package_id in selected_history.items():
        history_members[package_id].append(event_id)
    packages: dict[str, EventPackage] = {}
    resulting_by_event: dict[str, EventPackage] = {}
    for package_id, incoming_ids in sorted(history_members.items()):
        target = active_packages[package_id]
        existing_events = canonical_package_members(engine.registry, target)
        incoming_events = [event_map[event_id] for event_id in incoming_ids]
        rebuilt = compiler.compile(target, [*existing_events, *incoming_events])
        packages[rebuilt.package_id] = rebuilt
        for event_id in incoming_ids:
            resulting_by_event[event_id] = rebuilt
    for component in new_components:
        root_event_id = component[0]
        root = provisional[root_event_id]
        rebuilt = compiler.compile(
            root,
            [event_map[event_id] for event_id in component],
            seed=seeds[root_event_id],
            force_version=1,
        )
        packages[rebuilt.package_id] = rebuilt
        for event_id in component:
            resulting_by_event[event_id] = rebuilt

    def commit() -> list[PackageAssignmentRecord]:
        assignments: list[PackageAssignmentRecord] = []
        for package in sorted(packages.values(), key=lambda item: item.package_id):
            engine.registry.save_package(package)
        for event_id in sorted(event_map):
            event = event_map[event_id]
            package = resulting_by_event[event_id]
            decision = wave_a.get(event_id) or wave_b.get(event_id)
            selected = selected_history.get(event_id)
            if selected is not None:
                action = PackageAction.ADD_TO_PACKAGE
                relation = PackageAssignmentRelation.MEMBER
                reason = "N12_WAVE_A_SELECTED_HISTORY"
            elif len(package.member_event_ids) > 1:
                action = PackageAction.ADD_TO_PACKAGE
                relation = PackageAssignmentRelation.MEMBER
                reason = "N12_WAVE_B_MEMBER"
            else:
                action = PackageAction.CREATE_NEW_PACKAGE
                relation = PackageAssignmentRelation.NOT_RELATED
                reason = (
                    "N12_UNJUDGEABLE_FAILED_SINGLETON"
                    if wave_b_candidates.get(event_id) and decision is None
                    else "N12_WAVE_B_SINGLETON"
                )
            engine._save_membership(event, package, seeds[event_id].membership_relation)
            assignment_key, seed_hash, links_hash = keys[event_id]
            record = engine._package_record(
                run_id=run_id,
                event=event,
                action=action,
                relation=relation,
                candidate_package_id=selected,
                resulting_package_id=package.package_id,
                candidate_assessments=(decision.candidate_assessments if decision else []),
                ranked_member_package_ids=(decision.ranked_member_package_ids if decision else []),
                selected_member_package_id=selected,
                selection_reason=(decision.selection_reason if selected and decision else None),
                package_assignment_key=assignment_key,
                package_seed_hash=seed_hash,
                package_field_links_hash=links_hash,
                reason=reason,
            )
            engine.registry.save_package_assignment(record)
            engine.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=stable_id("audit", {"assignment": record.assignment_id}),
                    run_id=run_id,
                    decision_type="PACKAGE_ASSIGNMENT",
                    subject_id=event_id,
                    payload={
                        "assignment": record.model_dump(mode="json"),
                        "wave": "A" if selected is not None else "B",
                        "snapshot_semantics": "IMMUTABLE",
                    },
                )
            )
            assignments.append(record)
        return assignments

    assignments = writer.run(commit)
    telemetry: dict[str, object] = {
        "wave_a_task_count": len(wave_a_unresolved),
        "wave_b_task_count": len(wave_b_unresolved),
        "new_component_count": len(new_components),
        "largest_new_component": max((len(value) for value in new_components), default=0),
        "wave_b_scheduler_edges": wave_b_index.stats().emitted_edges,
        "wave_b_scheduler_edge_cap": 24 * len(unmatched_ids),
    }
    return list(packages.values()), assignments, telemetry
