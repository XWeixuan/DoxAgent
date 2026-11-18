"""Bounded final removal of filesystem work already detached by its owner."""

import os
import re
import shutil
from pathlib import Path
from uuid import uuid4

_NAME = re.compile(r'[0-9a-f]{64}\.[0-9a-f]{32}')


def detach(path: Path, queue: Path) -> None:
    """Called under the relevant SQLite owner fence. The path cannot be reused."""
    queue.mkdir(parents=True, exist_ok=True)
    if queue.is_symlink() or path.is_symlink() or not re.fullmatch('[0-9a-f]{64}', path.name):
        raise ValueError('invalid artifact detach path')
    path.rename(queue / f'{path.name}.{uuid4().hex}')


def drain(queue: Path, *, limit: int = 4) -> int:
    """Retry detached work after commit or process restart without touching canonical paths."""
    if queue.is_symlink():
        raise ValueError('linked artifact exit queue')
    if not queue.is_dir():
        return 0
    count = 0
    with os.scandir(queue) as entries:
        for entry in entries:
            if count >= limit:
                break
            if not _NAME.fullmatch(entry.name) or not entry.is_dir(follow_symlinks=False):
                continue
            try:
                shutil.rmtree(entry.path)
            except FileNotFoundError:
                if Path(entry.path).exists():
                    raise
            count += 1
    return count
