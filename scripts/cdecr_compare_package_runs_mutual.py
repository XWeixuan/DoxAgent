"""Compare two CDECR Package partitions on bidirectionally matched, Gold-evaluable Atomics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.cdecr_evaluate_n12_package_gold import (
    _active_packages,
    _align_events,
    _connection,
    _gold_labels,
    _partition_evaluation,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--previous-registry", type=Path, required=True)
    parser.add_argument("--current-registry", type=Path, required=True)
    parser.add_argument("--previous-package-evaluation", type=Path, required=True)
    parser.add_argument("--current-package-evaluation", type=Path, required=True)
    parser.add_argument("--gold-registry", type=Path, required=True)
    parser.add_argument("--gold-review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=0.90)
    parser.add_argument("--minimum-margin", type=float, default=0.01)
    return parser.parse_args()


def _accepted_gold(path: Path) -> dict[str, str]:
    report = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(item["current_event_id"]): str(item["gold_event_id"])
        for item in report["alignment"]
        if item.get("accepted")
    }


def _summary(evaluation: dict[str, Any]) -> dict[str, Any]:
    return {
        "metrics": evaluation["metrics"],
        "fragmentation": evaluation["fragmentation"],
        "errors": evaluation["errors"],
    }


def main() -> None:
    args = _args()
    previous_connection = _connection(args.previous_registry)
    current_connection = _connection(args.current_registry)
    forward, forward_audit, previous_events, _ = _align_events(
        previous_connection,
        current_connection,
        args.threshold,
        args.minimum_margin,
    )
    reverse, reverse_audit, current_events, _ = _align_events(
        current_connection,
        previous_connection,
        args.threshold,
        args.minimum_margin,
    )
    mutual = {
        previous_id: current_id
        for previous_id, current_id in forward.items()
        if reverse.get(current_id) == previous_id
    }
    previous_gold_events = _accepted_gold(args.previous_package_evaluation)
    current_gold_events = _accepted_gold(args.current_package_evaluation)
    gold_connection = _connection(args.gold_registry)
    gold_review = json.loads(args.gold_review.read_text(encoding="utf-8"))
    gold_labels = _gold_labels(gold_connection, gold_review)
    previous_event_package, _ = _active_packages(previous_connection)
    current_event_package, _ = _active_packages(current_connection)

    gold: dict[str, str] = {}
    previous_predicted: dict[str, str] = {}
    current_predicted: dict[str, str] = {}
    pairs: list[dict[str, Any]] = []
    for previous_id, current_id in sorted(mutual.items()):
        previous_gold_id = previous_gold_events.get(previous_id)
        current_gold_id = current_gold_events.get(current_id)
        if (
            previous_gold_id is None
            or current_gold_id is None
            or previous_gold_id not in gold_labels
            or current_gold_id not in gold_labels
            or gold_labels[previous_gold_id] != gold_labels[current_gold_id]
            or previous_id not in previous_event_package
            or current_id not in current_event_package
        ):
            continue
        gold[previous_id] = gold_labels[previous_gold_id]
        previous_predicted[previous_id] = previous_event_package[previous_id]
        current_predicted[previous_id] = current_event_package[current_id]
        pairs.append(
            {
                "previous_event_id": previous_id,
                "current_event_id": current_id,
                "gold_label": gold[previous_id],
                "previous_package_id": previous_predicted[previous_id],
                "current_package_id": current_predicted[previous_id],
                "previous_proposition": previous_events[previous_id]["canonical_proposition"],
                "current_proposition": current_events[current_id]["canonical_proposition"],
            }
        )

    previous_evaluation = _partition_evaluation(
        gold, previous_predicted, previous_events
    )
    current_evaluation = _partition_evaluation(
        gold, current_predicted, previous_events
    )
    output = {
        "report_version": "cdecr-mutual-package-run-comparison-v1",
        "method": {
            "forward_matches": len(forward),
            "reverse_matches": len(reverse),
            "mutual_matches": len(mutual),
            "gold_evaluable_mutual_matches": len(gold),
            "threshold": args.threshold,
            "minimum_margin": args.minimum_margin,
        },
        "previous": _summary(previous_evaluation),
        "current": _summary(current_evaluation),
        "aligned_pairs": pairs,
        "alignment_audit": {
            "forward": forward_audit,
            "reverse": reverse_audit,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "method": output["method"],
                "previous": previous_evaluation["metrics"],
                "current": current_evaluation["metrics"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
