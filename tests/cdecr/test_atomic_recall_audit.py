from __future__ import annotations

from cdecr.atomic_recall_audit import (
    AtomicRecallGold,
    candidate_root_id,
    evaluate_ranked_candidate_snapshots,
)


class RootRegistry:
    def resolve_atomic_event_root(self, event_id: str, *, max_depth: int = 32) -> str:
        del max_depth
        return {"atomic:old": "atomic:root"}.get(event_id, event_id)


def test_candidate_root_normalizes_provisional_and_redirected_ids() -> None:
    registry = RootRegistry()

    assert candidate_root_id(registry, "provisional:abc") == "atomic:abc"
    assert candidate_root_id(registry, "atomic:old") == "atomic:root"


def test_ranked_snapshot_evaluator_uses_root_level_rank_and_routes() -> None:
    snapshots = [
        {
            "mention_id": "M1",
            "candidates": [
                {
                    "candidate_root_id": "A",
                    "recall_routes": ["FIELD_ID", "PROPOSITION_EMBEDDING"],
                },
                {
                    "candidate_root_id": "B",
                    "recall_routes": ["CORE_ENTITY"],
                },
            ],
        },
        {
            "mention_id": "M2",
            "candidates": [
                {
                    "candidate_root_id": "C",
                    "recall_routes": ["PROPOSITION_EMBEDDING"],
                }
            ],
        },
    ]
    metrics = evaluate_ranked_candidate_snapshots(
        snapshots,
        [
            AtomicRecallGold("M1", frozenset({"B"}), frozenset({"A"})),
            AtomicRecallGold("M2", frozenset({"Z"})),
        ],
        top_k=1,
    )

    assert metrics["recall_at_k"] == 0.0
    assert metrics["pool_recall"] == 0.5
    assert metrics["mrr"] == 0.25
    assert metrics["related_selected_rate"] == 0.5
    assert metrics["unrelated_selected_rate"] == 0.5
    assert metrics["create_new_exposure_rate"] == 1.0
    assert (
        metrics["route_metrics"]["PROPOSITION_EMBEDDING"]["unique_gold_contribution"] == 0
    )
