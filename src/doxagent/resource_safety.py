"""Small fail-open view of host pressure published by the safety controller."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class SafetyLevel(StrEnum):
    NORMAL = "NORMAL"
    PRESSURE = "PRESSURE"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class SafetySnapshot:
    level: SafetyLevel = SafetyLevel.NORMAL
    observed_at: float = 0.0
    reasons: tuple[str, ...] = ()
    stale: bool = False


class SafetyStateReader:
    """Read one optional state file; missing/stale telemetry never gates work."""

    def __init__(self, path: Path | None, *, stale_after_seconds: float = 15.0) -> None:
        self.path = path
        self.stale_after_seconds = stale_after_seconds

    def read(self) -> SafetySnapshot:
        if self.path is None:
            return SafetySnapshot()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            observed_at = float(payload["observed_at"])
            stale = time.time() - observed_at > self.stale_after_seconds
            if stale:
                return SafetySnapshot(observed_at=observed_at, stale=True)
            return SafetySnapshot(
                level=SafetyLevel(str(payload.get("level", "NORMAL"))),
                observed_at=observed_at,
                reasons=tuple(str(item) for item in payload.get("reasons", [])),
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return SafetySnapshot(stale=True)
