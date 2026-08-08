"""Append-only N7 ranked-candidate snapshots and offline quality metrics."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from cdecr.cross_document_contracts import AtomicCandidate
from cdecr.ports import CDECRRegistry, DecisionAuditRecord

LEGACY_ATOMIC_RANKER_VERSION = "atomic-recall-ranker-v1-legacy-audit"


def candidate_root_id(
    registry: CDECRRegistry,
    event_id: str,
) -> str:
    """Return the stable canonical target used for audit and root-level evaluation."""

    if event_id.startswith("provisional:"):
        return f"atomic:{event_id.removeprefix('provisional:')}"
    return registry.resolve_atomic_event_root(event_id)


def append_ranked_candidate_snapshot(
    *,
    registry: CDECRRegistry,
    run_id: str,
    mention_id: str,
    ranked_candidates: Sequence[AtomicCandidate],
    raw_embedding_similarities: dict[str, float],
    observed_conflicts: dict[str, list[str]],
    selected_top_k: int,
    audit_sink: Callable[[DecisionAuditRecord], object] | None = None,
) -> None:
    """Persist the complete legacy pool before Top-K truncation without changing ranking."""

    candidates: list[dict[str, Any]] = []
    for rank, candidate in enumerate(ranked_candidates, start=1):
        event_id = candidate.event.event_id
        candidates.append(
            {
                "candidate_root_id": (
                    candidate.candidate_root_id or candidate_root_id(registry, event_id)
                ),
                "original_candidate_event_id": event_id,
                "rank": rank,
                "rank_band": None,
                "recall_routes": [route.value for route in candidate.recall_routes],
                "raw_embedding_similarity": raw_embedding_similarities.get(event_id),
                "matched_axes": [],
                "conflicted_axes": [],
                "ambiguous_axes": [],
                "adapter_kind": None,
                "sidecar_signature_hash": None,
                "decision_eligible": not candidate.hard_conflicts,
                "observed_conflicts": observed_conflicts.get(event_id, []),
                "selected": rank <= selected_top_k,
            }
        )
    (audit_sink or registry.append_decision_audit)(
        DecisionAuditRecord(
            audit_id=f"n7-ranked-candidates:{run_id}:{mention_id}",
            run_id=run_id,
            decision_type="ATOMIC_N7_RANKED_CANDIDATES",
            subject_id=mention_id,
            payload={
                "mention_id": mention_id,
                "candidate_pool_size": len(ranked_candidates),
                "selected_top_k": min(selected_top_k, len(ranked_candidates)),
                "ranker_version": LEGACY_ATOMIC_RANKER_VERSION,
                "candidates": candidates,
            },
        )
    )


@dataclass(frozen=True)
class AtomicRecallGold:
    """Root-level N7 evaluation label for one incoming mention."""

    mention_id: str
    gold_root_ids: frozenset[str]
    related_root_ids: frozenset[str] = frozenset()


def evaluate_ranked_candidate_snapshots(
    snapshots: Iterable[dict[str, Any]],
    gold: Iterable[AtomicRecallGold],
    *,
    top_k: int = 5,
) -> dict[str, Any]:
    """Evaluate root-level recall/ranking and route contribution from frozen snapshots."""

    snapshots_by_mention = {
        str(item["mention_id"]): item
        for item in snapshots
        if isinstance(item, dict) and item.get("mention_id")
    }
    gold_items = list(gold)
    hit_at_k = 0
    pool_hit = 0
    reciprocal_rank = 0.0
    precision_numerator = 0
    precision_denominator = 0
    related = 0
    unrelated = 0
    route_trigger: dict[str, int] = {}
    route_gold_hit: dict[str, int] = {}
    route_unique_hit: dict[str, int] = {}
    create_new_exposure = 0
    missing_snapshots: list[str] = []

    for item in gold_items:
        snapshot = snapshots_by_mention.get(item.mention_id)
        if snapshot is None:
            missing_snapshots.append(item.mention_id)
            continue
        rows = [
            row
            for row in snapshot.get("candidates", [])
            if isinstance(row, dict) and row.get("candidate_root_id")
        ]
        pool_roots = [str(row["candidate_root_id"]) for row in rows]
        selected = rows[:top_k]
        selected_roots = [str(row["candidate_root_id"]) for row in selected]
        gold_ranks = [
            index
            for index, root_id in enumerate(pool_roots, start=1)
            if root_id in item.gold_root_ids
        ]
        if gold_ranks:
            pool_hit += 1
            reciprocal_rank += 1.0 / min(gold_ranks)
        if any(root_id in item.gold_root_ids for root_id in selected_roots):
            hit_at_k += 1
        elif item.gold_root_ids:
            create_new_exposure += 1
        for row in selected:
            root_id = str(row["candidate_root_id"])
            precision_denominator += 1
            if root_id in item.gold_root_ids:
                precision_numerator += 1
            elif root_id in item.related_root_ids:
                related += 1
            else:
                unrelated += 1
            routes = {str(value) for value in row.get("recall_routes", [])}
            for route in routes:
                route_trigger[route] = route_trigger.get(route, 0) + 1
                if root_id in item.gold_root_ids:
                    route_gold_hit[route] = route_gold_hit.get(route, 0) + 1
                    if len(routes) == 1:
                        route_unique_hit[route] = route_unique_hit.get(route, 0) + 1

    evaluated = len(gold_items)
    selected_non_gold = related + unrelated
    route_metrics = {
        route: {
            "triggers": triggers,
            "gold_hits": route_gold_hit.get(route, 0),
            "trigger_precision": (
                route_gold_hit.get(route, 0) / triggers if triggers else 0.0
            ),
            "unique_gold_contribution": route_unique_hit.get(route, 0),
        }
        for route, triggers in sorted(route_trigger.items())
    }
    return {
        "evaluated_mentions": evaluated,
        "missing_snapshot_mentions": missing_snapshots,
        "recall_at_k": hit_at_k / evaluated if evaluated else 0.0,
        "pool_recall": pool_hit / evaluated if evaluated else 0.0,
        "mrr": reciprocal_rank / evaluated if evaluated else 0.0,
        "precision_at_k": (
            precision_numerator / precision_denominator if precision_denominator else 0.0
        ),
        "related_selected_rate": related / selected_non_gold if selected_non_gold else 0.0,
        "unrelated_selected_rate": unrelated / selected_non_gold if selected_non_gold else 0.0,
        "create_new_exposure_rate": create_new_exposure / evaluated if evaluated else 0.0,
        "route_metrics": route_metrics,
    }


def load_gold_rows(
    rows: Iterable[dict[str, Any]],
    *,
    root_resolver: Callable[[str], str] | None = None,
) -> list[AtomicRecallGold]:
    """Parse an explicit gold mapping without inferring labels from model decisions."""

    resolve = root_resolver or (lambda value: value)
    output: list[AtomicRecallGold] = []
    for row in rows:
        mention_id = str(row["mention_id"])
        output.append(
            AtomicRecallGold(
                mention_id=mention_id,
                gold_root_ids=frozenset(
                    resolve(str(value)) for value in row.get("gold_root_ids", [])
                ),
                related_root_ids=frozenset(
                    resolve(str(value)) for value in row.get("related_root_ids", [])
                ),
            )
        )
    return output
