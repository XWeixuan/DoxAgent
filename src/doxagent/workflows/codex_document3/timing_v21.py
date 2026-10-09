"""Small serial host spans, separate from model/provider-reported durations."""

from datetime import UTC, datetime
from time import monotonic


class HostTiming:
    def __init__(self):
        self.spans = []
        self.name = None
        self.started = monotonic()
        self.started_at = datetime.now(UTC).isoformat()

    def step(self, name):
        now = monotonic()
        finished_at = datetime.now(UTC).isoformat()
        if self.name:
            self.spans.append(
                {
                    "name": self.name,
                    "duration_ms": (now - self.started) * 1000,
                    "started_at": self.started_at,
                    "finished_at": finished_at,
                }
            )
        self.name, self.started, self.started_at = name, now, finished_at

    def finish(self):
        self.step(None)
        return self.spans
