"""Trace strict Mention FN to Dreamer, Grounder, or later output loss."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path

from cdecr.config import CDECRSettings
from cdecr.models import DashScopeEmbeddingClient


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--gold", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    return dot / norm if norm else 0.0


def _gold(paths: list[Path]) -> dict[int, dict[str, str]]:
    result: dict[int, dict[str, str]] = {}
    for path in paths:
        for raw in json.loads(path.read_text(encoding="utf-8"))["documents"]:
            index = int(raw.get("index", raw.get("document_index")))
            values = raw.get("gold", raw.get("gold_mentions", []))
            result[index] = {
                (str(item["id"]) if isinstance(item, dict) else f"D{index}G{offset}"): (
                    str(item["proposition"]) if isinstance(item, dict) else str(item)
                )
                for offset, item in enumerate(values, start=1)
            }
    return result


def main() -> int:
    args = _args()
    evaluation = json.loads(args.evaluation.read_text(encoding="utf-8"))
    gold = _gold(args.gold)
    with sqlite3.connect(args.registry) as connection:
        run_by_message = {
            str(message_id): str(run_id)
            for run_id, message_id in connection.execute(
                "SELECT run_id, message_id FROM document_processing_runs"
            )
        }
        candidates_by_message: dict[str, list[dict[str, object]]] = {}
        for message_id, run_id in run_by_message.items():
            candidates_by_message[message_id] = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT payload_json FROM dream_candidates WHERE run_id = ?",
                    (run_id,),
                )
            ]
        candidate_state: dict[str, str] = {}
        for (payload_json,) in connection.execute(
            "SELECT payload_json FROM grounder_batch_results"
        ):
            payload = json.loads(payload_json)
            for draft in payload.get("drafts", []):
                for candidate_id in draft.get("source_candidate_ids", []):
                    candidate_state[str(candidate_id)] = "GROUNDED"
            for rejected in payload.get("rejected_candidates", []):
                candidate_state[str(rejected["candidate_id"])] = (
                    f"REJECTED_{rejected['code']}"
                )
        for (payload_json,) in connection.execute(
            "SELECT payload_json FROM decision_audits WHERE decision_type = 'MENTION_DERIVATION'"
        ):
            payload = json.loads(payload_json)
            for derivation in payload.get("derivations", []):
                for candidate_id in derivation.get("source_candidate_ids", []):
                    candidate_state[str(candidate_id)] = "FINAL_OUTPUT"

    texts: list[str] = []
    for document in evaluation["documents"]:
        texts.extend(gold[int(document["index"])].values())
        message_id = f"doxatlas:raw_media:{document['source_row_id']}"
        texts.extend(str(item["statement"]) for item in candidates_by_message[message_id])
    texts = list(dict.fromkeys(texts))
    settings = CDECRSettings()
    client = DashScopeEmbeddingClient(
        api_key=settings.require_dashscope(),
        base_url=settings.dashscope_base_url,
        model=settings.model_m1,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=settings.dashscope_fallback_api_keys(),
    )
    vectors: dict[str, list[float]] = {}
    input_tokens = 0
    latency_ms = 0
    for offset in range(0, len(texts), 10):
        batch = texts[offset : offset + 10]
        embedded = client.embed(batch)
        input_tokens += embedded.input_tokens or 0
        latency_ms += embedded.latency_ms
        vectors.update(zip(batch, embedded.vectors, strict=True))

    traces: list[dict[str, object]] = []
    for document in evaluation["documents"]:
        index = int(document["index"])
        message_id = f"doxatlas:raw_media:{document['source_row_id']}"
        candidates = candidates_by_message[message_id]
        for missed in document["missed_gold"]:
            proposition = gold[index][str(missed["gold_id"])]
            ranked = sorted(
                (
                    (
                        _cosine(vectors[proposition], vectors[str(candidate["statement"])]),
                        candidate,
                    )
                    for candidate in candidates
                ),
                key=lambda item: item[0],
                reverse=True,
            )
            score, candidate = ranked[0] if ranked else (0.0, {})
            candidate_id = str(candidate.get("candidate_id", ""))
            state = candidate_state.get(candidate_id, "NO_DISPOSITION")
            if score < 0.78:
                stage = "DREAMER_MISSING_OR_LOW_COVERAGE"
            elif state.startswith("REJECTED_"):
                stage = "GROUNDER_REJECTED"
            elif state == "NO_DISPOSITION":
                stage = "GROUNDER_TECHNICAL_LOSS"
            else:
                stage = "OUTPUT_PARTIAL_OR_JUDGE_LOSS"
            traces.append(
                {
                    "document_index": index,
                    "gold_id": missed["gold_id"],
                    "gold": proposition,
                    "best_candidate_id": candidate_id or None,
                    "best_candidate": candidate.get("statement"),
                    "similarity": round(score, 6),
                    "candidate_state": state,
                    "root_stage": stage,
                }
            )
    summary: dict[str, int] = {}
    for trace in traces:
        stage = str(trace["root_stage"])
        summary[stage] = summary.get(stage, 0) + 1
    report = {
        "report_version": "cdecr-mention-miss-trace-v1",
        "registry": str(args.registry),
        "similarity_threshold": 0.78,
        "summary": summary,
        "embedding_input_tokens": input_tokens,
        "embedding_latency_ms": latency_ms,
        "traces": traces,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
