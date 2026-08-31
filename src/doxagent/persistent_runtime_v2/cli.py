"""Explicit operational commands for Persistent Runtime V2."""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime
from pathlib import Path

from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.event_library.service import EventLibraryService
from doxagent.settings import DoxAgentSettings
from doxagent.workflows.codex_document3.service import build_document3_orchestrator
from doxagent.workflows.codex_event_library.remote_runner import (
    RemoteEventLibraryInitializer,
)

from .daily import PersistentRuntimeV2DailyCloseService
from .factory import build_persistent_runtime_v2_service
from .projection import RuntimeV2ProjectionOutbox


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m doxagent.persistent_runtime_v2.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    close = sub.add_parser("daily-close", help="Run/resume one explicit ET trading-day close")
    close.add_argument("--ticker", required=True)
    close.add_argument("--trading-date", required=True, type=date.fromisoformat)
    close.add_argument("--cutoff-at", type=datetime.fromisoformat)
    close.add_argument("--export-root", default=".tmp/persistent-runtime-v2-daily")
    return parser


def _event_repository(settings: DoxAgentSettings, ticker: str) -> EventLibraryRepository:
    if not settings.event_library_root:
        raise ValueError("Daily Close requires DOXAGENT_EVENT_LIBRARY_ROOT")
    path = (
        Path(settings.event_library_root)
        / settings.event_library_market.upper()
        / ticker.upper()
        / "event_library.sqlite3"
    )
    return EventLibraryRepository(path)


async def _run(args: argparse.Namespace) -> int:
    settings = DoxAgentSettings()
    runtime = build_persistent_runtime_v2_service(settings)
    if not settings.codex_worker_bearer_token or not settings.codex_capability_secret:
        raise ValueError("Daily Close requires the configured Codex worker credentials")
    event_repository = _event_repository(settings, args.ticker)
    worker = HttpCodexWorkerClient(
        settings.codex_worker_base_url,
        settings.codex_worker_bearer_token,
        capability_secret=settings.codex_capability_secret,
    )
    o2 = RemoteEventLibraryInitializer(
        worker=worker,
        workspace=worker,
        service=EventLibraryService(event_repository),
        local_workspace_root=settings.codex_workspace_root,
        model=settings.codex_model,
        model_provider=settings.codex_model_provider,
        effort=settings.codex_reasoning_effort,
        timeout_seconds=settings.codex_node_timeout_seconds,
    )
    projection_outbox = None
    if settings.persistent_runtime_v2_remote_projection_enabled:
        if not settings.database_url:
            raise ValueError("Remote Runtime projection requires DOXAGENT_DATABASE_URL")
        projection_outbox = RuntimeV2ProjectionOutbox(
            settings.persistent_runtime_v2_sqlite_path,
            database_url=settings.database_url,
        )
    daily = PersistentRuntimeV2DailyCloseService(
        repository=runtime.repository,
        event_repository=event_repository,
        o2_runner=o2,
        o3_maintainer=build_document3_orchestrator(settings),
        export_root=args.export_root,
        projection_outbox=projection_outbox,
    )
    result = await daily.close(
        ticker=args.ticker,
        trading_date=args.trading_date,
        cutoff_at=args.cutoff_at,
    )
    print(result.model_dump_json(indent=2))
    return 0


def main() -> int:
    return asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
