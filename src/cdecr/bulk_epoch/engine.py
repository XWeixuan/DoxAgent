"""BULK_EPOCH_V3_STAGE_GRAPH: one-shot Read/Decide/Reduce/Commit orchestration."""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from time import perf_counter
from typing import Literal

from cdecr.bulk_epoch.artifacts import StageArtifact, canonical_hash
from cdecr.bulk_epoch.atomic_late_stage import (
    LateStageConfig,
    run_atomic_late_convergence,
)
from cdecr.bulk_epoch.executor import AsyncModelExecutor
from cdecr.bulk_epoch.package_stage import (
    project_parent_partition,
    resolve_parent_partition,
)
from cdecr.bulk_epoch.snapshots import atomic_snapshot
from cdecr.bulk_epoch.task_ledger import BulkTaskLedger
from cdecr.bulk_epoch.writer import BulkWriter
from cdecr.canonical_field_resolution import CanonicalFieldResolutionEngine
from cdecr.contracts import AtomicEvent, EventMention
from cdecr.cross_document import (
    ENGINE_VERSION,
    PROMPT_VERSION,
    CrossDocumentEngine,
    _AuditedModels,
)
from cdecr.cross_document_contracts import (
    AtomicAssignmentDecision,
    AtomicAssignmentRecord,
    CrossDocumentResult,
    CrossDocumentStatus,
    PackageAssignmentRecord,
)
from cdecr.field_coreference import FieldCoreferenceResolver
from cdecr.identity_compiler import IdentityCompiler
from cdecr.kb_v2 import V2KnowledgeBase
from cdecr.package_global_clustering import PackageWorkflowV3Service
from cdecr.package_v3_contracts import FrozenPackagePartitionV3
from cdecr.parent_occurrence import ParentOccurrenceService
from cdecr.ports import CDECRRegistry, StructuredModelClient
from cdecr.single_document_contracts import ModelCallSummary

BULK_STAGE_GRAPH_VERSION = "cdecr-bulk-epoch-v12-token-quality-recovery"
FIELD_EPOCH_POLICY_VERSION = "field-epoch-planned-batching-v2-pure-prepare"
FIELD_PLAN_ARTIFACT_KIND = "field_plan_v2"
FIELD_OVERLAY_ARTIFACT_KIND = "field_overlay_v2"


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _recover_atomic_late_state(
    registry: CDECRRegistry,
    mentions: Sequence[EventMention],
) -> tuple[list[AtomicEvent], list[AtomicAssignmentRecord]]:
    """Recover committed main-Atomic state after an interrupted N9_LATE attempt."""

    events_by_id: dict[str, AtomicEvent] = {}
    assignments: list[AtomicAssignmentRecord] = []
    for mention in mentions:
        assignment = registry.get_latest_atomic_assignment_for_mention(mention.mention_id)
        root_id = (
            None
            if assignment is None or assignment.resulting_event_id is None
            else registry.resolve_atomic_event_root(assignment.resulting_event_id)
        )
        event = None if root_id is None else registry.get_current_atomic_event(root_id)
        if assignment is None or event is None:
            raise RuntimeError(
                f"ATOMIC_LATE_RECOVERY_MISSING_STATE:{mention.mention_id}"
            )
        assignments.append(assignment)
        events_by_id[event.event_id] = event
    return list(events_by_id.values()), assignments


class BulkEpochEngine:
    """The only production BULK_EPOCH entrypoint.

    Incremental processing remains in `CrossDocumentEngine.process`; this engine never calls that
    method. Each cross-document stage plans the whole immutable epoch before model decisions and
    publishes one stage artifact before the next stage starts.
    """

    def __init__(
        self,
        *,
        registry: CDECRRegistry,
        core: CrossDocumentEngine,
        executor: AsyncModelExecutor,
        field_active_requests: int,
        field_epoch_planned_batching: bool = True,
        knowledge_base: V2KnowledgeBase | None = None,
        atomic_late_convergence: bool = True,
        atomic_late_task_cap: int = 48,
        n9_late_active_requests: int = 24,
        parent_induction_active_requests: int = 96,
        parent_induction_max_documents: int = 4,
        parent_induction_max_slices: int = 48,
        parent_context_soft_token_budget: int = 6_000,
        parent_compact_wire_dto: bool = True,
        writer_queue_low_watermark: int = 1000,
        writer_queue_high_watermark: int = 5000,
        writer_queue_hard_limit: int = 10000,
        batch_audit_write: bool = True,
        stage_read_snapshot: bool = True,
        chunked_stage_apply: bool = True,
        batch_task_ledger: bool = True,
        embedding_batch_executor: bool = True,
        package_responses_client: StructuredModelClient | None = None,
        package_model_m4: str | None = None,
        package_v3_batch_size: int = 200,
        package_v3_context_token_budget: int = 100_000,
        package_v3_context_reserve_tokens: int = 8_000,
        package_v3_description_token_budget: int = 32_000,
        package_v3_description_active_requests: int = 16,
        package_v3_reasoning_effort: Literal["none", "low", "high", "max"] = "low",
        package_v3_description_reasoning_effort: Literal[
            "none", "low", "high", "max"
        ] = "none",
        package_v3_strict_output: bool = False,
    ) -> None:
        self.registry = registry
        self.core = core
        self.executor = executor
        self.field_active_requests = max(1, field_active_requests)
        self.field_epoch_planned_batching = field_epoch_planned_batching
        self.knowledge_base = knowledge_base or core.knowledge_base
        self.atomic_late_convergence = atomic_late_convergence
        self.parent_service = ParentOccurrenceService(
            registry=registry,
            induction_active_requests=parent_induction_active_requests,
            induction_max_documents=parent_induction_max_documents,
            induction_max_slices=parent_induction_max_slices,
            context_soft_token_budget=parent_context_soft_token_budget,
            compact_wire_dto=parent_compact_wire_dto,
        )
        self.package_service = PackageWorkflowV3Service(
            registry=registry,
            batch_size=package_v3_batch_size,
            context_token_budget=package_v3_context_token_budget,
            context_reserve_tokens=package_v3_context_reserve_tokens,
            description_pack_token_budget=package_v3_description_token_budget,
            description_active_requests=package_v3_description_active_requests,
            reasoning_effort=package_v3_reasoning_effort,
            description_reasoning_effort=package_v3_description_reasoning_effort,
            strict_output=package_v3_strict_output,
        )
        self.package_responses_client = package_responses_client
        self.package_model_m4 = package_model_m4 or core.model_m4
        self.late_config = LateStageConfig(
            atomic_task_cap=atomic_late_task_cap,
            atomic_active_requests=n9_late_active_requests,
        )
        self.writer_queue_low_watermark = writer_queue_low_watermark
        self.writer_queue_high_watermark = writer_queue_high_watermark
        self.writer_queue_hard_limit = writer_queue_hard_limit
        self.batch_audit_write = batch_audit_write
        self.stage_read_snapshot = stage_read_snapshot
        self.chunked_stage_apply = chunked_stage_apply
        self.batch_task_ledger = batch_task_ledger
        self.embedding_batch_executor = embedding_batch_executor
        self.core._bulk_batch_audit_write = batch_audit_write

    def _writer(self) -> BulkWriter:
        return BulkWriter(
            low_watermark=self.writer_queue_low_watermark,
            high_watermark=self.writer_queue_high_watermark,
            hard_limit=self.writer_queue_hard_limit,
        )

    def close(self) -> None:
        self.executor.close()

    def process_batch(self, message_ids: list[str]) -> list[CrossDocumentResult]:
        def source_order(message_id: str) -> tuple[datetime, str]:
            source = self.registry.get_source(message_id)
            if source is None:
                raise ValueError(f"unknown source message {message_id!r}")
            return source.published_at, message_id

        ordered_ids = sorted(
            dict.fromkeys(message_ids),
            key=source_order,
        )
        if not ordered_ids:
            return []
        manifest = {
            "message_ids": ordered_ids,
            "source_fingerprints": {
                message_id: self.registry.get_source_fingerprint(message_id)
                for message_id in ordered_ids
            },
            "engine_version": ENGINE_VERSION,
            "prompt_version": PROMPT_VERSION,
            "orchestrator_version": BULK_STAGE_GRAPH_VERSION,
            "model_config": self.core.model_config,
            "capacity_config": self.executor.capacity_config(),
            "git_commit": _git_commit(),
            "deterministic_runtime_config": {
                "batch_audit_write": self.batch_audit_write,
                "stage_read_snapshot": self.stage_read_snapshot,
                "chunked_stage_apply": self.chunked_stage_apply,
                "batch_task_ledger": self.batch_task_ledger,
                "embedding_batch_executor": self.embedding_batch_executor,
                "atomic_apply_chunk_size": 64,
                "package_apply_chunk_size": 32,
                "audit_chunk_size": 512,
                "embedding_preferred_batch_size": 8,
                "embedding_fallback_batch_size": 4,
                "embedding_active_requests": 4,
                "field_epoch_policy_version": FIELD_EPOCH_POLICY_VERSION,
                "field_epoch_planned_batching": self.field_epoch_planned_batching,
                "n9_overlap_batch_packing": self.core.n9_overlap_batch_packing,
                "parent_compact_wire_dto": self.parent_service.compact_wire_dto,
            },
        }
        manifest["deterministic_runtime_config_hash"] = canonical_hash(
            manifest["deterministic_runtime_config"]
        )
        manifest_hash = canonical_hash(manifest)
        epoch_id = f"bulk-epoch:{manifest_hash[:24]}"
        epoch = self.registry.start_bulk_epoch(
            epoch_id=epoch_id,
            manifest_hash=manifest_hash,
            orchestrator_version=BULK_STAGE_GRAPH_VERSION,
            message_ids=ordered_ids,
        )
        if epoch["status"] == "FINALIZED":
            return self._results_from_registry(
                epoch_id=epoch_id,
                message_ids=ordered_ids,
                summaries=[],
                candidate_counts={},
                reused=True,
                started_at=datetime.now(UTC),
            )

        started_at = datetime.now(UTC)
        wall_started = perf_counter()
        coordinator_run_id = str(uuid.uuid4())
        coordinator_message_id = ordered_ids[0]
        self.registry.start_cross_document_trace(
            trace_id=coordinator_run_id,
            message_id=coordinator_message_id,
            engine_version=ENGINE_VERSION,
            prompt_version=PROMPT_VERSION,
            model_config={**self.core.model_config, "bulk_stage_graph": BULK_STAGE_GRAPH_VERSION},
        )
        self.registry.start_cross_document_run(
            run_id=coordinator_run_id,
            processing_key=canonical_hash({"epoch": epoch_id, "attempt": coordinator_run_id}),
            message_id=coordinator_message_id,
            engine_version=ENGINE_VERSION,
            prompt_version=PROMPT_VERSION,
            model_config={**self.core.model_config, "bulk_stage_graph": BULK_STAGE_GRAPH_VERSION},
        )
        summaries: list[ModelCallSummary] = []
        models = _AuditedModels(
            registry=self.registry,
            run_id=coordinator_run_id,
            embedding_client=self.core.embedding_client,
            m2_client=self.core.m2_client,
            m3_client=self.core.m3_client,
            m4_client=self.core.m4_client,
            model_m1=self.core.model_m1,
            model_m2=self.core.model_m2,
            model_m3=self.core.model_m3,
            model_m4=self.core.model_m4,
            summaries=summaries,
            responses_m4_client=self.package_responses_client,
            responses_model_m4=self.package_model_m4,
        )
        ledger = BulkTaskLedger(
            registry=self.registry,
            epoch_id=epoch_id,
            batch_enabled=self.batch_task_ledger,
        )
        timings: dict[str, int] = {}
        deterministic_telemetry: dict[str, object] = {}
        writer_telemetry: list[dict[str, object]] = []
        candidate_counts = {
            "atomic_recalled": 0,
            "atomic_hard_conflict_observed": 0,
            "package_recalled": 0,
            "package_hard_blocked": 0,
            "package_hard_conflict_observed": 0,
        }
        try:
            documents = []
            mentions = []
            for message_id in ordered_ids:
                source = self.registry.get_source(message_id)
                assert source is not None
                document_result = self.registry.get_latest_completed_document_result_for_message(
                    message_id
                )
                message_mentions = (
                    list(document_result.mentions)
                    if document_result is not None
                    else self.registry.list_mentions_for_message(message_id)
                )
                message_mentions.sort(key=lambda item: item.mention_id)
                documents.append((source, message_mentions))
                mentions.extend(message_mentions)
            mentions.sort(key=lambda item: item.mention_id)
            self._save_artifact(
                epoch_id=epoch_id,
                manifest_hash=manifest_hash,
                kind="manifest_v1",
                upstream_hash=manifest_hash,
                payload={
                    "message_ids": ordered_ids,
                    "mention_ids": [mention.mention_id for mention in mentions],
                },
            )
            self.registry.update_bulk_epoch(
                epoch_id, status="RUNNING", current_stage="DOCUMENT_MAP"
            )

            field_started = perf_counter()
            self.registry.update_bulk_epoch(
                epoch_id, status="RUNNING", current_stage="FIELD_DECIDE"
            )
            field_artifact = self.registry.get_bulk_epoch_artifact(
                epoch_id, FIELD_OVERLAY_ARTIFACT_KIND
            )
            if field_artifact is None:
                resolver = FieldCoreferenceResolver(
                    registry=self.registry,
                    embedding_client=self.core.embedding_client,
                    model_client=self.core.m2_client,
                    embedding_model=self.core.model_m1,
                    catalog_hash=self.knowledge_base.catalog_hash,
                )
                field_engine = CanonicalFieldResolutionEngine(
                    registry=self.registry,
                    knowledge_base=self.knowledge_base,
                    field_resolver=resolver,
                    planned_batching=self.field_epoch_planned_batching,
                )
                field_snapshot_hash = canonical_hash(
                    {
                        "mentions": [
                            mention.model_dump(mode="json") for mention in mentions
                        ],
                        "policy_version": FIELD_EPOCH_POLICY_VERSION,
                        "planned_batching": self.field_epoch_planned_batching,
                    }
                )
                self._save_artifact(
                    epoch_id=epoch_id,
                    manifest_hash=manifest_hash,
                    kind=FIELD_PLAN_ARTIFACT_KIND,
                    upstream_hash=manifest_hash,
                    payload={
                        "snapshot_hash": field_snapshot_hash,
                        "mention_count": len(mentions),
                        "document_count": len(documents),
                        "task_semantics": "GLOBAL_SEMANTIC_KEY_DEDUP",
                        "candidate_cap": 8,
                        "batch_size": 12,
                        "policy_version": FIELD_EPOCH_POLICY_VERSION,
                        "planned_batching": self.field_epoch_planned_batching,
                    },
                )

                def field_task_input_hash(task_id: str) -> str:
                    return canonical_hash(
                        {
                            "task_id": task_id,
                            "policy_version": FIELD_EPOCH_POLICY_VERSION,
                            "planned_batching": self.field_epoch_planned_batching,
                        }
                    )

                def field_task_batch(records: Sequence[dict[str, str | None]]) -> None:
                    base = [
                        {
                            "stage": "FIELD",
                            "task_id": str(item["task_id"]),
                            "input_hash": field_task_input_hash(str(item["task_id"])),
                            "snapshot_hash": field_snapshot_hash,
                        }
                        for item in records
                    ]
                    running = [
                        row
                        for row, item in zip(base, records, strict=True)
                        if item["status"] == "RUNNING"
                    ]
                    succeeded = [
                        {**row, "decision_ref": {"field_task_key": row["task_id"]}}
                        for row, item in zip(base, records, strict=True)
                        if item["status"] == "SUCCEEDED"
                    ]
                    failed = [
                        {**row, "error_code": item["error_code"] or "FIELD_TASK_FAILED"}
                        for row, item in zip(base, records, strict=True)
                        if item["status"] == "FAILED"
                    ]
                    if running:
                        ledger.start_many(running)
                    if succeeded:
                        ledger.finish_many(succeeded)
                    if failed:
                        ledger.fail_many(failed)

                field_summary = field_engine.resolve_epoch(
                    documents,
                    run_id=coordinator_run_id,
                    max_workers=self.field_active_requests,
                    task_batch_hook=field_task_batch,
                    completed_task_ids={
                        task_id
                        for task_id, record in ledger.completed("FIELD").items()
                        if record["input_hash"] == field_task_input_hash(task_id)
                        and record["snapshot_hash"] == field_snapshot_hash
                    },
                )
                field_plan_artifact = self.registry.get_bulk_epoch_artifact(
                    epoch_id, FIELD_PLAN_ARTIFACT_KIND
                )
                assert field_plan_artifact is not None
                self._save_artifact(
                    epoch_id=epoch_id,
                    manifest_hash=manifest_hash,
                    kind=FIELD_OVERLAY_ARTIFACT_KIND,
                    upstream_hash=str(field_plan_artifact["artifact_hash"]),
                    payload={
                        "resolved_count": field_summary.resolved_count,
                        "unresolved_count": field_summary.unresolved_count,
                        "group_count": field_summary.group_count,
                        "failed_group_count": field_summary.failed_group_count,
                        "skipped_group_count": field_summary.skipped_group_count,
                        "field_links_hash": field_summary.field_links_hash,
                        "telemetry": field_summary.telemetry,
                    },
                )
                deterministic_telemetry["field_planned_batching"] = (
                    field_summary.telemetry or {}
                )
            timings["field_ms"] = round((perf_counter() - field_started) * 1000)
            self.registry.update_bulk_epoch(
                epoch_id, status="RUNNING", current_stage="FIELD_COMMITTED"
            )

            atomic_started = perf_counter()
            self.registry.update_bulk_epoch(
                epoch_id, status="RUNNING", current_stage="ATOMIC_DECIDE"
            )
            atomic_artifact = self.registry.get_bulk_epoch_artifact(epoch_id, "atomic_partition_v1")
            atomic_assignments: list[AtomicAssignmentRecord] = []
            atomic_late_plan_artifact = self.registry.get_bulk_epoch_artifact(
                epoch_id, "atomic_late_plan_v1"
            )
            if atomic_artifact is None and atomic_late_plan_artifact is not None:
                atomic_events, atomic_assignments = _recover_atomic_late_state(
                    self.registry, mentions
                )
                recovered_telemetry = {
                    "stage": "N9_LATE",
                    "event_count": len(atomic_events),
                    "assignment_count": len(atomic_assignments),
                    "recovered_after_interruption": True,
                }
                deterministic_telemetry["atomic_resume"] = recovered_telemetry
                atomic_plan_artifact = self.registry.get_bulk_epoch_artifact(
                    epoch_id, "atomic_plan_v1"
                )
                assert atomic_plan_artifact is not None
                atomic_late_task_hash = canonical_hash(
                    {
                        "epoch": epoch_id,
                        "stage": "ATOMIC_LATE",
                        "cap": self.late_config.atomic_task_cap,
                    }
                )
                ledger.finish(
                    stage="N9_LATE",
                    task_id="epoch",
                    input_hash=atomic_late_task_hash,
                    snapshot_hash=str(atomic_plan_artifact["artifact_hash"]),
                    decision_ref=recovered_telemetry,
                )
                if self.registry.get_bulk_epoch_artifact(
                    epoch_id, "atomic_late_partition_v1"
                ) is None:
                    self._save_artifact(
                        epoch_id=epoch_id,
                        manifest_hash=manifest_hash,
                        kind="atomic_late_partition_v1",
                        upstream_hash=str(atomic_late_plan_artifact["artifact_hash"]),
                        payload={
                            **recovered_telemetry,
                            "event_ids": sorted(event.event_id for event in atomic_events),
                        },
                    )
                self._save_artifact(
                    epoch_id=epoch_id,
                    manifest_hash=manifest_hash,
                    kind="atomic_partition_v1",
                    upstream_hash=str(atomic_plan_artifact["artifact_hash"]),
                    payload={
                        "event_ids": sorted(event.event_id for event in atomic_events),
                        "assignment_ids": sorted(
                            assignment.assignment_id for assignment in atomic_assignments
                        ),
                        "recovered_after_atomic_late_interruption": True,
                    },
                )
            elif atomic_artifact is None:
                base_atomic = atomic_snapshot(self.registry)
                compiler = IdentityCompiler(
                    registry=self.registry,
                    catalog_hash=self.knowledge_base.catalog_hash,
                )
                compiled = {mention.mention_id: compiler.compile(mention) for mention in mentions}
                eligible_mentions = [
                    mention
                    for mention in mentions
                    if compiled[mention.mention_id].identity_profile is not None
                ]
                with ThreadPoolExecutor(max_workers=2) as pool:
                    atomic_future = pool.submit(
                        lambda: self.core._sync_atomic_embeddings(
                            list(base_atomic.events),
                            models,
                            use_batch_executor=self.embedding_batch_executor,
                        )
                    )
                    mention_future = pool.submit(
                        lambda: self.core._embed_mentions(
                            eligible_mentions,
                            models,
                            use_batch_executor=self.embedding_batch_executor,
                        )
                    )
                    atomic_vectors = atomic_future.result()
                    mention_vectors = mention_future.result()
                candidates = self.core._atomic_candidates(
                    mentions,
                    mention_vectors,
                    compiled,
                    atomic_vectors,
                    run_id=coordinator_run_id,
                    candidate_counts=candidate_counts,
                )
                deterministic_telemetry["atomic_candidate"] = getattr(
                    self.core, "_last_atomic_candidate_telemetry", {}
                )
                field_overlay_artifact = self.registry.get_bulk_epoch_artifact(
                    epoch_id, FIELD_OVERLAY_ARTIFACT_KIND
                )
                assert field_overlay_artifact is not None
                atomic_plan_payload = {
                    "snapshot_hash": base_atomic.snapshot_hash,
                    "tasks": {
                        mention.mention_id: [
                            item.event.event_id for item in candidates[mention.mention_id]
                        ]
                        for mention in eligible_mentions
                    },
                    "scheduler_edge_cap": 12 * len(eligible_mentions),
                    "model_candidate_cap": 5,
                }
                self._save_artifact(
                    epoch_id=epoch_id,
                    manifest_hash=manifest_hash,
                    kind="atomic_plan_v1",
                    upstream_hash=str(field_overlay_artifact["artifact_hash"]),
                    payload=atomic_plan_payload,
                )
                completed_n9 = ledger.completed("N9")
                cached_decisions: dict[str, AtomicAssignmentDecision] = {}
                pending_mentions = []
                pending_task_rows: list[dict[str, object]] = []
                for mention in eligible_mentions:
                    task_payload = {
                        "mention_id": mention.mention_id,
                        "candidate_ids": [
                            item.event.event_id for item in candidates[mention.mention_id]
                        ],
                    }
                    task_hash = canonical_hash(task_payload)
                    completed_task = completed_n9.get(mention.mention_id)
                    decision_payload = (
                        completed_task.get("decision_ref", {}).get("decision")
                        if completed_task is not None
                        and completed_task.get("input_hash") == task_hash
                        and completed_task.get("snapshot_hash") == base_atomic.snapshot_hash
                        else None
                    )
                    if isinstance(decision_payload, dict) and "mention_id" in decision_payload:
                        cached_decisions[mention.mention_id] = (
                            AtomicAssignmentDecision.model_validate(decision_payload)
                        )
                    else:
                        pending_mentions.append(mention)
                        pending_task_rows.append(
                            {
                                "stage": "N9",
                                "task_id": mention.mention_id,
                                "input_hash": task_hash,
                                "snapshot_hash": base_atomic.snapshot_hash,
                            }
                        )
                if pending_task_rows:
                    ledger.start_many(pending_task_rows)
                decisions = {
                    **cached_decisions,
                    **self.core._atomic_decisions(pending_mentions, candidates, compiled, models),
                }
                deterministic_telemetry["n9_batch_packing"] = getattr(
                    self.core, "_last_n9_packing_telemetry", {}
                )
                finished_task_rows: list[dict[str, object]] = []
                failed_task_rows: list[dict[str, object]] = []
                for mention in pending_mentions:
                    task_payload = {
                        "mention_id": mention.mention_id,
                        "candidate_ids": [
                            item.event.event_id for item in candidates[mention.mention_id]
                        ],
                    }
                    task_hash = canonical_hash(task_payload)
                    if mention.mention_id in decisions or not candidates[mention.mention_id]:
                        finished_task_rows.append(
                            {
                                "stage": "N9",
                                "task_id": mention.mention_id,
                                "input_hash": task_hash,
                                "snapshot_hash": base_atomic.snapshot_hash,
                                "decision_ref": {
                                    "decision": (
                                        decisions[mention.mention_id].model_dump(mode="json")
                                        if mention.mention_id in decisions
                                        else {"deterministic": "NO_CANDIDATE"}
                                    )
                                },
                            }
                        )
                    else:
                        failed_task_rows.append(
                            {
                                "stage": "N9",
                                "task_id": mention.mention_id,
                                "input_hash": task_hash,
                                "snapshot_hash": base_atomic.snapshot_hash,
                                "error_code": "UNJUDGEABLE_FAILED",
                            }
                        )
                if finished_task_rows:
                    ledger.finish_many(finished_task_rows)
                if failed_task_rows:
                    ledger.fail_many(failed_task_rows)
                with self._writer() as writer:
                    atomic_events, atomic_assignments = self.core._apply_atomic(
                        mentions,
                        candidates,
                        decisions,
                        compiled,
                        models,
                        run_id=coordinator_run_id,
                        sync_embeddings=False,
                        chunked_apply=self.chunked_stage_apply,
                        stage_writer=writer,
                        apply_checkpoint_context={
                            "epoch_id": epoch_id,
                            "snapshot_hash": base_atomic.snapshot_hash,
                        },
                        completed_apply_chunks=ledger.completed("ATOMIC_APPLY"),
                    )
                writer_telemetry.append({"stage": "ATOMIC_APPLY", **writer.snapshot().__dict__})
                deterministic_telemetry["atomic_apply"] = getattr(
                    self.core, "_last_atomic_apply_telemetry", {}
                )
                atomic_events = self.core._correct_atomic(
                    atomic_events, mentions, run_id=coordinator_run_id
                )
                atomic_late_telemetry: dict[str, object] = {}
                if self.atomic_late_convergence:
                    late_started = perf_counter()
                    self.registry.update_bulk_epoch(
                        epoch_id, status="RUNNING", current_stage="ATOMIC_LATE"
                    )
                    atomic_plan_artifact = self.registry.get_bulk_epoch_artifact(
                        epoch_id, "atomic_plan_v1"
                    )
                    assert atomic_plan_artifact is not None
                    if atomic_late_plan_artifact is None:
                        self._save_artifact(
                            epoch_id=epoch_id,
                            manifest_hash=manifest_hash,
                            kind="atomic_late_plan_v1",
                            upstream_hash=str(atomic_plan_artifact["artifact_hash"]),
                            payload={
                                "snapshot_hash": canonical_hash(
                                    [
                                        (event.event_id, event.version)
                                        for event in sorted(
                                            atomic_events, key=lambda item: item.event_id
                                        )
                                    ]
                                ),
                                "task_cap": self.late_config.atomic_task_cap,
                                "rounds": 1,
                                "failure_semantics": "NEUTRAL_OMISSION",
                            },
                        )
                        atomic_late_plan_artifact = self.registry.get_bulk_epoch_artifact(
                            epoch_id, "atomic_late_plan_v1"
                        )
                    assert atomic_late_plan_artifact is not None
                    atomic_late_task_hash = canonical_hash(
                        {
                            "epoch": epoch_id,
                            "stage": "ATOMIC_LATE",
                            "cap": self.late_config.atomic_task_cap,
                        }
                    )
                    ledger.start(
                        stage="N9_LATE",
                        task_id="epoch",
                        input_hash=atomic_late_task_hash,
                        snapshot_hash=str(atomic_plan_artifact["artifact_hash"]),
                    )
                    atomic_events, atomic_late_telemetry = run_atomic_late_convergence(
                        engine=self.core,
                        events=atomic_events,
                        assignments=atomic_assignments,
                        models=models,
                        run_id=coordinator_run_id,
                        config=self.late_config,
                    )
                    ledger.finish(
                        stage="N9_LATE",
                        task_id="epoch",
                        input_hash=atomic_late_task_hash,
                        snapshot_hash=str(atomic_plan_artifact["artifact_hash"]),
                        decision_ref=atomic_late_telemetry,
                    )
                    atomic_late_plan = self.registry.get_bulk_epoch_artifact(
                        epoch_id, "atomic_late_plan_v1"
                    )
                    assert atomic_late_plan is not None
                    self._save_artifact(
                        epoch_id=epoch_id,
                        manifest_hash=manifest_hash,
                        kind="atomic_late_partition_v1",
                        upstream_hash=str(atomic_late_plan["artifact_hash"]),
                        payload={
                            **atomic_late_telemetry,
                            "event_ids": sorted(event.event_id for event in atomic_events),
                        },
                    )
                    timings["atomic_late_ms"] = round((perf_counter() - late_started) * 1000)
                self.core._sync_atomic_embeddings(
                    atomic_events,
                    models,
                    use_batch_executor=self.embedding_batch_executor,
                )
                atomic_plan_artifact = self.registry.get_bulk_epoch_artifact(
                    epoch_id, "atomic_plan_v1"
                )
                assert atomic_plan_artifact is not None
                self._save_artifact(
                    epoch_id=epoch_id,
                    manifest_hash=manifest_hash,
                    kind="atomic_partition_v1",
                    upstream_hash=str(atomic_plan_artifact["artifact_hash"]),
                    payload={
                        "event_ids": sorted(event.event_id for event in atomic_events),
                        "assignment_ids": sorted(
                            assignment.assignment_id for assignment in atomic_assignments
                        ),
                    },
                )
            else:
                atomic_events = [
                    event
                    for event_id in atomic_artifact["payload"]["event_ids"]
                    if (event := self.registry.get_current_atomic_event(str(event_id))) is not None
                ]
            timings["atomic_ms"] = round((perf_counter() - atomic_started) * 1000)
            self.registry.update_bulk_epoch(
                epoch_id, status="RUNNING", current_stage="ATOMIC_COMMITTED"
            )

            return self._run_parent_occurrence_package_stage(
                epoch_id=epoch_id,
                manifest_hash=manifest_hash,
                ordered_ids=ordered_ids,
                mentions=mentions,
                atomic_assignments=atomic_assignments,
                models=models,
                summaries=summaries,
                candidate_counts=candidate_counts,
                coordinator_run_id=coordinator_run_id,
                started_at=started_at,
                wall_started=wall_started,
                timings=timings,
                writer_telemetry=writer_telemetry,
                deterministic_telemetry=deterministic_telemetry,
            )

        except Exception as exc:
            error_code = str(getattr(exc, "code", type(exc).__name__))
            self.registry.update_bulk_epoch(
                epoch_id,
                status="PARTIAL",
                current_stage=self._current_stage(epoch_id),
                result={
                    "error_code": error_code,
                    "stage_timings": timings,
                    "wall_clock_ms": round((perf_counter() - wall_started) * 1000),
                },
            )
            self.registry.fail_cross_document_run(coordinator_run_id, error_code=error_code)
            self.registry.finish_cross_document_trace(coordinator_run_id, status="FAILED")
            return [
                CrossDocumentResult(
                    run_id=coordinator_run_id,
                    processing_key=canonical_hash({"epoch": epoch_id, "message": message_id}),
                    message_id=message_id,
                    status=CrossDocumentStatus.FAILED,
                    atomic_events=[],
                    packages=[],
                    atomic_assignments=[],
                    package_assignments=[],
                    model_calls=summaries if index == 0 else [],
                    candidate_counts=candidate_counts,
                    failure_stage=self._current_stage(epoch_id),
                    error_code=error_code,
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                )
                for index, message_id in enumerate(ordered_ids)
            ]

    def _run_parent_occurrence_package_stage(
        self,
        *,
        epoch_id: str,
        manifest_hash: str,
        ordered_ids: list[str],
        mentions: Sequence[object],
        atomic_assignments: list[AtomicAssignmentRecord],
        models: _AuditedModels,
        summaries: list[ModelCallSummary],
        candidate_counts: dict[str, int],
        coordinator_run_id: str,
        started_at: datetime,
        wall_started: float,
        timings: dict[str, int],
        writer_telemetry: list[dict[str, object]],
        deterministic_telemetry: dict[str, object],
    ) -> list[CrossDocumentResult]:
        """Resolve the complete parent partition, then perform the sole Package Apply."""

        package_started = perf_counter()
        self.registry.update_bulk_epoch(epoch_id, status="RUNNING", current_stage="PARENT_INDUCE")
        applied_artifact = self.registry.get_bulk_epoch_artifact(epoch_id, "package_partition_v3")
        package_assignments: list[PackageAssignmentRecord] = []
        package_stage_telemetry: dict[str, object] = {}
        if applied_artifact is None:
            final_events = self.registry.list_current_atomic_events(limit=10000)
            existing_packages = self.registry.list_current_packages(limit=10000)
            frozen_artifact = self.registry.get_bulk_epoch_artifact(
                epoch_id, "package_frozen_partition_v3"
            )
            if frozen_artifact is None:
                self.registry.update_bulk_epoch(
                    epoch_id, status="RUNNING", current_stage="PARENT_RESOLVE"
                )
                stage_result = resolve_parent_partition(
                    service=self.parent_service,
                    package_service=self.package_service,
                    events=final_events,
                    mentions=None,
                    sources=None,
                    existing_packages=existing_packages,
                    models=models,
                    run_id=coordinator_run_id,
                    persistence_scope_id=epoch_id,
                )
                package_stage_telemetry = dict(stage_result.telemetry)
                if stage_result.status != "FINALIZED" or stage_result.partition is None:
                    failure_payload: dict[str, object] = {
                        "status": stage_result.status,
                        "failures": [
                            item.model_dump(mode="json") for item in stage_result.failures
                        ],
                        "telemetry": package_stage_telemetry,
                    }
                    self._save_artifact(
                        epoch_id=epoch_id,
                        manifest_hash=manifest_hash,
                        kind=(
                            "package_partial_parent_resolution_v2:"
                            f"{canonical_hash(failure_payload)[:16]}"
                        ),
                        upstream_hash=manifest_hash,
                        payload=failure_payload,
                    )
                    timings["package_ms"] = round((perf_counter() - package_started) * 1000)
                    timings["wall_clock_ms"] = round((perf_counter() - wall_started) * 1000)
                    self.registry.update_bulk_epoch(
                        epoch_id,
                        status="PARTIAL_PARENT_RESOLUTION",
                        current_stage="PARTIAL_PARENT_RESOLUTION",
                        result={
                            **failure_payload,
                            "stage_timings": timings,
                        },
                    )
                    return [
                        CrossDocumentResult(
                            run_id=f"{epoch_id}:{index}",
                            processing_key=canonical_hash(
                                {"epoch": epoch_id, "message": message_id}
                            ),
                            message_id=message_id,
                            status=CrossDocumentStatus.PARTIAL_PARENT_RESOLUTION,
                            atomic_events=[
                                event
                                for event in final_events
                                if set(event.mention_ids).intersection(
                                    {
                                        item.mention_id
                                        for item in self.registry.list_mentions_for_message(
                                            message_id
                                        )
                                    }
                                )
                            ],
                            packages=[],
                            atomic_assignments=atomic_assignments,
                            package_assignments=[],
                            model_calls=summaries if index == 0 else [],
                            candidate_counts=candidate_counts,
                            failure_stage="parent_occurrence",
                            error_code="PARTIAL_PARENT_RESOLUTION",
                            started_at=started_at,
                            finished_at=datetime.now(UTC),
                        )
                        for index, message_id in enumerate(ordered_ids)
                    ]
                partition = stage_result.partition
                self._save_artifact(
                    epoch_id=epoch_id,
                    manifest_hash=manifest_hash,
                    kind="package_frozen_partition_v3",
                    upstream_hash=partition.registry_hash,
                    payload=partition.model_dump(mode="json"),
                )
            else:
                partition = FrozenPackagePartitionV3.model_validate(frozen_artifact["payload"])
                package_stage_telemetry = {
                    "resumed_from_frozen_partition": True,
                    "partition_hash": partition.partition_hash,
                }
            self.registry.update_bulk_epoch(
                epoch_id, status="RUNNING", current_stage="PACKAGE_APPLY"
            )
            (
                packages,
                memberships,
                package_assignments,
                external_relations,
                redirects,
            ) = project_parent_partition(
                partition,
                events=final_events,
                existing_packages=existing_packages,
                run_id=coordinator_run_id,
            )
            self.registry.activate_package_partition_v3(
                packages=packages,
                memberships=memberships,
                assignments=package_assignments,
                external_relations=external_relations,
                redirects=redirects,
                run_id=coordinator_run_id,
            )
            package_stage_telemetry.update(
                {
                    "apply_chunk_count": 1,
                    "apply_retry_count": 0,
                    "apply_degraded_count": 0,
                    "redirect_count": len(redirects),
                    "external_relation_count": len(external_relations),
                }
            )
            self._save_artifact(
                epoch_id=epoch_id,
                manifest_hash=manifest_hash,
                kind="package_partition_v3",
                upstream_hash=partition.partition_hash,
                payload={
                    "partition_hash": partition.partition_hash,
                    "package_ids": sorted(item.package_id for item in packages),
                    "assignment_ids": sorted(item.assignment_id for item in package_assignments),
                    **package_stage_telemetry,
                },
            )
        else:
            packages = [
                package
                for package_id in applied_artifact["payload"]["package_ids"]
                if (package := self.registry.get_current_package(str(package_id))) is not None
            ]
            event_ids = {event_id for package in packages for event_id in package.member_event_ids}
            package_assignments = [
                assignment
                for event_id in sorted(event_ids)
                if (assignment := self.registry.get_latest_package_assignment_for_event(event_id))
                is not None
            ]
            package_stage_telemetry = {
                key: value
                for key, value in applied_artifact["payload"].items()
                if key not in {"package_ids", "assignment_ids"}
            }
        final_packages = packages
        timings["package_ms"] = round((perf_counter() - package_started) * 1000)
        timings["wall_clock_ms"] = round((perf_counter() - wall_started) * 1000)
        self.registry.update_bulk_epoch(
            epoch_id, status="RUNNING", current_stage="PACKAGE_COMMITTED"
        )
        telemetry = [item.__dict__ for item in self.executor.telemetry()]
        attempt_telemetry = [item.__dict__ for item in self.executor.attempt_telemetry()]
        provider_telemetry = self.executor.provider_snapshot()
        result_payload = {
            "message_count": len(ordered_ids),
            "mention_count": len(mentions),
            "atomic_count": len(self.registry.list_current_atomic_events(limit=10000)),
            "package_count": len(self.registry.list_current_packages(limit=10000)),
            "stage_timings": timings,
            "package_stage": package_stage_telemetry,
            "async_executor": {
                "capacity_config": self.executor.capacity_config(),
                "call_count": len(telemetry),
                "max_active_by_tier": self._max_active_by_tier(telemetry),
                "queue_wait_ms": sum(_as_int(item["queue_wait_ms"]) for item in telemetry),
                "failed_call_count": sum(item["status"] == "FAILED" for item in telemetry),
                "provider": provider_telemetry.__dict__,
                "provider_by_stage": self.executor.provider_stage_snapshots(),
                "physical_attempt_count": len(attempt_telemetry),
                "retry_attempt_count": sum(
                    int(item["attempt_index"]) > 1 for item in attempt_telemetry
                ),
                "attempt_backoff_ms": sum(
                    _as_int(item["backoff_ms"]) for item in attempt_telemetry
                ),
                "attempts": attempt_telemetry,
            },
            "bulk_writer": writer_telemetry,
            "deterministic_runtime": deterministic_telemetry,
            "candidate_counts": candidate_counts,
            "final_package_ids": sorted(package.package_id for package in final_packages),
        }
        self.registry.update_bulk_epoch(
            epoch_id,
            status="FINALIZED",
            current_stage="FINALIZED",
            result=result_payload,
        )
        results = self._results_from_registry(
            epoch_id=epoch_id,
            message_ids=ordered_ids,
            summaries=summaries,
            candidate_counts=candidate_counts,
            reused=False,
            started_at=started_at,
            atomic_assignments=atomic_assignments,
            package_assignments=package_assignments,
        )
        self.registry.complete_cross_document_run(
            results[0].model_copy(
                update={
                    "run_id": coordinator_run_id,
                    "atomic_events": self.registry.list_current_atomic_events(limit=10000),
                    "packages": self.registry.list_current_packages(limit=10000),
                    "atomic_assignments": atomic_assignments,
                    "package_assignments": package_assignments,
                    "model_calls": summaries,
                }
            )
        )
        return results

    def _save_artifact(
        self,
        *,
        epoch_id: str,
        manifest_hash: str,
        kind: str,
        upstream_hash: str,
        payload: dict[str, object],
    ) -> None:
        artifact = StageArtifact(
            kind=kind,
            manifest_hash=manifest_hash,
            upstream_hash=upstream_hash,
            engine_version=BULK_STAGE_GRAPH_VERSION,
            payload=payload,
        )
        self.registry.save_bulk_epoch_artifact(
            epoch_id=epoch_id,
            artifact_kind=kind,
            artifact_hash=artifact.artifact_hash,
            upstream_hash=upstream_hash,
            payload=payload,
        )

    @staticmethod
    def _max_active_by_tier(telemetry: list[dict[str, object]]) -> dict[str, int]:
        output: dict[str, int] = {}
        for tier in {str(item["tier"]) for item in telemetry}:
            points: list[tuple[int, int]] = []
            for item in telemetry:
                if item["tier"] != tier:
                    continue
                points.append((_as_int(item["started_at_ms"]), 1))
                points.append((_as_int(item["finished_at_ms"]), -1))
            active = 0
            maximum = 0
            for _, delta in sorted(points, key=lambda value: (value[0], value[1])):
                active += delta
                maximum = max(maximum, active)
            output[tier] = maximum
        return output

    def _results_from_registry(
        self,
        *,
        epoch_id: str,
        message_ids: list[str],
        summaries: list[ModelCallSummary],
        candidate_counts: dict[str, int],
        reused: bool,
        started_at: datetime,
        atomic_assignments: list[AtomicAssignmentRecord] | None = None,
        package_assignments: list[PackageAssignmentRecord] | None = None,
    ) -> list[CrossDocumentResult]:
        all_events = self.registry.list_current_atomic_events(limit=10000)
        all_packages = self.registry.list_current_packages(limit=10000)
        atomic_records = list(atomic_assignments or [])
        package_records = list(package_assignments or [])
        results = []
        for index, message_id in enumerate(message_ids):
            mention_ids = {
                mention.mention_id
                for mention in self.registry.list_mentions_for_message(message_id)
            }
            events = [event for event in all_events if mention_ids.intersection(event.mention_ids)]
            event_ids = {event.event_id for event in events}
            packages = [
                package
                for package in all_packages
                if event_ids.intersection(package.member_event_ids)
            ]
            results.append(
                CrossDocumentResult(
                    run_id=f"{epoch_id}:{index}",
                    processing_key=canonical_hash({"epoch": epoch_id, "message": message_id}),
                    message_id=message_id,
                    status=CrossDocumentStatus.SUCCEEDED,
                    atomic_events=events,
                    packages=packages,
                    atomic_assignments=[
                        item for item in atomic_records if item.mention_id in mention_ids
                    ],
                    package_assignments=[
                        item for item in package_records if item.event_id in event_ids
                    ],
                    model_calls=summaries if index == 0 else [],
                    candidate_counts=candidate_counts,
                    reused=reused,
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                )
            )
        return results

    def _current_stage(self, epoch_id: str) -> str:
        epoch = self.registry.get_bulk_epoch(epoch_id)
        return str(epoch["current_stage"]) if epoch is not None else "UNKNOWN"


def _as_int(value: object) -> int:
    if not isinstance(value, int):
        raise TypeError("telemetry timestamp must be an integer")
    return value
