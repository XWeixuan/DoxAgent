"""Run the fixed CDECR corpus through N1-N5 only and emit a compact report."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from time import perf_counter

from cdecr.cli import _document_processor
from cdecr.config import CDECRSettings
from cdecr.registry import SQLiteCDECRRegistry


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-registry", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _args()
    if args.registry.exists():
        raise SystemExit(f"refusing to reuse output registry: {args.registry}")
    source_registry = SQLiteCDECRRegistry(args.source_registry)
    with sqlite3.connect(args.source_registry) as connection:
        source_rows = connection.execute(
            "SELECT message_id, fingerprint FROM source_messages ORDER BY published_at, message_id"
        ).fetchall()
    registry = SQLiteCDECRRegistry(args.registry)
    registry.initialize()
    message_ids: list[str] = []
    for message_id, fingerprint in source_rows:
        source = source_registry.get_source(str(message_id))
        if source is None:
            raise RuntimeError(f"missing source {message_id}")
        registry.save_source(source, fingerprint=str(fingerprint))
        message_ids.append(source.message_id)

    settings = CDECRSettings(CDECR_SQLITE_PATH=args.registry)
    processor = _document_processor(settings, registry)
    started = perf_counter()
    results = processor.process_batch(message_ids)
    wall_ms = round((perf_counter() - started) * 1000)

    by_stage: dict[str, dict[str, int]] = defaultdict(
        lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "latency_ms": 0}
    )
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
        audits = {
            str(kind): int(count)
            for kind, count in connection.execute(
                "SELECT decision_type, COUNT(*) FROM decision_audits GROUP BY decision_type"
            )
        }

    report = {
        "report_version": "cdecr-mention-only-v1",
        "source_registry": str(args.source_registry),
        "registry": str(args.registry),
        "document_count": len(results),
        "succeeded": sum(result.status.value == "SUCCEEDED" for result in results),
        "failed": sum(result.status.value != "SUCCEEDED" for result in results),
        "mention_count": sum(len(result.mentions) for result in results),
        "wall_ms": wall_ms,
        "model_calls": dict(by_stage),
        "audit_counts": audits,
        "documents": [
            {
                "message_id": result.message_id,
                "status": result.status.value,
                "mention_count": len(result.mentions),
                "failures": [item.model_dump(mode="json") for item in result.failures],
            }
            for result in results
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
