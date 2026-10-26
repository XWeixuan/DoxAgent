"""Replay the old coverage definition with an explicit receipt for each due slot."""
import json
from collections import Counter, defaultdict
from datetime import timedelta
from math import ceil, floor
from pathlib import Path

from doxagent.message_bus_v2.cadence import instant

root = Path(__file__).parent
data = json.loads((root.parent / "pipeline-live-test-20261026/final-steady-window.json").read_text(encoding="utf-8-sig"))
reference = json.loads((root.parent / "pipeline-live-test-20261026/acceptance-summary.json").read_text())["windows"]["after"]["cadence"]
since, until = instant(data["since"]), instant(data["at"])
groups = defaultdict(lambda: {"schedules": [], "runs": [], "captures": []})
for row in [*data["schedules"], *data["runs"]]:
    if not row["unit"].startswith("TAIL:"):
        groups[row["binding_id"], row["unit"]]["schedules" if row["status"] == "SCHEDULE" else "runs"].append(row)
for row in data["captures"]:
    for target in json.loads(row.get("targets_json") or "[]") or [{}]:
        value = {**row, **target}
        if not value["unit"].startswith("TAIL:"):
            groups[value["binding_id"], value["unit"]]["captures"].append(value)
units, sources = [], defaultdict(Counter)
all_cycles = []
for (binding, unit), data_unit in groups.items():
    schedules = sorted(data_unit["schedules"], key=lambda r: r["dispatch_at"])
    cycles, active = {}, []
    for index, row in enumerate(schedules):
        begin = max(since, instant(row["dispatch_at"]))
        end = min(until, instant(schedules[index+1]["dispatch_at"])) if index+1 < len(schedules) else until
        if row.get("reason") not in {"ENABLED", "ELIGIBILITY_UNKNOWN"} or begin >= end:
            continue
        epoch, period = instant(row.get("epoch_at") or row["due_at"]), row["interval_seconds"]
        active.append((epoch, period, begin, end))
        for n in range(max(0, ceil((begin-epoch).total_seconds()/period)), floor(((end-epoch).total_seconds()-0.000001)/period)+1):
            key = epoch.isoformat(), n
            cycles[key] = {"due_at": (epoch+timedelta(seconds=n*period)).isoformat(), "period": period,
                           "runs": [], "fresh": False, "captures": [], "rejections": []}

    def slot(at):
        for epoch, period, begin, end in active:
            if epoch <= at and begin <= at < end:
                key = epoch.isoformat(), floor((at-epoch).total_seconds()/period)
                return key if key in cycles else None
        return None

    def generation_matches(at, value):
        config = next((r for r in reversed(schedules) if instant(r["dispatch_at"]) <= at), None)
        return config is None or all(value.get(k) is None or config.get(k) is None or value[k] == config[k]
                                     for k in ("source_version", "binding_version"))

    by_run = {r["run_id"]: r for r in data_unit["runs"]}
    by_run.update({r["shared_run_id"]: r for r in data_unit["runs"] if r.get("shared_run_id") and r.get("requests")})
    for row in data_unit["runs"]:
        key = slot(max(instant(row["due_at"]), instant(row["dispatch_at"])))
        if key:
            cycles[key]["runs"].append({"run_id": row["run_id"], "status": row["status"], "reason": row.get("reason"),
                                         "requests": len(row.get("requests", []))})
    for row in data_unit["captures"]:
        at, run = instant(row["observed_at"]), by_run.get(row["run_id"])
        if not run or run["status"] != "CAPTURED" or not run.get("requests"):
            continue
        due = instant(run["due_at"])
        starts = [instant(r["request_started_at"]) for r in run["requests"] if r.get("request_started_at")]
        requested = instant(row["request_started_at"]) if row.get("request_started_at") else min(starts) if starts else due
        key = slot(max(due, requested))
        if key:
            cycles[key]["captures"].append(row["capture_id"])
            if generation_matches(at, row) and generation_matches(requested, run) and max(due, requested) <= at <= requested+timedelta(seconds=run["interval_seconds"]):
                cycles[key]["fresh"] = True
            else:
                cycles[key]["rejections"].append("generation_or_capture_deadline")
    counts = Counter()
    for key, row in cycles.items():
        runs = row["runs"]
        due = instant(row["due_at"])
        if row["fresh"]:
            category = "fresh_by_old_definition"
        elif due+timedelta(seconds=row["period"]) > until and not any(r["status"] != "DISPATCHED" for r in runs) and (runs or (until-due).total_seconds() <= max(5, .2*row["period"])):
            category = "window_pending"
        elif not runs:
            category = "no_dispatch_in_slot"
        elif any(r["status"] == "DEFERRED" for r in runs):
            category = "access_deferred"
        elif any(r["status"] == "FAILED" for r in runs):
            category = "execution_failed"
        elif not any(r["requests"] for r in runs):
            category = "no_linked_transport_proof"
        elif row["rejections"]:
            category = "capture_deadline_or_generation"
        else:
            category = "cross_slot_or_capture_link"
        row.update(binding_id=binding, unit=unit, category=category)
        counts[category] += 1
        all_cycles.append(row)
    if cycles:
        units.append({"binding_id": binding, "unit": unit, "expected": len(cycles), "categories": dict(counts)})
        sources[binding.split(":", 1)[1]].update(counts)
total = sum((Counter(u["categories"]) for u in units), Counter())
assert sum(total.values()) == reference["expected_cycles"]
assert total["fresh_by_old_definition"] == reference["fresh_cycles"], total
assert total["window_pending"] == reference["pending_cycles"], total
out = {"since": since.isoformat(), "until": until.isoformat(), "total": dict(total),
       "sources": {k: dict(v) for k,v in sorted(sources.items())}, "units": units, "cycles": all_cycles,
       "warning": "Reproduces old coverage only; transport roles, old capture reuse and consumer timestamps need separate validation."}
(root / "coverage-reconciliation.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"total": out["total"], "sources": out["sources"]}, ensure_ascii=False))
