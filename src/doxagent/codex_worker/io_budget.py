"""Cancellation-safe blocking I/O; a cancelled await never frees a live I/O slot."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")


async def blocking(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    submitted=time.monotonic()
    def call():
        from doxagent.w3_timing import span
        with span('file_io.call',queued_ms=round((time.monotonic()-submitted)*1000,3),
                  operation=getattr(function,'__qualname__',type(function).__name__)):
            return function(*args,**kwargs)
    task = asyncio.create_task(asyncio.to_thread(call))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Keep ownership until the real thread finishes, including repeated cancellation.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        raise


class DiskBudget:
    def __init__(self) -> None:
        self.slot = asyncio.Semaphore(1)

    async def run(self, function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        from doxagent.w3_timing import span
        with span('disk.wait'):
            await self.slot.acquire()
        try:
            return await blocking(function, *args, **kwargs)
        finally:
            self.slot.release()
