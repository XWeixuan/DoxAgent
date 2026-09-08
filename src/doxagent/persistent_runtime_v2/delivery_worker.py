"""Independent durable intent delivery, including when every ticker is stopped.

This process admits frozen intents to the existing executor; it owns no broker socket.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from doxagent.trade_execution.intake import ExecutionIntake

from .journal import RuntimeJournal
from .trade_output import TradeOutputService


async def run(journal: RuntimeJournal, *, once: bool = False) -> None:
    service, intake = TradeOutputService(journal), ExecutionIntake(journal)
    while True:
        await service.deliver(intake, limit=20)
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
