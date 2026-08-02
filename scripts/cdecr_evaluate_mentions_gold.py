"""Evaluate one Mention-only registry against the fixed source-centered Gold."""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cdecr.config import CDECRSettings
from cdecr.models import DashScopeStructuredModelClient, ModelTier
from cdecr.ports import StructuredModelRequest
from cdecr.registry import SQLiteCDECRRegistry


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OutputJudgment(StrictModel):
    mention_id: str
    gold_id: str | None = None
    verdict: Literal["STRICT_TP", "PARTIAL", "FP"]
    error_codes: list[str] = Field(default_factory=list)
    reason: str


class MissedGold(StrictModel):
    gold_id: str
    reason: str
    first_failure: Literal[
        "DREAMER_MISSING",
        "GROUNDER_REJECTED",
        "GROUNDER_MISSING_OR_INVALID",
        "JUDGE_REJECTED_OR_MERGED",
        "OUTPUT_PARTIAL",
        "UNKNOWN",
    ]


class DocumentEvaluation(StrictModel):
    judgments: list[OutputJudgment]
    missed_gold: list[MissedGold]

    @model_validator(mode="after")
    def unique_items(self) -> DocumentEvaluation:
        mention_ids = [item.mention_id for item in self.judgments]
        gold_ids = [item.gold_id for item in self.missed_gold]
        if len(mention_ids) != len(set(mention_ids)):
            raise ValueError("mention judgments must be unique")
        if len(gold_ids) != len(set(gold_ids)):
            raise ValueError("missed Gold IDs must be unique")
        return self


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--gold", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    return parser.parse_args()


def _as_int(value: object) -> int:
    if isinstance(value, (int, str)):
        return int(value)
    raise TypeError(f"expected integer-compatible value, got {type(value).__name__}")


def _gold_documents(paths: list[Path]) -> list[dict[str, object]]:
    documents: list[dict[str, object]] = []
    for path in paths:
        payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
        for raw in cast(list[dict[str, Any]], payload["documents"]):
            index = _as_int(raw.get("index", raw.get("document_index")))
            message_id = raw.get("message_id")
            source_row_id = raw.get("source_row_id")
            if source_row_id is None and isinstance(message_id, str):
                source_row_id = message_id.rsplit(":", 1)[-1]
            raw_gold = raw.get("gold", raw.get("gold_mentions", []))
            gold = [
                (
                    item
                    if isinstance(item, dict)
                    else {"id": f"D{index}G{gold_index}", "proposition": str(item)}
                )
                for gold_index, item in enumerate(raw_gold, start=1)
            ]
            documents.append(
                {
                    "index": index,
                    "source_row_id": source_row_id,
                    "title": raw.get("title", ""),
                    "gold": gold,
                }
            )
    return sorted(documents, key=lambda item: _as_int(item["index"]))


def _mention_payload(mention: object) -> dict[str, object]:
    payload = mention.model_dump(mode="json", exclude_none=True)  # type: ignore[attr-defined]
    return {
        key: payload[key]
        for key in (
            "mention_id",
            "canonical_proposition",
            "source_claim",
            "event_family",
            "predicate",
            "participants",
            "time",
            "assertion_state",
            "quantities",
            "open_attributes",
            "schema_projection",
            "local_package_hint",
            "evidence_records",
            "evidence_spans",
        )
        if key in payload
    }


def main() -> int:
    args = _args()
    settings = CDECRSettings()
    api_key = settings.require_dashscope()
    registry = SQLiteCDECRRegistry(args.registry)
    gold_documents = _gold_documents(args.gold)
    parts_dir = args.output.parent / f"{args.output.stem}_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)

    def evaluate(document: dict[str, object]) -> dict[str, object]:
        part_path = parts_dir / f"D{_as_int(document['index']):02d}.json"
        if part_path.exists():
            return cast(
                dict[str, object],
                json.loads(part_path.read_text(encoding="utf-8")),
            )
        source_row_id = str(document["source_row_id"])
        message_id = f"doxatlas:raw_media:{source_row_id}"
        source = registry.get_source(message_id)
        if source is None:
            raise RuntimeError(f"missing source {message_id}")
        mentions = registry.list_mentions_for_message(message_id)
        gold = document["gold"]
        assert isinstance(gold, list)
        gold_ids = {str(item["id"]) for item in gold if isinstance(item, dict)}
        mention_ids = {item.mention_id for item in mentions}
        client = DashScopeStructuredModelClient(
            tier=ModelTier.M4,
            api_key=api_key,
            base_url=settings.dashscope_base_url,
            model=settings.model_m4,
            timeout_seconds=settings.model_timeout_seconds,
            fallback_api_keys=settings.dashscope_fallback_api_keys(),
        )
        request = StructuredModelRequest(
            system_prompt=(
                "You evaluate CDECR Event Mentions against source-centered Gold. Match one "
                "output to at most one Gold and one Gold to at most one STRICT_TP. STRICT_TP "
                "requires the same complete event including subject, action, polarity, primary "
                "metric, qualifier/benchmark/basis, period/session and assertion when supported. "
                "PARTIAL is supported but incomplete, fragmented, compound, or materially "
                "mis-fielded. FP is background, umbrella repetition, unsupported, duplicate "
                "after the one-to-one match, or a different event. A PARTIAL does not satisfy "
                "Gold recall. List every unmatched Gold exactly once. Use concise reasons."
            ),
            user_prompt=json.dumps(
                {
                    "document_index": document["index"],
                    "title": source.title,
                    "gold": gold,
                    "outputs": [_mention_payload(item) for item in mentions],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            json_schema=DocumentEvaluation.model_json_schema(),
        )
        input_tokens = 0
        output_tokens = 0
        latency_ms = 0
        coverage_retried = False
        result = client.complete(request)
        input_tokens += result.input_tokens or 0
        output_tokens += result.output_tokens or 0
        latency_ms += result.latency_ms
        evaluation = DocumentEvaluation.model_validate(result.payload)
        if {item.mention_id for item in evaluation.judgments} != mention_ids:
            coverage_retried = True
            retry = client.complete(
                request.model_copy(
                    update={
                        "system_prompt": (
                            request.system_prompt
                            + " Return exactly one judgment for every supplied output "
                            "mention_id; do not omit or invent IDs."
                        )
                    }
                )
            )
            input_tokens += retry.input_tokens or 0
            output_tokens += retry.output_tokens or 0
            latency_ms += retry.latency_ms
            evaluation = DocumentEvaluation.model_validate(retry.payload)
        judgments_by_id = {
            item.mention_id: item
            for item in evaluation.judgments
            if item.mention_id in mention_ids
        }
        for missing_mention_id in sorted(mention_ids - judgments_by_id.keys()):
            judgments_by_id[missing_mention_id] = OutputJudgment(
                mention_id=missing_mention_id,
                verdict="FP",
                error_codes=["ASSESSMENT_OMITTED"],
                reason="Evaluator omitted this output after one coverage retry.",
            )
        normalized_judgments: list[OutputJudgment] = []
        strict_gold: set[str] = set()
        for item in judgments_by_id.values():
            if item.gold_id not in gold_ids:
                item = item.model_copy(
                    update={
                        "gold_id": None,
                        "verdict": "FP",
                        "error_codes": [*item.error_codes, "UNKNOWN_GOLD_ID"],
                    }
                )
            elif item.verdict == "STRICT_TP" and item.gold_id in strict_gold:
                item = item.model_copy(
                    update={
                        "verdict": "FP",
                        "error_codes": [*item.error_codes, "DUPLICATE_GOLD_MATCH"],
                    }
                )
            elif item.verdict == "STRICT_TP" and item.gold_id is not None:
                strict_gold.add(item.gold_id)
            normalized_judgments.append(item)
        model_missed = {item.gold_id: item for item in evaluation.missed_gold}
        partial_by_gold = {
            item.gold_id: item
            for item in normalized_judgments
            if item.verdict == "PARTIAL" and item.gold_id is not None
        }
        normalized_missed: list[MissedGold] = []
        for gold_id in sorted(gold_ids - strict_gold):
            missed = model_missed.get(gold_id)
            partial = partial_by_gold.get(gold_id)
            normalized_missed.append(
                missed
                or MissedGold(
                    gold_id=gold_id,
                    reason=(
                        partial.reason
                        if partial is not None
                        else "No STRICT_TP output matched this Gold occurrence."
                    ),
                    first_failure="OUTPUT_PARTIAL" if partial is not None else "UNKNOWN",
                )
            )
        payload = {
            "index": document["index"],
            "source_row_id": source_row_id,
            "title": source.title,
            "gold_count": len(gold_ids),
            "output_count": len(mention_ids),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": latency_ms,
            "coverage_retried": coverage_retried,
            "judgments": [item.model_dump(mode="json") for item in normalized_judgments],
            "missed_gold": [item.model_dump(mode="json") for item in normalized_missed],
        }
        part_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return payload

    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 4))) as executor:
        documents = list(executor.map(evaluate, gold_documents))
    strict_tp = sum(
        item["verdict"] == "STRICT_TP"
        for document in documents
        for item in cast(list[dict[str, object]], document["judgments"])
    )
    partial = sum(
        item["verdict"] == "PARTIAL"
        for document in documents
        for item in cast(list[dict[str, object]], document["judgments"])
    )
    fp = sum(
        item["verdict"] == "FP"
        for document in documents
        for item in cast(list[dict[str, object]], document["judgments"])
    )
    output_count = strict_tp + partial + fp
    gold_count = sum(_as_int(document["gold_count"]) for document in documents)
    precision = strict_tp / output_count if output_count else 0.0
    recall = strict_tp / gold_count if gold_count else 0.0
    report = {
        "report_version": "cdecr-mention-gold-eval-v1",
        "registry": str(args.registry),
        "summary": {
            "gold_count": gold_count,
            "output_count": output_count,
            "strict_tp": strict_tp,
            "partial": partial,
            "fp": fp,
            "fn": gold_count - strict_tp,
            "precision": precision,
            "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
            "input_tokens": sum(_as_int(item["input_tokens"]) for item in documents),
            "output_tokens": sum(_as_int(item["output_tokens"]) for item in documents),
            "latency_ms": sum(_as_int(item["latency_ms"]) for item in documents),
        },
        "documents": documents,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
