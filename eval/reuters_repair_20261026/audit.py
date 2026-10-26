"""Full-window read-only acceptance. No browser/provider requests or secrets."""

import argparse
import collections
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--since", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()
end = datetime.now(timezone.utc).isoformat()
root = Path("/var/lib/docker/volumes")
reg = root / "doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3"
bus = root / "doxagent-v2_v2-data/_data/bus/bus.sqlite3"


def read(path):
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("pragma query_only=on")
    return db


def pick(v, keys):
    return {k: v[k] for k in keys.split() if k in v}


def query(db, sql, params=()):
    return [dict(r) for r in db.execute(sql, params)]


r, b = read(reg), read(bus)
out = {"since": args.since, "until": end}
counts = collections.Counter()
timeline = []
starts = set()
finishes = set()
for row in r.execute(
    "select * from access_events where site_id='reuters' and "
    "julianday(occurred_at)>=julianday(?) and julianday(occurred_at)<julianday(?) order by julianday(occurred_at)",
    (args.since, end),
):
    p = json.loads(row["payload_json"])
    cat = row["category"]
    counts[
        (
            cat,
            p.get("identity_id"),
            p.get("purpose"),
            p.get("disposition") or p.get("result"),
            p.get("reason_code") or p.get("reason"),
        )
    ] += 1
    if cat == "PROBE_STARTED":
        starts.add(p.get("probe_id"))
    if cat == "PROBE_FINISHED":
        finishes.add(p.get("probe_id"))
    timeline.append(
        {
            "at": row["occurred_at"],
            "category": cat,
            'operation_id':row['operation_id'],
            'stage_timings':{k:v for k,v in p.get('stage_timings',{}).items() if isinstance(v,(int,float))},
            **pick(
                p,
                "identity_id recovery_id purpose disposition reason reason_code result error_type probe_id request_sent "
                "stage total_ms queue_ms control_ms site_ms retained task_id cost attempt_no retry_action_id egress_reason",
            ),
        }
    )
out["event_counts"] = [{"key": key, "n": n} for key, n in counts.items()]
out["timeline"] = timeline
out["probe_closure"] = {
    "started": len(starts),
    "finished": len(finishes),
    "unfinished": list(starts - finishes),
}
out["jobs"] = [
    pick(
        json.loads(row[0]),
        "job_id recovery_id identity_id created_at updated_at status task_id reason "
        "submitted_at provider_ready_at applied_at provider_cost cost verification_state solver_verification "
        "verification_reason business_recovery natural_success_at recurrence_at late_recovery_at route_activated_at",
    )
    for row in r.execute(
        "select data_json from datadome_solver_jobs where identity_id in ('digitimes-de-1','reuters-backup') "
        "and julianday(created_at)>=julianday(?)",
        (args.since,),
    )
]
out["recoveries"] = [
    pick(
        json.loads(row[0]),
        "identity_id recovery_id phase generation handling_mode page_status "
        "original_target_id original_instance_id opened_at updated_at next_due_at next_probe_at last_error "
        "manual_review_reason pending_purposes recovered_purposes scene_preparation",
    )
    for row in r.execute(
        "select data_json from browser_recoveries where identity_id in ('digitimes-de-1','reuters-backup')"
    )
]
out["identity_runtime"] = [
    pick(
        json.loads(row[0]),
        "identity_id instance_id capability_windows recent_relapses paid_cooldown_until",
    )
    for row in r.execute(
        "select data_json from identity_runtime where identity_id in ('digitimes-de-1','reuters-backup')"
    )
]
out["targets"] = query(
    r,
    "select allocation_id,identity_id,browser_instance_id,target_id,owner_kind,state from owned_browser_targets "
    "where identity_id in ('digitimes-de-1','reuters-backup') and state not in ('CLOSED','LOST')",
)
out["body"] = query(
    r,
    "select outcome,reason,count(*) n,min(completed_at) first_at,max(completed_at) last_at from body_outcomes "
    "where final_site_id='reuters' and julianday(completed_at)>=julianday(?) and julianday(completed_at)<julianday(?) group by outcome,reason",
    (args.since, end),
)
out["poll_states"] = []
for row in b.execute("select data_json from poll_states where source_id='reuters_site_search'"):
    v = json.loads(row[0])
    item = pick(
        v,
        "ticker status last_success_at last_attempt_at last_error_code next_dispatch_at target_due_at",
    )
    item["query_cycle"] = v.get("checkpoint", {}).get("query_cycle")
    item["units"] = {
        k: pick(
            u,
            "status last_success_at last_attempt_at last_error_code target_due_at next_dispatch_at last_window_coverage",
        )
        for k, u in v.get("units", {}).items()
    }
    out["poll_states"].append(item)
out["poll_runs"] = query(
    b,
    "select ticker,unit,status,reason,count(*) n,min(dispatch_at) first_at,max(dispatch_at) last_at "
    "from poll_runs where source_id='reuters_site_search' and julianday(dispatch_at)>=julianday(?) and julianday(dispatch_at)<julianday(?) "
    "group by ticker,unit,status,reason",
    (args.since, end),
)
out['projection_read_at']=datetime.now(timezone.utc).isoformat()
out["physical_intervals"] = {}
for row in b.execute(
    "select ticker,unit,dispatch_at from poll_runs where source_id='reuters_site_search' and status='CAPTURED' "
    "and julianday(dispatch_at)>=julianday(?) and julianday(dispatch_at)<julianday(?) order by julianday(dispatch_at)",
    (args.since, end),
):
    key = row["ticker"] + ":" + row["unit"]
    out["physical_intervals"].setdefault(key, []).append(row["dispatch_at"])
# Dispatch/capture intervals are labelled as such, not fabricated physical website request times.
for key, times in list(out["physical_intervals"].items()):
    stamps = [datetime.fromisoformat(t.replace("Z", "+00:00")) for t in times]
    deltas = sorted((c - a).total_seconds() for a, c in zip(stamps, stamps[1:]))
    out["physical_intervals"][key] = {
        "captured_runs": len(stamps),
        "captured_dispatch_interval_seconds": {
            "p50": deltas[len(deltas) // 2],
            "p95": deltas[min(len(deltas) - 1, int(len(deltas) * 0.95))],
            "max": max(deltas),
        }
        if deltas
        else None,
    }
containers = json.loads(
    subprocess.check_output(
        [
            "docker",
            "inspect",
            *[
                "doxagent-v2-v2-" + n + "-1"
                for n in ("site-access", "chrome-supervisor", "message-bus", "content-enrichment")
            ],
        ],
        text=True,
    )
)
out["containers"] = [
    {
        "name": c["Name"],
        "id": c["Id"],
        "image": c["Image"],
        "started_at": c["State"]["StartedAt"],
        "restarts": c["RestartCount"],
        "network_mode": c["HostConfig"]["NetworkMode"],
    }
    for c in containers
]
out["resources"] = {
    k: subprocess.check_output(cmd, text=True)
    for k, cmd in [("disk", ["df", "-h", "/"]), ("memory", ["free", "-m"]), ("load", ["uptime"])]
}
out["resources"]["cpu_psi"] = Path("/proc/pressure/cpu").read_text()
out["resources"]["memory_psi"] = Path("/proc/pressure/memory").read_text()
evidence = reg.parent.parent / "challenge-evidence"
out["resources"]["evidence_bytes"] = sum(
    p.stat().st_size for p in evidence.glob("*") if p.is_file() and not p.is_symlink()
)
out["resources"]["registry_wal_bytes"] = (
    Path(str(reg) + "-wal").stat().st_size if Path(str(reg) + "-wal").exists() else 0
)
out["resources"]["bus_wal_bytes"] = (
    Path(str(bus) + "-wal").stat().st_size if Path(str(bus) + "-wal").exists() else 0
)
Path(args.output).write_text(json.dumps(out, indent=2), encoding="utf8")
print(
    json.dumps({"output": args.output, "since": args.since, "until": end, "events": len(timeline)})
)
