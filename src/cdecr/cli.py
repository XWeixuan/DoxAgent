"""Machine-readable CLI for the standalone CDECR step-one module."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cdecr.config import CDECRSettings
from cdecr.cross_document import (
    ENGINE_VERSION as CROSS_DOCUMENT_ENGINE_VERSION,
)
from cdecr.cross_document import (
    PROMPT_VERSION as CROSS_DOCUMENT_PROMPT_VERSION,
)
from cdecr.cross_document import (
    CrossDocumentEngine,
)
from cdecr.cross_document_contracts import CrossDocumentResult, CrossDocumentStatus
from cdecr.data import DoxAtlasRawMediaReader, SourceReadError, write_manifest, write_snapshot
from cdecr.evaluation import evaluate_results
from cdecr.models import (
    STRUCTURED_OUTPUT_MODE,
    STRUCTURED_REASONING_EFFORT,
    DashScopeEmbeddingClient,
    DashScopeStructuredModelClient,
    ModelAdapterError,
    ModelTier,
    probe_models,
)
from cdecr.normalization import CATALOG_VERSION
from cdecr.ports import DecisionAuditRecord, SourceQuery
from cdecr.preprocessing import PIPELINE_VERSION
from cdecr.registry import RegistryError, SQLiteCDECRRegistry
from cdecr.result_export import export_final_clusters
from cdecr.single_document import PROMPT_VERSION, SingleDocumentProcessor
from cdecr.single_document_contracts import ProcessingStatus, SingleDocumentResult
from cdecr.step4_evaluation import (
    M4ReviewArtifact,
    Step4Idempotency,
    build_step4_report,
    load_step4_corpus,
    run_m4_reviews,
    step4_evaluation_lock,
    write_step4_report,
)


def _json_stdout(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _json_stderr(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamps must include a timezone")
    return parsed


def _tiers(value: str) -> list[ModelTier]:
    try:
        tiers = [ModelTier(item.strip().lower()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "tiers must be a comma-separated subset of m1,m2,m3,m4"
        ) from exc
    if not tiers or len(tiers) != len(set(tiers)):
        raise argparse.ArgumentTypeError("tiers must be non-empty and unique")
    return tiers


def _registry(settings: CDECRSettings) -> SQLiteCDECRRegistry:
    registry = SQLiteCDECRRegistry(settings.sqlite_path)
    registry.initialize()
    return registry


def _registry_init(settings: CDECRSettings, _: argparse.Namespace) -> int:
    registry = _registry(settings)
    _json_stdout(
        {
            "ok": True,
            "command": "registry.init",
            "path": str(registry.path),
            "pragma": registry.pragma_state(),
        }
    )
    return 0


def _snapshot(settings: CDECRSettings, args: argparse.Namespace) -> int:
    supabase_url, key = settings.require_supabase()
    query = SourceQuery(
        market=args.market,
        ticker=args.ticker,
        start_at=args.start,
        end_at=args.end,
        limit=args.limit,
        min_text_chars=args.min_text_chars,
    )
    with DoxAtlasRawMediaReader(
        supabase_url=supabase_url,
        publishable_key=key,
        timeout_seconds=settings.http_timeout_seconds,
        page_size=args.page_size,
    ) as reader:
        batch = reader.read(query)
    write_snapshot(batch, path=args.output)
    if args.manifest is not None:
        write_manifest(batch, path=args.manifest)

    registry = _registry(settings)
    inserted = sum(
        registry.save_source(record.message, fingerprint=record.document_fingerprint)
        for record in batch.accepted
    )
    query_hash = hashlib.sha256(query.model_dump_json().encode("utf-8")).hexdigest()[:16]
    rejection_audits = 0
    for rejected in batch.rejected:
        rejection_audits += registry.append_decision_audit(
            DecisionAuditRecord(
                audit_id=f"source-rejection:{query_hash}:{rejected.source_row_id}",
                decision_type="SOURCE_REJECTION",
                subject_id=rejected.source_row_id,
                payload={"reason_codes": rejected.reason_codes},
            )
        )
    _json_stdout(
        {
            "ok": True,
            "command": "data.snapshot",
            "raw_count": batch.raw_count,
            "accepted_count": len(batch.accepted),
            "rejected_count": len(batch.rejected),
            "registry_inserted": inserted,
            "rejection_audits_inserted": rejection_audits,
            "snapshot_path": str(args.output),
            "manifest_path": str(args.manifest) if args.manifest is not None else None,
        }
    )
    return 0


def _model_names(settings: CDECRSettings) -> dict[ModelTier, str]:
    return {
        ModelTier.M1: settings.model_m1,
        ModelTier.M2: settings.model_m2,
        ModelTier.M3: settings.model_m3,
        ModelTier.M4: settings.model_m4,
    }


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _required_int(value: object, *, name: str) -> int:
    if not isinstance(value, int):
        raise ValueError(f"model probe result is missing integer {name}")
    return value


def _models_probe(settings: CDECRSettings, args: argparse.Namespace) -> int:
    api_key = settings.require_dashscope()
    registry = _registry(settings)
    results: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    for tier in args.tiers:
        call_id = str(uuid.uuid4())
        try:
            result = probe_models(
                api_key=api_key,
                base_url=settings.dashscope_base_url,
                tiers=[tier],
                model_names=_model_names(settings),
                dimensions=settings.embedding_dimensions,
                timeout_seconds=settings.model_timeout_seconds,
                fallback_api_keys=settings.dashscope_fallback_api_keys(),
            )[0]
        except ModelAdapterError as exc:
            registry.record_model_call(
                model_call_id=call_id,
                run_id=None,
                tier=tier.value,
                model=_model_names(settings)[tier],
                status="FAILED",
                input_tokens=None,
                output_tokens=None,
                latency_ms=exc.latency_ms,
                error_code=exc.code,
                metadata={"probe": True, "status_code": exc.status_code},
            )
            failures.append(
                {
                    "tier": tier.value,
                    "model": _model_names(settings)[tier],
                    "ok": False,
                    "error_code": exc.code,
                    "status_code": exc.status_code,
                }
            )
            continue
        registry.record_model_call(
            model_call_id=call_id,
            run_id=None,
            tier=tier.value,
            model=str(result["model"]),
            status="SUCCEEDED",
            input_tokens=_optional_int(result.get("input_tokens")),
            output_tokens=_optional_int(result.get("output_tokens")),
            latency_ms=_required_int(result.get("latency_ms"), name="latency_ms"),
            error_code=None,
            metadata={"probe": True, "dimensions": result.get("dimensions")},
        )
        results.append(result)
    payload: dict[str, Any] = {
        "ok": not failures,
        "command": "models.probe",
        "results": results,
        "failures": failures,
    }
    _json_stdout(payload)
    return 0 if not failures else 1


def _document_processor(
    settings: CDECRSettings, registry: SQLiteCDECRRegistry
) -> SingleDocumentProcessor:
    api_key = settings.require_dashscope()
    embedding = DashScopeEmbeddingClient(
        api_key=api_key,
        base_url=settings.dashscope_base_url,
        model=settings.model_m1,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=settings.dashscope_fallback_api_keys(),
    )
    m2 = DashScopeStructuredModelClient(
        tier=ModelTier.M2,
        api_key=api_key,
        base_url=settings.dashscope_base_url,
        model=settings.model_m2,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=settings.dashscope_fallback_api_keys(),
    )
    m3 = DashScopeStructuredModelClient(
        tier=ModelTier.M3,
        api_key=api_key,
        base_url=settings.dashscope_base_url,
        model=settings.model_m3,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=settings.dashscope_fallback_api_keys(),
    )
    m4 = DashScopeStructuredModelClient(
        tier=ModelTier.M4,
        api_key=api_key,
        base_url=settings.dashscope_base_url,
        model=settings.model_m4,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=settings.dashscope_fallback_api_keys(),
    )
    return SingleDocumentProcessor(
        registry=registry,
        embedding_client=embedding,
        m2_client=m2,
        m3_client=m3,
        m4_client=m4,
        model_m1=settings.model_m1,
        model_m2=settings.model_m2,
        model_m3=settings.model_m3,
        model_m4=settings.model_m4,
    )


def _cross_document_engine(
    settings: CDECRSettings, registry: SQLiteCDECRRegistry
) -> CrossDocumentEngine:
    api_key = settings.require_dashscope()
    embedding = DashScopeEmbeddingClient(
        api_key=api_key,
        base_url=settings.dashscope_base_url,
        model=settings.model_m1,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=settings.dashscope_fallback_api_keys(),
    )
    m2 = DashScopeStructuredModelClient(
        tier=ModelTier.M2,
        api_key=api_key,
        base_url=settings.dashscope_base_url,
        model=settings.model_m2,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=settings.dashscope_fallback_api_keys(),
    )
    m3 = DashScopeStructuredModelClient(
        tier=ModelTier.M3,
        api_key=api_key,
        base_url=settings.dashscope_base_url,
        model=settings.model_m3,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=settings.dashscope_fallback_api_keys(),
    )
    return CrossDocumentEngine(
        registry=registry,
        embedding_client=embedding,
        m2_client=m2,
        m3_client=m3,
        model_m1=settings.model_m1,
        model_m2=settings.model_m2,
        model_m3=settings.model_m3,
    )


def _document_summary(value: SingleDocumentResult) -> dict[str, object]:
    return {
        "run_id": value.run_id,
        "message_id": value.message_id,
        "processing_key": value.processing_key,
        "status": value.status.value,
        "mention_ids": [mention.mention_id for mention in value.mentions],
        "mention_count": len(value.mentions),
        "model_call_count": len(value.model_calls),
        "judge_invoked": value.judge_routing.invoked,
        "judge_reasons": value.judge_routing.reasons,
        "failures": [failure.model_dump(mode="json") for failure in value.failures],
        "reused": value.reused,
    }


def _documents_process(settings: CDECRSettings, args: argparse.Namespace) -> int:
    registry = _registry(settings)
    result = _document_processor(settings, registry).process(args.message_id)
    payload = _document_summary(result)
    payload.update(
        {"ok": result.status is ProcessingStatus.SUCCEEDED, "command": "documents.process"}
    )
    _json_stdout(payload)
    return 0 if result.status is ProcessingStatus.SUCCEEDED else 1


def _documents_batch(settings: CDECRSettings, args: argparse.Namespace) -> int:
    registry = _registry(settings)
    sources = registry.list_sources(
        market=args.market,
        ticker=args.ticker,
        start_at=args.start,
        end_at=args.end,
        limit=args.limit,
    )
    loaded_from = "registry"
    if not sources:
        supabase_url, key = settings.require_supabase()
        query = SourceQuery(
            market=args.market,
            ticker=args.ticker,
            start_at=args.start,
            end_at=args.end,
            limit=args.limit,
            min_text_chars=args.min_text_chars,
        )
        with DoxAtlasRawMediaReader(
            supabase_url=supabase_url,
            publishable_key=key,
            timeout_seconds=settings.http_timeout_seconds,
            page_size=min(args.limit, 200),
        ) as reader:
            batch = reader.read(query)
        for record in batch.accepted:
            registry.save_source(record.message, fingerprint=record.document_fingerprint)
        sources = [record.message for record in batch.accepted]
        loaded_from = "supabase_read_only"
    processor = _document_processor(settings, registry)
    results = processor.process_batch([source.message_id for source in sources])
    failed = sum(result.status is ProcessingStatus.FAILED for result in results)
    _json_stdout(
        {
            "ok": failed == 0,
            "command": "documents.batch",
            "loaded_from": loaded_from,
            "document_count": len(results),
            "succeeded_count": len(results) - failed,
            "failed_count": failed,
            "results": [_document_summary(result) for result in results],
        }
    )
    return 0 if failed == 0 else 1


def _event_summary(value: CrossDocumentResult) -> dict[str, object]:
    return {
        "run_id": value.run_id,
        "message_id": value.message_id,
        "processing_key": value.processing_key,
        "status": value.status.value,
        "atomic_event_ids": [event.event_id for event in value.atomic_events],
        "package_ids": [package.package_id for package in value.packages],
        "atomic_assignment_count": len(value.atomic_assignments),
        "package_assignment_count": len(value.package_assignments),
        "hold_ids": value.hold_ids,
        "model_call_count": len(value.model_calls),
        "candidate_counts": value.candidate_counts,
        "failure_stage": value.failure_stage,
        "error_code": value.error_code,
        "reused": value.reused,
    }


def _events_process(settings: CDECRSettings, args: argparse.Namespace) -> int:
    registry = _registry(settings)
    document = _document_processor(settings, registry).process(args.message_id)
    if document.status is ProcessingStatus.FAILED:
        _json_stdout(
            {
                "ok": False,
                "command": "events.process",
                "document": _document_summary(document),
                "events": None,
            }
        )
        return 1
    events = _cross_document_engine(settings, registry).process(args.message_id)
    _json_stdout(
        {
            "ok": events.status is CrossDocumentStatus.SUCCEEDED,
            "command": "events.process",
            "document": _document_summary(document),
            "events": _event_summary(events),
        }
    )
    return 0 if events.status is CrossDocumentStatus.SUCCEEDED else 1


def _events_batch(settings: CDECRSettings, args: argparse.Namespace) -> int:
    registry = _registry(settings)
    sources = registry.list_sources(
        market=args.market,
        ticker=args.ticker,
        start_at=args.start,
        end_at=args.end,
        limit=args.limit,
    )
    loaded_from = "registry"
    if not sources:
        supabase_url, key = settings.require_supabase()
        query = SourceQuery(
            market=args.market,
            ticker=args.ticker,
            start_at=args.start,
            end_at=args.end,
            limit=args.limit,
            min_text_chars=args.min_text_chars,
        )
        with DoxAtlasRawMediaReader(
            supabase_url=supabase_url,
            publishable_key=key,
            timeout_seconds=settings.http_timeout_seconds,
            page_size=min(args.limit, 200),
        ) as reader:
            batch = reader.read(query)
        for record in batch.accepted:
            registry.save_source(record.message, fingerprint=record.document_fingerprint)
        sources = [record.message for record in batch.accepted]
        loaded_from = "supabase_read_only"
    document_processor = _document_processor(settings, registry)
    event_engine = _cross_document_engine(settings, registry)
    results: list[dict[str, object]] = []
    failed = 0
    for source in sources:
        document = document_processor.process(source.message_id)
        events: CrossDocumentResult | None = None
        if document.status is ProcessingStatus.SUCCEEDED:
            events = event_engine.process(source.message_id)
        if document.status is ProcessingStatus.FAILED or (
            events is not None and events.status is CrossDocumentStatus.FAILED
        ):
            failed += 1
        results.append(
            {
                "document": _document_summary(document),
                "events": _event_summary(events) if events is not None else None,
            }
        )
    _json_stdout(
        {
            "ok": failed == 0,
            "command": "events.batch",
            "loaded_from": loaded_from,
            "document_count": len(results),
            "succeeded_count": len(results) - failed,
            "failed_count": failed,
            "results": results,
        }
    )
    return 0 if failed == 0 else 1


def _evaluation_run(settings: CDECRSettings, args: argparse.Namespace) -> int:
    with step4_evaluation_lock(args.registry):
        return _evaluation_run_locked(settings, args)


def _evaluation_review(settings: CDECRSettings, args: argparse.Namespace) -> int:
    _, corpus = load_step4_corpus(args.snapshot, args.manifest, limit=args.limit)
    registry = SQLiteCDECRRegistry(args.registry)
    registry.initialize()
    api_key = settings.require_dashscope()
    client = DashScopeStructuredModelClient(
        tier=ModelTier.M4,
        api_key=api_key,
        base_url=settings.dashscope_base_url,
        model=settings.model_m4,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=settings.dashscope_fallback_api_keys(),
    )
    with step4_evaluation_lock(args.registry):
        artifact = run_m4_reviews(
            corpus=corpus,
            registry=registry,
            client=client,
            model=settings.model_m4,
            output_path=args.output,
        )
    _json_stdout(
        {
            "ok": True,
            "command": "evaluation.review",
            "document_count": len(artifact.documents),
            "m4_review_status": artifact.m4_review_status,
            "human_review_status": artifact.human_review_status,
            "output_path": str(args.output),
        }
    )
    return 0


def _evaluation_export(_: CDECRSettings, args: argparse.Namespace) -> int:
    registry = SQLiteCDECRRegistry(args.registry)
    registry.initialize()
    quality = None
    if args.m4_review.exists():
        artifact = M4ReviewArtifact.model_validate_json(
            args.m4_review.read_text(encoding="utf-8")
        )
        results = [
            registry.get_latest_completed_document_result_for_message(document.message_id)
            for document in artifact.documents
        ]
        quality = evaluate_results(
            [result for result in results if result is not None], artifact.documents
        )
    counts = export_final_clusters(
        registry=registry,
        json_path=args.output_json,
        markdown_path=args.output_markdown,
        quality_metrics=quality,
    )
    _json_stdout(
        {
            "ok": True,
            "command": "evaluation.export",
            "json_path": str(args.output_json),
            "markdown_path": str(args.output_markdown),
            "counts": counts,
            "quality_metrics": quality.model_dump(mode="json") if quality else None,
        }
    )
    return 0


def _evaluation_run_locked(settings: CDECRSettings, args: argparse.Namespace) -> int:
    manifest_version, corpus = load_step4_corpus(
        args.snapshot, args.manifest, limit=args.limit
    )
    registry = SQLiteCDECRRegistry(args.registry)
    registry.initialize()
    for row, source in corpus:
        registry.save_source(source, fingerprint=row.document_fingerprint)

    document_processor = _document_processor(settings, registry)
    event_engine = _cross_document_engine(settings, registry)
    document_results: list[SingleDocumentResult] = []
    event_results: list[CrossDocumentResult | None] = []
    checkpoint_path = args.output.with_suffix(args.output.suffix + ".checkpoint.json")
    processing_corpus = sorted(
        corpus, key=lambda item: (item[1].published_at, item[1].message_id)
    )
    for index, (row, source) in enumerate(processing_corpus, start=1):
        document = document_processor.process(source.message_id)
        event = (
            event_engine.process(source.message_id)
            if document.status is ProcessingStatus.SUCCEEDED
            else None
        )
        document_results.append(document)
        event_results.append(event)
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text(
            json.dumps(
                {
                    "report_version": "cdecr-step4-checkpoint-v1",
                    "completed_rows": index,
                    "total_rows": len(corpus),
                    "last_source_row_id": row.source_row_id,
                    "outcomes": [
                        {
                            "message_id": item.message_id,
                            "document_status": item.status.value,
                            "event_status": getattr(
                                event_results[offset], "status", None
                            ),
                        }
                        for offset, item in enumerate(document_results)
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    # Load the original persisted results so a resumed run reports all prior calls.
    current_document_by_id = {item.message_id: item for item in document_results}
    current_event_by_id = {
        item.message_id: item for item in event_results if item is not None
    }
    persisted_documents: list[SingleDocumentResult] = []
    persisted_events: list[CrossDocumentResult | None] = []
    for _, source in corpus:
        current_document = current_document_by_id[source.message_id]
        current_event = current_event_by_id.get(source.message_id)
        document = (
            registry.get_latest_completed_document_result_for_message(source.message_id)
            or current_document
        )
        persisted_documents.append(document)
        if current_event is None:
            persisted_events.append(None)
            continue
        mentions = sorted(document.mentions, key=lambda item: item.mention_id)
        persisted_events.append(
            registry.get_completed_cross_document_result(
                event_engine.processing_key(source.message_id, mentions)
            )
            or current_event
        )

    model_calls_before = registry.count_model_calls()
    mention_count_before = sum(
        len(registry.list_mentions_for_message(source.message_id)) for _, source in corpus
    )
    atomic_count_before = len(registry.list_current_atomic_events())
    package_count_before = len(registry.list_current_packages())

    # Re-open the Registry and clients to prove process-restart recovery and idempotency.
    restarted_registry = SQLiteCDECRRegistry(args.registry)
    restarted_registry.initialize()
    restarted_documents = _document_processor(settings, restarted_registry)
    restarted_events = _cross_document_engine(settings, restarted_registry)
    rerun_documents: list[SingleDocumentResult] = []
    rerun_events: list[CrossDocumentResult] = []
    for (_, source), document in zip(corpus, persisted_documents, strict=True):
        if document.status is not ProcessingStatus.SUCCEEDED:
            continue
        reused_document = restarted_documents.process(source.message_id)
        rerun_documents.append(reused_document)
        reused_event = restarted_events.process(source.message_id)
        rerun_events.append(reused_event)

    idempotency = Step4Idempotency(
        rerun_model_call_delta=restarted_registry.count_model_calls() - model_calls_before,
        rerun_mention_delta=sum(
            len(restarted_registry.list_mentions_for_message(source.message_id))
            for _, source in corpus
        )
        - mention_count_before,
        rerun_atomic_delta=(
            len(restarted_registry.list_current_atomic_events()) - atomic_count_before
        ),
        rerun_package_delta=(
            len(restarted_registry.list_current_packages()) - package_count_before
        ),
        all_successful_documents_reused=all(item.reused for item in rerun_documents),
        all_successful_events_reused=all(item.reused for item in rerun_events),
    )
    report = build_step4_report(
        manifest_version=manifest_version,
        corpus=corpus,
        document_results=persisted_documents,
        event_results=persisted_events,
        registry=restarted_registry,
        idempotency=idempotency,
    )
    write_step4_report(report, args.output)
    _json_stdout(
        {
            "ok": report.acceptance_passed,
            "command": "evaluation.run",
            "report_path": str(args.output),
            "selected_document_count": report.selected_document_count,
            "completed_document_count": report.completed_document_count,
            "completed_event_count": report.completed_event_count,
            "failed_document_count": report.failed_document_count,
            "atomic_event_count": report.atomic_event_count,
            "package_count": report.package_count,
            "model_call_count": report.call_budget.call_count,
            "acceptance_passed": report.acceptance_passed,
        }
    )
    return 0 if report.acceptance_passed else 1


def _boundary_violations(package_root: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                if module == "doxagent" or module.startswith("doxagent."):
                    violations.append(f"{path}:{getattr(node, 'lineno', 0)}:{module}")
    return violations


def _doctor(settings: CDECRSettings, args: argparse.Namespace) -> int:
    checks: dict[str, dict[str, object]] = {}
    package_root = Path(__file__).resolve().parent
    violations = _boundary_violations(package_root)
    checks["package_boundary"] = {"ok": not violations, "violations": violations}

    try:
        registry = _registry(settings)
        pragma = registry.pragma_state()
        registry_ok = (
            pragma["user_version"] == 5
            and pragma["foreign_keys"] == 1
            and str(pragma["journal_mode"]).lower() == "wal"
        )
        checks["sqlite"] = {"ok": registry_ok, "path": str(registry.path), "pragma": pragma}
    except (OSError, RegistryError) as exc:
        checks["sqlite"] = {"ok": False, "error": type(exc).__name__}

    try:
        supabase_url, key = settings.require_supabase()
        start = args.start or datetime.now(UTC) - timedelta(days=1)
        end = args.end or datetime.now(UTC)
        query = SourceQuery(
            market=args.market,
            ticker=args.ticker,
            start_at=start,
            end_at=end,
            limit=1,
            min_text_chars=1,
        )
        with DoxAtlasRawMediaReader(
            supabase_url=supabase_url,
            publishable_key=key,
            timeout_seconds=settings.http_timeout_seconds,
            page_size=1,
        ) as reader:
            batch = reader.read(query)
        checks["supabase_read_only"] = {
            "ok": True,
            "raw_count": batch.raw_count,
            "accepted_count": len(batch.accepted),
        }
    except (ValueError, SourceReadError) as exc:
        checks["supabase_read_only"] = {"ok": False, "error": type(exc).__name__}

    try:
        settings.require_dashscope()
        models = _model_names(settings)
        checks["model_configuration"] = {
            "ok": settings.embedding_dimensions == 1024
            and STRUCTURED_OUTPUT_MODE == "json_object"
            and STRUCTURED_REASONING_EFFORT == "none",
            "models": {tier.value: name for tier, name in models.items()},
            "embedding_dimensions": settings.embedding_dimensions,
            "structured_output_mode": STRUCTURED_OUTPUT_MODE,
            "reasoning_effort": STRUCTURED_REASONING_EFFORT,
            "schema_projection_model_output": "disabled",
        }
    except ValueError as exc:
        checks["model_configuration"] = {"ok": False, "error": type(exc).__name__}

    checks["single_document_versions"] = {
        "ok": all((PIPELINE_VERSION, PROMPT_VERSION, CATALOG_VERSION)),
        "pipeline_version": PIPELINE_VERSION,
        "prompt_version": PROMPT_VERSION,
        "catalog_version": CATALOG_VERSION,
    }
    checks["single_document_routing"] = {
        "ok": settings.model_m2 == "deepseek-v4-flash"
        and settings.model_m3 == "qwen3.7-plus"
        and settings.model_m4 == "qwen3.7-max",
        "short_dreamer": "m2",
        "long_dreamer": "m3",
        "grounder": "m3",
        "judge": "m4_all_drafts",
    }
    prompt_root = package_root / "prompts" / "v1"
    cross_prompts = [
        prompt_root / "atomic_coreference.md",
        prompt_root / "package_assignment.md",
        prompt_root / "package_merge.md",
    ]
    checks["cross_document_versions"] = {
        "ok": bool(CROSS_DOCUMENT_ENGINE_VERSION and CROSS_DOCUMENT_PROMPT_VERSION)
        and all(path.is_file() for path in cross_prompts),
        "engine_version": CROSS_DOCUMENT_ENGINE_VERSION,
        "prompt_version": CROSS_DOCUMENT_PROMPT_VERSION,
    }
    checks["cross_document_routing"] = {
        "ok": settings.model_m1 == "text-embedding-v4"
        and settings.model_m2 == "deepseek-v4-flash"
        and settings.model_m3 == "qwen3.7-plus",
        "recall": "m0+m1",
        "hard_cannot_link": "m0",
        "atomic_default": "m2_batch",
        "atomic_complex": "m3_batch",
        "bounded_package": "m0_then_m2",
        "episode_package": "m2_or_m3",
    }

    ok = all(bool(check["ok"]) for check in checks.values())
    _json_stdout({"ok": ok, "command": "doctor", "checks": checks})
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m cdecr")
    commands = parser.add_subparsers(dest="command", required=True)

    registry = commands.add_parser("registry")
    registry_commands = registry.add_subparsers(dest="registry_command", required=True)
    registry_init = registry_commands.add_parser("init")
    registry_init.set_defaults(handler=_registry_init)

    data = commands.add_parser("data")
    data_commands = data.add_subparsers(dest="data_command", required=True)
    snapshot = data_commands.add_parser("snapshot")
    snapshot.add_argument("--market", required=True)
    snapshot.add_argument("--ticker", required=True)
    snapshot.add_argument("--start", required=True, type=_parse_timestamp)
    snapshot.add_argument("--end", required=True, type=_parse_timestamp)
    snapshot.add_argument("--limit", type=int, default=200)
    snapshot.add_argument("--min-text-chars", type=int, default=200)
    snapshot.add_argument("--page-size", type=int, default=200)
    snapshot.add_argument("--output", type=Path, required=True)
    snapshot.add_argument("--manifest", type=Path)
    snapshot.set_defaults(handler=_snapshot)

    models = commands.add_parser("models")
    model_commands = models.add_subparsers(dest="models_command", required=True)
    probe = model_commands.add_parser("probe")
    probe.add_argument("--tiers", type=_tiers, default=_tiers("m1,m2,m3,m4"))
    probe.set_defaults(handler=_models_probe)

    documents = commands.add_parser("documents")
    document_commands = documents.add_subparsers(dest="documents_command", required=True)
    process = document_commands.add_parser("process")
    process.add_argument("--message-id", required=True)
    process.set_defaults(handler=_documents_process)
    batch = document_commands.add_parser("batch")
    batch.add_argument("--market", required=True)
    batch.add_argument("--ticker", required=True)
    batch.add_argument("--start", required=True, type=_parse_timestamp)
    batch.add_argument("--end", required=True, type=_parse_timestamp)
    batch.add_argument("--limit", type=int, default=200)
    batch.add_argument("--min-text-chars", type=int, default=200)
    batch.set_defaults(handler=_documents_batch)

    events = commands.add_parser("events")
    event_commands = events.add_subparsers(dest="events_command", required=True)
    event_process = event_commands.add_parser("process")
    event_process.add_argument("--message-id", required=True)
    event_process.set_defaults(handler=_events_process)
    event_batch = event_commands.add_parser("batch")
    event_batch.add_argument("--market", required=True)
    event_batch.add_argument("--ticker", required=True)
    event_batch.add_argument("--start", required=True, type=_parse_timestamp)
    event_batch.add_argument("--end", required=True, type=_parse_timestamp)
    event_batch.add_argument("--limit", type=int, default=200)
    event_batch.add_argument("--min-text-chars", type=int, default=200)
    event_batch.set_defaults(handler=_events_batch)

    evaluation = commands.add_parser("evaluation")
    evaluation_commands = evaluation.add_subparsers(
        dest="evaluation_command", required=True
    )
    evaluation_run = evaluation_commands.add_parser("run")
    evaluation_run.add_argument(
        "--snapshot",
        type=Path,
        default=Path(".tmp/cdecr/baselines/us_mu_2026-06-25.jsonl"),
    )
    evaluation_run.add_argument(
        "--manifest",
        type=Path,
        default=Path(
            "dev_plan/CDECR/baselines/mu_2026-06-25_step2_eval_manifest.json"
        ),
    )
    evaluation_run.add_argument(
        "--registry",
        type=Path,
        default=Path(".tmp/cdecr/evaluation/step4.sqlite3"),
    )
    evaluation_run.add_argument(
        "--output",
        type=Path,
        default=Path(".tmp/cdecr/evaluation/step4_report.json"),
    )
    evaluation_run.add_argument("--limit", type=int, default=24)
    evaluation_run.set_defaults(handler=_evaluation_run)
    evaluation_review = evaluation_commands.add_parser("review")
    evaluation_review.add_argument(
        "--snapshot",
        type=Path,
        default=Path(".tmp/cdecr/baselines/us_mu_2026-06-25.jsonl"),
    )
    evaluation_review.add_argument(
        "--manifest",
        type=Path,
        default=Path(
            "dev_plan/CDECR/baselines/mu_2026-06-25_step2_eval_manifest.json"
        ),
    )
    evaluation_review.add_argument(
        "--registry",
        type=Path,
        default=Path(".tmp/cdecr/evaluation/step4.sqlite3"),
    )
    evaluation_review.add_argument(
        "--output",
        type=Path,
        default=Path(".tmp/cdecr/evaluation/mu_2026-06-25_step2_gold.json"),
    )
    evaluation_review.add_argument("--limit", type=int, default=24)
    evaluation_review.set_defaults(handler=_evaluation_review)
    evaluation_export = evaluation_commands.add_parser("export")
    evaluation_export.add_argument(
        "--registry",
        type=Path,
        default=Path(".tmp/cdecr/evaluation/step4.sqlite3"),
    )
    evaluation_export.add_argument(
        "--m4-review",
        type=Path,
        default=Path(".tmp/cdecr/evaluation/mu_2026-06-25_step2_gold.json"),
    )
    evaluation_export.add_argument("--output-json", type=Path, required=True)
    evaluation_export.add_argument("--output-markdown", type=Path, required=True)
    evaluation_export.set_defaults(handler=_evaluation_export)

    doctor = commands.add_parser("doctor")
    doctor.add_argument("--market", default="US")
    doctor.add_argument("--ticker", default="MU")
    doctor.add_argument("--start", type=_parse_timestamp)
    doctor.add_argument("--end", type=_parse_timestamp)
    doctor.set_defaults(handler=_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = CDECRSettings()
        return int(args.handler(settings, args))
    except (ModelAdapterError, SourceReadError, RegistryError, ValueError, OSError) as exc:
        error_code = exc.code if isinstance(exc, ModelAdapterError) else type(exc).__name__
        _json_stderr({"ok": False, "error_code": error_code})
        return 1
    except Exception as exc:  # pragma: no cover - final credential-safe boundary
        _json_stderr({"ok": False, "error_code": type(exc).__name__})
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
