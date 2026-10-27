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
    if pressure is not None and pressure >= 10:
        return False
    for kind, ceiling in [('io', 10), ('memory', 2)]:
        try:
            line = Path('/proc/pressure', kind).read_text().splitlines()[0]
            if float(dict(x.split('=') for x in line.split()[1:])['avg60']) >= ceiling:
                return False
        except (OSError, ValueError, KeyError, IndexError):
            pass
    return True
