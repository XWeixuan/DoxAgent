"""Capsule-wide tool slots, shared by MCP processes and native child agents.

OS file locks are released on process death. Control/observation calls bypass this
budget. The directory is explicitly inherited from the owning execution capsule.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import BinaryIO


def try_lock(path: Path) -> BinaryIO | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            if path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            getattr(msvcrt, "locking")(handle.fileno(), getattr(msvcrt, "LK_NBLCK"), 1)  # noqa: B009
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


@asynccontextmanager
async def _slot(root: Path, name: str, count: int) -> AsyncIterator[None]:
    handle = None
    while handle is None:
        for index in range(count):
            handle = try_lock(root / f"{name}-{index}.lock")
            if handle is not None:
                break
        if handle is None:
            await asyncio.sleep(0.1)
    try:
        yield
    finally:
        handle.close()


@asynccontextmanager
async def tool_budget(*, expensive: bool = False) -> AsyncIterator[None]:
    directory = os.environ.get("DOXAGENT_MCP_BUDGET_ROOT")
    if not directory:
        yield
        return
    root = Path(directory)
    # Consistent lock order also covers PDFs discovered after downloading a URL.
    if expensive:
        async with _slot(root, "external", 4), _slot(root, "heavy", 1):
            yield
    else:
        async with _slot(root, "external", 4):
            yield


@asynccontextmanager
async def extraction_budget(*, expensive: bool) -> AsyncIterator[None]:
    """Content-type aware extraction; caller already owns an external slot."""
    directory = os.environ.get("DOXAGENT_MCP_BUDGET_ROOT")
    if directory and expensive:
        async with _slot(Path(directory), "heavy", 1):
            yield
    else:
        yield
