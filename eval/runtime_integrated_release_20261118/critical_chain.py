"""Derive disjoint Trade intervals for selected, fully completed natural Cases."""
import gzip
import json
from datetime import datetime
from pathlib import Path

p = Path(__file__).parent
with gzip.open(p / "cohort-final.json.gz") as stream:
    cases = json.load(stream)["cases"]
with gzip.open(p / "logs-final.json.gz") as stream:
    traces = json.load(stream)["traces"]
acceptance = json.loads((p / "acceptance-final.json").read_text())


def ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


rows = []
for measured in sorted(acceptance["rows"], key=lambda r: r.get("whole_runtime_non_model", -1), reverse=True)[:10]:
    c = next(c for c in cases if c["case_id"] == measured["case_id"])
    owners = sorted([t for t in traces if t.get("case_id") == c["case_id"] and t["segment"] == "effect_owner" and t.get("phase") == "route_to_w3"], key=lambda t: t["started_at"])
    stage_jobs = {s["worker_job_id"] for s in c["stages"] if s["phase"].startswith("trade_decision")}
    jobs = [j for j in c["jobs"] if j["id"] in stage_jobs and j["timing"].get("sdk_completed_at")]
    if not owners or len(jobs) != 2:
        rows.append({**measured, "chain_unavailable": True})
        continue
    effect = next(e for e in c["effects"] if e["effect_type"] == "route_to_w3")
    intervals = sorted((ts(j["timing"]["sdk_dispatched_at"]), ts(j["timing"]["sdk_completed_at"])) for j in jobs)
    union = intervals[0][1] - intervals[0][0] + max(0, intervals[1][1] - max(intervals[1][0], intervals[0][1]))
    first, last = intervals[0][0], max(b for _, b in intervals)
    parts = {
        "route_to_first_owner": ts(owners[0]["started_at"]) - ts(effect["created_at"]),
        "first_owner": (ts(owners[0]["finished_at"]) - ts(owners[0]["started_at"])),
        "first_owner_to_first_model": first - ts(owners[0]["finished_at"]),
        "between_model_union_intervals": last - first - union,
        "last_model_to_final_owner": ts(owners[-1]["started_at"]) - last,
        "final_owner": ts(owners[-1]["finished_at"]) - ts(owners[-1]["started_at"]),
    }
    rows.append({**measured, "trade_disjoint_seconds": parts,
                 "sum_seconds": sum(parts.values()), "job_count": len(jobs),
                 "first_owner_totals": owners[0].get("totals", {}),
                 "first_owner_events_sampled": owners[0].get("events_truncated")})
(p / "slow-case-critical-chain.json").write_text(json.dumps(rows, indent=2))
