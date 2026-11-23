"""Bounded final removal of filesystem work already detached by its owner."""

import os
import re
import time
from pathlib import Path
from uuid import uuid4

_NAME = re.compile(r'[0-9a-f]{64}\.[0-9a-f]{32}')


def detach(path: Path, queue: Path) -> None:
    """Called under the relevant SQLite owner fence. The path cannot be reused."""
    queue.mkdir(parents=True, exist_ok=True)
    if queue.is_symlink() or path.is_symlink() or not re.fullmatch('[0-9a-f]{64}', path.name):
        raise ValueError('invalid artifact detach path')
    path.rename(queue / f'{path.name}.{uuid4().hex}')


def unlink_page(path: Path, *, max_entries=256,deadline=None):
    """A detached tree has no retained prefix; deleting entries advances it."""
    deadline=deadline if deadline is not None else time.monotonic()+.05
    entries=0;allocated=0
    def visit(directory):
        nonlocal entries,allocated
        with os.scandir(directory) as children:
            for item in children:
                if entries>=max_entries or time.monotonic()>=deadline:return False
                if item.is_dir(follow_symlinks=False):
                    if not visit(Path(item.path)):return False
                else:
                    info=item.stat(follow_symlinks=False);os.unlink(item.path)
                    entries+=1
                    if info.st_nlink<=1:allocated+=getattr(info,'st_blocks',(info.st_size+511)//512)*512
        if entries>=max_entries or time.monotonic()>=deadline:return False
        info=directory.stat();directory.rmdir();entries+=1
        allocated+=getattr(info,'st_blocks',0)*512
        return True
    return visit(path) if path.exists() else True,entries,allocated


def drain(queue: Path, *, limit: int = 4, max_entries=256, seconds=.05) -> int:
    """Retry detached work after commit or process restart without touching canonical paths."""
    if queue.is_symlink():
        raise ValueError('linked artifact exit queue')
    if not queue.is_dir():
        return 0
    count = 0;removed_entries=0;deadline=time.monotonic()+seconds
    with os.scandir(queue) as children:
        for entry in children:
            if count >= limit or removed_entries>=max_entries or time.monotonic()>=deadline:
                break
            if not _NAME.fullmatch(entry.name) or not entry.is_dir(follow_symlinks=False):
                continue
            try:
                done,removed,_=unlink_page(Path(entry.path),max_entries=max_entries-removed_entries,deadline=deadline)
                removed_entries+=removed
            except FileNotFoundError:
                if Path(entry.path).exists():
                    raise
                done=True
            if done:count += 1
    return count
