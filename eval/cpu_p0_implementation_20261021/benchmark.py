"""Offline same-fixture SQL/CPU comparison; no network or production access."""
import json
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from doxagent.message_bus_v2.scheduler import GlobalPollScheduler
from tests.fixtures.message_bus_v2 import _bus


def main():
    with tempfile.TemporaryDirectory(prefix="doxagent-cpu-p0-") as root:
        repo, bus = _bus(Path(root) / "bus.db")
        for ticker in ["MU", "RKLB", "BE", "INTC", "NBIS", "COHR", "ONDS", "CEG", "SNOW"]:
            bus.start_ticker(ticker)
        scheduler = GlobalPollScheduler(repo, bus, None)
        source = subprocess.check_output(
            ["git", "show", "HEAD:src/doxagent/message_bus_v2/scheduler.py"], text=True,
        )
        previous = {"__name__": "doxagent.message_bus_v2._cpu_baseline",
                    "__package__": "doxagent.message_bus_v2"}
        exec(compile(source, "git-HEAD-scheduler.py", "exec"), previous)
        old = previous["GlobalPollScheduler"](repo, bus, None)
        now = datetime(2026, 10, 21, 15, tzinfo=UTC)
        scheduler._prepare_poll_cycle(now)  # Equal steady-state, no first admission/rephase.

        def baseline():
            eligible = old._eligible_bindings(now)
            old._initialize_due_slots(eligible, now)
            old._update_capacity_alerts(eligible)
            roster = old._eligible_bindings(now, include_inactive_hours=True)
            states = {b.binding_id: repo.get_poll_state(b) for _, b in eligible}
            return eligible, roster, states

        def result(value):
            eligible, roster, states = value
            return {
                "eligible": sorted(b.binding_id for _, b in eligible),
                "roster": sorted(b.binding_id for _, b in roster),
                "due": sorted(b.binding_id for _, b in eligible
                              if states[b.binding_id].next_dispatch_at is None
                              or states[b.binding_id].next_dispatch_at <= now),
            }

        native_connect = sqlite3.connect
        samples = {"before": [], "after": []}
        equivalent = True
        for _ in range(3):
            outputs = []
            for name, operation in [("before", baseline),
                                    ("after", lambda: scheduler._prepare_poll_cycle(now))]:
                count = 0

                def connect(*a, **kw):
                    nonlocal count
                    count += 1
                    return native_connect(*a, **kw)

                sqlite3.connect = connect
                cpu, wall = time.process_time(), time.perf_counter()
                try:
                    outputs.append(result(operation()))
                finally:
                    samples[name].append({"connections": count,
                                          "cpu_ms": (time.process_time()-cpu)*1000,
                                          "wall_ms": (time.perf_counter()-wall)*1000})
                    sqlite3.connect = native_connect
            equivalent &= outputs[0] == outputs[1]
        summary = {k: {field: statistics.median(row[field] for row in rows)
                       for field in ["connections", "cpu_ms", "wall_ms"]}
                   for k, rows in samples.items()}
        print(json.dumps({"fixture": {"tickers": 9, "bindings": len(repo.list_bindings()),
                                      "eligible": len(outputs[0]["eligible"])},
                          "equivalent_due_and_roster": equivalent,
                          "samples": samples, "median": summary}, indent=2))
        assert equivalent


if __name__ == "__main__":
    main()
