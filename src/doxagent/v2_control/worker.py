"""Dedicated local control processor; it does not execute initialization nodes."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.runtime_scheduler.repository import SQLiteRuntimeSchedulerRepository
from doxagent.ticker_initialization.repository import InitializationRepository
from doxagent.trade_execution.worker import WriterLock

from .repository import ControlRepository
from .service import ControlService


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runtime-db", "initialization-db", "bus-db", "scheduler-db"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    for path in (args.runtime_db, args.initialization_db, args.bus_db, args.scheduler_db):
        if not path.is_file():
            parser.error("all source databases must exist before starting the control processor")
    control = ControlRepository(RuntimeJournal(args.runtime_db, initialize=False))
    with control.read() as db:
        db.execute("SELECT id FROM v2_operations LIMIT 0")
    service = ControlService(
        control,
        InitializationRepository(args.initialization_db),
        MessageBusV2Service(MessageBusV2Repository(args.bus_db)),
        SQLiteRuntimeSchedulerRepository(args.scheduler_db),
    )
    with WriterLock(Path(str(args.runtime_db) + ".v2-control")):
        while True:
            service.tick()
            if args.once:
                return
            time.sleep(1)


if __name__ == "__main__":
    main()
