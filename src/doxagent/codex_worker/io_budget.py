"""Cancellation-safe blocking I/O; a cancelled await never frees a live I/O slot."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")


async def blocking(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
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
        async with self.slot:
            return await blocking(function, *args, **kwargs)
