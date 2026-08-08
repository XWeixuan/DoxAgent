"""Evaluate current Mention fields against the fixed source-centered Gold mapping."""

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

FieldStatus = Literal["CORRECT", "INCORRECT", "NOT_APPLICABLE"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GoldFieldAssessment(StrictModel):
    gold_id: str
    mention_id: str | None = None
    predicate: FieldStatus
    participant: FieldStatus
    metric: FieldStatus
    fiscal_period: FieldStatus
    error_codes: list[str]
    reason: str


class DocumentFieldAssessment(StrictModel):
    assessments: list[GoldFieldAssessment]


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--mention-evaluation", type=Path, required=True)
    parser.add_argument("--gold", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    return parser.parse_args()


def _connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _field_links(connection: sqlite3.Connection, mention_id: str) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT l.field_path, l.registry_id, l.method,
                   r.namespace, r.canonical_text, r.external_id
            FROM canonical_field_links l
            JOIN canonical_field_registry r ON r.id = l.registry_id
            WHERE l.mention_id = ?
            ORDER BY l.field_path
            """,
            (mention_id,),
        )
    ]


def main() -> int:
    args = _args()
    mention_evaluation = json.loads(args.mention_evaluation.read_text(encoding="utf-8"))
    connection = _connection(args.registry)
    mentions = {
        str(row["mention_id"]): json.loads(str(row["payload_json"]))
        for row in connection.execute("SELECT mention_id, payload_json FROM event_mentions")
    }
    gold_by_index: dict[int, list[dict[str, object]]] = {}
    for path in args.gold:
        source = json.loads(path.read_text(encoding="utf-8"))
        for document in source["documents"]:
            index = int(document.get("index", document.get("document_index")))
            raw_gold = document.get("gold", document.get("gold_mentions", []))
            gold_by_index[index] = [
                item
                if isinstance(item, dict)
                else {"id": f"D{index}G{position}", "proposition": str(item)}
                for position, item in enumerate(raw_gold, start=1)
            ]
    documents: list[dict[str, object]] = []
    for document in mention_evaluation["documents"]:
        matched_by_gold: dict[str, str] = {}
        for judgment in document["judgments"]:
            gold_id = judgment.get("gold_id")
            mention_id = judgment.get("mention_id")
            if (
                isinstance(gold_id, str)
                and isinstance(mention_id, str)
                and judgment.get("verdict") in {"STRICT_TP", "PARTIAL"}
                and gold_id not in matched_by_gold
            ):
                matched_by_gold[gold_id] = mention_id
        gold = []
        for item in gold_by_index[int(document["index"])]:
            gold_id = str(item["id"])
            mention_id = matched_by_gold.get(gold_id)
            mention = mentions.get(mention_id) if mention_id else None
            gold.append(
                {
                    "gold_id": gold_id,
                    "proposition": item["proposition"],
                    "mention_id": mention_id,
                    "output": (
                        {
                            key: mention.get(key)
                            for key in (
                                "predicate",
                                "participants",
                                "time",
                                "quantities",
                                "canonical_proposition",
                            )
                        }
                        if mention is not None
                        else None
                    ),
                    "canonical_links": (
                        _field_links(connection, mention_id) if mention_id is not None else []
                    ),
                }
            )
        documents.append(
            {
                "index": document["index"],
                "title": document["title"],
                "gold": gold,
            }
        )

    settings = CDECRSettings()
    parts_dir = args.output.with_name(f"{args.output.stem}_parts")
    parts_dir.mkdir(parents=True, exist_ok=True)
    system_prompt = (
        "Evaluate four CDECR fields against each fixed Gold proposition. Predicate "
        "must express the Gold action and polarity. Participant must preserve all "
        "material entities and roles. Metric includes metric identity, value, unit, "
        "basis, qualifier and benchmark when the Gold supports them. Fiscal period "
        "means reporting/fiscal period, not publication date or market session. Use "
        "NOT_APPLICABLE only when the Gold genuinely has no requirement for that "
        "field. A missing output makes every applicable field INCORRECT. Return every "
        "gold_id exactly once and do not invent mention IDs."
    )

    def evaluate(document: dict[str, object]) -> dict[str, object]:
        part_path = parts_dir / f"D{int(cast(int, document['index'])):02d}.json"
        if part_path.exists():
            persisted = json.loads(part_path.read_text(encoding="utf-8"))
            expected_ids = {
                str(item["gold_id"])
                for item in cast(list[dict[str, object]], document["gold"])
            }
            persisted_ids = {
                str(item["gold_id"])
                for item in cast(list[dict[str, object]], persisted.get("assessments", []))
            }
            if persisted_ids == expected_ids:
                return cast(dict[str, object], persisted)
        client = _structured_client(settings, ModelTier.M4)
        totals = {"input_tokens": 0, "output_tokens": 0, "latency_ms": 0}

        def complete(target: dict[str, object]) -> list[GoldFieldAssessment]:
            result = client.complete(
                StructuredModelRequest(
                    system_prompt=system_prompt,
                    user_prompt=json.dumps(target, ensure_ascii=False, separators=(",", ":")),
                    json_schema=DocumentFieldAssessment.model_json_schema(),
                )
            )
            totals["input_tokens"] += result.input_tokens or 0
            totals["output_tokens"] += result.output_tokens or 0
            totals["latency_ms"] += result.latency_ms
            return DocumentFieldAssessment.model_validate(result.payload).assessments

        gold_items = cast(list[dict[str, object]], document["gold"])
        expected = {str(item["gold_id"]) for item in gold_items}
        assessments_by_id: dict[str, GoldFieldAssessment] = {}
        for assessment in complete(document):
            if assessment.gold_id in expected and assessment.gold_id not in assessments_by_id:
                assessments_by_id[assessment.gold_id] = assessment

        missing = expected - assessments_by_id.keys()
        if missing:
            recovery_document = {
                "index": document["index"],
                "title": document["title"],
                "gold": [item for item in gold_items if str(item["gold_id"]) in missing],
            }
            for assessment in complete(recovery_document):
                if assessment.gold_id in missing and assessment.gold_id not in assessments_by_id:
                    assessments_by_id[assessment.gold_id] = assessment

        missing = expected - assessments_by_id.keys()
        for gold_id in sorted(missing):
            recovery_document = {
                "index": document["index"],
                "title": document["title"],
                "gold": [item for item in gold_items if str(item["gold_id"]) == gold_id],
            }
            for assessment in complete(recovery_document):
                if assessment.gold_id == gold_id:
                    assessments_by_id[gold_id] = assessment
                    break

        if assessments_by_id.keys() != expected:
            raise ValueError(
                "field evaluator coverage recovery failed for: "
                + ",".join(sorted(expected - assessments_by_id.keys()))
            )
        reviewed_document = {
            "index": document["index"],
            **totals,
            "assessments": [
                assessments_by_id[str(item["gold_id"])].model_dump(mode="json")
                for item in gold_items
            ],
        }
        part_path.write_text(
            json.dumps(reviewed_document, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return reviewed_document

    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 48))) as executor:
        reviewed = list(executor.map(evaluate, documents))
    fields = ("predicate", "participant", "metric", "fiscal_period")
    metrics: dict[str, dict[str, float | int]] = {}
    for field in fields:
        values = [
            str(item[field])
            for document in reviewed
            for item in cast(list[dict[str, object]], document["assessments"])
        ]
        applicable = sum(value != "NOT_APPLICABLE" for value in values)
        correct = values.count("CORRECT")
        metrics[field] = {
            "applicable": applicable,
            "correct": correct,
            "incorrect": values.count("INCORRECT"),
            "accuracy": correct / applicable if applicable else 1.0,
        }
    total_applicable = sum(int(item["applicable"]) for item in metrics.values())
    total_correct = sum(int(item["correct"]) for item in metrics.values())
    payload = {
        "report_version": "cdecr-field-gold-eval-v1",
        "summary": {
            **metrics,
            "total": {
                "applicable": total_applicable,
                "correct": total_correct,
                "incorrect": total_applicable - total_correct,
                "accuracy": total_correct / total_applicable if total_applicable else 1.0,
            },
            "input_tokens": sum(cast(int, item["input_tokens"]) for item in reviewed),
            "output_tokens": sum(cast(int, item["output_tokens"]) for item in reviewed),
            "latency_ms": sum(cast(int, item["latency_ms"]) for item in reviewed),
        },
        "documents": reviewed,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
