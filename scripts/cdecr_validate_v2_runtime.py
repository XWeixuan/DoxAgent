"""Run a bounded real-corpus smoke of N5.5 through N11 without exporting article text."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from cdecr.cli import _cross_document_engine
from cdecr.config import CDECRSettings
from cdecr.contracts import EventMention
from cdecr.identity_compiler import IdentityCompiler
from cdecr.kb_v2 import V2KnowledgeBase
from cdecr.registry import SQLiteCDECRRegistry


def _mentions_for_message(
    registry: SQLiteCDECRRegistry, message_id: str
) -> list[EventMention]:
    document = registry.get_latest_completed_document_result_for_message(message_id)
    if document is not None:
        return list(document.mentions)
    return list(registry.list_mentions_for_message(message_id))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-registry", type=Path, required=True)
    parser.add_argument("--output-registry", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--max-text-chars", type=int, default=20_000)
    parser.add_argument("--max-passes", type=int, default=3)
    parser.add_argument("--resume", action="store_true")
    return parser


def _mention_hashes(registry: SQLiteCDECRRegistry, message_ids: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for message_id in message_ids:
        for mention in registry.list_mentions_for_message(message_id):
            result[mention.mention_id] = hashlib.sha256(
                mention.model_dump_json().encode()
            ).hexdigest()
    return result


def main() -> int:
    args = _parser().parse_args()
    if args.limit < 1 or args.limit > 30:
        raise ValueError("limit must be between 1 and 30")
    if args.max_passes < 1 or args.max_passes > 10:
        raise ValueError("max-passes must be between 1 and 10")
    if args.output_registry.exists() and not args.resume:
        raise FileExistsError("output registry already exists; refusing to overwrite it")
    if args.resume and not args.output_registry.exists():
        raise FileNotFoundError("resume requires an existing output registry")
    source_registry = SQLiteCDECRRegistry(args.source_registry)
    source_registry.initialize()
    candidates: list[tuple[str, int, int]] = []
    for source in source_registry.list_sources(limit=1000):
        mentions = _mentions_for_message(source_registry, source.message_id)
        if not mentions or len(source.text) > args.max_text_chars:
            continue
        rank = int(hashlib.sha256(source.message_id.encode()).hexdigest()[:12], 16)
        candidates.append((source.message_id, len(mentions), rank))
    selected = sorted(candidates, key=lambda item: item[2])[: args.limit]
    if len(selected) != args.limit:
        raise ValueError("source registry does not contain enough bounded completed documents")

    registry = SQLiteCDECRRegistry(args.output_registry)
    registry.initialize()
    if not args.resume:
        for message_id, _, _ in selected:
            selected_source = source_registry.get_source(message_id)
            mentions = _mentions_for_message(source_registry, message_id)
            assert selected_source is not None and mentions
            fingerprint = source_registry.get_source_fingerprint(message_id)
            registry.save_source(selected_source, fingerprint=fingerprint or "0" * 64)
            for mention in mentions:
                registry.save_mention(mention)

    settings = CDECRSettings(CDECR_SQLITE_PATH=args.output_registry)
    engine = _cross_document_engine(settings, registry)
    message_ids = [item[0] for item in selected]
    before_hashes = _mention_hashes(registry, message_ids)
    passes: list[dict[str, object]] = []
    final_results = []
    previous_calls = registry.count_model_calls()
    for pass_index in range(1, args.max_passes + 1):
        current_results = []
        for document_index, message_id in enumerate(message_ids, start=1):
            result = engine.process(message_id)
            current_results.append(result)
            print(
                json.dumps(
                    {
                        "pass": pass_index,
                        "document": document_index,
                        "status": result.status.value,
                        "reused": result.reused,
                    }
                ),
                flush=True,
            )
        current_calls = registry.count_model_calls()
        passes.append(
            {
                "pass": pass_index,
                "model_call_delta": current_calls - previous_calls,
                "reused_count": sum(result.reused for result in current_results),
                "statuses": [result.status.value for result in current_results],
            }
        )
        final_results = current_results
        previous_calls = current_calls
        if all(result.reused for result in current_results):
            break
    after_hashes = _mention_hashes(registry, message_ids)

    kb = V2KnowledgeBase()
    compiler = IdentityCompiler(registry=registry, catalog_hash=kb.catalog_hash)
    compiled = [
        compiler.compile(mention)
        for message_id in message_ids
        for mention in registry.list_mentions_for_message(message_id)
    ]
    external_links = 0
    provisional_links = 0
    for identity in compiled:
        for link in registry.list_field_links_for_mention(identity.mention_id):
            entry = registry.resolve_field_registry_entry(link.registry_id)
            if entry is None:
                continue
            if entry.external_id is None:
                provisional_links += 1
            else:
                external_links += 1
    report = {
        "source_registry": str(args.source_registry),
        "output_registry": str(args.output_registry),
        "catalog_hash": kb.catalog_hash,
        "document_count": len(message_ids),
        "mention_count": len(compiled),
        "message_ids": message_ids,
        "document_statuses": [result.status.value for result in final_results],
        "identity_complete": sum(item.identity_profile is not None for item in compiled),
        "identity_incomplete": sum(item.identity_profile is None for item in compiled),
        "external_field_links": external_links,
        "provisional_field_links": provisional_links,
        "atomic_events": len(registry.list_current_atomic_events(limit=10000)),
        "packages": len(registry.list_current_packages(limit=10000)),
        "failed_cross_document_runs": sum(
            result.status.value == "FAILED" for result in final_results
        ),
        "candidate_counts": {
            key: sum(result.candidate_counts[key] for result in final_results)
            for key in (
                "atomic_recalled",
                "atomic_hard_conflict_observed",
                "package_recalled",
                "package_hard_blocked",
            )
        },
        "passes": passes,
        "converged": bool(final_results) and all(result.reused for result in final_results),
        "mentions_immutable": before_hashes == after_hashes,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if all(result.status.value == "SUCCEEDED" for result in final_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
