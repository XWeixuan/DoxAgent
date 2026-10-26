"""All-due-slot accounting, including pauses, missing requests and open-ended gaps."""

from collections import defaultdict
from datetime import datetime, timedelta
from math import ceil, floor


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def distribution(values):
    values = sorted(values)
    return {
        "samples": len(values),
        "mean": sum(values) / len(values) if values else None,
        "p95": values[max(0, ceil(len(values) * 0.95) - 1)] if values else None,
        "max": max(values) if values else None,
    }


def cadence(runs, captures, since, until):
    groups = defaultdict(lambda: {"schedule": [], "runs": [], "captures": []})
    for row in runs:
        if row["unit"].startswith("TAIL:"):
            continue
        key = row["binding_id"], row["unit"]
        groups[key]["schedule" if row["status"] == "SCHEDULE" else "runs"].append(row)
    for row in captures:
        if not row["unit"].startswith("TAIL:"):
            groups[(row["binding_id"], row["unit"])]["captures"].append(row)
    output = []
    all_gaps = []
    for (binding, unit), data in groups.items():
        schedules = sorted(data["schedule"], key=lambda r: r["dispatch_at"])
        expected = 0
        active = []
        eligibility_unknown = False
        for index, row in enumerate(schedules):
            end = (
                min(until, instant(schedules[index + 1]["dispatch_at"]))
                if index + 1 < len(schedules)
                else until
            )
            start = max(since, instant(row["dispatch_at"]))
            if row.get("reason") not in {"ENABLED", "ELIGIBILITY_UNKNOWN"} or start >= end:
                continue
            eligibility_unknown |= row.get("reason") == "ELIGIBILITY_UNKNOWN"
            epoch = instant(row.get("epoch_at") or row["due_at"])
            period = row["interval_seconds"]
            first = max(0, ceil((start - epoch).total_seconds() / period))
            last = floor(((end - epoch).total_seconds() - 0.000001) / period)
            expected += max(0, last - first + 1)
            active.append((epoch, period, start, end, instant(row["dispatch_at"])))
        seen, started, fresh, late_recovered = set(), set(), set(), set()
        terminal, inflight = set(), set()
        dispatch_late, start_late = [], []
        statuses = defaultdict(int)
        by_run = {r["run_id"]: r for r in data["runs"]}
        by_run.update(
            {
                r["shared_run_id"]: r
                for r in data["runs"]
                if r.get("shared_run_id") and r.get("requests")
            }
        )

        def slot(at, active=active):
            for epoch, period, begin, end, _ in active:
                if epoch <= at and begin <= at < end:
                    index = floor((at - epoch).total_seconds() / period)
                    if epoch + timedelta(seconds=index * period) >= begin:
                        return epoch.isoformat(), index
            return None

        def generation_matches(at, value, schedules=schedules):
            config = next((r for r in reversed(schedules) if instant(r["dispatch_at"]) <= at), None)
            return config is None or all(
                value.get(key) is None or config.get(key) is None or value[key] == config[key]
                for key in ("source_version", "binding_version")
            )

        for row in data["runs"]:
            if since <= instant(row["dispatch_at"]) <= until:
                statuses[row["status"]] += 1
            due = instant(row["due_at"])
            key = slot(due)
            if key:
                seen.add(key)
                (inflight if row["status"] == "DISPATCHED" else terminal).add(key)
            dispatch_late.append(max(0, (instant(row["dispatch_at"]) - due).total_seconds()))
            requests = [r for r in row.get("requests", []) if r.get("role", "listing") == "listing"]
            times = [
                instant(r["request_started_at"]) for r in requests if r.get("request_started_at")
            ]
            if times:
                start = min(times)
                start_late.append(max(0, (start - due).total_seconds()))
                current = slot(due)
                if current and generation_matches(start, row):
                    started.add(current)
        times = []
        for row in data["captures"]:
            at = instant(row["observed_at"])
            run = by_run.get(row["run_id"])
            listing_requests = [r for r in (run.get('requests', []) if run else [])
                                if r.get('role', 'listing') == 'listing' and r.get('request_started_at')]
            proven = row.get("request_started_at") or listing_requests
            valid = not run or run['status'] == 'CAPTURED'
            if at <= until and proven and valid and generation_matches(at, row):
                times.append(at)
            if run and valid and listing_requests:
                due = instant(run["due_at"])
                starts = [
                    instant(r["request_started_at"])
                    for r in run["requests"]
                    if r.get("request_started_at") and r.get("role", "listing") == "listing"
                ]
                requested = (
                    instant(row["request_started_at"])
                    if row.get("request_started_at")
                    else min(starts)
                    if starts
                    else due
                )
                key = slot(due)
                if (
                    key
                    and generation_matches(requested, run)
                    and due <= requested < due + timedelta(seconds=run["interval_seconds"])
                    and requested <= at <= requested + timedelta(seconds=run["interval_seconds"])
                ):
                    (fresh if at < due + timedelta(seconds=run['interval_seconds']) else late_recovered).add(key)
        times = sorted(set(times))
        prior = [at for at in times if at < since]
        in_window = [at for at in times if since <= at <= until]
        gaps = []
        exposure = []
        for _, _, begin, end, exposed_at in active:
            if exposure and exposure[-1][1] == begin:
                exposure[-1] = (exposure[-1][0], end, exposure[-1][2])
            else:
                exposure.append((begin, end, exposed_at))
        for begin, end, exposed_at in exposure:
            earlier = [at for at in times if exposed_at <= at < begin]
            inside = [at for at in times if begin <= at <= end]
            bounds = [earlier[-1] if earlier else begin, *inside, end]
            gaps.extend((b - a).total_seconds() for a, b in zip(bounds, bounds[1:], strict=False))
        if not active and not schedules:
            bounds = [prior[-1] if prior else since, *in_window, until]
            gaps = [(b - a).total_seconds() for a, b in zip(bounds, bounds[1:], strict=False)]
        all_gaps.extend(gaps)
        pending = set()
        for epoch, period, begin, end, _ in active:
            last = floor(((end - epoch).total_seconds() - 0.000001) / period)
            due = epoch + timedelta(seconds=last * period)
            key = (epoch.isoformat(), last)
            if (
                not eligibility_unknown
                and begin <= due < end
                and due + timedelta(seconds=period) > until
                and key not in fresh
                and key not in terminal
                and (key in inflight or (until - due).total_seconds() <= max(5, 0.2 * period))
            ):
                pending.add(key)
        settled = expected - len(pending)
        output.append(
            {
                "binding_id": binding,
                "unit": unit,
                "expected_cycles": expected,
                "dispatched_cycles": len(seen),
                "request_cycles": len(started),
                "fresh_cycles": len(fresh),
                "late_recovered_cycles": len(late_recovered - fresh),
                "pending_cycles": len(pending),
                "settled_cycles": settled,
                "missing_cycles": max(0, expected - len(seen) - len(pending - seen)),
                "all_cycle_coverage": len(fresh) / expected if expected else None,
                "coverage": len(fresh) / settled if settled else None,
                "statuses": dict(statuses),
                "dispatch_late_seconds": distribution(dispatch_late),
                "request_late_seconds": distribution(start_late),
                "fresh_gap_seconds": distribution(gaps),
                "refresh_age_seconds": (until - times[-1]).total_seconds() if times else None,
                "eligibility_recorded": bool(schedules) and not eligibility_unknown,
                "boundary_censored": not prior,
            }
        )
    expected = sum(r["expected_cycles"] for r in output)
    fresh = sum(r["fresh_cycles"] for r in output)
    settled = sum(r["settled_cycles"] for r in output)
    return {
        "expected_cycles": expected,
        "fresh_cycles": fresh,
        "missing_cycles": sum(r["missing_cycles"] for r in output),
        "pending_cycles": sum(r["pending_cycles"] for r in output),
        "settled_cycles": settled,
        "coverage": fresh / settled if settled else None,
        "all_cycle_coverage": fresh / expected if expected else None,
        "units": output,
        "basis": "ALL_SCHEDULED_CYCLES",
        "coverage_basis": "SETTLED_OR_OVERDUE_CYCLES",
        "request_basis": "TRANSPORT_OBSERVATION",
        "fresh_gap_seconds": distribution(all_gaps),
        "eligibility_unknown": sum(not r["eligibility_recorded"] for r in output),
    }
