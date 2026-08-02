"""Independently review persisted N7 candidates and N9 assignments at Atomic granularity."""

from __future__ import annotations

import argparse
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict

from cdecr.cli import _structured_client
from cdecr.config import CDECRSettings
from cdecr.models import ModelTier
from cdecr.ports import StructuredModelRequest


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CandidateJudgment(StrictModel):
    candidate_event_id: str
    relation: Literal["SAME_ATOMIC", "NOT_SAME_ATOMIC", "UNJUDGEABLE"]
    reason: str


class TaskJudgment(StrictModel):
    mention_id: str
    candidates: list[CandidateJudgment]


class ReviewBatch(StrictModel):
    tasks: list[TaskJudgment]


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=12)
    return parser.parse_args()


def _connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _event_before(
    connection: sqlite3.Connection, event_id: str, created_at: str
) -> dict[str, object] | None:
    row = connection.execute(
        """
        SELECT payload_json
        FROM atomic_event_versions
        WHERE event_id = ? AND created_at <= ?
        ORDER BY version DESC
        LIMIT 1
        """,
        (event_id, created_at),
    ).fetchone()
    if row is None:
        row = connection.execute(
            """
            SELECT payload_json
            FROM atomic_event_versions
            WHERE event_id = ?
            ORDER BY version ASC
            LIMIT 1
            """,
            (event_id,),
        ).fetchone()
    return json.loads(str(row["payload_json"])) if row is not None else None


def _task_payloads(path: Path) -> list[dict[str, object]]:
    connection = _connection(path)
    mentions = {
        str(row["mention_id"]): json.loads(str(row["payload_json"]))
        for row in connection.execute("SELECT mention_id, payload_json FROM event_mentions")
    }
    snapshots = {
        str(row["subject_id"]): json.loads(str(row["payload_json"]))
        for row in connection.execute(
            """
            SELECT subject_id, payload_json
            FROM decision_audits
            WHERE decision_type = 'ATOMIC_N7_RANKED_CANDIDATES'
            """
        )
    }
    tasks: list[dict[str, object]] = []
    for row in connection.execute(
        """
        SELECT mention_id, action, candidate_event_id, resulting_event_id,
               json_extract(payload_json, '$.reason') AS reason, created_at
        FROM atomic_assignment_decisions
        ORDER BY created_at, mention_id
        """
    ):
        mention_id = str(row["mention_id"])
        snapshot = snapshots.get(mention_id, {})
        raw_candidates = snapshot.get("candidates", [])
        candidates: list[dict[str, object]] = []
        if isinstance(raw_candidates, list):
            for raw in raw_candidates:
                if not isinstance(raw, dict) or not raw.get("selected"):
                    continue
                event_id = raw.get("candidate_root_id")
                if not isinstance(event_id, str):
                    continue
                event = _event_before(connection, event_id, str(row["created_at"]))
                if event is None:
                    continue
                claims = event.get("consensus_claims", {})
                source_claims = claims.get("source_claims", []) if isinstance(claims, dict) else []
                propositions = [
                    str(item.get("canonical_proposition"))
                    for item in source_claims
                    if isinstance(item, dict) and item.get("canonical_proposition")
                ][:5]
                if not propositions and event.get("canonical_proposition"):
                    propositions = [str(event["canonical_proposition"])]
                candidates.append(
                    {
                        "candidate_event_id": event_id,
                        "rank": raw.get("rank"),
                        "propositions": propositions,
                    }
                )
        mention = mentions[mention_id]
        tasks.append(
            {
                "mention_id": mention_id,
                "incoming": {
                    key: mention.get(key)
                    for key in (
                        "canonical_proposition",
                        "event_family",
                        "predicate",
                        "participants",
                        "time",
                        "assertion_state",
                        "quantities",
                        "open_attributes",
                    )
                },
                "candidates": candidates,
                "n9_action": str(row["action"]),
                "n9_candidate_event_id": row["candidate_event_id"],
                "n9_reason": row["reason"],
            }
        )
    return tasks


def main() -> int:
    args = _args()
    tasks = _task_payloads(args.registry)
    batches = [
        tasks[index : index + args.batch_size]
        for index in range(0, len(tasks), args.batch_size)
    ]
    settings = CDECRSettings()

    def review(batch: list[dict[str, object]]) -> dict[str, object]:
        client = _structured_client(settings, ModelTier.M4)
        result = client.complete(
            StructuredModelRequest(
                system_prompt=(
                    "Independently judge minimum Atomic event identity. SAME_ATOMIC requires the "
                    "same complete fact: compatible participant roles, action/polarity, metric or "
                    "object, assertion, period/session and occurrence. A shared report, episode, "
                    "company, topic or causal context is insufficient. Different values may be "
                    "claim conflict only when every identity dimension is the same. Return one "
                    "judgment for every supplied candidate ID and concise reasons."
                ),
                user_prompt=json.dumps({"tasks": batch}, ensure_ascii=False, separators=(",", ":")),
                json_schema=ReviewBatch.model_json_schema(),
            )
        )
        parsed = ReviewBatch.model_validate(result.payload)
        return {
            "input_tokens": result.input_tokens or 0,
            "output_tokens": result.output_tokens or 0,
            "latency_ms": result.latency_ms,
            "tasks": [item.model_dump(mode="json") for item in parsed.tasks],
        }

    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 4))) as executor:
        reviews = list(executor.map(review, batches))
    judgment_by_pair = {
        (str(task["mention_id"]), str(candidate["candidate_event_id"])): candidate
        for review in reviews
        for task in cast(list[dict[str, object]], review["tasks"])
        for candidate in cast(list[dict[str, object]], task["candidates"])
    }
    rows: list[dict[str, object]] = []
    candidate_coverage = 0
    opportunities = hits = correct_merges = incorrect_merges = 0
    create_new_correct = create_new_incorrect = 0
    for task in tasks:
        candidate_ids = [
            str(item["candidate_event_id"])
            for item in cast(list[dict[str, object]], task["candidates"])
        ]
        judgments = [
            judgment_by_pair.get((str(task["mention_id"]), candidate_id))
            for candidate_id in candidate_ids
        ]
        covered = all(item is not None for item in judgments)
        candidate_coverage += int(covered)
        same_ids = {
            candidate_id
            for candidate_id, judgment in zip(candidate_ids, judgments, strict=True)
            if judgment is not None and judgment["relation"] == "SAME_ATOMIC"
        }
        has_opportunity = bool(same_ids)
        opportunities += int(has_opportunity)
        chosen = task["n9_candidate_event_id"]
        is_merge = task["n9_action"] == "MERGE"
        correct_merge = is_merge and isinstance(chosen, str) and chosen in same_ids
        hits += int(correct_merge)
        correct_merges += int(correct_merge)
        incorrect_merges += int(is_merge and not correct_merge)
        create_new_correct += int(not is_merge and not has_opportunity)
        create_new_incorrect += int(not is_merge and has_opportunity)
        rows.append(
            {
                **task,
                "review_same_candidate_ids": sorted(same_ids),
                "review_judgments": judgments,
                "candidate_coverage": covered,
            }
        )
    merge_total = correct_merges + incorrect_merges
    create_total = create_new_correct + create_new_incorrect
    payload = {
        "report_version": "cdecr-n9-independent-review-v1",
        "method": "Independent M4 review of every selected N7 candidate at assignment time.",
        "summary": {
            "task_count": len(tasks),
            "candidate_coverage": candidate_coverage / len(tasks) if tasks else 1.0,
            "n7_judgeable_same_opportunities": opportunities,
            "n9_correct_merge": correct_merges,
            "n9_incorrect_merge": incorrect_merges,
            "n9_merge_precision": correct_merges / merge_total if merge_total else 1.0,
            "n9_conditional_merge_recall": hits / opportunities if opportunities else 1.0,
            "create_new_correct": create_new_correct,
            "create_new_incorrect": create_new_incorrect,
            "create_new_accuracy": create_new_correct / create_total if create_total else 1.0,
            "input_tokens": sum(cast(int, item["input_tokens"]) for item in reviews),
            "output_tokens": sum(cast(int, item["output_tokens"]) for item in reviews),
            "latency_ms": sum(cast(int, item["latency_ms"]) for item in reviews),
        },
        "tasks": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
