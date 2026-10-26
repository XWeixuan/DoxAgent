"""Await an owned, bounded cleanup without abandoning it on repeated cancellation."""

from __future__ import annotations

import asyncio


async def await_owned(task):
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    # Cleanup failure takes precedence: its physical owner is not safe to reuse.
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result
