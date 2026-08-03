"""Bounded one-shot late convergence for BULK_EPOCH V3.

The stage consumes only residual positive evidence from an already committed stage.  Invalid or
missing model output is neutral: it never creates a negative edge and never fails the epoch.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from cdecr.atomic_identity_contracts import AtomicIdentitySidecar
from cdecr.atomic_identity_sidecar import (
    combine_atomic_identity_sidecars,
    compile_atomic_identity_sidecar,
)
from cdecr.atomic_merge_invariant import (
    AtomicMergeInvariantResult,
    evaluate_atomic_merge_invariant,
)
from cdecr.bulk_epoch.indexes import MultiKeyBoundedIndex
from cdecr.contracts import (
    AtomicEvent,
    AtomicSemanticRelation,
    EventMention,
    EventPackage,
    PackageMergeRelation,
)
from cdecr.coreference_rules import add_mention_to_atomic, stable_id
from cdecr.cross_document_contracts import (
    AtomicAssignmentRecord,
    AtomicLateDecisionBatch,
    PackageLateDecisionBatch,
    PackagePairMergeDecision,
    RecallRoute,
)
from cdecr.identity_compiler import IdentityCompiler
from cdecr.models import ModelTier
from cdecr.ports import CDECRRegistry, DecisionAuditRecord, StructuredModelRequest
from cdecr.wire import compact_json

if TYPE_CHECKING:
    from cdecr.cross_document import CrossDocumentEngine, _AuditedModels


_PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts" / "v1"


@dataclass(frozen=True)
class LateStageConfig:
    atomic_task_cap: int = 48
    package_pair_cap: int = 64
    max_spoke_members: int = 4
    max_spokes_per_hub: int = 4


def _prompt(name: str) -> str:
    return (_PROMPT_DIR / name).read_text(encoding="utf-8")


def _normalized(value: str) -> str:
    return re.sub(r"\W+", " ", value.casefold()).strip()


def _event_sidecar(
    registry: CDECRRegistry,
    event: AtomicEvent,
    compiler: IdentityCompiler,
) -> AtomicIdentitySidecar:
    sidecars: list[AtomicIdentitySidecar] = []
    for mention_id in event.mention_ids:
        mention = registry.get_mention(mention_id)
        if mention is None:
            continue
        compiled = compiler.compile(mention)
        if compiled.atomic_identity_sidecar is not None:
            sidecars.append(compiled.atomic_identity_sidecar)
        else:
            sidecars.append(compile_atomic_identity_sidecar(mention, event.identity_profile))
    return combine_atomic_identity_sidecars(sidecars)


def run_atomic_late_convergence(
    *,
    engine: CrossDocumentEngine,
    events: Sequence[AtomicEvent],
    assignments: Sequence[AtomicAssignmentRecord],
    models: _AuditedModels,
    run_id: str,
    config: LateStageConfig,
) -> tuple[list[AtomicEvent], dict[str, object]]:
    """Recheck bounded RELATED_NOT_SAME residuals and absorb only small source roots."""

    registry = engine.registry
    compiler = IdentityCompiler(registry=registry, catalog_hash=engine.knowledge_base.catalog_hash)
    event_by_id = {event.event_id: event for event in events}
    candidate_pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for assignment in assignments:
        if not assignment.reason.startswith("N9_RELATED_CREATE_NEW"):
            continue
        source_id = assignment.resulting_event_id
        if source_id is None:
            continue
        source_root = registry.resolve_atomic_event_root(source_id)
        source = registry.get_current_atomic_event(source_root)
        if source is None or len(source.mention_ids) > 2:
            continue
        for candidate_id in assignment.related_candidate_event_ids[:2]:
            target_root = registry.resolve_atomic_event_root(candidate_id)
            if target_root is None or target_root == source_root:
                continue
            pair = (source_root, target_root)
            if pair in seen:
                continue
            seen.add(pair)
            candidate_pairs.append(pair)
    candidate_pairs = candidate_pairs[: config.atomic_task_cap]

    deterministic: set[tuple[str, str]] = set()
    residual: list[tuple[str, str]] = []
    hard_blocked = 0
    for source_id, target_id in candidate_pairs:
        source = registry.get_current_atomic_event(source_id) or event_by_id.get(source_id)
        target = registry.get_current_atomic_event(target_id) or event_by_id.get(target_id)
        if source is None or target is None:
            continue
        source_sidecar = _event_sidecar(registry, source, compiler)
        target_sidecar = _event_sidecar(registry, target, compiler)
        invariant = evaluate_atomic_merge_invariant(
            source_sidecar,
            target_sidecar,
            enforced_rules=engine.atomic_enforced_rules,
        )
        if invariant.result is AtomicMergeInvariantResult.LOCKED_OUT:
            hard_blocked += 1
            continue
        exact_profile = source.identity_profile.model_dump(
            mode="json"
        ) == target.identity_profile.model_dump(mode="json")
        exact_proposition = _normalized(source.canonical_proposition) == _normalized(
            target.canonical_proposition
        )
        if exact_profile and exact_proposition:
            deterministic.add((source_id, target_id))
        else:
            residual.append((source_id, target_id))

    model_same: set[tuple[str, str]] = set()
    failed_pairs: set[tuple[str, str]] = set()
    batches = [residual[offset : offset + 8] for offset in range(0, len(residual), 8)]

    def decide_batch(
        indexed: tuple[int, list[tuple[str, str]]],
    ) -> tuple[int, set[tuple[str, str]], set[tuple[str, str]]]:
        batch_index, batch = indexed
        short_events: dict[str, dict[str, object]] = {}
        pair_by_short: dict[str, tuple[str, str]] = {}
        wire_pairs: list[dict[str, str]] = []
        for index, pair in enumerate(batch, start=1):
            pair_id = f"r{index}"
            pair_by_short[pair_id] = pair
            left_id, right_id = pair
            left = registry.get_current_atomic_event(left_id)
            right = registry.get_current_atomic_event(right_id)
            if left is None or right is None:
                continue
            left_short, right_short = f"a{index}l", f"a{index}r"
            short_events[left_short] = {
                "prop": left.canonical_proposition,
                "family": left.event_family.value,
                "identity": left.identity_profile.model_dump(mode="json"),
                "time": left.time.model_dump(mode="json"),
            }
            short_events[right_short] = {
                "prop": right.canonical_proposition,
                "family": right.event_family.value,
                "identity": right.identity_profile.model_dump(mode="json"),
                "time": right.time.model_dump(mode="json"),
            }
            wire_pairs.append({"pair_id": pair_id, "left": left_short, "right": right_short})
        request = StructuredModelRequest(
            system_prompt=(
                _prompt("atomic_coreference.md")
                + "\nRecheck only the supplied RELATED_NOT_SAME residual pairs. Decide whether "
                "each pair is the same minimal occurrence after normalizing wording and detail "
                "level. Return SAME_EVENT unless a material identity boundary differs; do not "
                "merge merely because both facts share a parent Package."
            ),
            user_prompt=compact_json({"atomics": short_events, "pairs": wire_pairs}),
            json_schema=AtomicLateDecisionBatch.model_json_schema(),
        )
        try:
            result = models._structured(
                tier=ModelTier.M3,
                stage="atomic_late_convergence",
                request=request,
                repaired=False,
            )
            output = AtomicLateDecisionBatch.model_validate(result.payload)
        except Exception:
            return batch_index, set(), set(batch)
        returned: dict[str, AtomicSemanticRelation] = {}
        invalid: set[tuple[str, str]] = set()
        for decision in output.decisions:
            if decision.pair_id not in pair_by_short or decision.pair_id in returned:
                continue
            returned[decision.pair_id] = decision.relation
        same = {
            pair_by_short[pair_id]
            for pair_id, relation in returned.items()
            if relation is AtomicSemanticRelation.SAME_EVENT
        }
        invalid.update(pair for pair_id, pair in pair_by_short.items() if pair_id not in returned)
        return batch_index, same, invalid

    if batches:
        with ThreadPoolExecutor(max_workers=min(16, len(batches))) as pool:
            outputs = list(pool.map(decide_batch, enumerate(batches)))
        for _, same, invalid in sorted(outputs):
            model_same.update(same)
            failed_pairs.update(invalid)

    applied: list[dict[str, object]] = []
    consumed_sources: set[str] = set()
    known_mentions: list[EventMention] = [
        mention
        for event in registry.list_current_atomic_events(limit=10000)
        for mention_id in event.mention_ids
        if (mention := registry.get_mention(mention_id)) is not None
    ]
    for source_id, target_id in [*sorted(deterministic), *sorted(model_same)]:
        source_root = registry.resolve_atomic_event_root(source_id)
        target_root = registry.resolve_atomic_event_root(target_id)
        if (
            source_root is None
            or target_root is None
            or source_root == target_root
            or source_root in consumed_sources
        ):
            continue
        source = registry.get_current_atomic_event(source_root)
        target = registry.get_current_atomic_event(target_root)
        if source is None or target is None or len(source.mention_ids) > 2:
            continue
        invariant = evaluate_atomic_merge_invariant(
            _event_sidecar(registry, source, compiler),
            _event_sidecar(registry, target, compiler),
            enforced_rules=engine.atomic_enforced_rules,
        )
        if invariant.result is AtomicMergeInvariantResult.LOCKED_OUT:
            continue
        merged = target
        for mention_id in source.mention_ids:
            mention = registry.get_mention(mention_id)
            if mention is None:
                continue
            merged = add_mention_to_atomic(
                merged,
                mention,
                known_mentions=known_mentions,
                claim_conflict=False,
                identity_differences=[],
                incoming_profile=source.identity_profile,
            )
        registry.save_atomic_event(merged)
        registry.save_atomic_redirect(
            source_event_id=source_root,
            target_event_id=merged.event_id,
            run_id=run_id,
            reason=(
                "N9_LATE_L0_EXACT" if (source_id, target_id) in deterministic else "N9_LATE_L1_M3"
            ),
        )
        consumed_sources.add(source_root)
        applied.append(
            {
                "source": source_root,
                "target": merged.event_id,
                "decision_source": ("L0" if (source_id, target_id) in deterministic else "L1_M3"),
            }
        )

    registry.append_decision_audit(
        DecisionAuditRecord(
            audit_id=stable_id("atomic-late-audit", {"run": run_id}),
            run_id=run_id,
            decision_type="ATOMIC_LATE_CONVERGENCE",
            subject_id=run_id,
            payload={
                "candidate_count": len(candidate_pairs),
                "hard_blocked": hard_blocked,
                "l0_same": len(deterministic),
                "l1_task_count": len(residual),
                "l1_same": len(model_same),
                "failed_neutral": len(failed_pairs),
                "applied": applied,
            },
        )
    )
    result_roots = {
        root
        for event in events
        if (root := registry.resolve_atomic_event_root(event.event_id)) is not None
    }
    current = [
        event
        for event_id in sorted(result_roots)
        if (event := registry.get_current_atomic_event(event_id)) is not None
    ]
    return current, {
        "candidate_count": len(candidate_pairs),
        "l0_merge_count": sum(item["decision_source"] == "L0" for item in applied),
        "l1_merge_count": sum(item["decision_source"] == "L1_M3" for item in applied),
        "l1_task_count": len(residual),
        "failed_neutral_count": len(failed_pairs),
        "hard_blocked_count": hard_blocked,
        "applied_edges": applied,
    }


def run_package_wave_c(
    *,
    engine: CrossDocumentEngine,
    packages: Sequence[EventPackage],
    models: _AuditedModels,
    run_id: str,
    config: LateStageConfig,
) -> tuple[list[EventPackage], dict[str, object]]:
    """Run one bounded package-fragment convergence wave over the frozen Wave-B partition."""

    active = {package.package_id: package for package in packages}

    def index_keys(package: EventPackage) -> list[str]:
        keys: list[str] = []
        if package.anchor_artifact_id:
            keys.append(f"artifact:{package.anchor_artifact_id}")
        keys.extend(f"anchor:{value}" for value in package.package_anchor_ids)
        keys.extend(f"entity:{value}" for value in package.anchor_entities)
        if package.anchor_period_id:
            keys.append(f"period:{package.anchor_period_id}")
        for event in engine._package_member_events(package):
            keys.append(
                "identity:"
                + stable_id("wave-c-identity", event.identity_profile.model_dump(mode="json"))
            )
            for mention_id in event.mention_ids:
                mention = engine.registry.get_mention(mention_id)
                if mention is not None:
                    keys.append(f"source:{mention.message_id}")
        return sorted(set(keys))

    candidate_index = MultiKeyBoundedIndex()
    keys_by_package = {package_id: index_keys(package) for package_id, package in active.items()}
    for package_id in sorted(active):
        candidate_index.add(package_id, keys_by_package[package_id])
    pair_rows: list[tuple[EventPackage, EventPackage, list[RecallRoute], float]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for left_id in sorted(active):
        for right_id in candidate_index.query(
            keys_by_package[left_id],
            limit=16,
            exclude=left_id,
        ):
            pair_key = (left_id, right_id) if left_id < right_id else (right_id, left_id)
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            left, right = active[left_id], active[right_id]
            routes, _ = engine._package_pair_signals(left, right, {})
            strong = set(routes).intersection(
                {
                    RecallRoute.CANONICAL_ARTIFACT,
                    RecallRoute.PACKAGE_ANCHOR,
                    RecallRoute.PARENT_CONTEXT,
                    RecallRoute.SAME_SOURCE_MEMBER,
                    RecallRoute.MEMBER_IDENTITY,
                }
            )
            if not strong:
                continue
            score = float(
                sum(
                    route in strong
                    for route in (
                        RecallRoute.CANONICAL_ARTIFACT,
                        RecallRoute.PACKAGE_ANCHOR,
                        RecallRoute.PARENT_CONTEXT,
                        RecallRoute.SAME_SOURCE_MEMBER,
                        RecallRoute.MEMBER_IDENTITY,
                    )
                )
            )
            score += 0.5 if RecallRoute.CORE_ENTITY in routes else 0.0
            score += 0.5 if RecallRoute.TIME_WINDOW in routes else 0.0
            pair_rows.append((left, right, routes, score))
    pair_rows.sort(key=lambda row: (-row[3], row[0].package_id, row[1].package_id))

    per_fragment: dict[str, int] = defaultdict(int)
    bounded: list[tuple[EventPackage, EventPackage, list[RecallRoute], float]] = []
    for row in pair_rows:
        left, right, _, _ = row
        if per_fragment[left.package_id] >= 4 or per_fragment[right.package_id] >= 4:
            continue
        bounded.append(row)
        per_fragment[left.package_id] += 1
        per_fragment[right.package_id] += 1
        if len(bounded) >= config.package_pair_cap:
            break

    direct_same: set[tuple[str, str]] = set()
    residual: list[tuple[EventPackage, EventPackage, list[RecallRoute], float]] = []
    hard_blocked = 0
    for left, right, routes, score in bounded:
        if engine._package_pair_external_guard(left, right) is not None:
            hard_blocked += 1
            continue
        if RecallRoute.CANONICAL_ARTIFACT in routes or set(left.package_anchor_ids).intersection(
            right.package_anchor_ids
        ):
            direct_same.add((left.package_id, right.package_id))
        elif score >= 1.5:
            residual.append((left, right, routes, score))

    model_same: set[tuple[str, str]] = set()
    failed_neutral = 0
    batches = [residual[offset : offset + 12] for offset in range(0, len(residual), 12)]

    def decide_batch(
        indexed: tuple[
            int,
            list[tuple[EventPackage, EventPackage, list[RecallRoute], float]],
        ],
    ) -> tuple[int, set[tuple[str, str]], int]:
        batch_index, batch = indexed
        cards: dict[str, dict[str, object]] = {}
        pair_by_short: dict[str, tuple[str, str]] = {}
        pairs: list[dict[str, object]] = []
        for index, (left, right, routes, _) in enumerate(batch, start=1):
            pair_id = f"r{index}"
            pair_by_short[pair_id] = (left.package_id, right.package_id)
            for package, short in ((left, f"p{index}l"), (right, f"p{index}r")):
                members = engine._package_member_events(package)
                cards[short] = {
                    "kind": package.package_kind.value,
                    "family": package.package_family.value,
                    "anchors": package.package_anchor_ids[:4],
                    "primary_anchor": package.primary_anchor_id,
                    "artifact": package.anchor_artifact_id,
                    "period": package.anchor_period_id,
                    "entities": package.anchor_entities[:4],
                    "member_count": len(package.member_event_ids),
                    "representatives": [event.canonical_proposition for event in members[:3]],
                }
            pairs.append(
                {
                    "pair_id": pair_id,
                    "left": f"p{index}l",
                    "right": f"p{index}r",
                    "routes": [route.value for route in routes],
                }
            )
        request = StructuredModelRequest(
            system_prompt=(
                _prompt("package_assignment.md")
                + "\nJudge shared parent membership, not Atomic equality. Different child facts "
                "may be MEMBER when evidence anchors them to the same bounded parent occurrence; "
                "choose NOT_RELATED only for a material parent-boundary conflict. Missing parent "
                "detail or a Package-family mismatch alone is not such a conflict. Return only "
                "pair_id and SAME_PARENT, DIFFERENT_PARENT, or UNCERTAIN."
            ),
            user_prompt=compact_json({"packages": cards, "pairs": pairs}),
            json_schema=PackageLateDecisionBatch.model_json_schema(),
        )
        try:
            result = models._structured(
                tier=ModelTier.M3,
                stage="package_wave_c",
                request=request,
                repaired=False,
            )
            output = PackageLateDecisionBatch.model_validate(result.payload)
        except Exception:
            return batch_index, set(), len(batch)
        returned: dict[str, str] = {}
        for decision in output.decisions:
            if decision.pair_id in pair_by_short and decision.pair_id not in returned:
                returned[decision.pair_id] = decision.relation
        same = {
            pair_by_short[pair_id]
            for pair_id, relation in returned.items()
            if relation == "SAME_PARENT"
        }
        return batch_index, same, len(pair_by_short) - len(returned)

    if batches:
        with ThreadPoolExecutor(max_workers=min(16, len(batches))) as pool:
            outputs = list(pool.map(decide_batch, enumerate(batches)))
        for _, same, failed in sorted(outputs):
            model_same.update(same)
            failed_neutral += failed

    candidate_edges = [(*pair, "N12_WAVE_C_M0") for pair in sorted(direct_same)] + [
        (*pair, "N12_WAVE_C_M3") for pair in sorted(model_same)
    ]
    spokes_by_hub: dict[str, int] = defaultdict(int)
    consumed: set[str] = set()
    applied: list[dict[str, object]] = []
    for left_id, right_id, source_kind in candidate_edges:
        left_root = engine.registry.resolve_package_root(left_id)
        right_root = engine.registry.resolve_package_root(right_id)
        if left_root is None or right_root is None or left_root == right_root:
            continue
        current_left = engine.registry.get_current_package(left_root)
        current_right = engine.registry.get_current_package(right_root)
        if current_left is None or current_right is None:
            continue
        if engine._package_pair_external_guard(current_left, current_right) is not None:
            continue
        target, source = sorted(
            (current_left, current_right),
            key=lambda item: (-len(item.member_event_ids), item.package_id),
        )
        if (
            source.package_id in consumed
            or len(source.member_event_ids) > config.max_spoke_members
            or spokes_by_hub[target.package_id] >= config.max_spokes_per_hub
        ):
            continue
        decision = PackagePairMergeDecision(
            source_package_id=source.package_id,
            target_package_id=target.package_id,
            relation=PackageMergeRelation.SAME_PACKAGE,
            reason=source_kind,
        )
        decision_id = stable_id(
            "package-merge-decision",
            {"run": run_id, "source": source.package_id, "target": target.package_id},
        )
        engine.registry.save_package_merge_decision(
            decision_id=decision_id,
            run_id=run_id,
            decision=decision,
        )
        engine._merge_package_pair(
            target,
            source,
            run_id=run_id,
            reason=source_kind,
        )
        consumed.add(source.package_id)
        spokes_by_hub[target.package_id] += 1
        applied.append(
            {
                "source": source.package_id,
                "target": target.package_id,
                "decision_source": source_kind,
            }
        )

    engine.registry.append_decision_audit(
        DecisionAuditRecord(
            audit_id=stable_id("package-wave-c-audit", {"run": run_id}),
            run_id=run_id,
            decision_type="PACKAGE_WAVE_C",
            subject_id=run_id,
            payload={
                "candidate_pair_count": len(bounded),
                "m0_same": len(direct_same),
                "m3_task_count": len(residual),
                "m3_same": len(model_same),
                "hard_blocked": hard_blocked,
                "failed_neutral": failed_neutral,
                "applied": applied,
            },
        )
    )
    result_roots = {
        root
        for package in packages
        if (root := engine.registry.resolve_package_root(package.package_id)) is not None
    }
    current = [
        package
        for package_id in sorted(result_roots)
        if (package := engine.registry.get_current_package(package_id)) is not None
    ]
    return current, {
        "candidate_pair_count": len(bounded),
        "m0_merge_count": sum(item["decision_source"] == "N12_WAVE_C_M0" for item in applied),
        "m3_task_count": len(residual),
        "m3_merge_count": sum(item["decision_source"] == "N12_WAVE_C_M3" for item in applied),
        "failed_neutral_count": failed_neutral,
        "hard_blocked_count": hard_blocked,
        "scheduler_edges": candidate_index.stats().emitted_edges,
        "scheduler_edge_cap": 16 * len(active),
        "applied_edges": applied,
    }
