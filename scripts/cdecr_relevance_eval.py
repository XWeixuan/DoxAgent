"""Evaluate the production CDECR relevance Gate on a fixed candidate corpus."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from importlib import resources
from pathlib import Path
from time import perf_counter
from typing import Any

from cdecr.cli import _document_processor, _structured_client
from cdecr.config import CDECRSettings
from cdecr.contracts import SourceMessage
from cdecr.models import ModelAdapterError, ModelTier
from cdecr.preprocessing import exact_document_fingerprint, preprocess_source
from cdecr.registry import SQLiteCDECRRegistry
from cdecr.relevance_filter import (
    dreamer_block_exposed_lengths,
    dreamer_block_request,
    frozen_relevance_response_request,
    select_candidates_fail_open,
)
from cdecr.single_document_contracts import DreamCandidate, DreamerModelOutput

DEFAULT_INPUT = Path(
    "eval/cdecr_relevance_filter/mu_relevance_30_v1/"
    "cdecr_mu_relevance_30_candidates_input.jsonl"
)
DEFAULT_GOLD = Path(
    "eval/cdecr_relevance_filter/mu_relevance_30_v1/"
    "cdecr_mu_relevance_30_candidates_gold_readjudicated_v2.jsonl"
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("frozen", "end-to-end"), required=True)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--mention-gold-source", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--resume-from", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--gate-mode", choices=("off", "shadow", "enforce"), default="shadow")
    return parser.parse_args()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _prompt(name: str) -> str:
    return resources.files("cdecr.prompts.v1").joinpath(name).read_text(encoding="utf-8")


def _target_ticker(row: dict[str, Any]) -> str:
    target = row["target"]
    return str(target["ticker"]).strip().upper()


def _gold_by_document(path: Path) -> dict[str, dict[str, str]]:
    values: dict[str, dict[str, str]] = {}
    for row in _read_jsonl(path):
        values[str(row["document_id"])] = {
            str(item["candidate_id"]): str(item["relevance"])
            for item in row["candidate_gold"]
        }
    return values


def _candidate_direct_gold_ids(path: Path) -> set[str]:
    """Include candidate-level Direct adjudications that legacy Mention mapping cannot see."""

    direct_ids: set[str] = set()
    for row in _read_jsonl(path):
        for item in row["candidate_gold"]:
            relevance_type = item.get("second_adjudication_relevance_type")
            if relevance_type is None:
                relevance_type = item.get("relevance_type")
            if str(relevance_type).upper() == "DIRECT":
                direct_ids.add(str(item["candidate_id"]))
    return direct_ids


def _candidate_mention_mapping(
    *, input_rows: list[dict[str, Any]], mention_source: Path | None
) -> list[dict[str, Any]]:
    if mention_source is None:
        return []
    source_by_document = {
        str(row["document_id"]): row for row in _read_jsonl(mention_source)
    }
    mappings: list[dict[str, Any]] = []
    for row in input_rows:
        document_id = str(row["document_id"])
        mention_evidence: list[tuple[str, int, int, str, dict[str, Any]]] = []
        for mention in source_by_document[document_id]["mentions"]:
            payload = mention["payload"]
            records = payload.get("evidence_records", [])
            spans = payload.get("evidence_spans", [])
            for index, record in enumerate(records):
                paired = [item for item in spans if item.get("text") == record.get("text")]
                if not paired and len(records) == len(spans):
                    paired = [spans[index]]
                for span in paired:
                    mention_evidence.append(
                        (
                            str(record.get("segment_id")),
                            int(span.get("start_char", -1)),
                            int(span.get("end_char", -1)),
                            str(record.get("text", "")),
                            mention,
                        )
                    )
        for candidate in row["dream_candidates"]:
            hits: dict[str, tuple[dict[str, Any], str]] = {}
            for evidence in candidate["evidence_locations"]:
                for segment_id, start_char, end_char, text, mention in mention_evidence:
                    if str(evidence["segment_id"]) != segment_id:
                        continue
                    exact = str(evidence["text"]) == text
                    overlap = max(int(evidence["start_char"]), start_char) < min(
                        int(evidence["end_char"]), end_char
                    )
                    if exact or overlap:
                        mention_id = str(mention["mention_id"])
                        method = "exact" if exact else "overlap"
                        existing = hits.get(mention_id)
                        if existing is None or method == "exact":
                            hits[mention_id] = (mention, method)
            labels = {
                bool(mention["relevance_gold"]["is_relevant"])
                for mention, _ in hits.values()
            }
            methods = {method for _, method in hits.values()}
            mappings.append(
                {
                    "document_id": document_id,
                    "candidate_id": candidate["candidate_id"],
                    "mapping_method": (
                        "exact"
                        if "exact" in methods
                        else "overlap_only"
                        if methods
                        else "unmapped"
                    ),
                    "mention_ids": sorted(hits),
                    "mention_relevance_types": sorted(
                        {
                            str(mention["relevance_gold"]["relevance_type"])
                            for mention, _ in hits.values()
                        }
                    ),
                    "mention_label_conflict": len(labels) > 1,
                }
            )
    return mappings


def _assign_candidates_to_blocks(
    source: SourceMessage,
    candidates: list[DreamCandidate],
) -> tuple[Any, list[tuple[Any, list[DreamCandidate]]]]:
    document = preprocess_source(source, known_documents=[]).document
    grouped: dict[int, list[DreamCandidate]] = defaultdict(list)
    for candidate in candidates:
        assigned = False
        for index, block in enumerate(document.document_blocks):
            exposed = dreamer_block_exposed_lengths(document, block)
            if all(
                evidence.segment_id in exposed
                and evidence.end_char <= exposed[evidence.segment_id]
                for evidence in candidate.evidence_locations
            ):
                grouped[index].append(candidate)
                assigned = True
                break
        if not assigned:
            raise ValueError(
                "candidate cannot be assigned to a Dreamer block: "
                f"{candidate.candidate_id}"
            )
    return document, [
        (document.document_blocks[index], grouped[index]) for index in sorted(grouped)
    ]


def _frozen_document(
    row: dict[str, Any],
    *,
    client: Any,
    settings: CDECRSettings,
) -> dict[str, Any]:
    source = SourceMessage.model_validate(row["source_message"])
    candidates = [DreamCandidate.model_validate(item) for item in row["dream_candidates"]]
    document, groups = _assign_candidates_to_blocks(source, candidates)
    predictions: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    for block, group in groups:
        dreamer_request = dreamer_block_request(
            source=source,
            document=document,
            block=block,
            system_prompt=_prompt("dreamer.md"),
        )
        frozen_output = DreamerModelOutput(
            candidates=[
                {
                    "statement": candidate.statement,
                    "evidence_locations": [
                        {"segment_id": item.segment_id, "text": item.text}
                        for item in candidate.evidence_locations
                    ],
                }
                for candidate in group
            ]
        )
        request, short_to_full = frozen_relevance_response_request(
            dreamer_request=dreamer_request,
            frozen_output=frozen_output,
            target=_target_ticker(row),
            candidates=group,
            relevance_system_prompt=_prompt("relevance_filter.md"),
        )
        request = request.model_copy(
            update={
                "metadata": {
                    "stage": "dreamer_relevance_frozen_eval",
                    "document_id": row["document_id"],
                    "block_id": block.block_id,
                    "candidate_count": len(group),
                }
            }
        )
        started = perf_counter()
        try:
            result = client.complete_response(request)
        except Exception as exc:
            code = exc.code if isinstance(exc, ModelAdapterError) else type(exc).__name__
            decisions = [
                {
                    "candidate_id": candidate.candidate_id,
                    "prediction": None,
                    "effective_action": "KEEP_FAIL_OPEN",
                    "decision_reason": f"GATE_CALL_FAILED:{code}",
                    "block_id": block.block_id,
                }
                for candidate in group
            ]
            calls.append(
                {
                    "block_id": block.block_id,
                    "candidate_count": len(group),
                    "status": "FAILED",
                    "error_code": code,
                    "latency_ms": round((perf_counter() - started) * 1000),
                }
            )
        else:
            selection = select_candidates_fail_open(
                group,
                result.payload,
                short_to_full=short_to_full,
            )
            decisions = [
                {
                    "candidate_id": item.candidate_id,
                    "prediction": item.relevance,
                    "effective_action": "DROP" if not item.keep else "KEEP",
                    "decision_reason": item.reason,
                    "block_id": block.block_id,
                }
                for item in selection.decisions
            ]
            calls.append(
                {
                    "block_id": block.block_id,
                    "candidate_count": len(group),
                    "status": "SUCCEEDED",
                    "input_tokens": result.input_tokens,
                    "cached_input_tokens": result.cached_input_tokens,
                    "output_tokens": result.output_tokens,
                    "reasoning_tokens": result.reasoning_tokens,
                    "latency_ms": result.latency_ms,
                    "request_id": result.request_id,
                    "response_id": result.response_id,
                }
            )
        predictions.extend(decisions)
    by_id = {item["candidate_id"]: item for item in predictions}
    if set(by_id) != {candidate.candidate_id for candidate in candidates}:
        raise ValueError(f"prediction ID mismatch for {row['document_id']}")
    return {
        "document_id": row["document_id"],
        "selection_category": row["selection_category"],
        "predictions": [by_id[candidate.candidate_id] for candidate in candidates],
        "calls": calls,
    }


def _score(
    rows: list[dict[str, Any]],
    *,
    gold: dict[str, dict[str, str]],
    direct_candidate_ids: set[str],
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for row in rows:
        document_gold = gold[str(row["document_id"])]
        for prediction in row["predictions"]:
            candidate_id = str(prediction["candidate_id"])
            records.append(
                {
                    **prediction,
                    "document_id": row["document_id"],
                    "selection_category": row["selection_category"],
                    "gold": document_gold[candidate_id],
                    "direct_gold": candidate_id in direct_candidate_ids,
                }
            )

    def metrics(values: list[dict[str, Any]]) -> dict[str, Any]:
        relevant = [item for item in values if item["gold"] == "RELEVANT"]
        dropped = [item for item in values if item["effective_action"] == "DROP"]
        valid = [item for item in values if item["prediction"] is not None]
        correct = [item for item in valid if item["prediction"] == item["gold"]]
        relevant_kept = [item for item in relevant if item["effective_action"] != "DROP"]
        correct_drops = [item for item in dropped if item["gold"] == "IRRELEVANT"]
        direct_false_negatives = [
            item
            for item in values
            if item["direct_gold"] and item["effective_action"] == "DROP"
        ]
        return {
            "candidates": len(values),
            "gold_relevant": len(relevant),
            "gold_irrelevant": len(values) - len(relevant),
            "valid_predictions": len(valid),
            "fail_open_predictions": len(values) - len(valid),
            "dropped_candidates": len(dropped),
            "relevant_recall": len(relevant_kept) / len(relevant) if relevant else None,
            "dropped_candidate_precision": (
                len(correct_drops) / len(dropped) if dropped else None
            ),
            "valid_prediction_accuracy": len(correct) / len(valid) if valid else None,
            "direct_relevant_false_negatives": len(direct_false_negatives),
            "direct_relevant_false_negative_ids": [
                item["candidate_id"] for item in direct_false_negatives
            ],
        }

    by_slice = {
        category: metrics(
            [item for item in records if item["selection_category"] == category]
        )
        for category in sorted({str(item["selection_category"]) for item in records})
    }
    calls = [call for row in rows for call in row["calls"]]
    usage_fields = (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "latency_ms",
    )
    return {
        "overall": metrics(records),
        "slices": by_slice,
        "provider_calls": {
            "total": len(calls),
            "succeeded": sum(call["status"] == "SUCCEEDED" for call in calls),
            "failed": sum(call["status"] != "SUCCEEDED" for call in calls),
            **{
                field: sum(int(call.get(field) or 0) for call in calls)
                for field in usage_fields
            },
        },
        "bad_cases": [
            item
            for item in records
            if item["prediction"] is not None and item["prediction"] != item["gold"]
        ],
    }


def _run_frozen(args: argparse.Namespace) -> int:
    input_rows = _read_jsonl(args.input)
    gold = _gold_by_document(args.gold)
    if set(gold) != {str(row["document_id"]) for row in input_rows}:
        raise SystemExit("Gold document IDs do not match the frozen input")
    settings = CDECRSettings()
    client = _structured_client(settings, ModelTier.M2)
    previous_by_document: dict[str, dict[str, Any]] = {}
    retry_history: list[dict[str, Any]] = []
    rows_to_run = input_rows
    if args.resume_from is not None:
        previous = _read_jsonl(args.resume_from)
        previous_by_document = {str(row["document_id"]): row for row in previous}
        if set(previous_by_document) != {str(row["document_id"]) for row in input_rows}:
            raise SystemExit("--resume-from document IDs do not match the frozen input")
        retry_ids = {
            document_id
            for document_id, row in previous_by_document.items()
            if any(item["prediction"] is None for item in row["predictions"])
        }
        rows_to_run = [
            row for row in input_rows if str(row["document_id"]) in retry_ids
        ]
        retry_history = [
            {"document_id": document_id, "calls": row["calls"]}
            for document_id, row in previous_by_document.items()
            if document_id in retry_ids
        ]
        if not rows_to_run:
            raise SystemExit("--resume-from contains no fail-open predictions to retry")
    worker_count = max(1, min(args.workers, len(rows_to_run)))
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        rerun_rows = list(
            pool.map(
                lambda row: _frozen_document(
                    row,
                    client=client,
                    settings=settings,
                ),
                rows_to_run,
            )
        )
    rerun_by_document = {str(row["document_id"]): row for row in rerun_rows}
    rows = [
        rerun_by_document.get(
            str(row["document_id"]),
            previous_by_document.get(str(row["document_id"])),
        )
        for row in input_rows
    ]
    if any(row is None for row in rows):
        raise RuntimeError("frozen evaluation did not produce every document result")
    mention_mapping = _candidate_mention_mapping(
        input_rows=input_rows,
        mention_source=args.mention_gold_source,
    )
    mention_direct_ids = {
        str(item["candidate_id"])
        for item in mention_mapping
        if "DIRECT" in item["mention_relevance_types"]
    }
    adjudicated_direct_ids = _candidate_direct_gold_ids(args.gold)
    direct_ids = mention_direct_ids | adjudicated_direct_ids
    score = _score(rows, gold=gold, direct_candidate_ids=direct_ids)
    mapping_counts = Counter(
        str(item["mapping_method"]) for item in mention_mapping
    )
    report = {
        "report_version": "cdecr-relevance-frozen-v1",
        "mode": "frozen",
        "quality_corpus_only": True,
        "cost_estimation_valid": False,
        "provider": settings.model_m2_provider,
        "model": settings.model_m2,
        "documents": len(rows),
        "candidates": sum(len(row["predictions"]) for row in rows),
        "resume": {
            "source": str(args.resume_from) if args.resume_from is not None else None,
            "retried_documents": [row["document_id"] for row in rerun_rows],
            "prior_attempts": retry_history,
        },
        "candidate_mention_mapping": {
            "used_for_gate_requests": False,
            "mapped": mapping_counts["exact"] + mapping_counts["overlap_only"],
            "unmapped": mapping_counts["unmapped"],
            "exact": mapping_counts["exact"],
            "overlap_only": mapping_counts["overlap_only"],
            "mention_label_conflicts": sum(
                bool(item["mention_label_conflict"]) for item in mention_mapping
            ),
            "mention_direct_candidate_count": len(mention_direct_ids),
            "candidate_gold_direct_count": len(adjudicated_direct_ids),
            "direct_candidate_count": len(direct_ids),
        },
        **score,
    }
    report["provider_calls"]["actual_attempts_including_prior_failures"] = (
        report["provider_calls"]["total"]
        + sum(len(item["calls"]) for item in retry_history)
    )
    report["provider_calls"]["actual_failed_attempts_including_prior"] = (
        report["provider_calls"]["failed"]
        + sum(
            call["status"] != "SUCCEEDED"
            for item in retry_history
            for call in item["calls"]
        )
    )
    report["provider_calls"]["actual_latency_ms_including_prior"] = (
        report["provider_calls"]["latency_ms"]
        + sum(
            int(call.get("latency_ms") or 0)
            for item in retry_history
            for call in item["calls"]
        )
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = args.output_dir / "predictions.jsonl"
    predictions_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    if mention_mapping:
        (args.output_dir / "candidate_mention_mapping.jsonl").write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in mention_mapping),
            encoding="utf-8",
        )
    (args.output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output_dir": str(args.output_dir), **report["overall"]}))
    return 0


def _run_end_to_end(args: argparse.Namespace) -> int:
    if args.registry is None:
        raise SystemExit("--registry is required for end-to-end mode")
    if args.registry.exists():
        raise SystemExit(f"refusing to reuse output registry: {args.registry}")
    rows = _read_jsonl(args.input)
    settings = CDECRSettings(
        CDECR_SQLITE_PATH=args.registry,
        CDECR_RELEVANCE_FILTER_MODE=args.gate_mode,
        CDECR_RELEVANCE_TARGET_PROFILES={},
    )
    registry = SQLiteCDECRRegistry(args.registry)
    registry.initialize()
    message_ids: list[str] = []
    for row in rows:
        source = SourceMessage.model_validate(row["source_message"])
        registry.save_source(source, fingerprint=exact_document_fingerprint(source))
        message_ids.append(source.message_id)
    started = perf_counter()
    results = _document_processor(settings, registry).process_batch(message_ids)
    wall_ms = round((perf_counter() - started) * 1000)
    by_stage: dict[str, dict[str, int]] = {}
    with sqlite3.connect(args.registry) as connection:
        for stage, calls, input_tokens, output_tokens, latency_ms in connection.execute(
            """
            SELECT stage, COUNT(*), COALESCE(SUM(input_tokens), 0),
                   COALESCE(SUM(output_tokens), 0), COALESCE(SUM(latency_ms), 0)
            FROM model_calls GROUP BY stage ORDER BY stage
            """
        ):
            by_stage[str(stage)] = {
                "calls": int(calls),
                "input_tokens": int(input_tokens),
                "output_tokens": int(output_tokens),
                "latency_ms": int(latency_ms),
            }
    report = {
        "report_version": "cdecr-relevance-end-to-end-v1",
        "mode": "end-to-end",
        "gate_mode": args.gate_mode,
        "quality_corpus_only": True,
        "cost_estimation_valid": False,
        "documents": len(results),
        "succeeded": sum(result.status.value == "SUCCEEDED" for result in results),
        "failed": sum(result.status.value != "SUCCEEDED" for result in results),
        "mentions": sum(len(result.mentions) for result in results),
        "wall_ms": wall_ms,
        "model_calls": by_stage,
        "results": [
            {
                "message_id": result.message_id,
                "status": result.status.value,
                "mention_count": len(result.mentions),
                "failures": [item.model_dump(mode="json") for item in result.failures],
            }
            for result in results
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["failed"] == 0 else 1


def main() -> int:
    args = _args()
    if args.workers < 1:
        raise SystemExit("--workers must be at least 1")
    if args.mode == "frozen":
        return _run_frozen(args)
    return _run_end_to_end(args)


if __name__ == "__main__":
    raise SystemExit(main())
