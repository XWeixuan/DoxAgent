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
from time import perf_counter
from typing import Any, cast

from cdecr.bulk_epoch.engine import BulkEpochEngine
from cdecr.bulk_epoch.executor import AsyncModelExecutor, AsyncStructuredModelClient
from cdecr.canonical_field_resolution import FIELD_RESOLVER_VERSION
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
from cdecr.identity_compiler import IDENTITY_COMPILER_VERSION
from cdecr.kb_v2 import CATALOG_NAMES, V2KnowledgeBase
from cdecr.mention_finalization import FINALIZATION_VERSION
from cdecr.model_routing import (
    InvocationChannel,
    general_lane_channel,
    general_lane_profile,
)
from cdecr.models import (
    STRUCTURED_OUTPUT_MODE,
    STRUCTURED_REASONING_EFFORT,
    DashScopeEmbeddingClient,
    DashScopeStructuredModelClient,
    DeepSeekStructuredModelClient,
    ModelAdapterError,
    ModelTier,
    ProbePayload,
    probe_models,
)
from cdecr.parent_occurrence_contracts import ParentInductionBatch
from cdecr.ports import DecisionAuditRecord, SourceQuery, StructuredModelRequest
from cdecr.preprocessing import PIPELINE_VERSION
from cdecr.registry import SCHEMA_VERSION, RegistryError, SQLiteCDECRRegistry
from cdecr.result_export import export_final_clusters
from cdecr.scheduler import CDECRScheduler
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
    registry = SQLiteCDECRRegistry(
        settings.sqlite_path, bulk_read_mode=settings.bulk_registry_read_mode
    )
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


def _registry_rebuild_derived(settings: CDECRSettings, args: argparse.Namespace) -> int:
    registry = _registry(settings)
    message_ids = sorted({item.message_id for item in registry.list_all_mentions(limit=100000)})
    engine = None if args.clear_only else _cross_document_engine(settings, registry)
    deleted = registry.rebuild_derived_state()
    if args.clear_only:
        _json_stdout(
            {
                "ok": True,
                "command": "registry.rebuild-derived",
                "mode": "clear_only",
                "deleted": deleted,
                "message_count": len(message_ids),
            }
        )
        return 0

    assert engine is not None
    results: list[CrossDocumentResult] = []
    for message_id in message_ids:
        results.append(engine.process(message_id))
    failures = [item for item in results if item.status is not CrossDocumentStatus.SUCCEEDED]
    _json_stdout(
        {
            "ok": not failures,
            "command": "registry.rebuild-derived",
            "mode": "rebuild",
            "deleted": deleted,
            "message_count": len(message_ids),
            "succeeded_count": len(results) - len(failures),
            "failed_count": len(failures),
            "results": [_event_summary(item) for item in results],
        }
    )
    return 0 if not failures else 1


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


def _scheduler(settings: CDECRSettings) -> CDECRScheduler:
    return CDECRScheduler(
        m1_limit=settings.scheduler_m1_concurrency,
        m2_limit=settings.scheduler_m2_concurrency,
        m3_limit=settings.scheduler_m3_concurrency,
        m4_limit=settings.scheduler_m4_concurrency,
        structured_start_interval_seconds=(settings.structured_request_start_interval_seconds),
        structured_provider_target=settings.structured_provider_target_concurrency,
        structured_provider_hard_limit=settings.structured_provider_hard_concurrency,
        structured_provider_start_rate=settings.structured_provider_start_rate,
        structured_provider_initial_burst=settings.structured_provider_initial_burst,
        stage_limits={
            "dreamer": settings.dreamer_active_requests,
            "dreamer_zero_recovery": settings.dreamer_active_requests,
            "dreamer_relevance": settings.dreamer_relevance_active_requests,
            "grounder": settings.grounder_active_requests,
            "grounder_item_repair": settings.item_repair_active_requests,
            "grounder_missing_recovery": settings.item_repair_active_requests,
            "grounder_missing_item_recovery": settings.item_repair_active_requests,
            "judge": settings.judge_active_requests,
            "judge_coverage_recovery": settings.item_repair_active_requests,
            "judge_item_repair": settings.item_repair_active_requests,
        },
        repair_limit=settings.item_repair_active_requests,
        max_retries=settings.structured_provider_max_retries,
        provider_first_pause_seconds=settings.provider_first_pause_seconds,
        provider_second_pause_seconds=settings.provider_second_pause_seconds,
        provider_half_open_probes=settings.provider_half_open_probes,
        provider_recovery_start_rate=settings.provider_recovery_start_rate,
        provider_recovery_initial_concurrency=settings.provider_recovery_initial_concurrency,
    )


def _structured_client(
    settings: CDECRSettings,
    scheduler_lane: ModelTier,
    *,
    model_profile: ModelTier | None = None,
    invocation_channel: InvocationChannel | None = None,
) -> DashScopeStructuredModelClient | DeepSeekStructuredModelClient:
    profile = model_profile or general_lane_profile(scheduler_lane)
    channel = invocation_channel or general_lane_channel(scheduler_lane)
    provider = {
        ModelTier.M2: settings.model_m2_provider,
        ModelTier.M3: settings.model_m3_provider,
        ModelTier.M4: settings.model_m4_provider,
    }[scheduler_lane]
    if provider == "deepseek":
        return DeepSeekStructuredModelClient(
            tier=profile,
            api_key=settings.require_deepseek(),
            base_url=settings.deepseek_base_url,
            model={
                ModelTier.M2: settings.model_m2,
                ModelTier.M3: settings.model_m3,
                ModelTier.M4: settings.model_m4,
            }[profile],
            reasoning_effort={
                ModelTier.M2: settings.model_m2_reasoning_effort,
                ModelTier.M3: settings.model_m3_reasoning_effort,
                ModelTier.M4: settings.model_m4_reasoning_effort,
            }[profile],
            strict={
                ModelTier.M2: settings.model_m2_strict,
                ModelTier.M3: settings.model_m3_strict,
                ModelTier.M4: settings.model_m4_strict,
            }[scheduler_lane],
            timeout_seconds=settings.model_timeout_seconds,
        )
    api_key = settings.require_dashscope()
    model = {
        ModelTier.M2: settings.model_m2,
        ModelTier.M3: settings.model_m3,
        ModelTier.M4: settings.model_m4,
    }[profile]
    return DashScopeStructuredModelClient(
        tier=profile,
        api_key=api_key,
        base_url=settings.dashscope_base_url,
        model=model,
        reasoning_effort={
            ModelTier.M2: settings.model_m2_reasoning_effort,
            ModelTier.M3: settings.model_m3_reasoning_effort,
            ModelTier.M4: settings.model_m4_reasoning_effort,
        }[profile],
        strict={
            ModelTier.M2: settings.model_m2_strict,
            ModelTier.M3: settings.model_m3_strict,
            ModelTier.M4: settings.model_m4_strict,
        }[scheduler_lane],
        structured_transport=(
            "chat"
            if channel is InvocationChannel.CHAT_JSON_OBJECT
            else "responses"
        ),
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=settings.dashscope_fallback_api_keys(),
        key_rotation_enabled=settings.provider_key_rotation_enabled,
        auto_quarantine_enabled=settings.provider_auto_quarantine_enabled,
    )


def _package_v3_client(settings: CDECRSettings) -> DashScopeStructuredModelClient:
    """Keep Package Responses transport isolated from the general M4 lane."""

    return DashScopeStructuredModelClient(
        tier=ModelTier.M3,
        api_key=settings.require_dashscope(),
        base_url=settings.dashscope_base_url,
        model=settings.model_m3,
        reasoning_effort=settings.model_m3_reasoning_effort,
        strict=False,
        structured_transport="responses",
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=settings.dashscope_fallback_api_keys(),
        key_rotation_enabled=settings.provider_key_rotation_enabled,
        auto_quarantine_enabled=settings.provider_auto_quarantine_enabled,
    )


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _required_int(value: object, *, name: str) -> int:
    if not isinstance(value, int):
        raise ValueError(f"model probe result is missing integer {name}")
    return value


def _models_probe(settings: CDECRSettings, args: argparse.Namespace) -> int:
    registry = _registry(settings)
    results: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    for tier in args.tiers:
        call_id = str(uuid.uuid4())
        result: dict[str, object]
        try:
            provider = {
                ModelTier.M2: settings.model_m2_provider,
                ModelTier.M3: settings.model_m3_provider,
                ModelTier.M4: settings.model_m4_provider,
            }[tier]
            if provider == "deepseek":
                structured = _structured_client(
                    settings,
                    tier,
                    model_profile=tier,
                ).complete(
                    StructuredModelRequest(
                        system_prompt="You are a deterministic API health probe.",
                        user_prompt=f"Return ok=true, tier={tier.value}, and value=1.",
                        json_schema=ProbePayload.model_json_schema(),
                    )
                )
                validated_probe = ProbePayload.model_validate(structured.payload)
                if (
                    not validated_probe.ok
                    or validated_probe.tier is not tier
                    or validated_probe.value != 1
                ):
                    raise ModelAdapterError(
                        tier=tier,
                        code="probe_value_mismatch",
                        latency_ms=structured.latency_ms,
                    )
                result = {
                    "tier": tier.value,
                    "model": structured.model,
                    "ok": True,
                    "input_tokens": structured.input_tokens,
                    "output_tokens": structured.output_tokens,
                    "latency_ms": structured.latency_ms,
                }
            else:
                result = probe_models(
                    api_key=settings.require_dashscope(),
                    base_url=settings.dashscope_base_url,
                    tiers=[tier],
                    model_names=_model_names(settings),
                    dimensions=settings.embedding_dimensions,
                    timeout_seconds=settings.model_timeout_seconds,
                    fallback_api_keys=settings.dashscope_fallback_api_keys(),
                    reasoning_efforts={
                        ModelTier.M2: settings.model_m2_reasoning_effort,
                        ModelTier.M3: settings.model_m3_reasoning_effort,
                        ModelTier.M4: settings.model_m4_reasoning_effort,
                    },
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
    settings: CDECRSettings,
    registry: SQLiteCDECRRegistry,
    scheduler: CDECRScheduler | None = None,
) -> SingleDocumentProcessor:
    if settings.grounder_issue_protocol in {"canary", "on"}:
        raise ValueError("Grounder issue_codes protocol has not passed its node A/B gate")
    if settings.targeted_repair_protocol in {"canary", "on"}:
        raise ValueError("targeted repair protocol has not passed its node A/B gate")
    scheduler = scheduler or _scheduler(settings)
    api_key = settings.require_dashscope()
    embedding = DashScopeEmbeddingClient(
        api_key=api_key,
        base_url=settings.dashscope_base_url,
        model=settings.model_m1,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=settings.dashscope_fallback_api_keys(),
        key_rotation_enabled=settings.provider_key_rotation_enabled,
        auto_quarantine_enabled=settings.provider_auto_quarantine_enabled,
    )
    m2 = _structured_client(settings, ModelTier.M2)
    m3 = _structured_client(settings, ModelTier.M3)
    m4 = _structured_client(settings, ModelTier.M4)
    scheduled_m2 = scheduler.structured_client(m2, tier=ModelTier.M2)
    scheduled_relevance = scheduler.structured_client(m2, tier=ModelTier.M3)
    return SingleDocumentProcessor(
        registry=registry,
        embedding_client=scheduler.embedding_client(embedding),
        m2_client=scheduled_m2,
        m3_client=scheduler.structured_client(m3, tier=ModelTier.M3),
        m4_client=scheduler.structured_client(m4, tier=ModelTier.M4),
        dreamer_responses_client=scheduled_m2,
        relevance_responses_client=scheduled_relevance,
        relevance_filter_mode=settings.relevance_filter_mode,
        relevance_target_profiles=settings.relevance_target_profiles,
        model_m1=settings.model_m1,
        model_m2=settings.model_m2,
        model_m3=settings.model_m3,
        model_m4=settings.model_m4,
        document_workers=settings.document_workers,
        document_block_concurrency=settings.document_block_concurrency,
        grounder_safe_normalization=settings.grounder_safe_normalization,
        grounder_primary_normalization=settings.grounder_primary_normalization,
    )


def _cross_document_engine(
    settings: CDECRSettings,
    registry: SQLiteCDECRRegistry,
    scheduler: CDECRScheduler | None = None,
) -> CrossDocumentEngine:
    scheduler = scheduler or _scheduler(settings)
    api_key = settings.require_dashscope()
    embedding = DashScopeEmbeddingClient(
        api_key=api_key,
        base_url=settings.dashscope_base_url,
        model=settings.model_m1,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=settings.dashscope_fallback_api_keys(),
        key_rotation_enabled=settings.provider_key_rotation_enabled,
        auto_quarantine_enabled=settings.provider_auto_quarantine_enabled,
    )
    m2 = _structured_client(settings, ModelTier.M2)
    m3 = _structured_client(settings, ModelTier.M3)
    m4 = _structured_client(settings, ModelTier.M4)
    package_m4 = _package_v3_client(settings)
    return CrossDocumentEngine(
        registry=registry,
        embedding_client=scheduler.embedding_client(embedding),
        m2_client=scheduler.structured_client(m2, tier=ModelTier.M2),
        m3_client=scheduler.structured_client(m3, tier=ModelTier.M3),
        m4_client=scheduler.structured_client(m4, tier=ModelTier.M4),
        package_m4_client=scheduler.structured_client(package_m4, tier=ModelTier.M4),
        model_m1=settings.model_m1,
        model_m2=settings.model_m2,
        model_m3=settings.model_m3,
        model_m4=settings.model_m4,
        package_model_m4=settings.model_m3,
        hard_cannot_link_mode=settings.atomic_hard_cannot_link_mode,
        n9_wire_protocol=settings.n9_wire_protocol,
        n9_active_requests=settings.n9_active_requests,
        n9_overlap_batch_packing=settings.n9_overlap_batch_packing,
        parent_compact_wire_dto=settings.parent_compact_wire_dto,
        package_registry_scope_id=settings.package_v3_registry_scope,
        package_v3_batch_size=settings.package_v3_batch_size,
        package_v3_context_token_budget=settings.package_v3_context_token_budget,
        package_v3_context_reserve_tokens=settings.package_v3_context_reserve_tokens,
        package_v3_description_token_budget=settings.package_v3_description_token_budget,
        package_v3_description_active_requests=(
            settings.package_v3_description_active_requests
        ),
        package_v3_reasoning_effort=settings.model_m3_reasoning_effort,
        package_v3_description_reasoning_effort=settings.model_m2_reasoning_effort,
        package_v3_strict_output=settings.model_m4_strict,
        atomic_cosine_backend=settings.atomic_cosine_backend,
    )


def _bulk_epoch_engine(
    settings: CDECRSettings,
    registry: SQLiteCDECRRegistry,
    scheduler: CDECRScheduler | None = None,
) -> BulkEpochEngine:
    scheduler = scheduler or _scheduler(settings)
    embedding = DashScopeEmbeddingClient(
        api_key=settings.require_dashscope(),
        base_url=settings.dashscope_base_url,
        model=settings.model_m1,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=settings.dashscope_fallback_api_keys(),
        key_rotation_enabled=settings.provider_key_rotation_enabled,
        auto_quarantine_enabled=settings.provider_auto_quarantine_enabled,
    )
    raw_m2 = _structured_client(settings, ModelTier.M2)
    raw_m3 = _structured_client(settings, ModelTier.M3)
    raw_m4 = _structured_client(settings, ModelTier.M4)
    package_m4 = _package_v3_client(settings)
    executor = AsyncModelExecutor(
        clients={
            ModelTier.M2: cast(AsyncStructuredModelClient, raw_m2),
            ModelTier.M3: cast(AsyncStructuredModelClient, raw_m3),
            ModelTier.M4: cast(AsyncStructuredModelClient, raw_m4),
        },
        tier_limits={
            ModelTier.M2: settings.scheduler_m2_concurrency,
            ModelTier.M3: settings.scheduler_m3_concurrency,
            ModelTier.M4: settings.scheduler_m4_concurrency,
        },
        stage_limits={
            "field_coreference": settings.field_active_requests,
            "atomic_coreference": settings.n9_active_requests,
            "atomic_coreference_escalation": settings.n9_escalation_active_requests,
            "atomic_late_convergence": settings.n9_late_active_requests,
            "parent_induction": settings.parent_induction_active_requests,
        },
        repair_limit=settings.item_repair_active_requests,
        provider_target=settings.structured_provider_target_concurrency,
        provider_hard_limit=settings.structured_provider_hard_concurrency,
        provider_start_rate=settings.structured_provider_start_rate,
        provider_initial_burst=settings.structured_provider_initial_burst,
        max_retries=settings.structured_provider_max_retries,
        provider_first_pause_seconds=settings.provider_first_pause_seconds,
        provider_second_pause_seconds=settings.provider_second_pause_seconds,
        provider_half_open_probes=settings.provider_half_open_probes,
        provider_recovery_start_rate=settings.provider_recovery_start_rate,
        provider_recovery_initial_concurrency=settings.provider_recovery_initial_concurrency,
        rates={
            ModelTier.M2: (1000.0, settings.scheduler_m2_concurrency),
            ModelTier.M3: (1000.0, settings.scheduler_m3_concurrency),
            ModelTier.M4: (1000.0, settings.scheduler_m4_concurrency),
        },
    )
    core = CrossDocumentEngine(
        registry=registry,
        embedding_client=scheduler.embedding_client(embedding),
        m2_client=executor.client(ModelTier.M2),
        m3_client=executor.client(ModelTier.M3),
        m4_client=executor.client(ModelTier.M4),
        model_m1=settings.model_m1,
        model_m2=settings.model_m2,
        model_m3=settings.model_m3,
        model_m4=settings.model_m4,
        hard_cannot_link_mode=settings.atomic_hard_cannot_link_mode,
        n9_wire_protocol=settings.n9_wire_protocol,
        n9_active_requests=settings.n9_active_requests,
        n9_overlap_batch_packing=settings.n9_overlap_batch_packing,
        parent_compact_wire_dto=settings.parent_compact_wire_dto,
    )
    return BulkEpochEngine(
        registry=registry,
        core=core,
        executor=executor,
        field_active_requests=settings.field_active_requests,
        field_epoch_planned_batching=settings.field_epoch_planned_batching,
        atomic_late_convergence=settings.atomic_late_convergence,
        atomic_late_task_cap=settings.atomic_late_task_cap,
        n9_late_active_requests=settings.n9_late_active_requests,
        parent_induction_active_requests=settings.parent_induction_active_requests,
        parent_induction_max_documents=settings.parent_induction_max_documents,
        parent_induction_max_slices=settings.parent_induction_max_slices,
        parent_context_soft_token_budget=settings.parent_context_soft_token_budget,
        parent_compact_wire_dto=settings.parent_compact_wire_dto,
        writer_queue_low_watermark=settings.writer_queue_low_watermark,
        writer_queue_high_watermark=settings.writer_queue_high_watermark,
        writer_queue_hard_limit=settings.writer_queue_hard_limit,
        batch_audit_write=settings.batch_audit_write,
        stage_read_snapshot=settings.stage_read_snapshot,
        chunked_stage_apply=settings.chunked_stage_apply,
        batch_task_ledger=settings.batch_task_ledger,
        embedding_batch_executor=settings.embedding_batch_executor,
        package_responses_client=scheduler.structured_client(package_m4, tier=ModelTier.M4),
        package_model_m4=settings.model_m3,
        package_v3_batch_size=settings.package_v3_batch_size,
        package_v3_context_token_budget=settings.package_v3_context_token_budget,
        package_v3_context_reserve_tokens=settings.package_v3_context_reserve_tokens,
        package_v3_description_token_budget=settings.package_v3_description_token_budget,
        package_v3_description_active_requests=(
            settings.package_v3_description_active_requests
        ),
        package_v3_reasoning_effort=settings.model_m3_reasoning_effort,
        package_v3_description_reasoning_effort=settings.model_m2_reasoning_effort,
        package_v3_strict_output=settings.model_m4_strict,
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
    sources = sorted(sources, key=lambda item: (item.published_at, item.message_id))
    scheduler = _scheduler(settings)
    document_processor = _document_processor(settings, registry, scheduler)
    documents = document_processor.process_batch([source.message_id for source in sources])
    document_by_id = {document.message_id: document for document in documents}
    eligible_message_ids = [
        source.message_id
        for source in sources
        if document_by_id[source.message_id].status is ProcessingStatus.SUCCEEDED
    ]
    if args.execution_mode == "BULK_EPOCH":
        event_engine = _bulk_epoch_engine(settings, registry, scheduler)
        try:
            events = event_engine.process_batch(eligible_message_ids)
        finally:
            event_engine.close()
    else:
        incremental_engine = _cross_document_engine(settings, registry, scheduler)
        events = incremental_engine.process_batch(eligible_message_ids)
    event_by_id = {event.message_id: event for event in events}
    results: list[dict[str, object]] = []
    failed = 0
    for source in sources:
        document = document_by_id[source.message_id]
        event = event_by_id.get(source.message_id)
        if document.status is ProcessingStatus.FAILED or (
            event is not None and event.status is CrossDocumentStatus.FAILED
        ):
            failed += 1
        results.append(
            {
                "document": _document_summary(document),
                "events": _event_summary(event) if event is not None else None,
            }
        )
    _json_stdout(
        {
            "ok": failed == 0,
            "command": "events.batch",
            "execution_mode": args.execution_mode,
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
    registry = SQLiteCDECRRegistry(
        args.registry, bulk_read_mode=settings.bulk_registry_read_mode
    )
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
        artifact = M4ReviewArtifact.model_validate_json(args.m4_review.read_text(encoding="utf-8"))
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


def _checkpoint_outcomes(
    document_results: list[SingleDocumentResult],
    event_results: list[CrossDocumentResult | None],
) -> list[dict[str, object]]:
    """Project only rows that have reached the sequential cross-document barrier."""

    completed_documents = document_results[: len(event_results)]
    return [
        {
            "message_id": document.message_id,
            "document_status": document.status.value,
            "event_status": getattr(event, "status", None),
        }
        for document, event in zip(
            completed_documents,
            event_results,
            strict=True,
        )
    ]


def _evaluation_run_locked(settings: CDECRSettings, args: argparse.Namespace) -> int:
    evaluation_started = perf_counter()
    manifest_version, corpus = load_step4_corpus(args.snapshot, args.manifest, limit=args.limit)
    registry = SQLiteCDECRRegistry(
        args.registry, bulk_read_mode=settings.bulk_registry_read_mode
    )
    registry.initialize()
    for row, source in corpus:
        registry.save_source(source, fingerprint=row.document_fingerprint)

    scheduler = _scheduler(settings)
    document_processor = _document_processor(settings, registry, scheduler)
    event_engine = _bulk_epoch_engine(settings, registry, scheduler)
    document_results: list[SingleDocumentResult] = []
    event_results: list[CrossDocumentResult | None] = []
    checkpoint_path = args.output.with_suffix(args.output.suffix + ".checkpoint.json")
    processing_corpus = sorted(corpus, key=lambda item: (item[1].published_at, item[1].message_id))
    document_results = document_processor.process_batch(
        [source.message_id for _, source in processing_corpus]
    )
    eligible_cross_document_ids = [
        source.message_id
        for (_, source), document in zip(
            processing_corpus,
            document_results,
            strict=True,
        )
        if document.status is ProcessingStatus.SUCCEEDED
    ]
    try:
        bulk_events = event_engine.process_batch(eligible_cross_document_ids)
    finally:
        event_engine.close()
    bulk_event_by_id = {event.message_id: event for event in bulk_events}
    event_results = [bulk_event_by_id.get(source.message_id) for _, source in processing_corpus]
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(
        json.dumps(
            {
                "report_version": "cdecr-step4-checkpoint-v2-bulk-epoch",
                "completed_rows": len(corpus),
                "total_rows": len(corpus),
                "last_source_row_id": (
                    processing_corpus[-1][0].source_row_id if processing_corpus else None
                ),
                "outcomes": _checkpoint_outcomes(
                    document_results,
                    event_results,
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    first_pass_wall_clock_ms = round((perf_counter() - evaluation_started) * 1000)

    # Load the original persisted results so a resumed run reports all prior calls.
    current_document_by_id = {item.message_id: item for item in document_results}
    current_event_by_id = {item.message_id: item for item in event_results if item is not None}
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
        persisted_events.append(current_event)

    model_calls_before = registry.count_model_calls()
    mention_count_before = sum(
        len(registry.list_mentions_for_message(source.message_id)) for _, source in corpus
    )
    atomic_count_before = len(registry.list_current_atomic_events())
    package_count_before = len(registry.list_current_packages())

    # Re-open the Registry and clients to prove process-restart recovery and idempotency.
    restarted_registry = SQLiteCDECRRegistry(args.registry)
    restarted_registry.initialize()
    restarted_scheduler = _scheduler(settings)
    restarted_documents = _document_processor(settings, restarted_registry, restarted_scheduler)
    restarted_events = _bulk_epoch_engine(settings, restarted_registry, restarted_scheduler)
    successful_document_ids = [
        source.message_id
        for (_, source), document in zip(corpus, persisted_documents, strict=True)
        if document.status is ProcessingStatus.SUCCEEDED
    ]
    rerun_documents = restarted_documents.process_batch(successful_document_ids)
    successful_event_ids = [
        event.message_id
        for event in persisted_events
        if event is not None and event.status is CrossDocumentStatus.SUCCEEDED
    ]
    try:
        rerun_events = restarted_events.process_batch(successful_event_ids)
    finally:
        restarted_events.close()

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
        expected_document_count=len(corpus),
        first_pass_wall_clock_ms=first_pass_wall_clock_ms,
        total_wall_clock_ms=round((perf_counter() - evaluation_started) * 1000),
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
            pragma["user_version"] == SCHEMA_VERSION
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
        "ok": all((PIPELINE_VERSION, PROMPT_VERSION, FINALIZATION_VERSION)),
        "pipeline_version": PIPELINE_VERSION,
        "prompt_version": PROMPT_VERSION,
        "finalization_version": FINALIZATION_VERSION,
    }
    checks["single_document_routing"] = {
        "ok": settings.model_m2 == "deepseek-v4-flash-0731"
        and settings.model_m3 == settings.model_m2
        and settings.model_m4 == settings.model_m2
        and settings.model_m2_reasoning_effort == "none"
        and settings.model_m3_reasoning_effort == "low"
        and settings.model_m4_reasoning_effort == "high",
        "dreamer": "m2_profile_m2_lane_responses",
        "relevance": "m2_profile_m3_lane_responses",
        "grounder": "m2_profile_m3_lane_responses",
        "judge": "m2_profile_m4_lane_responses",
    }
    prompt_root = package_root / "prompts" / "v1"
    cross_prompts = [
        prompt_root / "atomic_coreference.md",
        prompt_root / "parent_occurrence_induction.md",
    ]
    parent_schemas = {
        "parent_induction": ParentInductionBatch.model_json_schema(),
    }
    checks["cross_document_versions"] = {
        "ok": bool(CROSS_DOCUMENT_ENGINE_VERSION and CROSS_DOCUMENT_PROMPT_VERSION)
        and all(path.is_file() for path in cross_prompts)
        and all(bool(schema.get("properties")) for schema in parent_schemas.values()),
        "engine_version": CROSS_DOCUMENT_ENGINE_VERSION,
        "prompt_version": CROSS_DOCUMENT_PROMPT_VERSION,
        "parent_schema_names": sorted(parent_schemas),
    }
    try:
        knowledge_base = V2KnowledgeBase()
        checks["canonical_field_resolution"] = {
            "ok": bool(
                knowledge_base.catalog_hash and FIELD_RESOLVER_VERSION and IDENTITY_COMPILER_VERSION
            ),
            "catalog_count": len(CATALOG_NAMES),
            "catalog_hash": knowledge_base.catalog_hash,
            "field_resolver_version": FIELD_RESOLVER_VERSION,
            "identity_compiler_version": IDENTITY_COMPILER_VERSION,
            "ordering": "v2_kb_then_field_coreference",
            "package_hint_stage": "removed_parent_occurrence_v2",
        }
    except (FileNotFoundError, ValueError) as exc:
        checks["canonical_field_resolution"] = {
            "ok": False,
            "error": type(exc).__name__,
        }
    checks["cross_document_routing"] = {
        "ok": settings.model_m1 == "qwen3.7-text-embedding"
        and settings.model_m2 == "deepseek-v4-flash-0731"
        and settings.model_m3 == settings.model_m2
        and settings.model_m4 == settings.model_m2,
        "recall": "m0+m1",
        "hard_cannot_link": settings.atomic_hard_cannot_link_mode,
        "atomic_default": "m2_profile_m2_lane_chat",
        "atomic_escalation": "m2_profile_m3_lane_responses",
        "parent_induction": "m2_profile_m3_lane_responses",
        "package_clustering": "m3_profile_m4_lane_responses",
        "package_description": "m2_profile_m4_lane_responses",
        "package_apply": "frozen_partition_once",
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
    registry_rebuild = registry_commands.add_parser("rebuild-derived")
    registry_rebuild.add_argument("--clear-only", action="store_true")
    registry_rebuild.set_defaults(handler=_registry_rebuild_derived)

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
    event_batch.add_argument(
        "--execution-mode",
        choices=("INCREMENTAL", "BULK_EPOCH"),
        default="BULK_EPOCH",
    )
    event_batch.set_defaults(handler=_events_batch)

    evaluation = commands.add_parser("evaluation")
    evaluation_commands = evaluation.add_subparsers(dest="evaluation_command", required=True)
    evaluation_run = evaluation_commands.add_parser("run")
    evaluation_run.add_argument(
        "--snapshot",
        type=Path,
        default=Path(".tmp/cdecr/baselines/us_mu_2026-06-25.jsonl"),
    )
    evaluation_run.add_argument(
        "--manifest",
        type=Path,
        default=Path("dev_plan/CDECR/baselines/mu_2026-06-25_step2_eval_manifest.json"),
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
        default=Path("dev_plan/CDECR/baselines/mu_2026-06-25_step2_eval_manifest.json"),
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
