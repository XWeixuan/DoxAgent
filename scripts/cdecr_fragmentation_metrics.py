from __future__ import annotations

import argparse
import itertools
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _pair_metrics(
    units: list[dict[str, Any]],
    *,
    gold_key: str,
    predicted_key: str,
) -> dict[str, Any]:
    tp = fp = fn = 0
    for left, right in itertools.combinations(units, 2):
        gold_same = left[gold_key] == right[gold_key]
        predicted_same = left[predicted_key] == right[predicted_key]
        if gold_same and predicted_same:
            tp += 1
        elif predicted_same:
            fp += 1
        elif gold_same:
            fn += 1
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "pair_fragmentation_rate": 1.0 - recall if recall is not None else None,
    }


def _component_profiles(
    units: list[dict[str, Any]],
    *,
    gold_key: str,
    predicted_key: str,
) -> tuple[float | None, list[dict[str, Any]]]:
    gold_members: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for unit in units:
        gold_members[str(unit[gold_key])].append(unit)
    multi_member = {key: value for key, value in gold_members.items() if len(value) > 1}
    profiles: list[dict[str, Any]] = []
    for gold_id, members in multi_member.items():
        counts = sorted(
            Counter(str(member[predicted_key]) for member in members).values(), reverse=True
        )
        if len(counts) < 2:
            continue
        profiles.append(
            {
                "gold_cluster_id": gold_id,
                "gold_member_count": len(members),
                "component_count": len(counts),
                "component_sizes": counts,
                "component_distribution": "+".join(str(value) for value in counts),
                "largest_component_coverage": counts[0] / len(members),
            }
        )
    rate = len(profiles) / len(multi_member) if multi_member else None
    profiles.sort(key=lambda item: (-item["gold_member_count"], item["gold_cluster_id"]))
    return rate, profiles


def evaluate_level(
    items: list[dict[str, Any]],
    *,
    gold_key: str,
    predicted_key: str,
) -> dict[str, Any]:
    required = {"source_occurrence_id", gold_key, predicted_key}
    units = [
        item for item in items if required.issubset(item) and all(item[key] for key in required)
    ]
    metrics = _pair_metrics(units, gold_key=gold_key, predicted_key=predicted_key)
    rate, profiles = _component_profiles(units, gold_key=gold_key, predicted_key=predicted_key)
    return {
        "unit_count": len(units),
        **metrics,
        "fragmented_cluster_rate": rate,
        "fragmented_clusters": len(profiles),
        "component_profiles": profiles,
    }


def evaluate_package_conditional(items: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if (
            item.get("gold_atomic_id")
            and item.get("gold_package_id")
            and item.get("predicted_package_id")
        ):
            grouped[str(item["gold_atomic_id"])].append(item)
    units: list[dict[str, Any]] = []
    conflicts: list[str] = []
    for gold_atomic_id, members in grouped.items():
        gold_packages = {str(member["gold_package_id"]) for member in members}
        if len(gold_packages) != 1:
            conflicts.append(gold_atomic_id)
            continue
        units.append(
            {
                "gold_atomic_id": gold_atomic_id,
                "gold_package_id": next(iter(gold_packages)),
                "predicted_package_ids": {
                    str(member["predicted_package_id"]) for member in members
                },
            }
        )
    tp = fp = fn = 0
    for left, right in itertools.combinations(units, 2):
        gold_same = left["gold_package_id"] == right["gold_package_id"]
        predicted_same = bool(
            left["predicted_package_ids"].intersection(right["predicted_package_ids"])
        )
        if gold_same and predicted_same:
            tp += 1
        elif predicted_same:
            fp += 1
        elif gold_same:
            fn += 1
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {
        "gold_atomic_unit_count": len(units),
        "inconsistent_gold_atomic_ids": sorted(conflicts),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "pair_fragmentation_rate": 1.0 - recall if recall is not None else None,
    }


def evaluate(items: list[dict[str, Any]]) -> dict[str, Any]:
    occurrence_ids = [str(item.get("source_occurrence_id")) for item in items]
    if len(occurrence_ids) != len(set(occurrence_ids)):
        raise ValueError("source_occurrence_id must be unique")
    return {
        "report_version": "cdecr-fragmentation-v1",
        "atomic": evaluate_level(
            items,
            gold_key="gold_atomic_id",
            predicted_key="predicted_atomic_id",
        ),
        "package_end_to_end": evaluate_level(
            items,
            gold_key="gold_package_id",
            predicted_key="predicted_package_id",
        ),
        "package_conditional": evaluate_package_conditional(items),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute redirect-resolved fragmentation metrics")
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    source = json.loads(args.mapping.read_text(encoding="utf-8"))
    items = source.get("items") if isinstance(source, dict) else None
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError("mapping must contain an items array")
    result = evaluate(items)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
