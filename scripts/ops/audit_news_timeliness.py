"""Read-only, bounded source/body audit; no CDC history scan or business writes."""

import argparse
import json
import math
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def audit(path, since, until):
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result = {
            "window": {"since": since, "until": until},
            "cohort_basis": "first observed canonical article",
        }
        if "article_body_results" in tables:
            rows = db.execute(
                "SELECT status,count(*),sum(json_extract(data_json,'$.outcome') IN "
                "('FULL','SHORT_FULL')) FROM article_body_results WHERE "
                "first_observed_at>=? AND first_observed_at<? AND "
                "coalesce(json_extract(data_json,'$.eligible'),1)=1 AND "
                "coalesce(json_extract(data_json,'$.legacy_result'),0)=0 GROUP BY status",
                (since, until),
            ).fetchall()
            total = sum(r[1] for r in rows)
            full = sum(r[2] or 0 for r in rows)
            result["new_articles"] = {
                "total": total,
                "full": full,
                "ratio": full / total if total else None,
                "statuses": {r[0]: r[1] for r in rows},
            }
        else:
            result["new_articles"] = {"status": "NOT_INSTRUMENTED_LEGACY"}
        # Limit business row materialization; technical timing does not read stored bodies.
        polls = db.execute(
            "SELECT binding_id,status,json_extract(data_json,'$.last_latency_ms') AS "
            "latency_ms,json_extract(data_json,'$.last_stage_timings') AS "
            "stages,json_extract(data_json,'$.last_capture_at') AS "
            "capture_at,json_extract(data_json,'$.next_dispatch_at') AS "
            "next_dispatch_at FROM poll_states ORDER BY binding_id LIMIT 1000"
        ).fetchall()
        result["latest_poll_states"] = [dict(r) for r in polls]
        result["latest_poll_states_basis"] = "latest per binding, not a time-window average"
        if "content_enrichment_jobs" in tables:
            result["jobs"] = [
                dict(r)
                for r in db.execute(
                    "SELECT status,count(*) AS count FROM content_enrichment_jobs WHERE "
                    "created_at>=? AND created_at<? GROUP BY status",
                    (since, until),
                )
            ]
        attempts = db.execute(
            "SELECT json_extract(data_json,'$.payload.source_id') AS "
            "source_id,count(*) AS "
            "executions,sum(json_extract(data_json,'$.payload.succeeded')) AS "
            "succeeded,sum(json_extract(data_json,'$.payload.network_attempted')) AS "
            "network_attempts,sum(json_extract(data_json,'$.payload.cache_reused')) AS "
            "cache_reuses FROM audit_log WHERE entity_type='body_completion' AND "
            "created_at>=? AND created_at<? GROUP BY source_id",
            (since, until),
        ).fetchall()
        result["executions"] = [dict(r) for r in attempts]
        result["cadence"] = audit_cadence(db, tables, since, until)
    return result


def audit_cadence(db, tables, since, until):
    """Physical capture/request ledger; missing cycles remain in the denominator."""
    if not {"poll_runs", "poll_captures"} <= tables:
        return {"status": "NOT_INSTRUMENTED_LEGACY"}
    from doxagent.message_bus_v2.cadence import cadence, distribution

    limit = 100000
    runs = [
        dict(r)
        for r in db.execute(
            "SELECT * FROM poll_runs WHERE status!='SCHEDULE' AND dispatch_at>=? AND "
            "dispatch_at<? "
            "ORDER BY dispatch_at DESC LIMIT ?",
            (since, until, limit),
        )
    ]
    schedules = [
        dict(r)
        for r in db.execute(
            "SELECT * FROM poll_runs WHERE status='SCHEDULE' AND dispatch_at<? "
            "ORDER BY dispatch_at DESC LIMIT 20000",
            (until,),
        )
    ]
    for row in [*schedules, *runs]:
        row.update(json.loads(row.pop("data_json")))
    columns = (
        "capture_id,run_id,binding_id,source_id,ticker,unit,observed_at,committed_at,"
        "status,message_count,intake_position,first_intake_at,completed_at,targets_json,request_started_at,"
        "source_version,binding_version"
    )
    captures = [
        dict(r)
        for r in db.execute(
            f"SELECT {columns} FROM poll_captures WHERE observed_at>=? AND observed_at<? "
            "ORDER BY observed_at DESC LIMIT ?",
            (since, until, limit),
        )
    ]
    for binding, unit in {(r["binding_id"], r["unit"]) for r in [*runs, *schedules]}:
        prior = db.execute(
            f"SELECT {columns} FROM poll_captures WHERE binding_id=? AND unit=? "
            "AND observed_at<? ORDER BY observed_at DESC LIMIT 1",
            (binding, unit, since),
        ).fetchone()
        if prior:
            captures.append(dict(prior))
    fanout = []
    for row in captures:
        targets = json.loads(row.get("targets_json") or "[]")
        fanout.extend({**row, **target} for target in (targets or [{}]))
    result = cadence(
        [*schedules, *runs], fanout, datetime.fromisoformat(since), datetime.fromisoformat(until)
    )
    result["sample_limit_reached"] = (
        len(runs) >= limit or len(captures) >= limit or len(schedules) >= 20000
    )
    # Do not multiply shared network cost by its subscribed tickers.
    requests = {}
    for row in runs:
        for request in row.get("requests", []):
            key = request.get("request_key"), request.get("request_started_at")
            requests[key] = request
    costs = []
    for request in requests.values():
        if request.get("response_received_at") and request.get("request_started_at"):
            costs.append(
                (
                    datetime.fromisoformat(request["response_received_at"])
                    - datetime.fromisoformat(request["request_started_at"])
                ).total_seconds()
            )
    result["physical_request_cost_seconds"] = distribution(costs)
    result["physical_requests"] = len(requests)
    result["capture_intake"] = [
        dict(r)
        for r in db.execute(
            "SELECT status,count(*) AS captures,sum(message_count-intake_position) AS "
            "remaining_candidates,"
            "min(observed_at) AS oldest_observed_at FROM poll_captures "
            "WHERE status NOT IN ('COMPLETED','CANCELLED') GROUP BY status"
        )
    ]
    intake = [
        (
            datetime.fromisoformat(r["first_intake_at"]) - datetime.fromisoformat(r["observed_at"])
        ).total_seconds()
        for r in captures
        if r.get("first_intake_at") and r["observed_at"] >= since
    ]
    complete = [
        (
            datetime.fromisoformat(r["completed_at"]) - datetime.fromisoformat(r["observed_at"])
        ).total_seconds()
        for r in captures
        if r.get("completed_at") and r["observed_at"] >= since
    ]
    result["capture_to_first_intake_seconds"] = distribution(intake)
    result["capture_to_complete_intake_seconds"] = distribution(complete)
    return result


def audit_poll_window(path, since, until):
    """Bounded Read history; never scan source CDC or materialize article bodies."""
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as db:
        db.execute("PRAGMA query_only=ON")
        rows = db.execute(
            "SELECT id,payload,valid_from FROM object_current WHERE kind='native:poll_states' "
            "UNION ALL SELECT id,payload,valid_from FROM object_history WHERE kind='native:poll_states' "
            "ORDER BY valid_from DESC LIMIT 20000"
        ).fetchall()
    captures = {}
    since_at, until_at = datetime.fromisoformat(since), datetime.fromisoformat(until)
    for binding, payload, _ in rows:
        value = json.loads(payload)
        at = value.get("last_dispatch_at") or value.get("last_attempt_at")
        if at and since_at <= datetime.fromisoformat(at) < until_at:
            captures.setdefault((binding, at), value)
    groups = {}

    def summary(values):
        values = sorted(values)
        return {
            "samples": len(values),
            "mean": sum(values) / len(values) if values else None,
            "p95": values[max(0, math.ceil(0.95 * len(values)) - 1)] if values else None,
        }

    for (binding, _), value in captures.items():
        item = groups.setdefault(
            binding,
            {
                "captures": set(),
                "latencies": [],
                "accept_delays": [],
                "stages": {},
                "deferred": 0,
                "failed": 0,
                "pending_queries": 0,
                "pending_timestamps": 0,
            },
        )
        deferred = (value.get("last_error_code") or "").startswith("SOURCE_DEFERRED:")
        item["deferred"] += deferred
        item["failed"] += value.get("status") == "failed"
        if (
            not deferred
            and value.get("status") == "succeeded"
            and value.get("last_latency_ms") is not None
        ):
            item["latencies"].append(value["last_latency_ms"])
            for stage, elapsed in (value.get("last_stage_timings") or {}).items():
                if isinstance(elapsed, (float, int)):
                    item["stages"].setdefault(stage, []).append(elapsed)
            capture = value.get("last_capture_at")
            if capture:
                item["captures"].add(capture)
                if value.get("last_first_intake_at") and value.get("last_first_capture_at"):
                    item["accept_delays"].append(
                        max(
                            0,
                            (
                                datetime.fromisoformat(value["last_first_intake_at"])
                                - datetime.fromisoformat(value["last_first_capture_at"])
                            ).total_seconds()
                            * 1000,
                        )
                    )
        checkpoint = value.get("checkpoint") or {}
        item["pending_queries"] = max(
            item["pending_queries"],
            sum(
                not q.get("done", False)
                for q in checkpoint.get("queries", {}).values()
                if isinstance(q, dict)
            ),
        )
        item["pending_timestamps"] = max(
            item["pending_timestamps"], len(checkpoint.get("pending_timestamp_rows", []))
        )
    for item in groups.values():
        timestamps = sorted(datetime.fromisoformat(t) for t in item.pop("captures"))
        item["fresh_capture_interval_seconds"] = summary(
            [(b - a).total_seconds() for a, b in zip(timestamps, timestamps[1:])]
        )
        item["poll_ms"] = summary(item.pop("latencies"))
        item["capture_to_first_intake_ms"] = summary(item.pop("accept_delays"))
        item["stages_ms"] = {key: summary(values) for key, values in item.pop("stages").items()}
    return {
        "bindings": groups,
        "sample_limit_reached": len(rows) >= 20000,
        "stage_basis": "nested stage durations overlap; do not add them as independent percentages",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bus", required=True)
    parser.add_argument("--since", required=True, help="UTC ISO timestamp")
    parser.add_argument("--until", default=datetime.now(UTC).isoformat())
    parser.add_argument("--out")
    parser.add_argument(
        "--read-store", help="optional Read SQLite for bounded historical poll timings"
    )
    parser.add_argument(
        "--pid",
        type=int,
        action="append",
        default=[],
        help="optional Linux service PID for RSS and CPU counters",
    )
    args = parser.parse_args()
    since, until = (
        datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(UTC)
        for v in (args.since, args.until)
    )
    if until <= since or (until - since).total_seconds() > 3 * 86400:
        parser.error("window must be positive and no longer than 3 days")
    result = audit(args.bus, since.isoformat(), until.isoformat())
    if args.read_store:
        result["poll_window"] = audit_poll_window(
            args.read_store, since.isoformat(), until.isoformat()
        )
    else:
        result["poll_window"] = {"status": "NOT_SAMPLED", "reason": "--read-store not provided"}
    result["process_resources"] = []
    for pid in args.pid:
        proc = Path("/proc") / str(pid)
        try:
            status = dict(
                line.split(":", 1)
                for line in (proc / "status").read_text().splitlines()
                if ":" in line
            )
            result["process_resources"].append(
                {
                    "pid": pid,
                    "rss": status.get("VmRSS", "unavailable").strip(),
                    "cpu_stat": (proc / "stat").read_text().strip(),
                    "basis": "instant RSS and cumulative CPU counter",
                }
            )
        except OSError as exc:
            result["process_resources"].append({"pid": pid, "status": type(exc).__name__})
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
