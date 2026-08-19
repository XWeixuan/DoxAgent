"""Run Package Workflow V3 against persisted frozen Parent Occurrences and Atomics."""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from cdecr_run_parent_package_only import (
    _copy_frozen_atomic_snapshot,
    _model_usage,
    _package_shape,
)

from cdecr.cli import _cross_document_engine
from cdecr.config import CDECRSettings
from cdecr.cross_document import ENGINE_VERSION, PROMPT_VERSION, _AuditedModels
from cdecr.package_global_clustering import PackageWorkflowV3Service
from cdecr.package_projection import project_frozen_partition_v3
from cdecr.parent_occurrence_contracts import ParentProposalCard
from cdecr.registry import SQLiteCDECRRegistry
from cdecr.single_document_contracts import ModelCallSummary


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-registry", type=Path, required=True)
    parser.add_argument("--output-registry", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--resume-existing", action="store_true")
    return parser.parse_args()


def _run_ledger(path: Path) -> tuple[str, str] | None:
    connection = sqlite3.connect(path)
    row = connection.execute(
        "SELECT run_id,processing_key FROM cross_document_runs ORDER BY started_at LIMIT 1"
    ).fetchone()
    connection.close()
    return (str(row[0]), str(row[1])) if row is not None else None


def main() -> int:
    args = _args()
    source_path = args.source_registry.resolve()
    output_path = args.output_registry.resolve()
    if output_path.exists() and not args.resume_existing:
        raise FileExistsError(f"output Registry already exists: {output_path}")
    source = SQLiteCDECRRegistry(source_path)
    proposal_payloads = source.list_parent_occurrence_proposals()
    if not proposal_payloads:
        raise ValueError("source Registry has no frozen Parent Occurrence proposals")
    proposals = [ParentProposalCard.model_validate(item) for item in proposal_payloads]
    if output_path.exists():
        registry = SQLiteCDECRRegistry(output_path)
        registry.initialize()
        ledger = _run_ledger(output_path)
        if ledger is None:
            raise RuntimeError("resume Registry has no run ledger")
        run_id, scope_id = ledger
        snapshot: dict[str, object] = {
            "source_registry": str(source_path),
            "resumed": True,
            "proposal_count": len(proposals),
        }
    else:
        registry, snapshot = _copy_frozen_atomic_snapshot(source_path, output_path)
        run_id = str(uuid.uuid4())
        scope_id = f"package-v3-only:{run_id}"
        first_source = registry.list_sources(limit=1)[0]
        registry.start_cross_document_run(
            run_id=run_id,
            processing_key=scope_id,
            message_id=first_source.message_id,
            engine_version=ENGINE_VERSION,
            prompt_version=PROMPT_VERSION,
            model_config={"package_workflow": "v3", "source_registry": str(source_path)},
        )
        registry.save_parent_occurrence_proposals(
            run_id=scope_id,
            records=[item.model_dump(mode="json") for item in proposals],
        )
        snapshot["proposal_count"] = len(proposals)
    settings = CDECRSettings(CDECR_SQLITE_PATH=output_path)
    if settings.model_m3_provider != "dashscope":
        raise ValueError("Package V3 Gate requires CDECR_M3_PROVIDER=dashscope")
    if settings.model_m3 != "deepseek-v4-flash-0731":
        raise ValueError("Package V3 Gate requires deepseek-v4-flash-0731")
    engine = _cross_document_engine(settings, registry)
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
        responses_m4_client=engine.package_m4_client,
        responses_model_m4=engine.package_model_m4,
        summaries=summaries,
    )
    service = PackageWorkflowV3Service(
        registry=registry,
        batch_size=settings.package_v3_batch_size,
        context_token_budget=settings.package_v3_context_token_budget,
        context_reserve_tokens=settings.package_v3_context_reserve_tokens,
        description_pack_token_budget=settings.package_v3_description_token_budget,
        description_active_requests=settings.package_v3_description_active_requests,
        reasoning_effort=settings.model_m3_reasoning_effort,
        description_reasoning_effort=settings.model_m2_reasoning_effort,
    )
    events = registry.list_current_atomic_events(limit=10_000)
    calls_before = registry.count_model_calls()
    started = perf_counter()
    result = service.run(
        events=events,
        proposals=proposals,
        external_links=[],
        models=models,
        run_id=run_id,
        registry_scope_id=scope_id,
    )
    wall_ms = round((perf_counter() - started) * 1000)
    if result.status != "FINALIZED" or result.partition is None:
        registry.fail_cross_document_run(run_id, error_code=result.status)
        raise RuntimeError(result.model_dump_json())
    packages, memberships, assignments, external_relations, redirects = (
        project_frozen_partition_v3(
            result.partition,
            events=events,
            existing_packages=registry.list_current_packages(limit=10_000),
            run_id=run_id,
        )
    )
    apply = registry.activate_package_partition_v3(
        packages=packages,
        memberships=memberships,
        assignments=assignments,
        external_relations=external_relations,
        redirects=redirects,
        run_id=run_id,
    )
    first_calls = registry.count_model_calls() - calls_before
    replay_before = registry.count_model_calls()
    replay_started = perf_counter()
    replay = service.run(
        events=events,
        proposals=proposals,
        external_links=[],
        models=object(),
        run_id=run_id,
        registry_scope_id=scope_id,
    )
    replay_wall_ms = round((perf_counter() - replay_started) * 1000)
    replay_calls = registry.count_model_calls() - replay_before
    report = {
        "report_version": "cdecr-package-global-registry-v3-package-only-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "snapshot": snapshot,
        "run_id": run_id,
        "scope_id": scope_id,
        "status": result.status,
        "first_wall_ms": wall_ms,
        "telemetry": result.telemetry,
        "shape": _package_shape(registry),
        "model_usage": _model_usage(output_path),
        "apply": apply,
        "first_model_calls": first_calls,
        "idempotency": {
            "status": replay.status,
            "wall_ms": replay_wall_ms,
            "new_model_calls": replay_calls,
            "partition_hash_stable": bool(
                replay.partition is not None
                and replay.partition.partition_hash == result.partition.partition_hash
            ),
        },
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return int(
        replay_calls != 0
        or replay.partition is None
        or replay.partition.partition_hash != result.partition.partition_hash
    )


if __name__ == "__main__":
    raise SystemExit(main())
