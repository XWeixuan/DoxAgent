"""Read-only full-window receipts; no publisher, provider or browser operations."""
import argparse
import collections
import json
import re
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


r, b = read(registry), read(bus.with_name("acquisition.sqlite3"))
out = {"since": args.since, "until": now}
out["polling_owner"] = "acquisition.sqlite3; retired bus poll tables are not used"
counts = collections.Counter()
physical = set()
started, finished = {}, {}
for row in r.execute("select category,occurred_at,payload_json from access_events "
                     "where site_id='reuters' and occurred_at>=? and occurred_at<? "
                     "and julianday(occurred_at)>=julianday(?) "
                     "and julianday(occurred_at)<julianday(?) order by occurred_at", (lower, upper, args.since, now)):
    v = json.loads(row["payload_json"])
    counts[(row["category"], v.get("identity_id"), v.get("purpose"),
            v.get("disposition"), v.get("result"), v.get("reason") or v.get("reason_code"))] += 1
    if row["category"] == "ACCESS_RESULT":
        timings = v.get("stage_timings", {})
        keys = [k for k in timings if re.fullmatch(r"request_\d+_started_epoch_ms", k)]
        if not keys and timings.get("request_started_epoch_ms"):
            keys = ["request_started_epoch_ms"]
        for key in keys:
            at = timings[key]
            if isinstance(at, (int, float)) and at > 0:
                prefix = key.removesuffix("_started_epoch_ms")
                physical.add((v.get("identity_id"), v.get("purpose"), int(at), bool(timings.get(prefix + "_auxiliary"))))
    if v.get("probe_id") and row["category"] in {"PROBE_STARTED", "PROBE_FINISHED"}:
        target = started if row["category"] == "PROBE_STARTED" else finished
        target[v["probe_id"]] = {"at": row["occurred_at"], **select(v,
            "identity_id stage result request_sent total_ms reason_code control_failure_count")}
out["events"] = [{"category": k[0], "identity": k[1], "purpose": k[2], "disposition": k[3],
                  "result": k[4], "reason": k[5], "count": n} for k, n in counts.items()]
buckets = collections.Counter((i, p, datetime.fromtimestamp(at / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), aux)
                              for i, p, at, aux in physical)
out["observed_transport_starts"] = [{"identity": k[0], "purpose": k[1], "utc_minute": k[2],
                                     "auxiliary": k[3], "count": n} for k, n in sorted(buckets.items(), key=lambda x: str(x[0]))]
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
out["identity_runtime"] = []
out["identity_configuration"] = []
for row in r.execute("select v.spec_json from browser_identity_revisions v join browser_identity_heads h "
                     "on v.identity_id=h.identity_id and v.revision=h.active_revision "
                     "where h.identity_id in ('digitimes-de-1','reuters-backup')"):
    v = json.loads(row[0])
    out["identity_configuration"].append({**select(v, "identity_id runtime_kind profile_id"),
        "access": select(v.get("access", {}), "max_concurrency min_interval_ms")})
for row in r.execute("select data_json from identity_runtime where identity_id in ('digitimes-de-1','reuters-backup')"):
    v = json.loads(row[0])
    out["identity_runtime"].append(select(v, "identity_id operational_state recovered_at probation_until "
        "probation_interval_ms natural_success_at recent_relapses"))
out["auth"] = [select(json.loads(row[0]), "identity_id auth_state verified_at reason_code last_check_at last_check_reason")
               for row in r.execute("select data_json from site_identity_auth where site_id='reuters'")]
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
expected_queries = {}
by_ticker = {}
for row in b.execute("select ticker,unit,state,head_json from units where source_id='reuters_site_search' and required>0"):
    v = json.loads(row["state"])
    ticker, unit = row["ticker"], row["unit"]
    expected_queries.setdefault(ticker, set())
    if unit.startswith("QUERY:"):
        expected_queries[ticker].add(unit)
    projection = by_ticker.setdefault(ticker, {"ticker": ticker, "units": {}})
    projection["units"][unit] = select(v, "status last_success_at last_attempt_at last_error_code "
        "next_dispatch_at target_due_at schedule_interval_seconds")
out["poll_states"] = list(by_ticker.values())
out["poll_runs"] = [dict(row) for row in b.execute(
    "select ticker,unit,status,reason,count(*) n,min(dispatch_at) first_at,max(dispatch_at) last_at "
    "from cycles where source_id='reuters_site_search' and dispatch_at>=? and dispatch_at<? and status<>'SCHEDULE' "
    "and julianday(dispatch_at)>=julianday(?) and julianday(dispatch_at)<julianday(?) "
    "group by ticker,unit,status,reason", (lower, upper, args.since, now))]
cycles, seen, last_complete = collections.Counter(), collections.defaultdict(set), {}
for row in b.execute("select ticker,unit,dispatch_at from cycles where source_id='reuters_site_search' "
                      "and status='CAPTURED' and dispatch_at>=? and dispatch_at<? "
                      "and julianday(dispatch_at)>=julianday(?) and julianday(dispatch_at)<julianday(?) "
                      "order by dispatch_at", (lower, upper, args.since, now)):
    ticker, unit = row["ticker"], row["unit"]
    required = expected_queries.get(ticker, set())
    if unit not in required:
        continue
    seen[ticker].add(unit)
    if required and seen[ticker] >= required:
        cycles[ticker] += 1
        last_complete[ticker] = row["dispatch_at"]
        seen[ticker].clear()
out["query_coverage"] = [{"ticker": ticker, "required_queries": len(required),
    "captured_complete_cycles": cycles[ticker], "last_complete_dispatch_at": last_complete.get(ticker),
    "seen_in_incomplete_cycle": len(seen[ticker])} for ticker, required in expected_queries.items()]
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
