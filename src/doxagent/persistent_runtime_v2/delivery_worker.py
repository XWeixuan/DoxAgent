"""Independent durable intent delivery, including when every ticker is stopped.

This process admits frozen intents to the existing executor; it owns no broker socket.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

from doxagent.trade_execution.intake import ExecutionIntake
from doxagent.trade_execution.recovery import DELAYS, transient

from .journal import RuntimeJournal
from .trade_output import TradeOutputService


async def run(journal: RuntimeJournal, *, once: bool = False) -> None:
    service, intake = TradeOutputService(journal), ExecutionIntake(journal)
    next_heartbeat = 0.0
    failures = 0
    while True:
        try:
            await service.deliver(intake, limit=20)
            if time.monotonic() >= next_heartbeat:
                journal.set("v2_workers", "delivery", {"heartbeat_at": journal.clock().isoformat()})
                next_heartbeat = time.monotonic() + 10
            failures = 0
        except Exception as exc:
            if not transient(exc) or once:
                raise
            failures += 1
            await asyncio.sleep(DELAYS[min(failures - 1, 4)])
            continue
        if once:
            return
        await asyncio.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-db", required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    from doxagent.trade_execution.worker import WriterLock

    if not Path(args.runtime_db).is_file():
        parser.error("runtime database must already exist and be migrated")
    with WriterLock(Path(args.runtime_db + ".v2-delivery")):
        asyncio.run(run(RuntimeJournal(args.runtime_db, initialize=False), once=args.once))


if __name__ == "__main__":
    main()
