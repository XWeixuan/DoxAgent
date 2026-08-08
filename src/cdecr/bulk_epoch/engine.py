"""BULK_EPOCH_V3_STAGE_GRAPH: one-shot Read/Decide/Reduce/Commit orchestration."""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from time import perf_counter

from cdecr.bulk_epoch.artifacts import StageArtifact, canonical_hash
from cdecr.bulk_epoch.executor import AsyncModelExecutor
from cdecr.bulk_epoch.late_stage import (
    LateStageConfig,
    run_atomic_late_convergence,
    run_package_wave_c,
)
from cdecr.bulk_epoch.package_stage import assign_packages_epoch
from cdecr.bulk_epoch.snapshots import atomic_snapshot, package_snapshot
from cdecr.bulk_epoch.task_ledger import BulkTaskLedger
from cdecr.bulk_epoch.writer import BulkWriter
from cdecr.canonical_field_resolution import CanonicalFieldResolutionEngine
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
    PackagePairMergeDecision,
)
from cdecr.field_coreference import FieldCoreferenceResolver
from cdecr.identity_compiler import IdentityCompiler
from cdecr.kb_v2 import V2KnowledgeBase
from cdecr.ports import CDECRRegistry
from cdecr.single_document_contracts import ModelCallSummary

BULK_STAGE_GRAPH_VERSION = "cdecr-bulk-epoch-v6-stage-snapshot-batch-io"


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
        knowledge_base: V2KnowledgeBase | None = None,
        atomic_late_convergence: bool = True,
        package_wave_c: bool = True,
        n13_pair_local_apply: bool = False,
        atomic_late_task_cap: int = 48,
        package_wave_c_pair_cap: int = 64,
        n9_late_active_requests: int = 24,
        package_wave_c_active_requests: int = 32,
        late_total_input_budget_ratio: float = 0.08,
        late_wall_deadline_ratio: float = 0.12,
        late_max_spoke_members: int = 4,
        late_max_spokes_per_hub: int = 4,
        writer_queue_low_watermark: int = 1000,
        writer_queue_high_watermark: int = 5000,
        writer_queue_hard_limit: int = 10000,
        batch_audit_write: bool = True,
        stage_read_snapshot: bool = True,
        chunked_stage_apply: bool = True,
        batch_task_ledger: bool = True,
        embedding_batch_executor: bool = True,
    ) -> None:
        self.registry = registry
        self.core = core
        self.executor = executor
        self.field_active_requests = max(1, field_active_requests)
        self.knowledge_base = knowledge_base or core.knowledge_base
        self.atomic_late_convergence = atomic_late_convergence
        self.package_wave_c = package_wave_c
        self.n13_pair_local_apply = n13_pair_local_apply
        self.late_total_input_budget_ratio = late_total_input_budget_ratio
        self.late_wall_deadline_ratio = late_wall_deadline_ratio
        self.late_config = LateStageConfig(
            atomic_task_cap=atomic_late_task_cap,
            package_pair_cap=package_wave_c_pair_cap,
            atomic_active_requests=n9_late_active_requests,
            package_active_requests=package_wave_c_active_requests,
            max_spoke_members=late_max_spoke_members,
            max_spokes_per_hub=late_max_spokes_per_hub,
        )
        self.core.n13_pair_local_apply = n13_pair_local_apply
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
                "embedding_preferred_batch_size": 64,
                "embedding_active_requests": 4,
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
            model_m1=self.core.model_m1,
            model_m2=self.core.model_m2,
            model_m3=self.core.model_m3,
            summaries=summaries,
        )
        ledger = BulkTaskLedger(
            registry=self.registry,
            epoch_id=epoch_id,
            batch_enabled=self.batch_task_ledger,
        )
        timings: dict[str, int] = {}
        deterministic_telemetry: dict[str, object] = {}
        writer_telemetry: list[dict[str, object]] = []
        late_admission: dict[str, dict[str, object]] = {}
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
            field_artifact = self.registry.get_bulk_epoch_artifact(epoch_id, "field_overlay_v1")
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
                )
                field_snapshot_hash = canonical_hash(
                    [mention.model_dump(mode="json") for mention in mentions]
                )
                self._save_artifact(
                    epoch_id=epoch_id,
                    manifest_hash=manifest_hash,
                    kind="field_plan_v1",
                    upstream_hash=manifest_hash,
                    payload={
                        "snapshot_hash": field_snapshot_hash,
                        "mention_count": len(mentions),
                        "document_count": len(documents),
                        "task_semantics": "GLOBAL_SEMANTIC_KEY_DEDUP",
                        "candidate_cap": 8,
                        "batch_size": 12,
                    },
                )

                def field_task(task_id: str, status: str, error_code: str | None) -> None:
                    if status == "RUNNING":
                        ledger.start(
                            stage="FIELD",
                            task_id=task_id,
                            input_hash=task_id,
                            snapshot_hash=field_snapshot_hash,
                        )
                    elif status == "SUCCEEDED":
                        ledger.finish(
                            stage="FIELD",
                            task_id=task_id,
                            input_hash=task_id,
                            snapshot_hash=field_snapshot_hash,
                            decision_ref={"field_task_key": task_id},
                        )
                    else:
                        ledger.fail(
                            stage="FIELD",
                            task_id=task_id,
                            input_hash=task_id,
                            snapshot_hash=field_snapshot_hash,
                            error_code=error_code or "FIELD_TASK_FAILED",
                        )

                field_summary = field_engine.resolve_epoch(
                    documents,
                    run_id=coordinator_run_id,
                    max_workers=self.field_active_requests,
                    task_hook=field_task,
                    completed_task_ids=set(ledger.completed("FIELD")),
                )
                field_plan_artifact = self.registry.get_bulk_epoch_artifact(
                    epoch_id, "field_plan_v1"
                )
                assert field_plan_artifact is not None
                self._save_artifact(
                    epoch_id=epoch_id,
                    manifest_hash=manifest_hash,
                    kind="field_overlay_v1",
                    upstream_hash=str(field_plan_artifact["artifact_hash"]),
                    payload={
                        "resolved_count": field_summary.resolved_count,
                        "unresolved_count": field_summary.unresolved_count,
                        "group_count": field_summary.group_count,
                        "failed_group_count": field_summary.failed_group_count,
                        "skipped_group_count": field_summary.skipped_group_count,
                        "field_links_hash": field_summary.field_links_hash,
                    },
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
            if atomic_artifact is None:
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
                    epoch_id, "field_overlay_v1"
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

            package_started = perf_counter()
            self.registry.update_bulk_epoch(
                epoch_id, status="RUNNING", current_stage="PACKAGE_DECIDE"
            )
            package_artifact = self.registry.get_bulk_epoch_artifact(
                epoch_id, "package_partition_v1"
            )
            package_assignments: list[PackageAssignmentRecord] = []
            package_stage_telemetry: dict[str, object] = {}
            if package_artifact is None:
                base_package = package_snapshot(self.registry)
                atomic_partition_artifact = self.registry.get_bulk_epoch_artifact(
                    epoch_id, "atomic_partition_v1"
                )
                assert atomic_partition_artifact is not None
                atomic_upstream_artifact = (
                    self.registry.get_bulk_epoch_artifact(epoch_id, "atomic_late_partition_v1")
                    or atomic_partition_artifact
                )
                self._save_artifact(
                    epoch_id=epoch_id,
                    manifest_hash=manifest_hash,
                    kind="package_plan_v1",
                    upstream_hash=str(atomic_upstream_artifact["artifact_hash"]),
                    payload={
                        "snapshot_hash": base_package.snapshot_hash,
                        "event_ids": sorted(event.event_id for event in atomic_events),
                        "waves": 2,
                        "model_candidate_cap": 6,
                        "scheduler_edge_cap": 24 * len(atomic_events),
                    },
                )
                with self._writer() as writer:
                    packages, package_assignments, package_stage_telemetry = assign_packages_epoch(
                        engine=self.core,
                        events=atomic_events,
                        mentions=mentions,
                        models=models,
                        run_id=coordinator_run_id,
                        candidate_counts=candidate_counts,
                        base_packages=base_package.packages,
                        writer=writer,
                        ledger=ledger,
                        task_snapshot_hash=base_package.snapshot_hash,
                        use_embedding_batch_executor=self.embedding_batch_executor,
                        chunked_apply=self.chunked_stage_apply,
                    )
                writer_telemetry.append({"stage": "PACKAGE_APPLY", **writer.snapshot().__dict__})
                wave_c_budget = self._late_budget_snapshot(
                    summaries=summaries,
                    timings=timings,
                    wall_started=wall_started,
                )
                late_admission["package_wave_c"] = wave_c_budget
                if self.package_wave_c and bool(wave_c_budget["admitted"]):
                    wave_c_started = perf_counter()
                    self.registry.update_bulk_epoch(
                        epoch_id, status="RUNNING", current_stage="PACKAGE_WAVE_C"
                    )
                    self._save_artifact(
                        epoch_id=epoch_id,
                        manifest_hash=manifest_hash,
                        kind="package_wave_c_plan_v1",
                        upstream_hash=str(atomic_partition_artifact["artifact_hash"]),
                        payload={
                            "snapshot_hash": canonical_hash(
                                [
                                    (package.package_id, package.version)
                                    for package in sorted(
                                        packages, key=lambda item: item.package_id
                                    )
                                ]
                            ),
                            "pair_cap": self.late_config.package_pair_cap,
                            "rounds": 1,
                            "failure_semantics": "NEUTRAL_OMISSION",
                        },
                    )
                    wave_c_task_hash = canonical_hash(
                        {
                            "epoch": epoch_id,
                            "stage": "PACKAGE_WAVE_C",
                            "cap": self.late_config.package_pair_cap,
                        }
                    )
                    ledger.start(
                        stage="N12_C",
                        task_id="epoch",
                        input_hash=wave_c_task_hash,
                        snapshot_hash=str(atomic_partition_artifact["artifact_hash"]),
                    )
                    packages, wave_c_telemetry = run_package_wave_c(
                        engine=self.core,
                        packages=packages,
                        models=models,
                        run_id=coordinator_run_id,
                        config=self.late_config,
                    )
                    ledger.finish(
                        stage="N12_C",
                        task_id="epoch",
                        input_hash=wave_c_task_hash,
                        snapshot_hash=str(atomic_partition_artifact["artifact_hash"]),
                        decision_ref=wave_c_telemetry,
                    )
                    package_stage_telemetry["wave_c"] = wave_c_telemetry
                    wave_c_plan = self.registry.get_bulk_epoch_artifact(
                        epoch_id, "package_wave_c_plan_v1"
                    )
                    assert wave_c_plan is not None
                    self._save_artifact(
                        epoch_id=epoch_id,
                        manifest_hash=manifest_hash,
                        kind="package_wave_c_partition_v1",
                        upstream_hash=str(wave_c_plan["artifact_hash"]),
                        payload={
                            **wave_c_telemetry,
                            "package_ids": sorted(package.package_id for package in packages),
                        },
                    )
                    timings["package_wave_c_ms"] = round((perf_counter() - wave_c_started) * 1000)
                package_plan_artifact = self.registry.get_bulk_epoch_artifact(
                    epoch_id, "package_plan_v1"
                )
                assert package_plan_artifact is not None
                self._save_artifact(
                    epoch_id=epoch_id,
                    manifest_hash=manifest_hash,
                    kind="package_partition_v1",
                    upstream_hash=str(package_plan_artifact["artifact_hash"]),
                    payload={
                        "package_ids": sorted(package.package_id for package in packages),
                        "assignment_ids": sorted(
                            assignment.assignment_id for assignment in package_assignments
                        ),
                        **package_stage_telemetry,
                    },
                )
            else:
                packages = [
                    package
                    for package_id in package_artifact["payload"]["package_ids"]
                    if (package := self.registry.get_current_package(str(package_id))) is not None
                ]
            timings["package_ms"] = round((perf_counter() - package_started) * 1000)
            self.registry.update_bulk_epoch(
                epoch_id, status="RUNNING", current_stage="PACKAGE_REDUCED"
            )

            n13_started = perf_counter()
            self.registry.update_bulk_epoch(epoch_id, status="RUNNING", current_stage="N13_DECIDE")
            final_artifact = self.registry.get_bulk_epoch_artifact(
                epoch_id, "final_package_partition_v1"
            )
            if final_artifact is None:
                package_partition_artifact = self.registry.get_bulk_epoch_artifact(
                    epoch_id, "package_partition_v1"
                )
                assert package_partition_artifact is not None
                n13_snapshot = package_snapshot(self.registry)
                self._save_artifact(
                    epoch_id=epoch_id,
                    manifest_hash=manifest_hash,
                    kind="n13_pair_plan_v1",
                    upstream_hash=str(package_partition_artifact["artifact_hash"]),
                    payload={
                        "snapshot_hash": n13_snapshot.snapshot_hash,
                        "touched_package_ids": sorted(package.package_id for package in packages),
                        "planner_version": self.core.n13_planner_version,
                        "cheap_candidate_universe_cap": 64,
                        "m3_candidate_cap_per_package": 5,
                        "batch_size": 12,
                        "coverage": "INDEXED_MULTI_LANE_BOUNDED_PLAN",
                    },
                )

                def n13_task(
                    pair: tuple[str, str],
                    status: str,
                    decision: PackagePairMergeDecision | None,
                    error_code: str | None,
                ) -> None:
                    task_id = f"{pair[0]}|{pair[1]}"
                    task_hash = canonical_hash({"left": pair[0], "right": pair[1]})
                    if status == "RUNNING":
                        ledger.start(
                            stage="N13",
                            task_id=task_id,
                            input_hash=task_hash,
                            snapshot_hash=n13_snapshot.snapshot_hash,
                        )
                    elif status == "SUCCEEDED" and decision is not None:
                        ledger.finish(
                            stage="N13",
                            task_id=task_id,
                            input_hash=task_hash,
                            snapshot_hash=n13_snapshot.snapshot_hash,
                            decision_ref=decision.model_dump(mode="json"),
                        )
                    else:
                        ledger.fail(
                            stage="N13",
                            task_id=task_id,
                            input_hash=task_hash,
                            snapshot_hash=n13_snapshot.snapshot_hash,
                            error_code=error_code or "UNJUDGEABLE_FAILED",
                        )

                n13_budget = self._late_budget_snapshot(
                    summaries=summaries,
                    timings=timings,
                    wall_started=wall_started,
                )
                late_admission["n13_pair_local_apply"] = n13_budget
                n13_apply_started: float | None = None

                def mark_n13_apply_started() -> None:
                    nonlocal n13_apply_started
                    n13_apply_started = perf_counter()

                def n13_planner_chunk(
                    chunk_index: int,
                    touched_ids: Sequence[str],
                    pair_count: int,
                ) -> None:
                    task_id = f"chunk:{chunk_index}"
                    task_hash = canonical_hash(
                        {
                            "chunk_index": chunk_index,
                            "touched_package_ids": list(touched_ids),
                            "pair_count": pair_count,
                            "planner_version": self.core.n13_planner_version,
                        }
                    )
                    ledger.start(
                        stage="N13_RECALL_CHUNK",
                        task_id=task_id,
                        input_hash=task_hash,
                        snapshot_hash=n13_snapshot.snapshot_hash,
                    )
                    ledger.finish(
                        stage="N13_RECALL_CHUNK",
                        task_id=task_id,
                        input_hash=task_hash,
                        snapshot_hash=n13_snapshot.snapshot_hash,
                        decision_ref={"pair_count": pair_count},
                    )

                original_pair_local_apply = self.core.n13_pair_local_apply
                self.core.n13_pair_local_apply = self.n13_pair_local_apply
                n13_apply_telemetry: dict[str, int] = {}
                try:
                    final_packages = self.core._correct_packages_v13(
                        packages,
                        models,
                        run_id=coordinator_run_id,
                        task_hook=n13_task,
                        apply_started_hook=mark_n13_apply_started,
                        apply_telemetry=n13_apply_telemetry,
                        planner_chunk_hook=n13_planner_chunk,
                    )
                finally:
                    self.core.n13_pair_local_apply = original_pair_local_apply
                timings["n13_late_apply_ms"] = (
                    0
                    if n13_apply_started is None
                    else round((perf_counter() - n13_apply_started) * 1000)
                )
                timings.update(n13_apply_telemetry)
                package_stage_telemetry.update(n13_apply_telemetry)
                n13_apply_plan = {
                    "pair_local": self.n13_pair_local_apply,
                    "max_spoke_members": self.late_config.max_spoke_members,
                    "max_spokes_per_hub": self.late_config.max_spokes_per_hub,
                    "rounds": 1,
                    "package_ids": sorted(package.package_id for package in final_packages),
                }
                n13_plan_artifact = self.registry.get_bulk_epoch_artifact(
                    epoch_id, "n13_pair_plan_v1"
                )
                assert n13_plan_artifact is not None
                self._save_artifact(
                    epoch_id=epoch_id,
                    manifest_hash=manifest_hash,
                    kind="n13_late_apply_plan_v1",
                    upstream_hash=str(n13_plan_artifact["artifact_hash"]),
                    payload=n13_apply_plan,
                )
                n13_apply_hash = canonical_hash(n13_apply_plan)
                ledger.start(
                    stage="N13_LATE_APPLY",
                    task_id="epoch",
                    input_hash=n13_apply_hash,
                    snapshot_hash=n13_snapshot.snapshot_hash,
                )
                ledger.finish(
                    stage="N13_LATE_APPLY",
                    task_id="epoch",
                    input_hash=n13_apply_hash,
                    snapshot_hash=n13_snapshot.snapshot_hash,
                    decision_ref=n13_apply_plan,
                )
                n13_plan_artifact = self.registry.get_bulk_epoch_artifact(
                    epoch_id, "n13_pair_plan_v1"
                )
                assert n13_plan_artifact is not None
                self._save_artifact(
                    epoch_id=epoch_id,
                    manifest_hash=manifest_hash,
                    kind="final_package_partition_v1",
                    upstream_hash=str(n13_plan_artifact["artifact_hash"]),
                    payload={
                        "package_ids": sorted(package.package_id for package in final_packages)
                    },
                )
            else:
                final_packages = [
                    package
                    for package_id in final_artifact["payload"]["package_ids"]
                    if (package := self.registry.get_current_package(str(package_id))) is not None
                ]
            timings["n13_ms"] = round((perf_counter() - n13_started) * 1000)
            self.registry.update_bulk_epoch(
                epoch_id, status="RUNNING", current_stage="PACKAGE_COMMITTED"
            )
            timings["wall_clock_ms"] = round((perf_counter() - wall_started) * 1000)
            telemetry = [item.__dict__ for item in self.executor.telemetry()]
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
                },
                "bulk_writer": writer_telemetry,
                "deterministic_runtime": {
                    **deterministic_telemetry,
                    "embedding": {
                        key: value.__dict__
                        for key, value in self.core._embedding_telemetry_by_stage.items()
                    },
                    "embedding_batch_count": sum(
                        value.batch_count
                        for value in self.core._embedding_telemetry_by_stage.values()
                    ),
                    "embedding_batch_size_distribution": [
                        size
                        for value in self.core._embedding_telemetry_by_stage.values()
                        for size in value.batch_sizes
                    ],
                    "time_to_first_model_request_ms": (
                        max(
                            0,
                            min(_as_int(item["started_at_ms"]) for item in telemetry)
                            - round(wall_started * 1000),
                        )
                        if telemetry
                        else None
                    ),
                    "runtime_flags": {
                        "batch_audit_write": self.batch_audit_write,
                        "stage_read_snapshot": self.stage_read_snapshot,
                        "chunked_stage_apply": self.chunked_stage_apply,
                        "batch_task_ledger": self.batch_task_ledger,
                        "embedding_batch_executor": self.embedding_batch_executor,
                    },
                },
                "candidate_counts": candidate_counts,
                "audit_degraded": self.core._bulk_audit_degraded,
                "late_budget": {
                    "input_ratio": self.late_total_input_budget_ratio,
                    "wall_deadline_ratio": self.late_wall_deadline_ratio,
                    "single_round": True,
                    "admission": late_admission,
                },
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

    def _late_budget_snapshot(
        self,
        *,
        summaries: list[ModelCallSummary],
        timings: dict[str, int],
        wall_started: float,
    ) -> dict[str, object]:
        late_stages = {"atomic_late_convergence", "package_wave_c"}
        late_input = sum(
            summary.input_tokens or 0 for summary in summaries if summary.stage in late_stages
        )
        non_late_input = sum(
            summary.input_tokens or 0 for summary in summaries if summary.stage not in late_stages
        )
        late_wall_ms = sum(
            timings.get(stage, 0) for stage in ("atomic_late_ms", "package_wave_c_ms")
        )
        elapsed_ms = round((perf_counter() - wall_started) * 1000)
        non_late_wall_ms = max(1, elapsed_ms - late_wall_ms)
        input_ratio = late_input / max(1, non_late_input)
        wall_ratio = late_wall_ms / non_late_wall_ms
        input_allowed = input_ratio <= self.late_total_input_budget_ratio
        wall_allowed = wall_ratio <= self.late_wall_deadline_ratio
        return {
            "admitted": input_allowed and wall_allowed,
            "input_allowed": input_allowed,
            "wall_allowed": wall_allowed,
            "late_input_tokens": late_input,
            "non_late_input_tokens": non_late_input,
            "observed_input_ratio": input_ratio,
            "late_wall_ms": late_wall_ms,
            "non_late_wall_ms": non_late_wall_ms,
            "observed_wall_ratio": wall_ratio,
        }

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
