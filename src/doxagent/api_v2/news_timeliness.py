"""Bounded read-model queries for discovery cohorts and actual poll windows."""

import json
import math
from datetime import date, datetime, timedelta

from doxagent.semantic_clock import boundary, semantic_day


def body_cohort(store, ticker, seq, filters, days):
    table = store.snapshot_table(seq)
    where = [
        "o.kind='article_observation'",
        "o.ticker=?",
        "o.valid_from<=?",
        "(o.valid_to IS NULL OR o.valid_to>?)",
    ]
    args = [ticker, seq, seq]
    if days is not None:
        if not days:
            where.append("0")
        else:
            where.extend(
                [
                    "json_extract(b.payload,'$.first_observed_at')>=?",
                    "json_extract(b.payload,'$.first_observed_at')<?",
                ]
            )
            args.extend(
                [
                    boundary(date.fromisoformat(min(days))).isoformat(),
                    boundary(date.fromisoformat(max(days)) + timedelta(days=1)).isoformat(),
                ]
            )
    if filters.get("source_id"):
        where.append("o.source_id=?")
        args.append(filters["source_id"])
    if filters.get("q"):
        where.append("instr(o.search_text,?)>0")
        args.append(filters["q"].casefold())
    if filters.get("source_kind"):
        where.append(
            f"EXISTS(SELECT 1 FROM {table} s WHERE s.kind='native:source_definitions' AND s.id=o.source_id AND s.valid_from<=? AND (s.valid_to IS NULL OR s.valid_to>?) AND json_extract(s.payload,'$.kind')=?)"
        )
        args.extend([seq, seq, filters["source_kind"]])
    # Article canonical identity defines the denominator, including failures never published.
    with store.connect() as db:
        rows = db.execute(
            f"SELECT DISTINCT o.parent,b.payload FROM {table} o JOIN {table} b "
            "ON b.kind='native:article_body_results' AND b.ticker='' AND b.id=o.parent "
            "AND b.valid_from<=? AND (b.valid_to IS NULL OR b.valid_to>?) WHERE "
            + " AND ".join(where),
            [seq, seq, *args],
        ).fetchall()
    unique = {}
    for key, payload in rows:
        value = json.loads(payload)
        if not value.get("eligible", True) or value.get("legacy_result"):
            continue
        day = semantic_day(datetime.fromisoformat(value["first_observed_at"])).isoformat()
        if days is None or day in days:
            unique[key] = value
    full = sum(v.get("outcome") in {"FULL", "SHORT_FULL"} for v in unique.values())
    pending = sum(v.get("status") == "PENDING" for v in unique.values())
    with store.connect() as db:
        attempts = db.execute(
            f"SELECT coalesce(sum(json_extract(payload,'$.network_attempted')),0),coalesce(sum(json_extract(payload,'$.network_attempted') AND json_extract(payload,'$.succeeded')),0),coalesce(sum(json_extract(payload,'$.cache_reused')),0) FROM {table} WHERE kind='body_attempt' AND ticker=? AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) AND json_extract(payload,'$.body_result_key') IN (SELECT value FROM json_each(?))",
            (ticker, seq, seq, json.dumps(list(unique))),
        ).fetchone()
    return {
        "total": len(unique),
        "full": full,
        "pending": pending,
        "failed": len(unique) - full - pending,
        "ratio": full / len(unique) if unique else None,
        "network_attempts": attempts[0],
        "network_succeeded": attempts[1],
        "cache_reuses": attempts[2],
        "basis": "FIRST_OBSERVED_ARTICLE",
        "legacy_excluded": True,
    }


def poll_window(store, ticker, seq, as_of):
    until = datetime.fromisoformat(as_of)
    since = until - timedelta(minutes=30)
    days = sorted({semantic_day(since).isoformat(), semantic_day(until).isoformat()})
    clause = "kind='native:poll_states' AND ticker=? AND day IN (SELECT value FROM json_each(?)) AND valid_from<=?"
    with store.connect() as db:
        rows = db.execute(
            "SELECT id,json_extract(payload,'$.last_dispatch_at'),json_extract(payload,'$.last_attempt_at'),"
            "json_extract(payload,'$.status'),json_extract(payload,'$.last_latency_ms'),"
            "json_extract(payload,'$.last_error_code'),valid_from FROM object_current WHERE "
            + clause
            + " UNION ALL SELECT id,json_extract(payload,'$.last_dispatch_at'),json_extract(payload,'$.last_attempt_at'),"
            "json_extract(payload,'$.status'),json_extract(payload,'$.last_latency_ms'),"
            "json_extract(payload,'$.last_error_code'),valid_from FROM "
            + store.history_table
            + " WHERE "
            + clause
            + " ORDER BY valid_from DESC LIMIT 4000",
            (ticker, json.dumps(days), seq, ticker, json.dumps(days), seq),
        ).fetchall()
    captures = {}
    for binding, dispatch, attempt, status, latency, error, _ in rows:
        value = dict(status=status,last_latency_ms=latency,last_error_code=error)
        at = dispatch or attempt
        if at and since <= datetime.fromisoformat(at) <= until:
            captures.setdefault((binding, at), value)
    succeeded = [
        v
        for v in captures.values()
        if v.get("status") == "succeeded"
        and v.get("last_latency_ms") is not None
        and not (v.get("last_error_code") or "").startswith("SOURCE_DEFERRED:")
    ]
    latencies = sorted(v["last_latency_ms"] / 1000 for v in succeeded)
    return {
        "cadence": poll_cadence(store, ticker, seq, since, until),
        "window_seconds": 1800,
        "samples": len(latencies),
        "mean_seconds": sum(latencies) / len(latencies) if latencies else None,
        "p95_seconds": latencies[max(0, math.ceil(0.95 * len(latencies)) - 1)]
        if latencies
        else None,
        "deferred": sum(
            (v.get("last_error_code") or "").startswith("SOURCE_DEFERRED:")
            for v in captures.values()
        ),
        "failed": sum(v.get("status") == "failed" for v in captures.values()),
        "sample_limit_reached": len(rows) >= 4000,
    }


def poll_cadence(store, ticker, seq, since, until, binding_id=None, *, by_binding=False):
    from doxagent.message_bus_v2.cadence import cadence

    table = store.snapshot_table(seq)
    args = [ticker, seq, seq]
    where = "ticker=? AND valid_from<=? AND (valid_to IS NULL OR valid_to>?)"
    if binding_id:
        where += " AND json_extract(payload,'$.binding_id')=?"
        args.append(binding_id)
    with store.connect() as db:
        schedules = db.execute(
            f"SELECT payload FROM {table} WHERE kind='poll_schedule' AND "
            + where
            + " ORDER BY valid_from DESC LIMIT 4000",
            args,
        ).fetchall()
        # Physical branches avoid materializing the entire technical ledger at
        # a pinned watermark. Time is read from native scalars, including rows
        # written before the poll projector's sort metadata was corrected.
        from doxagent.v2_read.serving_schema import history_relation
        relations = ['object_current',history_relation(db)]
        def recent(kind):
            rows=[]
            for relation in relations:
                rows.extend(db.execute(
                    f"SELECT payload,valid_from FROM {relation} WHERE kind=? AND "+where+
                    " AND kind IN ('native:poll_runs','native:poll_captures')"
                    " AND coalesce(json_extract(payload,'$.dispatch_at'),json_extract(payload,'$.observed_at'))>=?"
                    " AND coalesce(json_extract(payload,'$.dispatch_at'),json_extract(payload,'$.observed_at'))<=?"
                    " ORDER BY coalesce(json_extract(payload,'$.dispatch_at'),json_extract(payload,'$.observed_at')) DESC LIMIT 16000",
                    [kind,*args,since.isoformat(),until.isoformat()]).fetchall())
            return sorted(rows,key=lambda row: row[1],reverse=True)[:16000]
        runs=recent('native:poll_runs')
        captures=recent('native:poll_captures')
    capture_rows = [json.loads(r[0]) for r in captures]
    run_rows = [json.loads(r[0]) for r in [*schedules, *runs]]
    limited = len(runs) >= 16000 or len(captures) >= 16000 or len(schedules) >= 4000
    if by_binding:
        from collections import defaultdict

        grouped = defaultdict(lambda: [[], []])
        for row in run_rows:
            grouped[row["binding_id"]][0].append(row)
        for row in capture_rows:
            grouped[row["binding_id"]][1].append(row)
        return {
            key: _cadence_summary(cadence(r, c, since, until), c, limited)
            for key, (r, c) in grouped.items()
        }
    return _cadence_summary(cadence(run_rows, capture_rows, since, until), capture_rows, limited)


def _cadence_summary(result, capture_rows, limited):
    gaps = [
        u["fresh_gap_seconds"]["max"]
        for u in result["units"]
        if u["fresh_gap_seconds"]["max"] is not None
    ]
    late = [
        u["request_late_seconds"]["p95"]
        for u in result["units"]
        if u["request_late_seconds"]["p95"] is not None
    ]
    return {
        "expected_cycles": result["expected_cycles"],
        "fresh_cycles": result["fresh_cycles"],
        "pending_cycles":result['pending_cycles'],
        "settled_cycles":result['settled_cycles'],
        "pending_intake_candidates": sum(
            max(0, r["message_count"] - r["intake_position"])
            for r in capture_rows
            if r["status"] in {"PENDING", "RUNNING"}
        ),
        "failed_intake_candidates": sum(
            max(0, r["message_count"] - r["intake_position"])
            for r in capture_rows
            if r["status"] == "FAILED"
        ),
        "missing_cycles": result["missing_cycles"],
        "coverage": result["coverage"],
        "max_gap_seconds": max(gaps) if gaps else None,
        "gap_mean_seconds": result["fresh_gap_seconds"]["mean"],
        "gap_p95_seconds": result["fresh_gap_seconds"]["p95"],
        "deferred": sum(u["statuses"].get("DEFERRED", 0) for u in result["units"]),
        "failed": sum(
            u["statuses"].get("FAILED", 0) + u["statuses"].get("CANCELLED", 0)
            for u in result["units"]
        ),
        "worst_request_p95_seconds": max(late) if late else None,
        "units": len(result["units"]),
        "eligibility_unknown": result["eligibility_unknown"],
        "sample_limit_reached": limited,
    }
