"""Small cgroup pressure controller; optional metrics never become business gates."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PressureSample:
    memory: int | None = None
    working_memory: int | None = None
    inactive_file: int | None = None
    available: int | None = None
    full: float | None = None
    swap: int | None = None


def sample() -> PressureSample:
    result = PressureSample()
    root = Path("/sys/fs/cgroup")
    try:
        # Container cgroup root only. No unlimited host-root pseudo-capacity inference.
        if (root / "memory.max").read_text().strip() != "max":
            result.memory = int((root / "memory.current").read_text())
            stats = {
                line.split()[0]: int(line.split()[1])
                for line in (root / "memory.stat").read_text().splitlines()
            }
            result.inactive_file = min(result.memory, stats.get("inactive_file", 0))
            result.working_memory = result.memory - result.inactive_file
            result.swap = int((root / "memory.swap.current").read_text())
            line = next(
                x
                for x in (root / "memory.pressure").read_text().splitlines()
                if x.startswith("full ")
            )
            result.full = float(line.split("avg10=")[1].split()[0])
    except (OSError, ValueError, StopIteration):
        pass
    # Linux procfs MemAvailable is host memory; do not substitute container RSS/free.
    try:
        line = next(
            x
            for x in Path("/proc/meminfo").read_text().splitlines()
            if x.startswith("MemAvailable:")
        )
        result.available = int(line.split()[1]) * 1024
    except (OSError, ValueError, StopIteration):
        pass
    return result


class PressureController:
    def __init__(self) -> None:
        self.paused = False
        self.extreme = False
        self._clear_since: float | None = None
        self._stall_since: float | None = None
        self._previous = 0

    def update(self, value: PressureSample, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        gib = 1024**3
        if value.full is not None and value.full >= 5:
            self._stall_since = self._stall_since if self._stall_since is not None else now
        else:
            self._stall_since = None
        stall = self._stall_since is not None and now - self._stall_since >= 10
        # memory.current includes reclaimable file cache. Pausing admission on the
        # raw value turns useful cache into a false OOM signal, so pressure control
        # follows the effective working set. Missing new metrics retain legacy
        # behavior for compatibility and conservative failure handling.
        memory = value.working_memory if value.working_memory is not None else value.memory or 0
        self.extreme = (memory >= 3.25 * gib and (stall or memory > self._previous)) or (
            value.available is not None and value.available < 384 * 1024**2 and stall
        )
        self._previous = memory
        if (
            memory >= 2.8 * gib
            or (value.available is not None and value.available < 768 * 1024**2)
            or stall
        ):
            self.paused = True
            self._clear_since = None
        elif (
            memory < 2.4 * gib
            and (value.available is None or value.available > gib)
            and (value.full is None or value.full < 1)
        ):
            self._clear_since = self._clear_since if self._clear_since is not None else now
            if now - self._clear_since >= 30:
                self.paused = False
        else:
            self._clear_since = None
