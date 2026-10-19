"""Keep synchronous owner work off the network loop without multiplying DB writers."""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any
from weakref import WeakKeyDictionary


class StorageLane:
    def __init__(self) -> None:
        # A service may also be used by independent API/test event loops. Never
        # share an asyncio Lock across loops; SQLite retains cross-owner fencing.
        self._locks: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
            WeakKeyDictionary()
        )

    async def call(self, operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        lock = self._locks.setdefault(asyncio.get_running_loop(), asyncio.Lock())
        async with lock:
            started = time.monotonic()
            work = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
            try:
                return await asyncio.shield(work)
            except asyncio.CancelledError:
                # Cancellation cannot stop a thread halfway through a commit.
                # Drain the admitted operation before its caller releases a lease.
                await asyncio.gather(work, return_exceptions=True)
                raise
            finally:
                elapsed = time.monotonic() - started
                if elapsed >= 1:
                    logging.getLogger(__name__).warning(
                        "bus_storage_slow operation=%s elapsed_ms=%s",
                        getattr(operation, "__name__", type(operation).__name__),
                        round(elapsed * 1000),
                    )
