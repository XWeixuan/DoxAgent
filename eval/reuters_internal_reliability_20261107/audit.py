"""Read-only full-window receipts; no publisher, provider or browser operations."""
import argparse
import collections
import json
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--since", required=True)
args = p.parse_args()
now = datetime.now(timezone.utc).isoformat()
lower = datetime.fromisoformat(args.since.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()[:19]
upper = (datetime.fromisoformat(now) + timedelta(seconds=1)).isoformat()[:19]
root = Path("/var/lib/docker/volumes")
registry = root / "doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3"
bus = root / "doxagent-v2_v2-data/_data/bus/bus.sqlite3"


def read(path):
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("pragma query_only=on")
    return db


def select(v, fields):
    return {k: v.get(k) for k in fields.split()}


r, b = read(registry), read(bus)
out = {"since": args.since, "until": now}
counts = collections.Counter()
started, finished = {}, {}
for row in r.execute("select category,occurred_at,payload_json from access_events "
                     "where site_id='reuters' and occurred_at>=? and occurred_at<? "
                     "and julianday(occurred_at)>=julianday(?) "
                     "and julianday(occurred_at)<julianday(?) order by occurred_at", (lower, upper, args.since, now)):
    v = json.loads(row["payload_json"])
    counts[(row["category"], v.get("identity_id"), v.get("purpose"),
            v.get("disposition"), v.get("result"), v.get("reason") or v.get("reason_code"))] += 1
    if v.get("probe_id") and row["category"] in {"PROBE_STARTED", "PROBE_FINISHED"}:
        target = started if row["category"] == "PROBE_STARTED" else finished
        target[v["probe_id"]] = {"at": row["occurred_at"], **select(v,
            "identity_id stage result request_sent total_ms reason_code control_failure_count")}
out["events"] = [{"category": k[0], "identity": k[1], "purpose": k[2], "disposition": k[3],
                  "result": k[4], "reason": k[5], "count": n} for k, n in counts.items()]
out["probes"] = {"started": len(started), "finished": len(finished),
                  "unclosed": sorted(started.keys() - finished.keys()), "receipts": finished}
out["recoveries"] = []
for row in r.execute("select data_json from browser_recoveries where json_extract(data_json,'$.site_id')='reuters'"):
    v = json.loads(row[0])
    out["recoveries"].append({**select(v, "identity_id recovery_id phase handling_mode opened_at "
        "next_due_at next_probe_at probe_stage last_error original_instance_id original_target_id "
        "page_status attempt_count resolved_at resolved_by"),
        "control": v.get("document_recovery", {}).get("control"),
        "preparation": select(v.get("scene_preparation", {}), "status attempt_no finished_at")})
out["jobs"] = []
for row in r.execute("select data_json from datadome_solver_jobs where julianday(updated_at)>=julianday(?)", (args.since,)):
    v = json.loads(row[0])
    if v.get("identity_id") not in {i["identity_id"] for i in out["recoveries"]}:
        continue
    out["jobs"].append(select(v, "job_id identity_id recovery_id status task_id created_at submitted_at "
        "applied_at provider_ready_at provider_cost cost verification_state verification_reason "
        "solver_verification recurrence_at diagnostics diagnostic_timeline"))
out["body"] = [dict(row) for row in r.execute("select outcome,reason,count(*) n,min(completed_at) first_at," 
    "max(completed_at) last_at from body_outcomes where final_site_id='reuters' and completed_at>=? and completed_at<? "
    "and julianday(completed_at)>=julianday(?) and julianday(completed_at)<julianday(?) "
    "group by outcome,reason", (lower, upper, args.since, now))]
out["poll_states"] = []
for row in b.execute("select data_json from poll_states where source_id='reuters_site_search'"):
    v = json.loads(row[0])
    out["poll_states"].append({**select(v, "ticker status last_error_code last_attempt_at last_success_at "
                                  "next_dispatch_at target_due_at"), "units": {
        k: select(u, "status last_success_at last_attempt_at last_error_code next_dispatch_at target_due_at")
        for k, u in v.get("units", {}).items()}})
out["poll_runs"] = [dict(row) for row in b.execute(
    "select ticker,unit,status,reason,count(*) n,min(dispatch_at) first_at,max(dispatch_at) last_at "
    "from poll_runs where source_id='reuters_site_search' and dispatch_at>=? and dispatch_at<? "
    "and julianday(dispatch_at)>=julianday(?) and julianday(dispatch_at)<julianday(?) "
    "group by ticker,unit,status,reason", (lower, upper, args.since, now))]
out["containers"] = []
for role in ("v2-site-access", "v2-chrome-supervisor", "v2-message-collector", "v2-content-enrichment"):
    v = json.loads(subprocess.check_output(["docker", "inspect", "doxagent-v2-" + role + "-1"]))[0]
    env = dict(x.split("=", 1) for x in v["Config"]["Env"] if "=" in x)
    out["containers"].append({"role": role, "id": v["Id"][:12], "started_at": v["State"]["StartedAt"],
                              "commit": env.get("DOXAGENT_BUILD_COMMIT"), "restarts": v["RestartCount"]})
mem = {line.split(":", 1)[0]: line.split(":", 1)[1].strip() for line in Path("/proc/meminfo").read_text().splitlines()}
out["resources"] = {"disk_free": shutil.disk_usage("/").free, "mem_available": mem["MemAvailable"],
    "cpu_pressure": Path("/proc/pressure/cpu").read_text().strip(),
    "registry_wal_bytes": Path(str(registry) + "-wal").stat().st_size if Path(str(registry) + "-wal").exists() else 0}
print(json.dumps(out, indent=2))
r.close()
b.close()
