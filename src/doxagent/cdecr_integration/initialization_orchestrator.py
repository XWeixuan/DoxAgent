"""Durable V2 initialization: D1 || CDECR -> O2 -> D2 -> optional D3."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from doxagent.cdecr_integration.contracts import (
    InitializationOrchestrationStage,
    InitializationOrchestrationState,
    O2UpstreamContextManifest,
    TickerPipelineResult,
)
from doxagent.cdecr_integration.coordinator import TickerCDECRPipelineCoordinator
from doxagent.codex_runtime.schema import GlobalResearchBundle
from doxagent.event_library.provider import PublishedEventLibraryReader
from doxagent.workflows.codex_global_research.schema import GlobalResearchRunRequest


class GlobalResearchRunner(Protocol):
    async def run(self, request: GlobalResearchRunRequest) -> GlobalResearchBundle: ...


class PinnedDocument2Runner(Protocol):
    async def run_pinned(
        self,
        *,
        source_global_run_id: str,
        ticker: str,
        as_of: datetime,
        event_library_version: int,
        event_library_sha256: str,
        event_library_published_at: datetime | None,
    ) -> str: ...


class PinnedDocument3Runner(Protocol):
    async def run_pinned(
        self,
        *,
        document2_run_id: str,
        ticker: str,
        as_of: datetime,
        event_library_version: int,
    ) -> str: ...


class InitializationStateRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS initialization_orchestrations(
                    run_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def get(self, run_id: str) -> InitializationOrchestrationState | None:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT state_json FROM initialization_orchestrations WHERE run_id=?",
                (run_id,),
            ).fetchone()
        return None if row is None else InitializationOrchestrationState.model_validate_json(row[0])

    def save(self, state: InitializationOrchestrationState) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO initialization_orchestrations(run_id,state_json,updated_at)
                VALUES (?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET
                    state_json=excluded.state_json,updated_at=excluded.updated_at
                """,
                (state.run_id, state.model_dump_json(), state.updated_at.isoformat()),
            )


class BlackboardInitializationOrchestrator:
    """Total, resumable initialization coordinator with explicit child run IDs."""

    def __init__(
        self,
        *,
        state_repository: InitializationStateRepository,
        global_research: GlobalResearchRunner,
        ticker_pipeline: TickerCDECRPipelineCoordinator,
        document2: PinnedDocument2Runner,
        document3: PinnedDocument3Runner | None = None,
    ) -> None:
        self._states = state_repository
        self._global = global_research
        self._ticker = ticker_pipeline
        self._document2 = document2
        self._document3 = document3

    async def run(
        self,
        *,
        run_id: str,
        market: str,
        ticker: str,
        as_of: datetime,
        global_request: GlobalResearchRunRequest,
        export_dir: str | Path,
        resume_finalized_only: bool = False,
    ) -> InitializationOrchestrationState:
        if global_request.ticker.upper() != ticker.upper():
            raise ValueError("Global Research ticker does not match initialization ticker")
        if global_request.run_id == run_id:
            raise ValueError("parent and D1 child run IDs must be distinct")
        state = self._states.get(run_id) or InitializationOrchestrationState(
            run_id=run_id,
            market=market.upper(),
            ticker=ticker.upper(),
            as_of=as_of,
            stage=InitializationOrchestrationStage.CREATED,
            d1_run_id=global_request.run_id,
            updated_at=datetime.now(UTC),
        )
        if state.stage is InitializationOrchestrationStage.PUBLISHED:
            return state
        try:
            state = self._advance(state, InitializationOrchestrationStage.UPSTREAM_RUNNING)
            d1_result, cdecr_result = await asyncio.gather(
                self._global.run(global_request),
                self._ticker.prepare_runtime_through_delta(
                    market=market,
                    ticker=ticker,
                    as_of=as_of,
                    export_dir=export_dir,
                    resume_finalized_only=resume_finalized_only,
                ),
                return_exceptions=True,
            )
            failures = [item for item in (d1_result, cdecr_result) if isinstance(item, Exception)]
            if failures:
                raise RuntimeError("; ".join(str(item) for item in failures))
            if not isinstance(d1_result, GlobalResearchBundle):
                raise RuntimeError("Global Research child returned an invalid result")
            pipeline = cast(TickerPipelineResult, cdecr_result)
            if d1_result.published_at is None or d1_result.citation_manifest is None:
                raise RuntimeError("Global Research child is missing Published context")
            if not pipeline.job.epoch_id or not pipeline.job.runtime_snapshot_id:
                raise RuntimeError("CDECR child did not produce a frozen Runtime snapshot")
            if not pipeline.delta_batch_id:
                raise RuntimeError("CDECR child did not produce a Delta batch")
            manifest = O2UpstreamContextManifest(
                ticker=ticker.upper(),
                unified_as_of=as_of,
                d1_run_id=d1_result.run_id,
                d1_published_at=d1_result.published_at,
                research_artifacts={
                    role: d1_result.reports[role].model_dump(mode="json")
                    for role in ("c1", "c3", "c5")
                },
                entity_relations=[
                    item.model_dump(mode="json") for item in d1_result.entity_relations
                ],
                future_nodes=[item.model_dump(mode="json") for item in d1_result.future_nodes],
                citation_manifest=d1_result.citation_manifest.model_dump(mode="json"),
                cdecr_epoch_id=pipeline.job.epoch_id,
                runtime_snapshot_id=pipeline.job.runtime_snapshot_id,
                delta_batch_id=pipeline.delta_batch_id,
            )
            state = self._advance(
                state,
                InitializationOrchestrationStage.UPSTREAM_READY,
                cdecr_job_id=pipeline.job.job_id,
                o2_run_id=pipeline.job.o2_run_id,
            )
            state = self._advance(state, InitializationOrchestrationStage.O2_RUNNING)
            o2_result = await self._ticker.run_o2_with_upstream_context(
                market=market,
                ticker=ticker,
                as_of=as_of,
                export_dir=export_dir,
                upstream_context_manifest=manifest.model_dump(mode="json"),
                resume_finalized_only=True,
            )
            if o2_result.published_library_version is None:
                raise RuntimeError("O2 did not publish an Event Library version")
            reader = PublishedEventLibraryReader(self._ticker.event_library_root, market=market)
            reference = reader.reference_view(ticker, version=o2_result.published_library_version)
            if reference is None:
                raise RuntimeError("Published O2 reference view is unavailable")
            state = self._advance(
                state,
                InitializationOrchestrationStage.O2_PUBLISHED,
                event_library_version=reference.version,
                event_library_sha256=reference.sha256,
                event_library_published_at=reference.published_at,
            )
            state = self._advance(state, InitializationOrchestrationStage.D2_RUNNING)
            d2_run_id = await self._document2.run_pinned(
                source_global_run_id=d1_result.run_id,
                ticker=ticker,
                as_of=as_of,
                event_library_version=reference.version,
                event_library_sha256=reference.sha256,
                event_library_published_at=reference.published_at,
            )
            if self._document3 is None:
                return self._advance(
                    state,
                    InitializationOrchestrationStage.PUBLISHED,
                    d2_run_id=d2_run_id,
                )
            state = self._advance(
                state,
                InitializationOrchestrationStage.D3_RUNNING,
                d2_run_id=d2_run_id,
            )
            d3_run_id = await self._document3.run_pinned(
                document2_run_id=d2_run_id,
                ticker=ticker,
                as_of=as_of,
                event_library_version=reference.version,
            )
            state = self._advance(
                state,
                InitializationOrchestrationStage.D3_PUBLISHED,
                d3_run_id=d3_run_id,
            )
            return self._advance(state, InitializationOrchestrationStage.PUBLISHED)
        except Exception as exc:
            self._advance(
                state,
                InitializationOrchestrationStage.FAILED,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

    def _advance(
        self,
        state: InitializationOrchestrationState,
        stage: InitializationOrchestrationStage,
        **updates: object,
    ) -> InitializationOrchestrationState:
        updated = state.model_copy(
            update={"stage": stage, "updated_at": datetime.now(UTC), **updates}
        )
        self._states.save(updated)
        return updated
