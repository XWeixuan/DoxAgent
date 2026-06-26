"""Single-threaded replay injector for Persistent Runtime Execution datasets."""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from doxagent.agents.runner import default_real_agent_runner
from doxagent.monitoring.schema import EventStreamItem, SourceType
from doxagent.persistent_runtime.datasets import (
    build_dataset,
    compact_runtime_context,
    count_monitoring_sqlite_events,
    event_observed_at,
    event_source_type,
    export_monitoring_sqlite_dataset,
    fetch_live_dataset,
    load_runtime_context_from_exports,
    read_dataset,
    write_dataset,
)
from doxagent.persistent_runtime.schema import (
    JsonObject,
    RuntimeExecutionRecord,
    RuntimeSourceMessage,
)
from doxagent.persistent_runtime.service import PersistentRuntimeExecutionService
from doxagent.persistent_runtime.workers import (
    AgentRunnerA2Worker,
    AgentRunnerO3Worker,
    AgentRunnerW1Worker,
    AgentRunnerW2Worker,
)
from doxagent.settings import DoxAgentSettings


class RuntimeReplayService(Protocol):
    def execute_event(
        self,
        event: EventStreamItem,
        *,
        context: JsonObject | None = None,
        mark_consumed: Callable[[str], object] | None = None,
    ) -> RuntimeExecutionRecord:
        ...

    def execute_social_batch(
        self,
        messages: list[RuntimeSourceMessage],
        *,
        ticker: str,
        batch_window_id: str,
        context: JsonObject | None = None,
    ) -> list[RuntimeExecutionRecord]:
        ...


@dataclass
class ReplaySummary:
    input_events: int = 0
    media_events: int = 0
    social_events: int = 0
    social_batches: int = 0
    records: int = 0
    dry_run: bool = False
    processed_event_ids: list[str] = field(default_factory=list)
    batch_window_ids: list[str] = field(default_factory=list)


class RuntimeDatasetReplayer:
    """Injects monitoring events one at a time in dataset order."""

    def __init__(
        self,
        service: RuntimeReplayService,
        *,
        context: JsonObject | None = None,
        mark_consumed: Callable[[str], object] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.service = service
        self.context = context or {}
        self.mark_consumed = mark_consumed
        self.sleep = sleep

    def replay(
        self,
        events: list[EventStreamItem],
        *,
        source_type: SourceType | None = None,
        interval_seconds: float = 0.0,
        speedup: float | None = None,
        max_interval_seconds: float | None = None,
        dry_run: bool = False,
        progress: Callable[[ReplaySummary], None] | None = None,
    ) -> ReplaySummary:
        ordered = build_dataset(
            events,
            ticker=events[0].ticker if events else "UNKNOWN",
            source_types=[source_type] if source_type else [SourceType.MEDIA, SourceType.SOCIAL],
        ).events
        summary = ReplaySummary(input_events=len(ordered), dry_run=dry_run)
        social_buffer: list[EventStreamItem] = []
        social_window_id: str | None = None
        previous_event: EventStreamItem | None = None
        for event in ordered:
            event_type = event_source_type(event)
            if source_type is not None and event_type is not source_type:
                continue
            sleep_ms = self._sleep_before_event(
                previous_event=previous_event,
                event=event,
                interval_seconds=interval_seconds,
                speedup=speedup,
                max_interval_seconds=max_interval_seconds,
            )
            previous_event = event
            if event_type is SourceType.SOCIAL:
                window_id = batch_window_id_for_event(event)
                if social_buffer and social_window_id != window_id:
                    summary.records += self._flush_social_batch(
                        social_buffer,
                        batch_window_id=str(social_window_id),
                        summary=summary,
                        dry_run=dry_run,
                    )
                    social_buffer = []
                social_window_id = window_id
                social_buffer.append(event)
                summary.social_events += 1
                continue
            if social_buffer:
                summary.records += self._flush_social_batch(
                    social_buffer,
                    batch_window_id=str(social_window_id),
                    summary=summary,
                    dry_run=dry_run,
                )
                social_buffer = []
                social_window_id = None
            summary.media_events += 1
            summary.processed_event_ids.append(event.event_id)
            if dry_run:
                if progress is not None:
                    progress(summary)
                continue
            replay_started_at = datetime.now(UTC)
            replay_started = time.monotonic()
            record = self.service.execute_event(
                event,
                context=self.context,
                mark_consumed=self.mark_consumed,
            )
            record = self._save_record_timing(
                record,
                {
                    "replay_layer": {
                        "started_at": _dt_json(replay_started_at),
                        "completed_at": _dt_json(datetime.now(UTC)),
                        "sleep_ms": sleep_ms,
                        "service_execute_event_ms": _duration_ms(replay_started),
                        "replay_total_ms": sleep_ms + _duration_ms(replay_started),
                        "source_type": event_type.value,
                    }
                },
            )
            summary.records += 1 if record else 0
            if progress is not None:
                progress(summary)
        if social_buffer:
            summary.records += self._flush_social_batch(
                social_buffer,
                batch_window_id=str(social_window_id),
                summary=summary,
                dry_run=dry_run,
            )
            if progress is not None:
                progress(summary)
        return summary

    def _sleep_before_event(
        self,
        *,
        previous_event: EventStreamItem | None,
        event: EventStreamItem,
        interval_seconds: float,
        speedup: float | None,
        max_interval_seconds: float | None,
    ) -> int:
        delay = max(0.0, interval_seconds)
        if previous_event is not None and speedup is not None and speedup > 0:
            original_delta = (
                event_observed_at(event) - event_observed_at(previous_event)
            ).total_seconds()
            delay = max(0.0, original_delta / speedup)
        if max_interval_seconds is not None:
            delay = min(delay, max(0.0, max_interval_seconds))
        if delay > 0:
            started = time.monotonic()
            self.sleep(delay)
            return _duration_ms(started)
        return 0

    def _flush_social_batch(
        self,
        events: list[EventStreamItem],
        *,
        batch_window_id: str,
        summary: ReplaySummary,
        dry_run: bool,
    ) -> int:
        if not events:
            return 0
        summary.social_batches += 1
        summary.batch_window_ids.append(batch_window_id)
        summary.processed_event_ids.extend(event.event_id for event in events)
        if dry_run:
            return len(events)
        replay_started_at = datetime.now(UTC)
        replay_started = time.monotonic()
        records = self.service.execute_social_batch(
            [RuntimeSourceMessage.from_event(event) for event in events],
            ticker=events[0].ticker,
            batch_window_id=batch_window_id,
            context=self.context,
        )
        batch_ms = _duration_ms(replay_started)
        if self.mark_consumed is not None:
            for event in events:
                self.mark_consumed(event.event_id)
        for record in records:
            self._save_record_timing(
                record,
                {
                    "replay_layer": {
                        "started_at": _dt_json(replay_started_at),
                        "completed_at": _dt_json(datetime.now(UTC)),
                        "batch_window_id": batch_window_id,
                        "batch_size": len(events),
                        "service_execute_social_batch_ms": batch_ms,
                        "source_type": SourceType.SOCIAL.value,
                    }
                },
            )
        return len(records)

    def _save_record_timing(
        self,
        record: RuntimeExecutionRecord,
        patch: JsonObject,
    ) -> RuntimeExecutionRecord:
        repository = getattr(self.service, "repository", None)
        save_execution = getattr(repository, "save_execution", None)
        if not callable(save_execution):
            return record
        timing = {**dict(record.timing), **patch}
        updated = record.model_copy(
            update={"timing": timing, "updated_at": datetime.now(UTC)},
            deep=True,
        )
        return cast(RuntimeExecutionRecord, save_execution(updated))


def batch_window_id_for_event(event: EventStreamItem) -> str:
    metadata = event.payload.get("metadata")
    if isinstance(metadata, dict):
        for key in ("batch_window_id", "polling_window_id", "poll_window_id"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return event_observed_at(event).strftime("%Y%m%d%H%M")


class _DryRunReplayService:
    def execute_event(
        self,
        event: EventStreamItem,
        *,
        context: JsonObject | None = None,
        mark_consumed: Callable[[str], object] | None = None,
    ) -> RuntimeExecutionRecord:
        raise RuntimeError("dry-run replay service must not execute media events.")

    def execute_social_batch(
        self,
        messages: list[RuntimeSourceMessage],
        *,
        ticker: str,
        batch_window_id: str,
        context: JsonObject | None = None,
    ) -> list[RuntimeExecutionRecord]:
        raise RuntimeError("dry-run replay service must not execute social batches.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare or dry-run Persistent Runtime Execution replay datasets."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_sqlite_parser = subparsers.add_parser("inspect-sqlite")
    inspect_sqlite_parser.add_argument("--sqlite-path", type=Path, default=None)
    inspect_sqlite_parser.add_argument("--ticker", required=True)
    inspect_sqlite_parser.add_argument("--days", type=int, default=7)
    _add_source_type_arg(inspect_sqlite_parser)

    export_sqlite_parser = subparsers.add_parser("export-sqlite")
    export_sqlite_parser.add_argument("--sqlite-path", type=Path, default=None)
    export_sqlite_parser.add_argument("--ticker", required=True)
    export_sqlite_parser.add_argument("--days", type=int, default=7)
    _add_source_type_arg(export_sqlite_parser)
    export_sqlite_parser.add_argument("--out", required=True, type=Path)

    fetch_live_parser = subparsers.add_parser("fetch-live")
    fetch_live_parser.add_argument("--ticker", required=True)
    fetch_live_parser.add_argument("--days", type=int, default=7)
    _add_source_type_arg(fetch_live_parser)
    fetch_live_parser.add_argument("--source-id", action="append", default=[])
    fetch_live_parser.add_argument("--search-term", action="append", default=[])
    fetch_live_parser.add_argument("--username", action="append", default=[])
    fetch_live_parser.add_argument("--rss-url", action="append", default=[])
    fetch_live_parser.add_argument("--limit-per-source", type=int)
    fetch_live_parser.add_argument("--out", required=True, type=Path)

    inspect_parser = subparsers.add_parser("inspect-dataset")
    inspect_parser.add_argument("dataset", type=Path)
    inspect_parser.add_argument("--manifest", type=Path)

    dry_run_parser = subparsers.add_parser("dry-run")
    dry_run_parser.add_argument("dataset", type=Path)
    dry_run_parser.add_argument("--manifest", type=Path)
    dry_run_parser.add_argument("--source-type", choices=[item.value for item in SourceType])
    dry_run_parser.add_argument("--interval-ms", type=int, default=0)
    dry_run_parser.add_argument("--speedup", type=float)
    dry_run_parser.add_argument("--max-interval-ms", type=int)

    replay_parser = subparsers.add_parser("replay")
    replay_parser.add_argument("dataset", type=Path)
    replay_parser.add_argument("--manifest", type=Path)
    replay_parser.add_argument("--source-run-export", required=True, type=Path)
    replay_parser.add_argument("--document3-export", type=Path)
    replay_parser.add_argument("--runtime-sqlite-path", required=True, type=Path)
    _add_source_type_arg(replay_parser)
    replay_parser.add_argument("--interval-ms", type=int, default=0)
    replay_parser.add_argument("--speedup", type=float)
    replay_parser.add_argument("--max-interval-ms", type=int)
    replay_parser.add_argument("--progress-every", type=int, default=1)
    replay_parser.add_argument(
        "--context-mode",
        choices=["compact", "full"],
        default="compact",
    )
    replay_parser.add_argument(
        "--worker-mode",
        choices=["heuristic", "real-experts", "real-all"],
        default="heuristic",
    )

    sample_parser = subparsers.add_parser("sample-dataset")
    sample_parser.add_argument("dataset", type=Path)
    sample_parser.add_argument("--manifest", type=Path)
    sample_parser.add_argument("--stride", type=int, default=5)
    sample_parser.add_argument("--offset", type=int, default=0)
    _add_source_type_arg(sample_parser)
    sample_parser.add_argument("--out", required=True, type=Path)

    context_parser = subparsers.add_parser("context")
    context_parser.add_argument("--source-run-export", required=True, type=Path)
    context_parser.add_argument("--document3-export", type=Path)

    copy_parser = subparsers.add_parser("normalize-dataset")
    copy_parser.add_argument("dataset", type=Path)
    copy_parser.add_argument("--manifest", type=Path)
    copy_parser.add_argument("--out", required=True, type=Path)

    args = parser.parse_args(argv)
    if args.command == "inspect-sqlite":
        source_type = _source_type_arg_value(args.source_type)
        sqlite_path = args.sqlite_path or Path(".tmp/monitoring_message_bus.sqlite3")
        count = count_monitoring_sqlite_events(
            sqlite_path,
            ticker=args.ticker,
            days=args.days,
            source_type=source_type,
        )
        print(
            {
                "sqlite_path": str(sqlite_path),
                "ticker": args.ticker.upper(),
                "days": args.days,
                "source_type": source_type.value if source_type else "both",
                "event_count": count,
            }
        )
        return 0
    if args.command == "export-sqlite":
        source_type = _source_type_arg_value(args.source_type)
        sqlite_path = args.sqlite_path or Path(".tmp/monitoring_message_bus.sqlite3")
        dataset = export_monitoring_sqlite_dataset(
            sqlite_path,
            ticker=args.ticker,
            days=args.days,
            source_type=source_type,
        )
        event_path, manifest_path = write_dataset(dataset, args.out)
        print(
            {
                "events_path": str(event_path),
                "manifest_path": str(manifest_path),
                "event_count": len(dataset.events),
                "source_type_counts": dataset.source_type_counts(),
            }
        )
        return 0
    if args.command == "fetch-live":
        source_type = _source_type_arg_value(args.source_type)
        result = fetch_live_dataset(
            ticker=args.ticker,
            days=args.days,
            source_type=source_type,
            source_ids=args.source_id,
            search_terms=args.search_term,
            usernames=args.username,
            rss_urls=args.rss_url,
            limit_per_source=args.limit_per_source,
        )
        event_path, manifest_path = write_dataset(result.dataset, args.out)
        print(
            {
                "events_path": str(event_path),
                "manifest_path": str(manifest_path),
                "event_count": len(result.dataset.events),
                "source_type_counts": result.dataset.source_type_counts(),
                "diagnostics": result.diagnostics,
            }
        )
        return 0
    if args.command == "inspect-dataset":
        dataset = read_dataset(args.dataset, manifest_path=args.manifest)
        print(
            {
                "dataset_id": dataset.manifest.dataset_id,
                "ticker": dataset.manifest.ticker,
                "event_count": len(dataset.events),
                "source_type_counts": dataset.source_type_counts(),
                "window_start": dataset.manifest.window_start,
                "window_end": dataset.manifest.window_end,
            }
        )
        return 0
    if args.command == "dry-run":
        dataset = read_dataset(args.dataset, manifest_path=args.manifest)
        source_type = SourceType(args.source_type) if args.source_type else None
        replayer = RuntimeDatasetReplayer(_DryRunReplayService())
        summary = replayer.replay(
            dataset.events,
            source_type=source_type,
            interval_seconds=args.interval_ms / 1000,
            speedup=args.speedup,
            max_interval_seconds=None
            if args.max_interval_ms is None
            else args.max_interval_ms / 1000,
            dry_run=True,
        )
        print(summary)
        return 0
    if args.command == "replay":
        dataset = read_dataset(args.dataset, manifest_path=args.manifest)
        source_type = _source_type_arg_value(args.source_type)
        context_bundle = load_runtime_context_from_exports(
            source_run_export=args.source_run_export,
            document3_export=args.document3_export,
        )
        runtime_context = (
            context_bundle.context
            if args.context_mode == "full"
            else compact_runtime_context(context_bundle.context)
        )
        settings = DoxAgentSettings(
            persistent_runtime_storage_mode="sqlite",
            persistent_runtime_sqlite_path=str(args.runtime_sqlite_path),
        )
        service = _service_for_worker_mode(args.worker_mode, settings)
        progress_every = max(1, int(args.progress_every))

        def report_progress(summary: ReplaySummary) -> None:
            processed = summary.media_events + summary.social_events
            if processed % progress_every != 0 and processed != summary.input_events:
                return
            print(
                json.dumps(
                    _compact_summary(
                        summary,
                        runtime_sqlite_path=args.runtime_sqlite_path,
                    ),
                    sort_keys=True,
                ),
                flush=True,
            )

        replayer = RuntimeDatasetReplayer(service, context=runtime_context)
        summary = replayer.replay(
            dataset.events,
            source_type=source_type,
            interval_seconds=args.interval_ms / 1000,
            speedup=args.speedup,
            max_interval_seconds=None
            if args.max_interval_ms is None
            else args.max_interval_ms / 1000,
            progress=report_progress,
        )
        print(
            json.dumps(
                {
                    "summary": _compact_summary(
                        summary,
                        runtime_sqlite_path=args.runtime_sqlite_path,
                    ),
                    "runtime_counts": {
                        "executions": len(
                            service.repository.list_executions(ticker=dataset.manifest.ticker)
                        ),
                        "trading_records": len(
                            service.repository.list_trading_records(ticker=dataset.manifest.ticker)
                        ),
                        "ingest_queue": len(
                            service.repository.list_ingest_queue(ticker=dataset.manifest.ticker)
                        ),
                        "archive": len(
                            service.repository.list_archive(ticker=dataset.manifest.ticker)
                        ),
                        "exceptions": len(
                            service.repository.list_exceptions(ticker=dataset.manifest.ticker)
                        ),
                    },
                    "context": {
                        "ticker": context_bundle.ticker,
                        "source_run_id": context_bundle.source_run_id,
                        "document3_run_id": context_bundle.document3_run_id,
                        "context_mode": args.context_mode,
                        "missing": context_bundle.missing,
                        "diagnostics": context_bundle.diagnostics,
                    },
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "sample-dataset":
        dataset = read_dataset(args.dataset, manifest_path=args.manifest)
        if args.stride < 1:
            raise ValueError("--stride must be >= 1.")
        source_type = _source_type_arg_value(args.source_type)
        filtered = [
            event
            for event in dataset.events
            if source_type is None or event_source_type(event) is source_type
        ]
        sampled = [
            event
            for index, event in enumerate(filtered)
            if (index - args.offset) % args.stride == 0
        ]
        output = build_dataset(
            sampled,
            ticker=dataset.manifest.ticker,
            source_types=[source_type] if source_type else dataset.manifest.source_types,
            window_start=dataset.manifest.window_start,
            window_end=dataset.manifest.window_end,
            source={
                "type": "sampled_dataset",
                "parent_dataset_id": dataset.manifest.dataset_id,
                "parent_event_count": len(dataset.events),
                "stride": args.stride,
                "offset": args.offset,
            },
            notes=[f"sampled every {args.stride} event(s) for replay smoke testing."],
        )
        event_path, manifest_path = write_dataset(output, args.out)
        print(
            {
                "events_path": str(event_path),
                "manifest_path": str(manifest_path),
                "event_count": len(output.events),
                "source_type_counts": output.source_type_counts(),
            }
        )
        return 0
    if args.command == "context":
        bundle = load_runtime_context_from_exports(
            source_run_export=args.source_run_export,
            document3_export=args.document3_export,
        )
        print(bundle.model_dump_json(indent=2))
        return 0
    if args.command == "normalize-dataset":
        dataset = read_dataset(args.dataset, manifest_path=args.manifest)
        write_dataset(dataset, args.out)
        print({"out": str(args.out), "events": len(dataset.events)})
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


def _add_source_type_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-type", choices=[item.value for item in SourceType])


def _source_type_arg_value(value: str | None) -> SourceType | None:
    return SourceType(value) if value else None


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _dt_json(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _compact_summary(
    summary: ReplaySummary,
    *,
    runtime_sqlite_path: Path,
) -> dict[str, object]:
    return {
        "runtime_sqlite_path": str(runtime_sqlite_path),
        "input_events": summary.input_events,
        "media_events": summary.media_events,
        "social_events": summary.social_events,
        "social_batches": summary.social_batches,
        "records": summary.records,
        "dry_run": summary.dry_run,
        "processed": summary.media_events + summary.social_events,
    }


def _service_for_worker_mode(
    worker_mode: str,
    settings: DoxAgentSettings,
) -> PersistentRuntimeExecutionService:
    if worker_mode == "heuristic":
        return PersistentRuntimeExecutionService.from_settings(settings)
    runner = default_real_agent_runner(settings=settings)
    if worker_mode == "real-experts":
        return PersistentRuntimeExecutionService.from_settings(
            settings,
            a2_worker=AgentRunnerA2Worker(runner),
            o3_worker=AgentRunnerO3Worker(runner),
        )
    if worker_mode == "real-all":
        return PersistentRuntimeExecutionService.from_settings(
            settings,
            w1_worker=AgentRunnerW1Worker(runner),
            w2_worker=AgentRunnerW2Worker(runner),
            a2_worker=AgentRunnerA2Worker(runner),
            o3_worker=AgentRunnerO3Worker(runner),
        )
    raise ValueError(f"Unsupported worker mode: {worker_mode}")


__all__ = [
    "ReplaySummary",
    "RuntimeDatasetReplayer",
    "RuntimeReplayService",
    "batch_window_id_for_event",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
