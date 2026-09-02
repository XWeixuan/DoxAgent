"""Checkpointed Daily Close: Runtime candidates -> O2 -> O3 maintenance."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from doxagent.event_library.compiler import EventLibraryViewCompiler
from doxagent.event_library.contracts import (
    DeltaBatch,
    FrozenRuntimeAtomic,
    FrozenRuntimeSnapshot,
    OccurrenceDateCandidate,
    OccurrenceDateCandidateSource,
    PublicationResult,
    ReferenceViewDeltaSnapshot,
)
from doxagent.event_library.repository import EventLibraryRepository

from .projection import RuntimeV2ProjectionOutbox
from .repository import PersistentRuntimeV2Repository
from .schema import (
    DailyCloseRun,
    DailyCloseStage,
    O3MaintenanceFeed,
    ProvisionalFactDetail,
    utc_now,
)

_EASTERN = ZoneInfo("America/New_York")


class O2IncrementalRunner(Protocol):
    async def run(
        self,
        *,
        snapshot: FrozenRuntimeSnapshot,
        run_id: str,
        cutoff_at: datetime,
        export_dir: str | Path,
        mode: Literal["INITIALIZE", "INCREMENTAL"] = "INCREMENTAL",
        upstream_context_manifest: dict[str, Any] | None = None,
    ) -> tuple[DeltaBatch, PublicationResult | None, Any, dict[str, Path]]: ...


class O3DailyMaintainer(Protocol):
    async def maintain(
        self,
        *,
        ticker: str,
        event_library_version: int | None = None,
        run_id: str | None = None,
        cutoff_at: datetime | None = None,
        maintenance_feed: O3MaintenanceFeed | None = None,
    ) -> Any: ...


class RuntimeDeltaBatchAdapter:
    """Translate frozen Runtime V2 facts without impersonating CDECR identity."""

    def __init__(self, repository: PersistentRuntimeV2Repository) -> None:
        self.repository = repository

    def freeze(
        self,
        *,
        ticker: str,
        trading_date: date,
        candidates: list[ProvisionalFactDetail],
        as_of: datetime,
    ) -> FrozenRuntimeSnapshot:
        deduplicated: list[ProvisionalFactDetail] = []
        signatures: set[str] = set()
        for item in sorted(candidates, key=_provisional_number):
            if item.runtime_signature in signatures:
                continue
            signatures.add(item.runtime_signature)
            deduplicated.append(item)
        atomics = [self._atomic(item) for item in deduplicated]
        identity_payload = [
            {
                "runtime_atomic_id": item.runtime_atomic_id,
                "version": item.version,
                "proposition": item.proposition,
                "subject_time": item.subject_time,
                "occurrence_date_candidates": [
                    value.model_dump(mode="json")
                    for value in item.occurrence_date_candidates
                ],
                "assertion_state": item.assertion_state.value,
                "entities": item.entities,
            }
            for item in atomics
        ]
        digest = hashlib.sha256(
            json.dumps(
                identity_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:24]
        normalized = ticker.upper()
        return FrozenRuntimeSnapshot(
            contract_version="frozen-runtime-time-v2",
            snapshot_id=f"runtime-v2-close:{normalized}:{trading_date.isoformat()}:{digest}",
            runtime_scope=f"runtime-v2:{normalized}",
            epoch_id=f"runtime-v2-day:{normalized}:{trading_date.isoformat()}",
            market="US",
            ticker=normalized,
            as_of=as_of,
            atomics=atomics,
            packages=[],
        )

    def _atomic(self, item: ProvisionalFactDetail) -> FrozenRuntimeAtomic:
        case = self.repository.get_case_by_source(item.source_message_id)
        if case is None:
            raise ValueError("Runtime candidate source Case is unavailable")
        candidate = item.candidate
        if candidate.occurrence_date is not None:
            occurrence = OccurrenceDateCandidate(
                candidate_date=candidate.occurrence_date,
                source_kind=OccurrenceDateCandidateSource.RUNTIME_CONFIRMED_OCCURRENCE,
                source_id=item.provisional_event_id,
                source_message_id=item.source_message_id,
            )
        else:
            source_time = case.source.occurrence_source_time
            occurrence = OccurrenceDateCandidate(
                candidate_date=source_time.astimezone(_EASTERN).date(),
                source_kind=OccurrenceDateCandidateSource.SOURCE_PUBLISHED_AT,
                source_id=case.source.source_id,
                source_message_id=item.source_message_id,
            )
        return FrozenRuntimeAtomic(
            runtime_atomic_id=_candidate_key(item),
            version=1,
            proposition=candidate.proposition,
            time=None,
            subject_time=candidate.subject_time,
            occurrence_date_candidates=[occurrence],
            source_message_ids=[item.source_message_id],
            assertion_state=candidate.assertion_state,
            entities=candidate.entities,
            runtime_package_ids=[],
        )


class PersistentRuntimeV2DailyCloseService:
    def __init__(
        self,
        *,
        repository: PersistentRuntimeV2Repository,
        event_repository: EventLibraryRepository,
        o2_runner: O2IncrementalRunner,
        o3_maintainer: O3DailyMaintainer,
        export_root: str | Path,
        projection_outbox: RuntimeV2ProjectionOutbox | None = None,
    ) -> None:
        self.repository = repository
        self.event_repository = event_repository
        self.o2_runner = o2_runner
        self.o3_maintainer = o3_maintainer
        self.export_root = Path(export_root)
        self.adapter = RuntimeDeltaBatchAdapter(repository)
        self.projection_outbox = projection_outbox

    async def close(
        self,
        *,
        ticker: str,
        trading_date: date,
        cutoff_at: datetime | None = None,
    ) -> DailyCloseRun:
        normalized = ticker.upper()
        cutoff = cutoff_at or utc_now()
        run = self.repository.get_daily_close(normalized, trading_date)
        if run is None:
            candidates = self.repository.list_daily_candidates(normalized, trading_date)
            trades = self.repository.list_daily_trades(normalized, trading_date)
            badcases = self.repository.list_daily_badcases(normalized, trading_date)
            w3_gaps = self.repository.list_daily_w3_coverage_gaps(
                normalized, trading_date
            )
            slug = f"{normalized.lower()}-{trading_date.isoformat()}"
            run = DailyCloseRun(
                run_id=f"runtime-v2-close-{slug}",
                ticker=normalized,
                trading_date=trading_date,
                stage=DailyCloseStage.PREPARED,
                base_library_version=self.event_repository.published_version(normalized),
                candidate_keys=[_candidate_key(item) for item in candidates],
                trade_record_ids=[item.trade_record_id for item in trades],
                badcase_ids=[item.badcase_id for item in badcases],
                w3_coverage_gap_ids=[item.coverage_gap_id for item in w3_gaps],
                o2_run_id=f"runtime-v2-o2-{slug}",
                o3_run_id=f"runtime-v2-o3-{slug}",
            )
            self.repository.save_daily_close(run)
        if run.stage is DailyCloseStage.COMPLETED:
            return run

        try:
            candidates = self._frozen_candidates(run)
            if run.stage is DailyCloseStage.PREPARED:
                published_version = run.base_library_version
                batch_id: str | None = None
                if candidates:
                    snapshot = self.adapter.freeze(
                        ticker=normalized,
                        trading_date=trading_date,
                        candidates=candidates,
                        as_of=cutoff,
                    )
                    batch, publication, _validation, _exports = await self.o2_runner.run(
                        snapshot=snapshot,
                        run_id=run.o2_run_id,
                        cutoff_at=cutoff,
                        export_dir=self.export_root / run.o2_run_id,
                        mode="INCREMENTAL",
                    )
                    batch_id = batch.batch_id
                    if publication is not None:
                        published_version = publication.published_library_version
                run = self._save(
                    run,
                    stage=DailyCloseStage.O2_COMPLETED,
                    published_library_version=published_version,
                    delta_batch_id=batch_id,
                )

            feed = self._feed(run)
            if run.stage is DailyCloseStage.O2_COMPLETED:
                run = self._save(run, stage=DailyCloseStage.FEED_ASSEMBLED)
            if run.stage is DailyCloseStage.FEED_ASSEMBLED:
                if (
                    not feed.reference_view_delta.reference_view_delta.strip()
                    and not feed.reference_view_delta.removed_event_ids
                    and not feed.trade_records
                    and not feed.badcase_records
                    and not feed.w3_coverage_gaps
                ):
                    result_payload = {"status": "NOOP", "reason": "empty_daily_feed"}
                else:
                    result = await self.o3_maintainer.maintain(
                        ticker=normalized,
                        event_library_version=run.published_library_version,
                        run_id=run.o3_run_id,
                        cutoff_at=cutoff,
                        maintenance_feed=feed,
                    )
                    result_payload = (
                        result.model_dump(mode="json")
                        if hasattr(result, "model_dump")
                        else {"result": str(result)}
                    )
                run = self._save(
                    run,
                    stage=DailyCloseStage.O3_COMPLETED,
                    o3_result=result_payload,
                )
            if run.stage is DailyCloseStage.O3_COMPLETED:
                self.repository.mark_daily_records_processed(
                    candidate_keys=run.candidate_keys,
                    trade_record_ids=run.trade_record_ids,
                    badcase_ids=run.badcase_ids,
                    w3_coverage_gap_ids=run.w3_coverage_gap_ids,
                )
                run = self._save(run, stage=DailyCloseStage.COMPLETED)
                if self.projection_outbox is not None:
                    self.projection_outbox.enqueue_daily(run)
                    try:
                        self.projection_outbox.flush(limit=20)
                    except Exception:
                        # The local outbox is authoritative for retry; remote projection
                        # availability must not roll back a completed Daily Close.
                        pass
            return run
        except Exception as exc:
            self.repository.save_daily_close(
                run.model_copy(
                    update={
                        "error": f"{type(exc).__name__}: {str(exc)[:1000]}",
                        "updated_at": utc_now(),
                    }
                )
            )
            raise

    def _frozen_candidates(self, run: DailyCloseRun) -> list[ProvisionalFactDetail]:
        wanted = set(run.candidate_keys)
        return [
            item
            for item in self.repository.list_provisional(run.ticker, run.trading_date)
            if _candidate_key(item) in wanted
        ]

    def _feed(self, run: DailyCloseRun) -> O3MaintenanceFeed:
        to_version = run.published_library_version
        if to_version is None:
            raise ValueError("Daily Close does not have an O2 checkpoint")
        if to_version > run.base_library_version:
            delta = EventLibraryViewCompiler(self.event_repository).reference_view_delta(
                run.ticker,
                from_version=run.base_library_version,
                to_version=to_version,
                persist=True,
            )
        else:
            delta = ReferenceViewDeltaSnapshot(
                ticker=run.ticker,
                from_library_version=to_version,
                to_library_version=to_version,
                reference_view_delta="",
                removed_event_ids=[],
            )
        trade_ids = set(run.trade_record_ids)
        badcase_ids = set(run.badcase_ids)
        gap_ids = set(run.w3_coverage_gap_ids)
        return O3MaintenanceFeed(
            ticker=run.ticker,
            trading_date=run.trading_date,
            reference_view_delta=delta,
            trade_records=[
                item
                for item in self.repository.list_daily_trades(
                    run.ticker, run.trading_date
                )
                if item.trade_record_id in trade_ids
            ],
            badcase_records=[
                item
                for item in self.repository.list_daily_badcases(
                    run.ticker, run.trading_date
                )
                if item.badcase_id in badcase_ids
            ],
            w3_coverage_gaps=[
                item
                for item in self.repository.list_daily_w3_coverage_gaps(
                    run.ticker, run.trading_date
                )
                if item.coverage_gap_id in gap_ids
            ],
        )

    def _save(self, run: DailyCloseRun, **updates: Any) -> DailyCloseRun:
        updated = run.model_copy(
            update={**updates, "error": None, "updated_at": utc_now()}
        )
        return self.repository.save_daily_close(updated)


def _candidate_key(item: ProvisionalFactDetail) -> str:
    return f"runtime-v2-msg:{item.source_message_id}:{item.candidate_index}"


def _provisional_number(item: ProvisionalFactDetail) -> int:
    return int(item.provisional_event_id[1:])
