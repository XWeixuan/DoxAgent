"""Project N12 decisions onto the reviewed 30-document Package Gold.

The two acceptance runs do not share Atomic IDs.  This evaluator aligns only
high-confidence Atomic pairs through their persisted embeddings, then compares
the Package partitions induced by current membership or by an N12 A/B report.
It deliberately reports coverage and alignment uncertainty alongside P/R/F1.
"""

from __future__ import annotations

import argparse
import array
import json
import math
import sqlite3
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--gold-registry", type=Path, required=True)
    parser.add_argument("--gold-review", type=Path, required=True)
    parser.add_argument("--ab-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--similarity-threshold", type=float, default=0.90)
    parser.add_argument("--minimum-margin", type=float, default=0.01)
    return parser.parse_args()


def _connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _redirects(
    connection: sqlite3.Connection,
    table: str,
    source: str,
    target: str,
) -> dict[str, str]:
    query = f"SELECT {source}, {target} FROM {table}"
    return {
        str(row[source]): str(row[target])
        for row in connection.execute(query)
    }


def _root(value: str, redirects: dict[str, str]) -> str:
    seen: set[str] = set()
    while value in redirects and value not in seen:
        seen.add(value)
        value = redirects[value]
    return value


def _current_events(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    redirects = _redirects(
        connection,
        "atomic_event_redirects",
        "source_event_id",
        "target_event_id",
    )
    rows = connection.execute(
        """
        SELECT v.event_id, v.payload_json
        FROM atomic_event_versions v
        JOIN atomic_event_heads h
          ON h.event_id = v.event_id AND h.current_version = v.version
        """
    )
    return {
        str(row["event_id"]): json.loads(str(row["payload_json"]))
        for row in rows
        if _root(str(row["event_id"]), redirects) == str(row["event_id"])
    }


def _latest_embeddings(
    connection: sqlite3.Connection,
    event_ids: set[str],
) -> dict[str, array.array[float]]:
    result: dict[str, array.array[float]] = {}
    for row in connection.execute(
        """
        SELECT owner_id, vector_f32
        FROM embeddings
        WHERE owner_kind = 'atomic_event'
        ORDER BY created_at
        """
    ):
        owner_id = str(row["owner_id"])
        if owner_id in event_ids:
            result[owner_id] = array.array("f", row["vector_f32"])
    return result


def _cosine(left: array.array[float], right: array.array[float]) -> float:
    if len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _align_events(
    current_connection: sqlite3.Connection,
    gold_connection: sqlite3.Connection,
    threshold: float,
    minimum_margin: float,
) -> tuple[
    dict[str, str],
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    current_events = _current_events(current_connection)
    gold_events = _current_events(gold_connection)
    current_vectors = _latest_embeddings(current_connection, set(current_events))
    gold_vectors = _latest_embeddings(gold_connection, set(gold_events))
    mapping: dict[str, str] = {}
    audit: list[dict[str, Any]] = []
    for event_id, vector in current_vectors.items():
        scores = sorted(
            ((_cosine(vector, other), other_id) for other_id, other in gold_vectors.items()),
            reverse=True,
        )
        if not scores:
            continue
        best_score, best_id = scores[0]
        margin = best_score - scores[1][0] if len(scores) > 1 else best_score
        accepted = best_score >= threshold and margin >= minimum_margin
        if accepted:
            mapping[event_id] = best_id
        audit.append(
            {
                "current_event_id": event_id,
                "gold_event_id": best_id,
                "similarity": round(best_score, 6),
                "margin": round(margin, 6),
                "accepted": accepted,
                "current_proposition": current_events[event_id].get("canonical_proposition"),
                "gold_proposition": gold_events[best_id].get("canonical_proposition"),
            }
        )
    return mapping, audit, current_events, gold_events


def _gold_labels(
    connection: sqlite3.Connection,
    review: dict[str, Any],
) -> dict[str, str]:
    memberships = {
        str(row["event_id"]): str(row["package_id"])
        for row in connection.execute("SELECT event_id, package_id FROM active_package_memberships")
    }
    cluster_by_package: dict[str, str] = {}
    for cluster in review["gold_multi_atomic_clusters"]:
        for fragment in cluster["current_fragments"]:
            cluster_by_package[str(fragment).split(" ", 1)[0]] = str(cluster["gold_cluster_id"])
    intruder_by_package = {
        str(item["package_id"]): str(item["intruder_atomic_id"])
        for item in review["atomic_pair_membership"]["false_merge_packages"]
        if item.get("intruder_atomic_id")
    }
    split_packages = {
        str(item["package_id"])
        for item in review["atomic_pair_membership"]["false_merge_packages"]
        if not item.get("intruder_atomic_id")
    }
    labels: dict[str, str] = {}
    for event_id in _current_events(connection):
        package_id = memberships.get(event_id)
        if package_id is None:
            labels[event_id] = f"G_SINGLETON:{event_id}"
        elif package_id in split_packages or intruder_by_package.get(package_id) == event_id:
            labels[event_id] = f"G_SINGLETON:{event_id}"
        elif package_id in cluster_by_package:
            labels[event_id] = cluster_by_package[package_id]
        else:
            labels[event_id] = f"G_PACKAGE:{package_id}"
    return labels


def _active_packages(connection: sqlite3.Connection) -> tuple[dict[str, str], dict[str, set[str]]]:
    redirects = _redirects(
        connection,
        "package_redirects",
        "source_package_id",
        "target_package_id",
    )
    event_package: dict[str, str] = {}
    package_events: dict[str, set[str]] = defaultdict(set)
    for row in connection.execute("SELECT event_id, package_id FROM active_package_memberships"):
        package_id = _root(str(row["package_id"]), redirects)
        event_id = str(row["event_id"])
        event_package[event_id] = package_id
        package_events[package_id].add(event_id)
    return event_package, package_events


def _pair_metrics(gold: dict[str, str], predicted: dict[str, str]) -> dict[str, Any]:
    event_ids = sorted(set(gold).intersection(predicted))
    tp = fp = fn = 0
    for left, right in combinations(event_ids, 2):
        gold_same = gold[left] == gold[right]
        predicted_same = predicted[left] == predicted[right]
        tp += int(gold_same and predicted_same)
        fp += int(not gold_same and predicted_same)
        fn += int(gold_same and not predicted_same)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "event_count": len(event_ids),
        "true_positive_pairs": tp,
        "false_positive_pairs": fp,
        "false_negative_pairs": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def _error_groups(
    gold: dict[str, str],
    predicted: dict[str, str],
    events: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    predicted_groups: dict[str, list[str]] = defaultdict(list)
    gold_groups: dict[str, list[str]] = defaultdict(list)
    for event_id in sorted(set(gold).intersection(predicted)):
        predicted_groups[predicted[event_id]].append(event_id)
        gold_groups[gold[event_id]].append(event_id)

    def members(event_ids: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "event_id": event_id,
                "gold_label": gold[event_id],
                "predicted_label": predicted[event_id],
                "proposition": events[event_id].get("canonical_proposition"),
            }
            for event_id in event_ids
        ]

    false_merges = [
        {
            "predicted_label": label,
            "gold_labels": sorted({gold[event_id] for event_id in event_ids}),
            "members": members(event_ids),
        }
        for label, event_ids in predicted_groups.items()
        if len({gold[event_id] for event_id in event_ids}) > 1
    ]
    fragmentations = [
        {
            "gold_label": label,
            "predicted_labels": sorted({predicted[event_id] for event_id in event_ids}),
            "members": members(event_ids),
        }
        for label, event_ids in gold_groups.items()
        if len({predicted[event_id] for event_id in event_ids}) > 1
    ]
    return {"false_merge_groups": false_merges, "fragmentation_groups": fragmentations}


def _fragmentation_metrics(
    gold: dict[str, str],
    predicted: dict[str, str],
) -> dict[str, Any]:
    gold_groups: dict[str, list[str]] = defaultdict(list)
    for event_id in sorted(set(gold).intersection(predicted)):
        gold_groups[gold[event_id]].append(event_id)
    details: list[dict[str, Any]] = []
    singleton_components = 0
    missed_pair_links = 0
    for label, event_ids in gold_groups.items():
        if len(event_ids) < 2:
            continue
        component_sizes: dict[str, int] = defaultdict(int)
        for event_id in event_ids:
            component_sizes[predicted[event_id]] += 1
        sizes = sorted(component_sizes.values(), reverse=True)
        gold_pairs = len(event_ids) * (len(event_ids) - 1) // 2
        retained_pairs = sum(size * (size - 1) // 2 for size in sizes)
        missed = gold_pairs - retained_pairs
        missed_pair_links += missed
        singleton_components += sum(size == 1 for size in sizes)
        details.append(
            {
                "gold_label": label,
                "gold_event_count": len(event_ids),
                "component_count": len(sizes),
                "component_sizes": sizes,
                "excess_components": len(sizes) - 1,
                "missed_pair_links": missed,
            }
        )
    fragmented = [item for item in details if item["component_count"] > 1]
    return {
        "multi_event_gold_group_count": len(details),
        "fragmented_gold_group_count": len(fragmented),
        "excess_components": sum(item["excess_components"] for item in fragmented),
        "singleton_components_within_multi_event_gold": singleton_components,
        "missed_pair_links": missed_pair_links,
        "details": fragmented,
    }


def _partition_evaluation(
    gold: dict[str, str],
    predicted: dict[str, str],
    events: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "metrics": _pair_metrics(gold, predicted),
        "fragmentation": _fragmentation_metrics(gold, predicted),
        "errors": _error_groups(gold, predicted, events),
    }


def _decision_projection(
    decisions: dict[str, Any],
    mapping: dict[str, str],
    gold_labels: dict[str, str],
    package_redirects: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    predicted: dict[str, str] = {}
    gold: dict[str, str] = {}
    for raw_event_id, decision in decisions.items():
        event_id = str(raw_event_id)
        gold_event_id = mapping.get(event_id)
        if gold_event_id is None or gold_event_id not in gold_labels:
            continue
        target = decision.get("selected_member_package_id")
        predicted[event_id] = (
            _root(str(target), package_redirects) if target else f"N12_NEW:{event_id}"
        )
        gold[event_id] = gold_labels[gold_event_id]
    return gold, predicted


def _recorded_decisions(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT event_id, payload_json
        FROM (
            SELECT event_id, payload_json,
                   ROW_NUMBER() OVER (
                       PARTITION BY event_id ORDER BY created_at DESC
                   ) AS ordinal
            FROM package_assignment_decisions
        )
        WHERE ordinal = 1
        """
    )
    return {
        str(row["event_id"]): json.loads(str(row["payload_json"]))
        for row in rows
    }


def _candidate_relation_evaluation(
    decisions: dict[str, Any],
    mapping: dict[str, str],
    gold_labels: dict[str, str],
    package_events: dict[str, set[str]],
    package_redirects: dict[str, str],
) -> dict[str, Any]:
    tp = fp = fn = tn = unjudgeable = 0
    evaluated_tasks = merge_opportunity_tasks = selected_tasks = 0
    correct_selected_tasks = correct_action_tasks = 0
    for raw_event_id, decision in decisions.items():
        event_id = str(raw_event_id)
        gold_event_id = mapping.get(event_id)
        if gold_event_id is None or gold_event_id not in gold_labels:
            continue
        incoming_label = gold_labels[gold_event_id]
        positive_candidates: set[str] = set()
        for assessment in decision.get("candidate_assessments", []):
            candidate_id = _root(
                str(assessment["candidate_package_id"]),
                package_redirects,
            )
            candidate_labels = {
                gold_labels[mapping[member_id]]
                for member_id in package_events.get(candidate_id, set())
                if member_id != event_id
                and member_id in mapping
                and mapping[member_id] in gold_labels
            }
            if not candidate_labels:
                unjudgeable += 1
                continue
            gold_member = incoming_label in candidate_labels
            predicted_member = assessment.get("relation") == "MEMBER"
            if gold_member:
                positive_candidates.add(candidate_id)
            tp += int(gold_member and predicted_member)
            fp += int(not gold_member and predicted_member)
            fn += int(gold_member and not predicted_member)
            tn += int(not gold_member and not predicted_member)
        evaluated_tasks += 1
        selected = decision.get("selected_member_package_id")
        selected_root = _root(str(selected), package_redirects) if selected else None
        has_opportunity = bool(positive_candidates)
        merge_opportunity_tasks += int(has_opportunity)
        selected_tasks += int(selected_root is not None)
        selected_correct = selected_root in positive_candidates if selected_root else False
        correct_selected_tasks += int(selected_correct)
        correct_action_tasks += int(
            selected_correct or (not has_opportunity and selected_root is None)
        )
    relation_precision = tp / (tp + fp) if tp + fp else 1.0
    relation_recall = tp / (tp + fn) if tp + fn else 1.0
    relation_f1 = (
        2 * relation_precision * relation_recall / (relation_precision + relation_recall)
        if relation_precision + relation_recall
        else 0.0
    )
    return {
        "candidate_relation": {
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "true_negative": tn,
            "unjudgeable": unjudgeable,
            "precision": relation_precision,
            "recall": relation_recall,
            "f1": relation_f1,
        },
        "task_action": {
            "evaluated_tasks": evaluated_tasks,
            "merge_opportunity_tasks": merge_opportunity_tasks,
            "selected_tasks": selected_tasks,
            "correct_selected_tasks": correct_selected_tasks,
            "merge_precision": (
                correct_selected_tasks / selected_tasks if selected_tasks else 1.0
            ),
            "conditional_merge_recall": (
                correct_selected_tasks / merge_opportunity_tasks
                if merge_opportunity_tasks
                else 1.0
            ),
            "action_accuracy": (
                correct_action_tasks / evaluated_tasks if evaluated_tasks else 1.0
            ),
        },
    }


def main() -> int:
    args = _args()
    current_connection = _connection(args.registry)
    gold_connection = _connection(args.gold_registry)
    review = json.loads(args.gold_review.read_text(encoding="utf-8"))
    mapping, alignment, current_events, _ = _align_events(
        current_connection,
        gold_connection,
        args.similarity_threshold,
        args.minimum_margin,
    )
    gold_labels = _gold_labels(gold_connection, review)
    current_membership, current_package_events = _active_packages(current_connection)
    previous_membership, _ = _active_packages(gold_connection)
    actual_gold = {
        event_id: gold_labels[gold_id]
        for event_id, gold_id in mapping.items()
        if gold_id in gold_labels
    }
    actual_predicted = {
        event_id: current_membership.get(event_id, f"CURRENT_SINGLETON:{event_id}")
        for event_id in actual_gold
    }
    modes: dict[str, Any] = {
        "previous_actual_packages_on_matched_gold": _partition_evaluation(
            actual_gold,
            {
                event_id: previous_membership.get(
                    gold_event_id,
                    f"PREVIOUS_SINGLETON:{gold_event_id}",
                )
                for event_id, gold_event_id in mapping.items()
                if gold_event_id in gold_labels
            },
            current_events,
        ),
        "current_actual_packages": _partition_evaluation(
            actual_gold,
            actual_predicted,
            current_events,
        ),
    }
    package_redirects = _redirects(
        current_connection,
        "package_redirects",
        "source_package_id",
        "target_package_id",
    )
    recorded = _recorded_decisions(current_connection)
    recorded_gold, recorded_predicted = _decision_projection(
        recorded,
        mapping,
        gold_labels,
        package_redirects,
    )
    modes["recorded_original_n12"] = {
        "decision_count": len(recorded),
        "gold_evaluable_decision_count": len(recorded_gold),
        **_partition_evaluation(recorded_gold, recorded_predicted, current_events),
        **_candidate_relation_evaluation(
            recorded,
            mapping,
            gold_labels,
            current_package_events,
            package_redirects,
        ),
    }
    ab_metadata: dict[str, Any] | None = None
    if args.ab_report is not None:
        ab = json.loads(args.ab_report.read_text(encoding="utf-8"))
        ab_metadata = {
            key: value
            for key, value in ab.items()
            if key not in {"tasks", "decisions"}
        }
        for mode, decisions in ab.get("decisions", {}).items():
            mode_gold, mode_predicted = _decision_projection(
                decisions,
                mapping,
                gold_labels,
                package_redirects,
            )
            modes[f"n12_{mode}"] = {
                "decision_count": len(decisions),
                "gold_evaluable_decision_count": len(mode_gold),
                **_partition_evaluation(mode_gold, mode_predicted, current_events),
                **_candidate_relation_evaluation(
                    decisions,
                    mapping,
                    gold_labels,
                    current_package_events,
                    package_redirects,
                ),
            }
    result = {
        "report_version": "cdecr-n12-package-gold-projection-v1",
        "method": {
            "similarity_threshold": args.similarity_threshold,
            "minimum_best_vs_second_margin": args.minimum_margin,
            "current_atomic_count": len(current_events),
            "gold_aligned_atomic_count": len(mapping),
            "coverage": len(mapping) / len(current_events) if current_events else 0.0,
            "limitation": (
                "Metrics cover only high-confidence cross-run Atomic alignments. "
                "N12 modes are frozen-candidate node projections, not a sequential Apply rerun."
            ),
        },
        "published_previous_levels": {
            "reviewed_20260728": {"precision": 0.9154, "recall": 0.7354, "f1": 0.8156},
            "postopt_20260731_provisional": {
                "precision": 0.8582,
                "recall": 0.9884,
                "f1": 0.9190,
            },
        },
        "ab_metadata": ab_metadata,
        "modes": modes,
        "alignment": sorted(alignment, key=lambda item: item["similarity"], reverse=True),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "method": result["method"],
        "modes": {key: value["metrics"] for key, value in modes.items()},
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
