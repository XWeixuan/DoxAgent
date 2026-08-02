"""Combine disjoint CDECR N9 A/B shards using raw counts, never averaged rates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sum_metric(shards: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    metrics = [item[arm]["metrics"] for item in shards]
    task_count = sum(item["task_count"] for item in metrics)
    correct_merge = sum(item["correct_merge"] for item in metrics)
    incorrect_merge = sum(item["incorrect_merge"] for item in metrics)
    correct_create_new = sum(item["correct_create_new"] for item in metrics)
    incorrect_create_new = sum(item["incorrect_create_new"] for item in metrics)
    opportunities = sum(item["judgeable_same_opportunities"] for item in metrics)
    tasks = [task for item in metrics for task in item["tasks"]]
    if arm == "b":
        tasks = [
            (
                {
                    **task,
                    "action": "CREATE_NEW",
                    "target": None,
                    "deterministic_reason": "NO_CANDIDATE_CREATE_NEW",
                }
                if task.get("action") is None
                else task
            )
            for task in tasks
        ]
    if arm == "a":
        tasks = [
            (
                {**task, "predicted_same_candidate_ids": [task["target"]]}
                if task.get("action") == "MERGE"
                and isinstance(task.get("target"), str)
                and not task.get("predicted_same_candidate_ids")
                else task
            )
            for task in tasks
        ]
    missed_links = sum(
        len(
            set(task["gold_same_candidate_ids"])
            - set(task["predicted_same_candidate_ids"])
        )
        for task in tasks
    )
    total_same_links = sum(len(item["gold_same_candidate_ids"]) for item in tasks)
    merge_total = correct_merge + incorrect_merge
    no_opportunity = task_count - opportunities
    action_correct = correct_merge + correct_create_new
    return {
        "task_count": task_count,
        "action_accuracy": action_correct / task_count if task_count else None,
        "merge_precision": correct_merge / merge_total if merge_total else None,
        "conditional_merge_recall": correct_merge / opportunities if opportunities else None,
        "create_new_accuracy": (
            correct_create_new / no_opportunity if no_opportunity else None
        ),
        "correct_merge": correct_merge,
        "incorrect_merge": incorrect_merge,
        "correct_create_new": correct_create_new,
        "incorrect_create_new": incorrect_create_new,
        "judgeable_same_opportunities": opportunities,
        "fragmented_tasks": incorrect_create_new,
        "fragmentation_rate": (
            incorrect_create_new / opportunities if opportunities else None
        ),
        "projected_excess_atomic_components": incorrect_create_new,
        "missed_same_candidate_links": missed_links,
        "same_candidate_link_recall": (
            (total_same_links - missed_links) / total_same_links
            if total_same_links
            else None
        ),
        "tasks": tasks,
    }


def _sum_usage(shards: list[dict[str, Any]], arm: str) -> dict[str, int]:
    keys = ["model_calls", "input_tokens", "output_tokens", "latency_ms"]
    if arm == "b":
        keys.append("payload_bytes")
    return {key: sum(int(item[arm].get(key) or 0) for item in shards) for key in keys}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite output: {args.output}")
    shards = [_load(path) for path in args.shard]
    messages = [message for item in shards for message in item["message_ids"]]
    if len(messages) != len(set(messages)):
        raise ValueError("N9 A/B shards overlap")
    a_metrics = _sum_metric(shards, "a")
    b_metrics = _sum_metric(shards, "b")
    a_usage = _sum_usage(shards, "a")
    b_usage = _sum_usage(shards, "b")
    payload = {
        "report_version": "cdecr-n9-node-ab-v1-combined",
        "comparison": "persisted_A_vs_real_B_only",
        "documents": len(messages),
        "message_ids": messages,
        "shards": [str(path) for path in args.shard],
        "model": shards[0]["model"],
        "a": {"metrics": a_metrics, **a_usage},
        "b": {"metrics": b_metrics, **b_usage},
        "delta": {
            "input_tokens": b_usage["input_tokens"] - a_usage["input_tokens"],
            "input_token_ratio": (
                b_usage["input_tokens"] / a_usage["input_tokens"]
                if a_usage["input_tokens"]
                else None
            ),
            "action_accuracy_pp": (
                b_metrics["action_accuracy"] - a_metrics["action_accuracy"]
            )
            * 100,
            "merge_precision_pp": (
                b_metrics["merge_precision"] - a_metrics["merge_precision"]
            )
            * 100,
            "conditional_merge_recall_pp": (
                b_metrics["conditional_merge_recall"]
                - a_metrics["conditional_merge_recall"]
            )
            * 100,
            "fragmented_tasks": (
                b_metrics["fragmented_tasks"] - a_metrics["fragmented_tasks"]
            ),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
