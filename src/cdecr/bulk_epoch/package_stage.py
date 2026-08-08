"""Frozen-snapshot N12 Wave A/B planning and deterministic Package reduction."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from functools import partial
from typing import TYPE_CHECKING, cast

from cdecr.bulk_epoch.artifacts import canonical_hash
from cdecr.bulk_epoch.indexes import MultiKeyBoundedIndex
from cdecr.bulk_epoch.stage_runtime import StageReadSnapshot
from cdecr.bulk_epoch.task_ledger import BulkTaskLedger
from cdecr.bulk_epoch.writer import BulkWriter
from cdecr.contracts import (
    AtomicEvent,
    EventMention,
    EventPackage,
    PackageAction,
    PackageAssignmentRelation,
    PackageMembership,
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
from cdecr.package_engine import PackageProfileCompiler
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
            f"entity-period:{value}:{seed.anchor_period_id}" for value in seed.anchor_entities
        )
    return keys


def _constrained_components(
    event_ids: Sequence[str],
    decisions: dict[str, PackageAssignmentDecision],
    owner_by_package: dict[str, str],
    *,
    hard_negative_pairs: set[frozenset[str]] | None = None,
) -> list[list[str]]:
    parent = {event_id: event_id for event_id in event_ids}
    members = {event_id: {event_id} for event_id in event_ids}
    hard_negative_pairs = hard_negative_pairs or set()
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
            if assessment.relation is PackageAssignmentRelation.MEMBER:
                same_edges.append((event_id, owner))

    for left, right in sorted(same_edges):
        left_root, right_root = root(left), root(right)
        if left_root == right_root:
            continue
        if any(
            frozenset((left_member, right_member)) in hard_negative_pairs
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
    ledger: BulkTaskLedger | None = None,
    task_snapshot_hash: str | None = None,
    use_embedding_batch_executor: bool = True,
    chunked_apply: bool = True,
) -> tuple[list[EventPackage], list[PackageAssignmentRecord], dict[str, object]]:
    """Run N12 against one immutable history snapshot, then cluster new seeds in Wave B."""

    stage_snapshot = StageReadSnapshot.load(
        engine.registry,
        packages=base_packages,
        embedding_owner_kind="atomic_event",
        embedding_model=engine.model_m1,
    )
    compiler = PackageProfileCompiler(engine.registry)
    event_map = {event.event_id: event for event in events}
    mention_map = {mention.mention_id: mention for mention in mentions}
    active_packages = {
        package.package_id: package
        for package in base_packages
        if package.quality_state is PackageQualityState.ACTIVE
    }
    package_embeddings = engine._sync_package_embeddings(
        list(active_packages.values()),
        models,
        use_batch_executor=use_embedding_batch_executor,
    )
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
            if (
                mention := mention_map.get(mention_id)
                or stage_snapshot.mentions_by_id.get(mention_id)
            )
            is not None
        ]
        seed, assignment_key, seed_hash, links_hash = engine._package_seed_and_key(
            event,
            event_mentions,
            read_snapshot=stage_snapshot,
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
            existing_packages=[
                active_packages[package_id]
                for package_id in stage_snapshot.package_ids_by_event.get(event.event_id, ())
                if package_id in active_packages
            ],
            run_id=run_id,
            candidate_counts=candidate_counts,
        )
    wave_a_unresolved = {
        event_id: [candidate for candidate in values if not candidate.hard_conflicts]
        for event_id, values in wave_a_candidates.items()
        if any(not candidate.hard_conflicts for candidate in values)
    }
    task_transactions = 0
    if ledger is not None and task_snapshot_hash is not None:
        result = ledger.start_many(
            [
                {
                    "stage": "N12_A",
                    "task_id": event_id,
                    "input_hash": canonical_hash({"event_id": event_id, "wave": "A"}),
                    "snapshot_hash": task_snapshot_hash,
                }
                for event_id in sorted(event_map)
            ]
        )
        task_transactions += result["transactions"]
    wave_a = engine._joint_package_decisions(
        unresolved=wave_a_unresolved,
        events=event_map,
        seeds=seeds,
        models=models,
    )
    if ledger is not None and task_snapshot_hash is not None:
        result = ledger.finish_many(
            [
                {
                    "stage": "N12_A",
                    "task_id": event_id,
                    "input_hash": canonical_hash({"event_id": event_id, "wave": "A"}),
                    "snapshot_hash": task_snapshot_hash,
                    "decision_ref": {
                        "decision": (
                            wave_a[event_id].model_dump(mode="json")
                            if event_id in wave_a
                            else {"deterministic": "NO_HISTORY_MEMBER"}
                        )
                    },
                }
                for event_id in sorted(event_map)
            ]
        )
        task_transactions += result["transactions"]
    selected_history = {
        event_id: decision.selected_member_package_id
        for event_id, decision in wave_a.items()
        if decision.selected_member_package_id is not None
    }
    unmatched_ids = [event_id for event_id in sorted(event_map) if event_id not in selected_history]
    provisional = {
        event_id: compiler.compile_singleton(
            event_map[event_id], seeds[event_id], read_snapshot=stage_snapshot
        )
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
    if ledger is not None and task_snapshot_hash is not None and unmatched_ids:
        result = ledger.start_many(
            [
                {
                    "stage": "N12_B",
                    "task_id": event_id,
                    "input_hash": canonical_hash({"event_id": event_id, "wave": "B"}),
                    "snapshot_hash": task_snapshot_hash,
                }
                for event_id in unmatched_ids
            ]
        )
        task_transactions += result["transactions"]
    wave_b = engine._joint_package_decisions(
        unresolved=wave_b_unresolved,
        events=event_map,
        seeds=seeds,
        models=models,
    )
    if ledger is not None and task_snapshot_hash is not None and unmatched_ids:
        failed_wave_b = {
            event_id
            for event_id in unmatched_ids
            if wave_b_candidates.get(event_id) and event_id not in wave_b
        }
        result = ledger.finish_many(
            [
                {
                    "stage": "N12_B",
                    "task_id": event_id,
                    "input_hash": canonical_hash({"event_id": event_id, "wave": "B"}),
                    "snapshot_hash": task_snapshot_hash,
                    "decision_ref": {
                        "decision": (
                            wave_b[event_id].model_dump(mode="json")
                            if event_id in wave_b
                            else {"deterministic": "NO_WAVE_B_MEMBER"}
                        )
                    },
                }
                for event_id in unmatched_ids
                if event_id not in failed_wave_b
            ]
        )
        task_transactions += result["transactions"]
        if failed_wave_b:
            result = ledger.fail_many(
                [
                    {
                        "stage": "N12_B",
                        "task_id": event_id,
                        "input_hash": canonical_hash({"event_id": event_id, "wave": "B"}),
                        "snapshot_hash": task_snapshot_hash,
                        "error_code": "UNJUDGEABLE_FAILED",
                    }
                    for event_id in sorted(failed_wave_b)
                ]
            )
            task_transactions += result["transactions"]
    owner_by_package = {package.package_id: event_id for event_id, package in provisional.items()}
    new_components = _constrained_components(unmatched_ids, wave_b, owner_by_package)

    history_members: dict[str, list[str]] = defaultdict(list)
    for event_id, package_id in selected_history.items():
        history_members[package_id].append(event_id)
    packages: dict[str, EventPackage] = {}
    resulting_by_event: dict[str, EventPackage] = {}
    for package_id, incoming_ids in sorted(history_members.items()):
        target = active_packages[package_id]
        existing_events = [
            stage_snapshot.events_by_id[event_id]
            for event_id in target.member_event_ids
            if event_id in stage_snapshot.events_by_id
        ]
        incoming_events = [event_map[event_id] for event_id in incoming_ids]
        rebuilt = compiler.compile(
            target,
            [*existing_events, *incoming_events],
            read_snapshot=stage_snapshot,
        )
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
            read_snapshot=stage_snapshot,
        )
        packages[rebuilt.package_id] = rebuilt
        for event_id in component:
            resulting_by_event[event_id] = rebuilt

    assignments: list[PackageAssignmentRecord] = []
    stage_records: list[dict[str, object]] = [
        {"package": package}
        for package in sorted(packages.values(), key=lambda item: item.package_id)
    ]
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
            unjudgeable = bool(wave_b_candidates.get(event_id) and decision is None)
            relation = (
                PackageAssignmentRelation.UNCERTAIN
                if unjudgeable
                else PackageAssignmentRelation.NOT_RELATED
            )
            reason = (
                "N12_UNJUDGEABLE_FAILED_SINGLETON" if unjudgeable else "N12_WAVE_B_SINGLETON"
            )
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
        membership = PackageMembership(
            membership_id=stable_id(
                "membership",
                {
                    "event": event.event_id,
                    "package": package.package_id,
                    "relation": seeds[event_id].membership_relation.value,
                },
            ),
            event_id=event.event_id,
            package_id=package.package_id,
            relation=seeds[event_id].membership_relation,
        )
        audit = DecisionAuditRecord(
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
        stage_records.append(
            {"memberships": [membership], "assignment": record, "audits": [audit]}
        )
        assignments.append(record)
    if chunked_apply:
        completed_apply = ledger.completed("PACKAGE_APPLY") if ledger is not None else {}
        chunks: list[tuple[dict[str, object], ...]] = []
        for chunk_index, offset in enumerate(range(0, len(stage_records), 32)):
            chunk = [dict(item) for item in stage_records[offset : offset + 32]]
            identities: list[dict[str, object]] = []
            for item in chunk:
                chunk_package = cast(EventPackage | None, item.get("package"))
                assignment = cast(PackageAssignmentRecord | None, item.get("assignment"))
                identities.append(
                    {
                        "package": (
                            None
                            if chunk_package is None
                            else (chunk_package.package_id, chunk_package.version)
                        ),
                        "assignment": (
                            None if assignment is None else assignment.assignment_id
                        ),
                    }
                )
            input_hash = canonical_hash(identities)
            task_id = f"package-apply:{chunk_index:06d}"
            completed = completed_apply.get(task_id)
            if (
                completed is not None
                and completed.get("input_hash") == input_hash
                and completed.get("snapshot_hash") == task_snapshot_hash
            ):
                continue
            if task_snapshot_hash is not None:
                chunk[0]["checkpoint"] = {
                    "epoch_id": ledger.epoch_id if ledger is not None else "",
                    "stage": "PACKAGE_APPLY",
                    "task_id": task_id,
                    "input_hash": input_hash,
                    "snapshot_hash": task_snapshot_hash,
                    "decision_ref": {"row_count": len(chunk)},
                }
            chunks.append(tuple(chunk))
        futures = [
            writer.submit(
                partial(
                    engine.registry.save_package_stage_batch,
                    chunk,
                    chunk_size=len(chunk),
                )
            )
            for chunk in chunks
        ]
        apply_results = [future.result() for future in futures]
    else:
        def legacy_commit() -> None:
            for item in stage_records:
                package = cast(EventPackage | None, item.get("package"))
                if package is not None:
                    engine.registry.save_package(package)
                memberships = cast(Sequence[PackageMembership], item.get("memberships", ()))
                for membership in memberships:
                    engine.registry.save_membership(membership)
                assignment = cast(PackageAssignmentRecord | None, item.get("assignment"))
                if assignment is not None:
                    engine.registry.save_package_assignment(assignment)
                audits = cast(Sequence[DecisionAuditRecord], item.get("audits", ()))
                for audit in audits:
                    engine.registry.append_decision_audit(audit)

        writer.run(legacy_commit)
        apply_results = [
            {"transactions": len(stage_records), "retries": 0, "degraded": 0}
        ]
    telemetry: dict[str, object] = {
        "wave_a_task_count": len(wave_a_unresolved),
        "wave_b_task_count": len(wave_b_unresolved),
        "new_component_count": len(new_components),
        "largest_new_component": max((len(value) for value in new_components), default=0),
        "wave_b_scheduler_edges": wave_b_index.stats().emitted_edges,
        "wave_b_scheduler_edge_cap": 24 * len(unmatched_ids),
        "snapshot_load_ms": stage_snapshot.load_ms,
        "snapshot_query_count": stage_snapshot.query_count,
        "task_transaction_count": task_transactions,
        "seed_fallback_count": 0,
        "apply_chunk_count": sum(item["transactions"] for item in apply_results),
        "apply_retry_count": sum(item["retries"] for item in apply_results),
        "apply_degraded_count": sum(item["degraded"] for item in apply_results),
    }
    return list(packages.values()), assignments, telemetry
