"""Reduce only one affected Case. All dashboard requests continue to read buckets."""

import json
from datetime import datetime


def span_union(intervals, start, end):
    merged = []
    for a, b in sorted((max(a, start), min(b, end)) for a, b in intervals if b > start and a < end):
        if b < a:
            raise ValueError("reversed model interval")
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(b, merged[-1][1])
        else:
            merged.append([a, b])
    return sum((b - a).total_seconds() for a, b in merged)


def project(store, ticker, summary, incoming):
    cid = summary["case_id"]
    native = store.get("native:runtime_v2_cases", ticker, cid) or {}
    with store.connect() as db:
        attempts = {
            r[0]: json.loads(r[1])
            for r in db.execute(
                "SELECT id,payload FROM object_current WHERE kind='attempt' "
                "AND ticker=? AND parent=?",
                (ticker, cid),
            )
        }
        effects = {
            r[0]: json.loads(r[1])
            for r in db.execute(
                "SELECT id,payload FROM object_current WHERE kind='native:runtime_v2_effects' "
                "AND ticker=? AND parent=?",
                (ticker, cid),
            )
        }
    for r in incoming:
        if r["kind"] == "native:runtime_v2_cases" and r["id"] == cid:
            native = r.get("data") or {}
        if r.get("parent") == cid and r.get("data"):
            if r["kind"] == "attempt":
                attempts[r["id"]] = r["data"]
            if r["kind"] == "native:runtime_v2_effects":
                effects[r["id"]] = r["data"]
    trade = [a for a in attempts.values() if a.get("phase") == "trade_decision"]
    model_seconds = non_model = None
    good = native.get("runtime_mode") == "REALTIME" and native.get("w3_trade_decision")
    starts = [(a.get("model_interval") or {}).get("start") for a in trade]
    wins = [
        a for a in trade if a["status"] == "SUCCEEDED" and a.get("model_completed_at") is not None
    ]
    if good and starts and all(starts) and wins:
        finish = max(float(a["model_completed_at"]) for a in wins)
        first = min(datetime.fromisoformat(s).timestamp() for s in starts)
        duration = finish - int(first)  # SDK completion is explicitly Unix seconds.
        if duration >= 0:
            model_seconds = duration
    effective = next(
        (
            e.get("payload", {}).get("decision_effective_at")
            for e in effects.values()
            if e.get("payload", {}).get("decision_effective_at")
        ),
        None,
    )
    if good and effective and native.get("created_at"):
        begin, end = datetime.fromisoformat(native["created_at"]), datetime.fromisoformat(effective)
        calls = [a for a in attempts.values() if a.get("phase") != "post_decision_operations"]
        intervals = [a.get("model_interval") or {} for a in calls]
        if calls and all(v.get("start") and v.get("end") for v in intervals) and end >= begin:
            covered = span_union(
                [
                    (datetime.fromisoformat(v["start"]), datetime.fromisoformat(v["end"]))
                    for v in intervals
                ],
                begin,
                end,
            )
            non_model = (end - begin).total_seconds() - covered
    from doxagent.persistent_runtime_v2.case_outcomes import effective_policy

    policy = effective_policy(native)[0]
    related = (
        bool(policy.get("policy_related") or policy.get("related_policy_ids")) if policy else None
    )
    values = {
        "related_cases": int(related) if related is not None else None,
        "related_cases_samples": int(related is not None),
        "w3_model_le600_seconds": model_seconds
        if model_seconds is not None and model_seconds <= 600
        else None,
        "w3_model_le600_samples": int(model_seconds is not None and model_seconds <= 600),
        "long_w3_cases": int(model_seconds > 180) if model_seconds is not None else None,
        "long_w3_cases_samples": int(model_seconds is not None),
        "non_model_seconds": non_model,
        "non_model_samples": int(non_model is not None),
    }
    summary = {
        **summary,
        "runtime_tags": ["LONG_W3_DECISION"]
        if model_seconds is not None and model_seconds > 180
        else [],
    }
    tag = dict(
        kind="case_tag",
        ticker=ticker,
        id=cid,
        parent="LONG_W3_DECISION",
        day=summary["semantic_day"],
        sort=summary["received_at"],
        data={"case_id": cid} if summary["runtime_tags"] else None,
    )
    counts = [
        dict(metric=k, ticker=ticker, entity=cid, day=summary["semantic_day"], value=v)
        for k, v in values.items()
    ]
    return summary, tag, counts
