"""Bounded Atomic-only late convergence for BULK_EPOCH.

The stage consumes only residual positive evidence from an already committed stage.  Invalid or
missing model output is neutral: it never creates a negative edge and never fails the epoch.
"""

from __future__ import annotations

import re
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
from cdecr.contracts import (
    AtomicEvent,
    AtomicSemanticRelation,
    EventMention,
)
from cdecr.coreference_rules import add_mention_to_atomic, stable_id
from cdecr.cross_document_contracts import (
    AtomicAssignmentRecord,
    AtomicLateDecisionBatch,
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
    atomic_active_requests: int = 24


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


def _merge_source_mentions_for_single_save(
    *,
    source: AtomicEvent,
    target: AtomicEvent,
    registry: CDECRRegistry,
    known_mentions: list[EventMention],
) -> AtomicEvent:
    """Merge all source Mentions while advancing the persisted target only once.

    ``add_mention_to_atomic`` increments the in-memory version for each Mention. A late-stage
    source may contain two Mentions, while the registry persists the completed merge as one
    atomic update and therefore requires exactly ``target.version + 1``.
    """

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
    return merged.model_copy(update={"version": target.version + 1})


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
        with ThreadPoolExecutor(
            max_workers=min(config.atomic_active_requests, len(batches))
        ) as pool:
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
        merged = _merge_source_mentions_for_single_save(
            source=source,
            target=target,
            registry=registry,
            known_mentions=known_mentions,
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

