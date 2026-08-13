"""Run the Parent Occurrence Package stage against a frozen Atomic snapshot.

The source Registry is read-only input.  A minimal new Registry is populated
with Source, Mention, Field and current Atomic records, then the current Parent
Induction/R1/R2 resolver is called with the configured real model provider.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from cdecr.bulk_epoch.package_stage import project_parent_partition
from cdecr.cli import _cross_document_engine
from cdecr.config import CDECRSettings
from cdecr.contracts import EventMention
from cdecr.cross_document import ENGINE_VERSION, PROMPT_VERSION, _AuditedModels
from cdecr.parent_occurrence import ParentOccurrenceService
from cdecr.registry import SQLiteCDECRRegistry
from cdecr.single_document_contracts import ModelCallSummary


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-registry", type=Path, required=True)
    parser.add_argument("--output-registry", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--resume-existing", action="store_true")
    parser.add_argument("--resume-incomplete", action="store_true")
    return parser.parse_args()


def _copy_frozen_atomic_snapshot(
    source_path: Path,
    output_path: Path,
) -> tuple[SQLiteCDECRRegistry, dict[str, object]]:
    if output_path.exists():
        raise FileExistsError(f"output Registry already exists: {output_path}")
    source = SQLiteCDECRRegistry(source_path)
    events = source.list_current_atomic_events(limit=10_000)
    required_mentions: dict[str, EventMention] = {}
    missing_mentions: list[str] = []
    for event in events:
        for mention_id in event.mention_ids:
            mention = source.get_mention(mention_id)
            if mention is None:
                missing_mentions.append(mention_id)
            else:
                required_mentions[mention_id] = mention
    if missing_mentions:
        raise ValueError(
            "frozen Atomic snapshot references missing Mentions: "
            + ", ".join(sorted(set(missing_mentions))[:20])
        )
    required_message_ids = sorted({item.message_id for item in required_mentions.values()})
    output = SQLiteCDECRRegistry(output_path)
    output.initialize()
    for message_id in required_message_ids:
        message = source.get_source(message_id)
        if message is None:
            raise ValueError(f"missing SourceMessage {message_id}")
        output.save_source(
            message,
            fingerprint=source.get_source_fingerprint(message_id) or "0" * 64,
        )
    for mention in sorted(required_mentions.values(), key=lambda item: item.mention_id):
        output.save_mention(mention)
    entries = source.list_field_registry_entries(limit=100_000)
    for entry in entries:
        output.create_field_registry_entry(entry.model_copy(update={"redirect_to": None}))
    for entry in entries:
        if entry.redirect_to is not None:
            output.save_field_redirect(entry.id, entry.redirect_to)
    for mention in required_mentions.values():
        for link in source.list_field_links_for_mention(mention.mention_id):
            output.save_field_link(link)
    for event in events:
        output.save_atomic_event(event.model_copy(update={"version": 1}))
    settings = CDECRSettings()
    event_ids = {item.event_id for item in events}
    for embedding in source.list_latest_embeddings(
        owner_kind="atomic_event", model=settings.model_m1, limit=100_000
    ):
        if embedding.owner_id not in event_ids:
            continue
        output.save_embedding(
            owner_kind="atomic_event",
            owner_id=embedding.owner_id,
            model=embedding.model,
            input_hash=embedding.input_hash,
            vector=embedding.vector,
        )
    return output, {
        "source_registry": str(source_path),
        "document_count": len(required_message_ids),
        "mention_count": len(required_mentions),
        "atomic_count": len(events),
        "field_registry_count": len(entries),
    }


def _model_usage(path: Path, *, created_at_lte: str | None = None) -> dict[str, object]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    query = """
        SELECT stage, COUNT(*) AS calls,
               COALESCE(SUM(input_tokens), 0) AS input_tokens,
               COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(latency_ms), 0) AS latency_ms,
               SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) AS failed_calls
        FROM model_calls
        {where_clause}
        GROUP BY stage
        ORDER BY input_tokens + output_tokens DESC, stage
        """.format(where_clause="WHERE created_at <= ?" if created_at_lte else "")
    rows = connection.execute(query, (created_at_lte,) if created_at_lte else ()).fetchall()
    stages = [dict(row) for row in rows]
    return {
        "call_count": sum(int(row["calls"]) for row in rows),
        "input_tokens": sum(int(row["input_tokens"]) for row in rows),
        "output_tokens": sum(int(row["output_tokens"]) for row in rows),
        "latency_ms": sum(int(row["latency_ms"]) for row in rows),
        "failed_call_count": sum(int(row["failed_calls"]) for row in rows),
        "stages": stages,
    }


def _package_shape(registry: SQLiteCDECRRegistry) -> dict[str, object]:
    packages = registry.list_current_packages(limit=10_000)
    sizes = [len(registry.list_memberships_for_package(item.package_id)) for item in packages]
    distribution = Counter(sizes)
    return {
        "package_count": len(packages),
        "membership_count": sum(sizes),
        "singleton_count": distribution.get(1, 0),
        "singleton_ratio": distribution.get(1, 0) / len(packages) if packages else 0.0,
        "max_size": max(sizes, default=0),
        "size_distribution": {str(key): distribution[key] for key in sorted(distribution)},
    }


def _resume_existing(args: argparse.Namespace) -> int:
    registry = SQLiteCDECRRegistry(args.output_registry.resolve())
    registry.initialize()
    connection = sqlite3.connect(args.output_registry.resolve())
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT run_id, snapshot_hash, payload_json, created_at "
        "FROM parent_occurrence_partitions WHERE status = 'FINALIZED' "
        "ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise RuntimeError("resume requested but no finalized Parent partition exists")
    events = registry.list_current_atomic_events(limit=10_000)
    model_calls_before = registry.count_model_calls()
    service = ParentOccurrenceService(registry=registry)
    started = perf_counter()
    replay = service.run(
        events=events,
        mentions=None,
        sources=None,
        existing_packages=[],
        models=object(),
        run_id=str(row["run_id"]),
        persistence_scope_id=str(row["run_id"]),
    )
    replay_wall_ms = round((perf_counter() - started) * 1000)
    model_calls_after = registry.count_model_calls()
    run_row = connection.execute(
        "SELECT started_at FROM cross_document_runs ORDER BY started_at LIMIT 1"
    ).fetchone()
    first_wall_ms = 0
    if run_row is not None:
        first_wall_ms = round(
            (
                datetime.fromisoformat(str(row["created_at"]))
                - datetime.fromisoformat(str(run_row["started_at"]))
            ).total_seconds()
            * 1000
        )
    clean_usage = _model_usage(
        args.output_registry.resolve(), created_at_lte=str(row["created_at"])
    )
    all_usage = _model_usage(args.output_registry.resolve())
    clean_call_count = clean_usage["call_count"]
    all_call_count = all_usage["call_count"]
    if not isinstance(clean_call_count, int) or not isinstance(all_call_count, int):
        raise TypeError("model usage call counts must be integers")
    mentions = registry.list_all_mentions(limit=1_000_000)
    sources = registry.list_all_sources(limit=100_000)
    fields = registry.list_field_registry_entries(limit=100_000)
    shape = _package_shape(registry)
    settings = CDECRSettings(CDECR_SQLITE_PATH=args.output_registry.resolve())
    partition_hash_stable = bool(
        replay.partition is not None
        and replay.partition.partition_hash
        == json.loads(str(row["payload_json"]))["partition_hash"]
    )
    idempotency = {
        "status": replay.status,
        "wall_ms": replay_wall_ms,
        "new_model_calls": model_calls_after - model_calls_before,
        "partition_hash_stable": partition_hash_stable,
    }
    report = {
        "report_version": "cdecr-parent-occurrence-v2.0r-package-only-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "reconstructed_after_runner_idempotency_fix": True,
        "snapshot": {
            "source_registry": str(args.source_registry.resolve()),
            "document_count": len(sources),
            "mention_count": len(mentions),
            "atomic_count": len(events),
            "field_registry_count": len(fields),
        },
        "provider": {
            "m2": settings.model_m2_provider,
            "m3": settings.model_m3_provider,
            "m4": settings.model_m4_provider,
            "model_m2": settings.model_m2,
            "model_m3": settings.model_m3,
            "model_m4": settings.model_m4,
        },
        "run_id": str(row["run_id"]),
        "scope_id": str(row["run_id"]),
        "status": replay.status,
        "first_wall_ms": first_wall_ms,
        "telemetry": replay.telemetry,
        "shape": shape,
        "model_usage": clean_usage,
        "post_partition_diagnostic_model_calls_excluded": all_call_count - clean_call_count,
        "idempotency": idempotency,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": replay.status,
                "report": str(args.report),
                "first_wall_ms": first_wall_ms,
                "shape": shape,
                "model_usage": clean_usage,
                "idempotency": idempotency,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return int(
        replay.status != "FINALIZED"
        or model_calls_after != model_calls_before
        or not partition_hash_stable
    )


def main() -> int:
    args = _args()
    args.output_registry.parent.mkdir(parents=True, exist_ok=True)
    if args.resume_existing:
        return _resume_existing(args)
    if args.resume_incomplete:
        if not args.output_registry.exists():
            raise FileNotFoundError("resume-incomplete Registry does not exist")
        registry = SQLiteCDECRRegistry(args.output_registry.resolve())
        registry.initialize()
        connection = sqlite3.connect(args.output_registry.resolve())
        connection.row_factory = sqlite3.Row
        run_row = connection.execute(
            "SELECT run_id, processing_key FROM cross_document_runs "
            "ORDER BY started_at LIMIT 1"
        ).fetchone()
        if run_row is None:
            raise RuntimeError("resume-incomplete Registry has no run ledger")
        run_id = str(run_row["run_id"])
        scope_id = str(run_row["processing_key"])
        snapshot = {
            "source_registry": str(args.source_registry.resolve()),
            "document_count": len(registry.list_all_sources(limit=100_000)),
            "mention_count": len(registry.list_all_mentions(limit=1_000_000)),
            "atomic_count": len(registry.list_current_atomic_events(limit=10_000)),
            "resumed_from_checkpoint": True,
            "pre_resume_model_call_count": registry.count_model_calls(),
        }
    else:
        registry, snapshot = _copy_frozen_atomic_snapshot(
            args.source_registry.resolve(), args.output_registry.resolve()
        )
        run_id = str(uuid.uuid4())
        scope_id = f"parent-package-only:{run_id}"
    settings = CDECRSettings(CDECR_SQLITE_PATH=args.output_registry.resolve())
    engine = _cross_document_engine(settings, registry)
    service = ParentOccurrenceService(
        registry=registry,
        induction_active_requests=settings.parent_induction_active_requests,
        resolution_active_requests=settings.parent_resolution_active_requests,
        reconcile_active_requests=settings.parent_reconcile_active_requests,
        induction_max_documents=settings.parent_induction_max_documents,
        induction_max_slices=settings.parent_induction_max_slices,
        resolution_max_proposals=settings.parent_resolution_max_proposals,
        resolution_max_existing_parents=settings.parent_resolution_max_existing_parents,
        resolution_max_input_tokens=settings.parent_resolution_max_input_tokens,
        route_structured_quota=settings.parent_route_structured_quota,
        route_semantic_quota=settings.parent_route_semantic_quota,
        route_total_k=settings.parent_route_total_k,
        context_soft_token_budget=settings.parent_context_soft_token_budget,
    )
    if not args.resume_incomplete:
        first_source = registry.list_sources(limit=1)[0]
        registry.start_cross_document_run(
            run_id=run_id,
            processing_key=scope_id,
            message_id=first_source.message_id,
            engine_version=ENGINE_VERSION,
            prompt_version=PROMPT_VERSION,
            model_config=engine.model_config,
        )
    summaries: list[ModelCallSummary] = []
    models = _AuditedModels(
        registry=registry,
        run_id=run_id,
        embedding_client=engine.embedding_client,
        m2_client=engine.m2_client,
        m3_client=engine.m3_client,
        m4_client=engine.m4_client,
        model_m1=engine.model_m1,
        model_m2=engine.model_m2,
        model_m3=engine.model_m3,
        model_m4=engine.model_m4,
        summaries=summaries,
    )
    events = registry.list_current_atomic_events(limit=10_000)
    started = perf_counter()
    stage = service.run(
        events=events,
        mentions=None,
        sources=None,
        existing_packages=[],
        models=models,
        run_id=run_id,
        persistence_scope_id=scope_id,
    )
    first_wall_ms = round((perf_counter() - started) * 1000)
    if stage.status != "FINALIZED" or stage.partition is None:
        registry.fail_cross_document_run(run_id, error_code=stage.status)
        raise RuntimeError(
            json.dumps(
                {
                    "status": stage.status,
                    "failures": [item.model_dump(mode="json") for item in stage.failures],
                    "telemetry": stage.telemetry,
                },
                ensure_ascii=False,
            )
        )
    packages, memberships, assignments, external_relations, redirects = (
        project_parent_partition(
            stage.partition,
            events=events,
            existing_packages=[],
            run_id=run_id,
        )
    )
    assignment_by_event = {item.event_id: item for item in assignments}
    records: list[dict[str, object]] = [{"package": item} for item in packages]
    for membership in memberships:
        records.append(
            {
                "memberships": [membership],
                "assignment": assignment_by_event[membership.event_id],
            }
        )
    if external_relations:
        records.append({"external_relations": external_relations})
    apply_result = registry.save_package_stage_batch(records, chunk_size=64)
    for source_id, target_id in redirects:
        registry.save_package_redirect(
            source_package_id=source_id,
            target_package_id=target_id,
            run_id=run_id,
            reason="PARENT_OCCURRENCE_V2_PACKAGE_ONLY",
        )
    calls_before_idempotency = registry.count_model_calls()
    idempotent_started = perf_counter()
    second = service.run(
        events=events,
        mentions=None,
        sources=None,
        existing_packages=[],
        models=models,
        run_id=run_id,
        persistence_scope_id=scope_id,
    )
    idempotent_wall_ms = round((perf_counter() - idempotent_started) * 1000)
    calls_after_idempotency = registry.count_model_calls()
    partition_hash_stable = bool(
        second.partition is not None
        and second.partition.partition_hash == stage.partition.partition_hash
    )
    shape = _package_shape(registry)
    model_usage = _model_usage(args.output_registry.resolve())
    idempotency = {
        "status": second.status,
        "wall_ms": idempotent_wall_ms,
        "new_model_calls": calls_after_idempotency - calls_before_idempotency,
        "partition_hash_stable": partition_hash_stable,
    }
    report = {
        "report_version": "cdecr-parent-occurrence-v2.0r-package-only-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "snapshot": snapshot,
        "provider": {
            "m2": settings.model_m2_provider,
            "m3": settings.model_m3_provider,
            "m4": settings.model_m4_provider,
            "model_m2": settings.model_m2,
            "model_m3": settings.model_m3,
            "model_m4": settings.model_m4,
        },
        "run_id": run_id,
        "scope_id": scope_id,
        "status": stage.status,
        "first_wall_ms": first_wall_ms,
        "telemetry": stage.telemetry,
        "failure_count": len(stage.failures),
        "failures": [item.model_dump(mode="json") for item in stage.failures],
        "apply": {
            **apply_result,
            "package_count": len(packages),
            "membership_count": len(memberships),
            "external_relation_count": len(external_relations),
            "redirect_count": len(redirects),
        },
        "shape": shape,
        "model_usage": model_usage,
        "idempotency": idempotency,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": stage.status,
                "output_registry": str(args.output_registry),
                "report": str(args.report),
                "first_wall_ms": first_wall_ms,
                "shape": shape,
                "model_usage": {
                    key: model_usage[key]
                    for key in (
                        "call_count",
                        "input_tokens",
                        "output_tokens",
                        "latency_ms",
                        "failed_call_count",
                    )
                },
                "idempotency": idempotency,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return int(
        second.status != "FINALIZED"
        or calls_after_idempotency != calls_before_idempotency
        or not partition_hash_stable
    )


if __name__ == "__main__":
    raise SystemExit(main())
