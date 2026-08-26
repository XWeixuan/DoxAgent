"""Durable new-ticker initialization from isolated news staging through Canonical V1."""

from __future__ import annotations

import hashlib
import inspect
import json
import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any

from cdecr.data import document_fingerprint
from cdecr.ports import CDECRRegistry
from doxagent.cdecr_integration.activity import project_runtime_activity
from doxagent.cdecr_integration.contracts import (
    CDECRWorkflowResult,
    HistoricalLoadReport,
    RuntimeNovelMessageBatch,
    RuntimeRegistryBinding,
    TickerJobMode,
    TickerJobStage,
    TickerJobState,
    TickerPipelineResult,
)
from doxagent.cdecr_integration.historical_loader import (
    HistoricalNewsLoader,
    HistoricalNewsProvider,
    HistoricalStagingRepository,
)
from doxagent.cdecr_integration.job_repository import TickerJobRepository
from doxagent.cdecr_integration.novel_batch import validate_runtime_novel_batch
from doxagent.cdecr_integration.registry_resolver import PerTickerRegistryResolver
from doxagent.cdecr_integration.workflow_runner import CDECRWorkflowRunner
from doxagent.event_library.contracts import FrozenRuntimeSnapshot
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.event_library.service import EventLibraryService
from doxagent.workflows.codex_event_library.remote_runner import (
    RemoteEventLibraryInitializer,
)

RuntimeFactory = Callable[
    [RuntimeRegistryBinding], tuple[CDECRRegistry, CDECRWorkflowRunner]
]


class TickerCDECRPipelineCoordinator:
    def __init__(
        self,
        *,
        registry_root: str | Path,
        state_root: str | Path,
        event_library_root: str | Path,
        providers: Sequence[HistoricalNewsProvider],
        runtime_factory: RuntimeFactory,
        o2_factory: Callable[[EventLibraryService], RemoteEventLibraryInitializer] | None = None,
        sample_seed: int = 20260824,
        max_sources: int = 500,
    ) -> None:
        self.registry_resolver = PerTickerRegistryResolver(registry_root)
        self.state_root = Path(state_root).resolve()
        self.event_library_root = Path(event_library_root).resolve()
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.event_library_root.mkdir(parents=True, exist_ok=True)
        self.jobs = TickerJobRepository(self.state_root / "ticker_jobs.sqlite3")
        self.providers = list(providers)
        self.runtime_factory = runtime_factory
        self.o2_factory = o2_factory
        self.sample_seed = sample_seed
        self.max_sources = max_sources

    async def initialize(
        self,
        *,
        market: str,
        ticker: str,
        as_of: datetime,
        export_dir: str | Path,
        run_o2: bool = True,
        resume_finalized_only: bool = False,
    ) -> TickerPipelineResult:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        binding = self.registry_resolver.bind(market=market, ticker=ticker)
        job_id = _job_id(binding.market, binding.ticker, as_of)
        staging_path = self.state_root / "jobs" / job_id / "historical_staging.sqlite3"
        event_library_path = (
            self.event_library_root / binding.market / binding.ticker / "event_library.sqlite3"
        )
        state = self.jobs.get(job_id)
        if resume_finalized_only:
            if state is None or not state.epoch_id:
                raise RuntimeError(
                    "resume-finalized-only requires an existing job with a CDECR epoch"
                )
            if _epoch_status(Path(binding.registry_path), state.epoch_id) != "FINALIZED":
                raise RuntimeError(
                    "resume-finalized-only refused to invoke CDECR because the saved epoch "
                    "is not FINALIZED"
                )
        if state is None:
            state = TickerJobState(
                job_id=job_id,
                market=binding.market,
                ticker=binding.ticker,
                mode=TickerJobMode.INITIALIZE,
                as_of=as_of,
                stage=TickerJobStage.CREATED,
                runtime_scope=binding.runtime_scope,
                registry_path=binding.registry_path,
                staging_path=str(staging_path),
                event_library_path=str(event_library_path),
                updated_at=datetime.now(UTC),
            )
            self.jobs.save(state)
        if state.stage is TickerJobStage.PUBLISHED:
            return TickerPipelineResult(
                job=state,
                delta_batch_id=state.delta_batch_id,
                published_library_version=state.published_library_version,
            )
        with self.jobs.ticker_lock(
            market=binding.market,
            ticker=binding.ticker,
            owner=job_id,
        ):
            registry, cdecr_runner = self.runtime_factory(binding)
            historical_report: HistoricalLoadReport | None = None
            if not state.message_ids:
                state = self._advance(state, TickerJobStage.HISTORICAL_STAGING)
                loader = HistoricalNewsLoader(
                    staging=HistoricalStagingRepository(staging_path),
                    providers=self.providers,
                    sample_seed=self.sample_seed,
                    max_sources=self.max_sources,
                )
                sources, historical_report = await loader.load(
                    market=binding.market,
                    ticker=binding.ticker,
                    as_of=as_of,
                )
                selected_message_ids = []
                for source in sources:
                    fingerprint = document_fingerprint(source.title, source.text)
                    if registry.has_source_fingerprint(fingerprint):
                        continue
                    registry.save_source(source, fingerprint=fingerprint)
                    selected_message_ids.append(source.message_id)
                state = self._advance(
                    state,
                    TickerJobStage.SOURCES_READY,
                    message_ids=selected_message_ids,
                )
            message_ids = list(state.message_ids)
            incomplete = _unfinished_epoch(Path(binding.registry_path))
            if incomplete is not None:
                raw_epoch_message_ids = incomplete["message_ids"]
                if not isinstance(raw_epoch_message_ids, list):
                    raise ValueError("unfinished CDECR epoch has invalid message IDs")
                epoch_message_ids = [str(item) for item in raw_epoch_message_ids]
                if set(epoch_message_ids) != set(message_ids):
                    message_ids = epoch_message_ids
                    state = self._advance(
                        state,
                        TickerJobStage.CDECR_RUNNING,
                        message_ids=message_ids,
                        epoch_id=str(incomplete["epoch_id"]),
                    )
            cdecr_result: CDECRWorkflowResult
            if state.epoch_id:
                epoch = registry.get_bulk_epoch(state.epoch_id)
            else:
                epoch = None
            if epoch is not None and str(epoch["status"]) == "FINALIZED":
                cdecr_result = CDECRWorkflowResult(
                    market=binding.market,
                    ticker=binding.ticker,
                    runtime_scope=binding.runtime_scope,
                    status="FINALIZED",
                    message_ids=message_ids,
                    document_count=len(message_ids),
                    eligible_document_count=len(message_ids),
                    epoch_id=state.epoch_id,
                    completed_at=datetime.now(UTC),
                )
            else:
                state = self._advance(state, TickerJobStage.CDECR_RUNNING)
                cdecr_result = _run_cdecr(cdecr_runner, message_ids, as_of)
            if cdecr_result.status == "FINALIZED_NOOP":
                state = self._advance(state, TickerJobStage.FINALIZED_NOOP)
                return TickerPipelineResult(
                    job=state,
                    historical=historical_report,
                    cdecr=cdecr_result,
                )
            assert cdecr_result.epoch_id is not None
            state = self._advance(
                state,
                TickerJobStage.RUNTIME_FINALIZED,
                epoch_id=cdecr_result.epoch_id,
            )
            activity = project_runtime_activity(
                registry=registry,
                runtime_scope=binding.runtime_scope,
                as_of=as_of,
            )
            self.jobs.save_activity(job_id, activity)
            snapshot = cdecr_runner.freeze_finalized_snapshot(
                epoch_id=cdecr_result.epoch_id,
                as_of=as_of,
                eligible_atomic_ids=activity.eligible_atomic_ids,
            )
            service = EventLibraryService(EventLibraryRepository(event_library_path))
            batch = service.delta_compiler.compile(snapshot)
            o2_run_id = state.o2_run_id or f"o2-{binding.ticker.lower()}-{job_id[-16:]}"
            state = self._advance(
                state,
                TickerJobStage.DELTA_READY,
                runtime_snapshot_id=snapshot.snapshot_id,
                delta_batch_id=batch.batch_id,
                o2_run_id=o2_run_id,
            )
            if not run_o2:
                return TickerPipelineResult(
                    job=state,
                    historical=historical_report,
                    cdecr=cdecr_result,
                    activity=activity,
                    delta_batch_id=batch.batch_id,
                )
            if self.o2_factory is None:
                raise RuntimeError("O2 real-model initializer is not configured")
            state = self._advance(state, TickerJobStage.O2_RUNNING)
            initializer = self.o2_factory(service)
            _, publication, _, _ = await initializer.run(
                snapshot=snapshot,
                run_id=o2_run_id,
                cutoff_at=as_of,
                export_dir=export_dir,
            )
            if publication is None:
                state = self._advance(state, TickerJobStage.FINALIZED_NOOP)
                return TickerPipelineResult(
                    job=state,
                    historical=historical_report,
                    cdecr=cdecr_result,
                    activity=activity,
                    delta_batch_id=batch.batch_id,
                )
            maintenance = service.repository.get_maintenance_run(o2_run_id) or {}
            state = self._advance(
                state,
                TickerJobStage.PUBLISHED,
                thread_id=(
                    str(maintenance["thread_id"]) if maintenance.get("thread_id") else None
                ),
                published_library_version=publication.published_library_version,
            )
            return TickerPipelineResult(
                job=state,
                historical=historical_report,
                cdecr=cdecr_result,
                activity=activity,
                delta_batch_id=batch.batch_id,
                frozen_view_id=(
                    str(maintenance["frozen_view_id"])
                    if maintenance.get("frozen_view_id")
                    else None
                ),
                published_library_version=publication.published_library_version,
            )

    def status(self, *, market: str, ticker: str) -> TickerJobState | None:
        return self.jobs.latest(market=market.upper(), ticker=ticker.upper())

    async def prepare_runtime_through_delta(
        self,
        *,
        market: str,
        ticker: str,
        as_of: datetime,
        export_dir: str | Path,
        resume_finalized_only: bool = False,
    ) -> TickerPipelineResult:
        """Durable first half used by the total initialization orchestrator."""

        return await self.initialize(
            market=market,
            ticker=ticker,
            as_of=as_of,
            export_dir=export_dir,
            run_o2=False,
            resume_finalized_only=resume_finalized_only,
        )

    async def run_o2_with_upstream_context(
        self,
        *,
        market: str,
        ticker: str,
        as_of: datetime,
        export_dir: str | Path,
        upstream_context_manifest: dict[str, Any],
        resume_finalized_only: bool = False,
    ) -> TickerPipelineResult:
        """Run only O2 from an already prepared FINALIZED Runtime snapshot."""

        prepared = await self.prepare_runtime_through_delta(
            market=market,
            ticker=ticker,
            as_of=as_of,
            export_dir=export_dir,
            resume_finalized_only=resume_finalized_only,
        )
        state = prepared.job
        if not state.epoch_id or not state.o2_run_id:
            raise RuntimeError("O2 continuation requires a FINALIZED epoch and prepared run ID")
        o2_run_id = state.o2_run_id
        binding = self.registry_resolver.bind(market=market, ticker=ticker)
        if _epoch_status(Path(binding.registry_path), state.epoch_id) != "FINALIZED":
            raise RuntimeError("O2 continuation refused because CDECR epoch is not FINALIZED")
        with self.jobs.ticker_lock(
            market=binding.market, ticker=binding.ticker, owner=state.job_id
        ):
            registry, cdecr_runner = self.runtime_factory(binding)
            activity = project_runtime_activity(
                registry=registry,
                runtime_scope=binding.runtime_scope,
                as_of=as_of,
            )
            snapshot = cdecr_runner.freeze_finalized_snapshot(
                epoch_id=state.epoch_id,
                as_of=as_of,
                eligible_atomic_ids=activity.eligible_atomic_ids,
            )
            service = EventLibraryService(EventLibraryRepository(state.event_library_path))
            batch = service.delta_compiler.compile(snapshot)
            if self.o2_factory is None:
                raise RuntimeError("O2 real-model initializer is not configured")
            state = self._advance(state, TickerJobStage.O2_RUNNING)
            initializer = self.o2_factory(service)
            _, publication, _, _ = await initializer.run(
                snapshot=snapshot,
                run_id=o2_run_id,
                cutoff_at=as_of,
                export_dir=export_dir,
                upstream_context_manifest=upstream_context_manifest,
            )
            if publication is None:
                state = self._advance(state, TickerJobStage.FINALIZED_NOOP)
                return TickerPipelineResult(
                    job=state, activity=activity, delta_batch_id=batch.batch_id
                )
            maintenance = service.repository.get_maintenance_run(o2_run_id) or {}
            state = self._advance(
                state,
                TickerJobStage.PUBLISHED,
                thread_id=(
                    str(maintenance["thread_id"]) if maintenance.get("thread_id") else None
                ),
                published_library_version=publication.published_library_version,
            )
            return TickerPipelineResult(
                job=state,
                activity=activity,
                delta_batch_id=batch.batch_id,
                frozen_view_id=(
                    str(maintenance["frozen_view_id"])
                    if maintenance.get("frozen_view_id")
                    else None
                ),
                published_library_version=publication.published_library_version,
            )

    async def update(
        self,
        *,
        batch: RuntimeNovelMessageBatch,
        export_dir: str | Path,
        run_o2: bool = True,
    ) -> TickerPipelineResult:
        """Consume the frozen test batch and incrementally publish one ticker."""

        binding = self.registry_resolver.bind(market=batch.market, ticker=batch.ticker)
        as_of = datetime.combine(batch.trading_date, time.max, tzinfo=UTC)
        job_id = _update_job_id(batch)
        staging_path = self.state_root / "jobs" / job_id / "runtime_novel_batch.json"
        event_library_path = (
            self.event_library_root / binding.market / binding.ticker / "event_library.sqlite3"
        )
        state = self.jobs.get(job_id)
        if state is None:
            state = TickerJobState(
                job_id=job_id,
                market=binding.market,
                ticker=binding.ticker,
                mode=TickerJobMode.UPDATE,
                as_of=as_of,
                stage=TickerJobStage.CREATED,
                runtime_scope=binding.runtime_scope,
                registry_path=binding.registry_path,
                staging_path=str(staging_path),
                event_library_path=str(event_library_path),
                upstream_batch_id=batch.batch_id,
                updated_at=datetime.now(UTC),
            )
            self.jobs.save(state)
        elif state.upstream_batch_id != batch.batch_id:
            raise ValueError("update resume state does not match RuntimeNovelMessageBatch")
        if state.stage in {TickerJobStage.PUBLISHED, TickerJobStage.FINALIZED_NOOP}:
            return TickerPipelineResult(
                job=state,
                delta_batch_id=state.delta_batch_id,
                published_library_version=state.published_library_version,
            )

        with self.jobs.ticker_lock(
            market=binding.market,
            ticker=binding.ticker,
            owner=job_id,
        ):
            registry, cdecr_runner = self.runtime_factory(binding)
            message_ids = validate_runtime_novel_batch(batch, registry=registry)
            if not message_ids:
                service = EventLibraryService(EventLibraryRepository(event_library_path))
                candidates = service.repository.due_reference_review_candidates(
                    ticker=binding.ticker, as_of=as_of
                )
                if not candidates:
                    state = self._advance(
                        state,
                        TickerJobStage.FINALIZED_NOOP,
                        message_ids=[],
                    )
                    return TickerPipelineResult(job=state)
                identity = hashlib.sha256(
                    f"{binding.runtime_scope}|{as_of.isoformat()}|REFERENCE_REVIEW".encode()
                ).hexdigest()
                snapshot = FrozenRuntimeSnapshot(
                    snapshot_id=f"runtime-review:{identity[:24]}",
                    runtime_scope=binding.runtime_scope,
                    epoch_id=f"review-only:{identity[:24]}",
                    market=binding.market,
                    ticker=binding.ticker,
                    as_of=as_of,
                    atomics=[],
                    packages=[],
                )
                delta = service.delta_compiler.compile(snapshot)
                o2_run_id = state.o2_run_id or (
                    f"o2-review-{binding.ticker.lower()}-{job_id[-16:]}"
                )
                state = self._advance(
                    state,
                    TickerJobStage.DELTA_READY,
                    message_ids=[],
                    runtime_snapshot_id=snapshot.snapshot_id,
                    delta_batch_id=delta.batch_id,
                    o2_run_id=o2_run_id,
                )
                if not run_o2:
                    return TickerPipelineResult(job=state, delta_batch_id=delta.batch_id)
                if self.o2_factory is None:
                    raise RuntimeError("O2 real-model maintainer is not configured")
                state = self._advance(state, TickerJobStage.O2_RUNNING)
                maintainer = self.o2_factory(service)
                _, publication, _, _ = await maintainer.run(
                    snapshot=snapshot,
                    run_id=o2_run_id,
                    cutoff_at=as_of,
                    export_dir=export_dir,
                    mode="INCREMENTAL",
                )
                if publication is None:
                    state = self._advance(state, TickerJobStage.FINALIZED_NOOP)
                    return TickerPipelineResult(job=state, delta_batch_id=delta.batch_id)
                maintenance = service.repository.get_maintenance_run(o2_run_id) or {}
                state = self._advance(
                    state,
                    TickerJobStage.PUBLISHED,
                    thread_id=(
                        str(maintenance["thread_id"])
                        if maintenance.get("thread_id")
                        else None
                    ),
                    published_library_version=publication.published_library_version,
                )
                return TickerPipelineResult(
                    job=state,
                    delta_batch_id=delta.batch_id,
                    published_library_version=publication.published_library_version,
                )
            if state.stage is TickerJobStage.CREATED:
                state = self._advance(
                    state,
                    TickerJobStage.SOURCES_READY,
                    message_ids=message_ids,
                )
            epoch = registry.get_bulk_epoch(state.epoch_id) if state.epoch_id else None
            if epoch is not None and str(epoch["status"]) == "FINALIZED":
                cdecr_result = CDECRWorkflowResult(
                    market=binding.market,
                    ticker=binding.ticker,
                    runtime_scope=binding.runtime_scope,
                    status="FINALIZED",
                    message_ids=message_ids,
                    document_count=len(message_ids),
                    eligible_document_count=len(message_ids),
                    epoch_id=state.epoch_id,
                    completed_at=datetime.now(UTC),
                )
            else:
                state = self._advance(state, TickerJobStage.CDECR_RUNNING)
                cdecr_result = _run_cdecr(cdecr_runner, message_ids, as_of)
            if cdecr_result.status == "FINALIZED_NOOP":
                state = self._advance(state, TickerJobStage.FINALIZED_NOOP)
                return TickerPipelineResult(job=state, cdecr=cdecr_result)
            assert cdecr_result.epoch_id is not None
            state = self._advance(
                state,
                TickerJobStage.RUNTIME_FINALIZED,
                epoch_id=cdecr_result.epoch_id,
            )
            activity = project_runtime_activity(
                registry=registry,
                runtime_scope=binding.runtime_scope,
                as_of=as_of,
            )
            self.jobs.save_activity(job_id, activity)
            snapshot = cdecr_runner.freeze_finalized_snapshot(
                epoch_id=cdecr_result.epoch_id,
                as_of=as_of,
                eligible_atomic_ids=activity.eligible_atomic_ids,
            )
            service = EventLibraryService(EventLibraryRepository(event_library_path))
            delta = service.delta_compiler.compile(snapshot)
            o2_run_id = state.o2_run_id or f"o2-update-{binding.ticker.lower()}-{job_id[-16:]}"
            state = self._advance(
                state,
                TickerJobStage.DELTA_READY,
                runtime_snapshot_id=snapshot.snapshot_id,
                delta_batch_id=delta.batch_id,
                o2_run_id=o2_run_id,
            )
            if not delta.items:
                state = self._advance(state, TickerJobStage.FINALIZED_NOOP)
                return TickerPipelineResult(
                    job=state,
                    cdecr=cdecr_result,
                    activity=activity,
                    delta_batch_id=delta.batch_id,
                )
            if not run_o2:
                return TickerPipelineResult(
                    job=state,
                    cdecr=cdecr_result,
                    activity=activity,
                    delta_batch_id=delta.batch_id,
                )
            if self.o2_factory is None:
                raise RuntimeError("O2 real-model maintainer is not configured")
            state = self._advance(state, TickerJobStage.O2_RUNNING)
            maintainer = self.o2_factory(service)
            _, publication, _, _ = await maintainer.run(
                snapshot=snapshot,
                run_id=o2_run_id,
                cutoff_at=as_of,
                export_dir=export_dir,
                mode="INCREMENTAL",
            )
            if publication is None:
                state = self._advance(state, TickerJobStage.FINALIZED_NOOP)
                return TickerPipelineResult(job=state, delta_batch_id=delta.batch_id)
            maintenance = service.repository.get_maintenance_run(o2_run_id) or {}
            state = self._advance(
                state,
                TickerJobStage.PUBLISHED,
                thread_id=(
                    str(maintenance["thread_id"]) if maintenance.get("thread_id") else None
                ),
                published_library_version=publication.published_library_version,
            )
            return TickerPipelineResult(
                job=state,
                cdecr=cdecr_result,
                activity=activity,
                delta_batch_id=delta.batch_id,
                frozen_view_id=(
                    str(maintenance["frozen_view_id"])
                    if maintenance.get("frozen_view_id")
                    else None
                ),
                published_library_version=publication.published_library_version,
            )

    def _advance(
        self,
        state: TickerJobState,
        stage: TickerJobStage,
        **updates: Any,
    ) -> TickerJobState:
        updated = state.model_copy(
            update={"stage": stage, "updated_at": datetime.now(UTC), **updates}
        )
        self.jobs.save(updated)
        return updated


def _job_id(market: str, ticker: str, as_of: datetime) -> str:
    identity = f"{market.upper()}|{ticker.upper()}|INITIALIZE|{as_of.astimezone(UTC).isoformat()}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"cdecr-init-{market.lower()}-{ticker.lower()}-{digest[:20]}"


def _update_job_id(batch: RuntimeNovelMessageBatch) -> str:
    identity = "|".join(
        [
            batch.market,
            batch.ticker,
            batch.trading_date.isoformat(),
            batch.batch_id,
            batch.source_snapshot_or_lookup_ref,
        ]
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"cdecr-update-{batch.market.lower()}-{batch.ticker.lower()}-{digest[:20]}"


def _unfinished_epoch(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "bulk_epochs" not in tables:
            return None
        row = connection.execute(
            """
            SELECT epoch_id,status,message_ids_json FROM bulk_epochs
            WHERE status IN ('RUNNING','PARTIAL')
            ORDER BY updated_at DESC LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    return {
        "epoch_id": str(row["epoch_id"]),
        "status": str(row["status"]),
        "message_ids": json.loads(str(row["message_ids_json"])),
    }


def _epoch_status(path: Path, epoch_id: str) -> str | None:
    if not path.is_file():
        return None
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT status FROM bulk_epochs WHERE epoch_id=?", (epoch_id,)
        ).fetchone()
    return None if row is None else str(row[0])


def _run_cdecr(
    runner: CDECRWorkflowRunner, message_ids: list[str], as_of: datetime
) -> CDECRWorkflowResult:
    if "as_of" in inspect.signature(runner.run).parameters:
        return runner.run(message_ids, as_of=as_of)
    # Compatibility for deterministic test/extension adapters built against v1.
    return runner.run(message_ids)
