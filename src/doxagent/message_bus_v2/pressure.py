"""Cheap Linux CPU signal for optional history/rechecks; realtime heads stay enabled."""

import time
from pathlib import Path

_sampled_at = -10.0
_avg60 = None


def cpu_pressure():
    global _sampled_at, _avg60
    now = time.monotonic()
    if now - _sampled_at >= 2:
        _sampled_at = now
        try:
            line = next(
                line
                for line in Path("/proc/pressure/cpu").read_text().splitlines()
                if line.startswith("some ")
            )
            _avg60 = float(dict(item.split("=") for item in line.split()[1:])["avg60"])
        except (OSError, ValueError, KeyError, StopIteration):
            _avg60 = None
    return _avg60


def background_allowed():
    pressure = cpu_pressure()
    return pressure is None or pressure < 10
