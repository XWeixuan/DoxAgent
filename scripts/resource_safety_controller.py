#!/usr/bin/env python3
"""Publish NORMAL/PRESSURE/CRITICAL from actual host and cgroup pressure."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

GIB = 1024**3
MIB = 1024**2
PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


@dataclass(frozen=True)
class Sample:
    observed_at: float
    memory_current: int
    memory_max: int
    memory_swap_current: int
    host_available: int
    psi_some_avg10: float
    psi_full_avg10: float
    swap_io_bytes_per_minute: float
    high_events: int
    oom_events: int


def _read_int(path: Path) -> int:
    value = path.read_text(encoding="utf-8").strip()
    return 2**63 - 1 if value == "max" else int(value)


def _key_values(path: Path) -> dict[str, int]:
    return {
        key: int(value)
        for key, value in (line.split() for line in path.read_text(encoding="utf-8").splitlines())
    }


def _host_available() -> int:
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("MemAvailable is missing")


def _psi(root: Path, kind: str) -> float:
    for line in (root / "memory.pressure").read_text(encoding="utf-8").splitlines():
        if line.startswith(kind + " "):
            fields = dict(field.split("=", 1) for field in line.split()[1:])
            return float(fields["avg10"])
    return 0.0


def _swap_pages() -> int:
    values = _key_values(Path("/proc/vmstat"))
    return values.get("pswpin", 0) + values.get("pswpout", 0)


class SafetyController:
    """Three-state exception controller with deliberately small state."""

    def __init__(self, root: Path, state_path: Path) -> None:
        self.root, self.state_path = root, state_path
        self.level = "NORMAL"
        self._pressure_since: float | None = None
        self._critical_since: float | None = None
        self._healthy_since: float | None = None
        self._previous_swap: tuple[float, int] | None = None
        self._previous_high: int | None = None
        self._previous_oom: int | None = None
        self._last_reasons: list[str] = []

    def sample(self) -> Sample:
        now = time.monotonic()
        pages = _swap_pages()
        swap_rate = 0.0
        if self._previous_swap is not None:
            previous_at, previous_pages = self._previous_swap
            elapsed = max(0.001, now - previous_at)
            swap_rate = max(0, pages - previous_pages) * PAGE_SIZE * 60 / elapsed
        self._previous_swap = (now, pages)
        events = _key_values(self.root / "memory.events")
        return Sample(
            observed_at=time.time(),
            memory_current=_read_int(self.root / "memory.current"),
            memory_max=_read_int(self.root / "memory.max"),
            memory_swap_current=_read_int(self.root / "memory.swap.current"),
            host_available=_host_available(),
            psi_some_avg10=_psi(self.root, "some"),
            psi_full_avg10=_psi(self.root, "full"),
            swap_io_bytes_per_minute=swap_rate,
            high_events=events.get("high", 0),
            oom_events=events.get("oom", 0) + events.get("oom_kill", 0),
        )

    def evaluate(self, sample: Sample, now: float | None = None) -> tuple[str, list[str]]:
        tick = time.monotonic() if now is None else now
        pressure_signal = (
            sample.psi_some_avg10 >= 5
            or sample.psi_full_avg10 >= 2
            or sample.swap_io_bytes_per_minute >= 64 * MIB
        )
        high_increased = (
            self._previous_high is not None and sample.high_events > self._previous_high
        )
        oom_increased = self._previous_oom is not None and sample.oom_events > self._previous_oom
        self._previous_high = sample.high_events
        self._previous_oom = sample.oom_events

        pressure_reasons = []
        if sample.memory_current >= 14 * GIB and pressure_signal:
            pressure_reasons.append("cgroup_high_with_pressure")
        if sample.host_available < GIB and pressure_signal:
            pressure_reasons.append("low_available_with_pressure")
        if high_increased and pressure_signal:
            pressure_reasons.append("memory_high_events_with_pressure")
        if pressure_reasons:
            self._pressure_since = self._pressure_since or tick
        else:
            self._pressure_since = None
        pressure_active = bool(
            self._pressure_since is not None and tick - self._pressure_since >= 15
        )

        critical_reasons = []
        if sample.memory_current >= int(14.75 * GIB):
            critical_reasons.append("cgroup_near_hard_boundary")
        if oom_increased:
            critical_reasons.append("oom_event_increased")
        if sample.host_available < 384 * MIB and pressure_signal:
            critical_reasons.append("host_available_critical")
        if critical_reasons:
            self._critical_since = self._critical_since or tick
        else:
            self._critical_since = None
        critical_active = oom_increased or bool(
            self._critical_since is not None and tick - self._critical_since >= 5
        )

        desired = "CRITICAL" if critical_active else "PRESSURE" if pressure_active else "NORMAL"
        reasons = critical_reasons if desired == "CRITICAL" else pressure_reasons
        if desired == "CRITICAL" or (desired == "PRESSURE" and self.level == "NORMAL"):
            self.level, self._healthy_since = desired, None
        elif self.level == "CRITICAL" and desired != "CRITICAL":
            self._healthy_since = self._healthy_since or tick
            if tick - self._healthy_since >= 30:
                self.level, self._healthy_since = "PRESSURE", None
        elif self.level == "PRESSURE" and desired == "NORMAL":
            self._healthy_since = self._healthy_since or tick
            if tick - self._healthy_since >= 60:
                self.level, self._healthy_since = "NORMAL", None
        elif desired == self.level:
            self._healthy_since = None
        if reasons:
            self._last_reasons = reasons
        elif self.level == "NORMAL":
            self._last_reasons = []
        return self.level, list(self._last_reasons)

    def publish(self, sample: Sample, reasons: list[str]) -> None:
        payload = {
            "version": 1,
            "level": self.level,
            "observed_at": sample.observed_at,
            "reasons": reasons,
            "metrics": asdict(sample),
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.state_path)

    def run(self, interval: float) -> None:
        while True:
            sample = self.sample()
            _, reasons = self.evaluate(sample)
            self.publish(sample, reasons)
            time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cgroup-root",
        type=Path,
        default=Path("/sys/fs/cgroup/doxagent.slice/doxagent-app.slice"),
    )
    parser.add_argument(
        "--state-path", type=Path, default=Path("/run/doxagent-resources/safety.json")
    )
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()
    SafetyController(args.cgroup_root, args.state_path).run(max(0.5, args.interval))


if __name__ == "__main__":
    main()
