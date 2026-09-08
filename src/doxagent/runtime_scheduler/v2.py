"""Managed V2 scheduler assembly. No legacy workflow or execution service is constructed."""

import argparse
import logging
import signal
from pathlib import Path
from threading import Event

from doxagent.message_bus_v2.factory import build_message_bus_v2_service
from doxagent.persistent_runtime_v2.factory import build_persistent_runtime_v2_service
from doxagent.settings import DoxAgentSettings
from doxagent.trade_execution.worker import WriterLock

from .repository import SQLiteRuntimeSchedulerRepository
from .service import UnifiedRuntimeSchedulerService


class ManagedDocuments:
    def __getattr__(self, name):
        raise RuntimeError(
            "V2 requires an admitted immutable activation; legacy documents disabled"
        )


class V2Scheduler(UnifiedRuntimeSchedulerService):
    def _run_admitted_once(self, admitted, *, now, event_limit):
        from doxagent.v2_control.repository import ControlRepository

        control = ControlRepository(self.runtime_v2_service.journal)
        result = []
        for ticker in sorted(admitted or ()):
            state = control.get(ticker)
            if not state or not state["analysis_allowed"]:
                continue
            try:
                result.append(self.tick_ticker(ticker, now=now, event_limit=event_limit))
            except Exception:
                logging.getLogger(__name__).exception("V2 ticker tick deferred: %s", ticker)
        return result


def build(settings=None):
    settings = settings or DoxAgentSettings()
    if not settings.ticker_initialization_control_path or not settings.message_bus_v2_enabled:
        raise ValueError("V2 scheduler requires managed initialization and Message Bus V2")
    if settings.runtime_scheduler_storage_mode == "memory":
        raise ValueError("V2 scheduler requires durable SQLite storage")
    for path in (
        settings.ticker_initialization_control_path,
        settings.runtime_scheduler_sqlite_path,
        settings.persistent_runtime_v2_sqlite_path,
    ):
        if not Path(path).is_file():
            raise ValueError("V2 source databases must be provisioned and migrated first")
    _, bus = build_message_bus_v2_service(settings)
    runtime = build_persistent_runtime_v2_service(settings)
    service = V2Scheduler(
        SQLiteRuntimeSchedulerRepository(settings.runtime_scheduler_sqlite_path),
        document_provider=ManagedDocuments(),
        monitoring_service=None,
        runtime_service=None,
        runtime_v2_service=runtime,
        message_bus_v2_service=bus,
        message_bus_v2_enabled=True,
        auto_media_enrichment_enabled=False,
    )
    service.initialization_control_path = settings.ticker_initialization_control_path
    return service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=1)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if not 0.1 <= args.interval <= 60 or not 1 <= args.limit <= 500:
        parser.error("interval must be 0.1..60 and limit 1..500")
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    settings = DoxAgentSettings()
    with WriterLock(Path(str(settings.runtime_scheduler_sqlite_path) + ".v2-scheduler")):
        scheduler = build(settings)
        try:
            while not stop.is_set():
                scheduler.run_due_once(event_limit=args.limit)
                if args.once:
                    break
                stop.wait(args.interval)
        finally:
            scheduler.runtime_v2_service.close()


if __name__ == "__main__":
    main()
