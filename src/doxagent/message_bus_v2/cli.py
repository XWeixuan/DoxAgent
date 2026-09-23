"""Independent Message Bus v2 worker CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
from pathlib import Path
from typing import Any

import yaml

from doxagent.message_bus_v2.factory import build_message_bus_v2_runtime
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService, TickerMonitoringTerms
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import AcquisitionMode, RawMessageInput
from doxagent.message_bus_v2.search_plan import build_query_plan
from doxagent.settings import DoxAgentSettings


async def _run_worker(settings: DoxAgentSettings, *, once: bool) -> int:
    if not settings.message_bus_v2_enabled:
        if once:
            return 0
        stop = _worker_stop_event()
        print("Message Bus v2 is disabled; worker is idle.")
        await stop.wait()
        return 0
    runtime = build_message_bus_v2_runtime(settings)
    try:
        if once:
            results = await runtime.scheduler.run_once()
            print(json.dumps([item.model_dump(mode="json") for item in results]))
            return 0
        stop = _worker_stop_event()
        await runtime.scheduler.run_forever(
            loop_sleep_seconds=settings.message_bus_v2_worker_sleep_seconds,
            stop=stop,
        )
        return 0
    finally:
        await runtime.close()


def _worker_stop_event() -> asyncio.Event:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        resolved = getattr(signal, name, None)
        if resolved is not None:
            try:
                loop.add_signal_handler(resolved, stop.set)
            except NotImplementedError:
                pass
    return stop


def main() -> None:
    parser = argparse.ArgumentParser(description="DoxAgent Message Bus v2 worker")
    parser.add_argument(
        "command",
        choices=("run-worker", "run-once", "status", "terms", "distribution", "migration"),
    )
    parser.add_argument("action", nargs="?")
    parser.add_argument("--file")
    parser.add_argument("--ticker")
    parser.add_argument("--source")
    parser.add_argument("--actor", default="user")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--backup-dir")
    parser.add_argument("--article")
    parser.add_argument("--language")
    args = parser.parse_args()
    settings = DoxAgentSettings()
    output: Any
    if args.command == "migration":
        from doxagent.message_bus_v2.migration import apply, preview

        if args.action == "preview":
            output = preview(settings.message_bus_v2_sqlite_path)
        elif args.action == "apply":
            output = apply(settings.message_bus_v2_sqlite_path, backup_dir=args.backup_dir)
        else:
            parser.error("migration action must be preview/apply")
        print(json.dumps(output, ensure_ascii=False))
        return
    if args.command == "terms":
        repository = MessageBusV2Repository(settings.message_bus_v2_sqlite_path)
        service = MonitoringTermsService(repository)
        if args.action in {"validate", "apply"}:
            if not args.file:
                parser.error("--file is required")
            data = yaml.safe_load(Path(args.file).read_text(encoding="utf-8"))
            terms = TickerMonitoringTerms.model_validate(data)
            terms.validate_languages(service.required_languages())
            revision = (
                service.apply(terms, actor=args.actor)
                if args.action == "apply"
                else terms.expected_revision + 1
            )
            output = {
                "ticker": terms.ticker,
                "revision": revision,
                "languages": sorted(service.required_languages() | {"en"}),
            }
            output["search_plans"] = [
                build_query_plan(source, terms, revision).model_dump(mode="json")
                for source in repository.list_sources()
                if source.acquisition_mode is AcquisitionMode.BY_SEARCH and source.enabled
            ]
        elif args.action == "test" and args.ticker and args.article:
            from doxagent.message_bus_v2.relevance import regex_relevant

            found = service.get(args.ticker)
            if found is None:
                parser.error("ticker has no submitted monitoring terms")
            article = RawMessageInput.model_validate_json(
                Path(args.article).read_text(encoding="utf-8")
            )
            source = repository.get_source(args.source) if args.source else None
            language = args.language or (source.content_language if source else None) or "en"
            output = {
                "ticker": args.ticker.upper(),
                "revision": found[0],
                "language": language,
                "regex_relevant": regex_relevant(found[1], language, article),
            }
        elif args.action == "history" and args.ticker:
            output = service.history(args.ticker)
        elif args.action in {"show", "preview"} and args.ticker:
            found = service.get(args.ticker)
            output = (
                None
                if found is None
                else (
                    {"revision": found[0], "terms": found[1].model_dump(mode="json")}
                    if args.action == "show"
                    else [
                        build_query_plan(source, found[1], found[0]).model_dump(mode="json")
                        for source in repository.list_sources()
                        if source.acquisition_mode is AcquisitionMode.BY_SEARCH and source.enabled
                    ]
                )
            )
        else:
            parser.error("terms action must be validate/apply/show/history/preview/test")
        print(json.dumps(output, ensure_ascii=False))
        return
    if args.command == "distribution":
        from doxagent.message_bus_v2.distribution_repository import DistributionRepository

        distribution_repository = DistributionRepository(
            MessageBusV2Repository(settings.message_bus_v2_sqlite_path)
        )
        if args.action == "status" and args.source:
            output = {
                "summary": distribution_repository.summary(args.source),
                "runs": distribution_repository.status(args.source),
            }
        elif args.action == "decisions" and args.ticker:
            output = distribution_repository.decisions(args.ticker, limit=args.limit)
        else:
            parser.error("distribution action must be status --source or decisions --ticker")
        print(json.dumps(output, ensure_ascii=False))
        return
    if args.command == "status":
        if not settings.message_bus_v2_enabled:
            print(
                json.dumps(
                    {
                        "enabled": False,
                        "sqlite_path": settings.message_bus_v2_sqlite_path,
                        "counts": {},
                    }
                )
            )
            return
        runtime = build_message_bus_v2_runtime(settings)
        try:
            print(
                json.dumps(
                    {
                        "enabled": settings.message_bus_v2_enabled,
                        "sqlite_path": settings.message_bus_v2_sqlite_path,
                        "counts": runtime.repository.snapshot_counts(),
                    }
                )
            )
        finally:
            asyncio.run(runtime.close())
        return
    raise SystemExit(asyncio.run(_run_worker(settings, once=args.command == "run-once")))


if __name__ == "__main__":
    main()
