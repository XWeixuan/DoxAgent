"""Recompute bounded natural-flow evidence; no network/model/business writes."""

import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
data = json.loads((ROOT / "natural_samples.json").read_text(encoding="utf-8"))
jobs = {job["attempt_id"]: job for job in data["jobs"]}
cases = {case["case_id"]: case for case in data["case_results"]}
receipts = sorted(data["receipts"], key=lambda r: r["started_at"])


def percentile(values, p, digits=3):
    values = sorted(v for v in values if v is not None)
    return round(values[max(0, math.ceil(len(values) * p) - 1)], digits) if values else None


def stats(values, digits=3):
    values = [v for v in values if v is not None]
    return {
        "n": len(values),
        "p50": percentile(values, 0.5, digits),
        "p90": percentile(values, 0.9, digits),
        "max": max(values) if values else None,
    }


def elapsed(start, end):
    if not start or not end:
        return None
    return (
        datetime.fromisoformat(end.replace("Z", "+00:00"))
        - datetime.fromisoformat(start.replace("Z", "+00:00"))
    ).total_seconds() * 1000


metrics, rounds = [], []
for receipt in receipts:
    job = jobs.get(receipt["attempt_id"], {})
    case = cases.get(receipt["case_id"], {})
    responses = receipt.get("responses", [])
    sdk_ms = job.get("sdk_duration_ms")
    complete = (
        receipt["status"] == "completed"
        and receipt.get("coverage", {}).get("turn_start_found")
        and not receipt.get("coverage", {}).get("native_scan_truncated")
        and not receipt.get("row_cap_reached")
    )
    first = responses[0]["usage"] if responses else {}
    last = responses[-1]["usage"] if responses else {}
    tools = receipt.get("tools", [])
    metrics.append(
        {
            "case_id": receipt["case_id"],
            "turn_id": receipt["turn_id"],
            "release": receipt["release_revision"],
            "ticker": receipt.get("ticker"),
            "case_type": receipt.get("case_type"),
            "model": receipt.get("model"),
            "effort": receipt.get("effort"),
            "observation_status": receipt["status"],
            "job_status": job.get("status"),
            "case_status": case.get("status"),
            "decision": (case.get("w3_trade_decision") or {}).get("decision"),
            "complete": bool(complete),
            "sdk_ms": sdk_ms,
            "observed_stream_ms": receipt.get("elapsed_ms"),
            "tool_union_ms": receipt.get("tool_union_ms"),
            "sdk_minus_observed_tools_ms": sdk_ms - receipt["tool_union_ms"]
            if sdk_ms is not None and receipt.get("tool_union_ms") is not None
            else None,
            "rounds": len(responses),
            "tool_batches": receipt.get("tool_count"),
            "first_input_tokens": first.get("input_tokens"),
            "last_input_tokens": last.get("input_tokens"),
            "last_cached_input_tokens": last.get("cached_input_tokens"),
            "last_uncached_input_tokens": last["input_tokens"] - last["cached_input_tokens"]
            if last
            else None,
            "input_growth_tokens": last["input_tokens"] - first["input_tokens"]
            if responses
            else None,
            "tool_output_bytes": receipt.get("total_output_bytes"),
            "last_after_tool_gap_ms": responses[-1].get("after_last_tool_return_ms")
            if responses
            else None,
            "control_batches": receipt.get("business_groups", {})
            .get("CONTROL_CONTRACT", {})
            .get("calls", 0),
            "control_batch_output_bytes": receipt.get("business_groups", {})
            .get("CONTROL_CONTRACT", {})
            .get("output_bytes", 0),
            "navigation_batches": receipt.get("business_groups", {})
            .get("RESEARCH_NAVIGATION", {})
            .get("calls", 0),
            "web_batches": receipt.get("business_groups", {})
            .get("WEB_RESEARCH", {})
            .get("calls", 0),
            "first_output_truncated": bool(tools and tools[0].get("output_truncated_marker")),
            "truncated_batches": sum(bool(t.get("output_truncated_marker")) for t in tools),
            "exact_repeat_candidates": len(receipt.get("repeat_candidates", [])),
            "error_marker_batches": sum(bool(t.get("failure_markers")) for t in tools),
            "projection_ms": receipt.get("projection_ms"),
            "case_to_decision_ms": elapsed(
                case.get("created_at"), case.get("decision_completed_at")
            ),
            "worker_runtime_start_ms": job.get("resource_receipt", {}).get("cold_start_ms"),
            "acknowledged": job.get("acknowledged"),
            "job_observation_reference": bool(
                job.get("resource_receipt", {}).get("w3_observation")
            ),
        }
    )
    for row in responses:
        rounds.append(
            {
                "case_id": receipt["case_id"],
                "turn_id": receipt["turn_id"],
                "response_id": row["response_id"],
                "sequence": row["sequence"] + 1,
                "at_utc": row["at_utc"],
                "issued_tool_call_ids": "|".join(row["issued_tool_call_ids"]),
                "preceding_tool_call_ids": "|".join(row["preceding_tool_call_ids"]),
                **row["usage"],
                "input_growth_tokens": row["input_growth_tokens"],
                "preceding_output_bytes": row["preceding_tool_output_bytes"],
                "after_tool_gap_ms": row["after_last_tool_return_ms"],
                "usage_interval_ms": row["since_previous_usage_ms"],
                "interval_tool_ms": row["observed_round_tool_ms"],
                "interval_residual_ms": row["observed_round_residual_ms"],
                "action_labels": "|".join(row["action_business_labels"]),
                "after_labels": "|".join(row["after_business_labels"]),
            }
        )

for name, rows in (("case_metrics.csv", metrics), ("round_metrics.csv", rounds)):
    with (ROOT / name).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

valid = [row for row in metrics if row["complete"]]
fields = (
    "sdk_ms",
    "tool_union_ms",
    "sdk_minus_observed_tools_ms",
    "rounds",
    "tool_batches",
    "first_input_tokens",
    "last_input_tokens",
    "input_growth_tokens",
    "last_uncached_input_tokens",
    "tool_output_bytes",
    "last_after_tool_gap_ms",
    "control_batches",
    "control_batch_output_bytes",
    "navigation_batches",
    "web_batches",
    "projection_ms",
    "case_to_decision_ms",
    "worker_runtime_start_ms",
)
grouped = defaultdict(list)
for row in metrics:
    grouped[
        (
            row["release"],
            row["case_type"],
            row["ticker"],
            row["decision"],
            row["observation_status"],
        )
    ].append(row)
groups = [
    {
        **dict(
            zip(
                ("release", "case_type", "ticker", "decision", "observation_status"),
                key,
                strict=True,
            )
        ),
        "count": len(rows),
        "sdk_ms": stats([r["sdk_ms"] for r in rows]),
        "rounds": stats([r["rounds"] for r in rows]),
    }
    for key, rows in sorted(grouped.items())
]

business = {}
for label in sorted({label for r in receipts for label in r.get("business_groups", {})}):
    selected = [
        r["business_groups"][label] for r in receipts if label in r.get("business_groups", {})
    ]
    business[label] = {
        "cases": len(selected),
        "batches": sum(g["calls"] for g in selected),
        "output_bytes": sum(g["output_bytes"] for g in selected),
        "per_matching_case_tool_union_ms": stats([g["observed_tool_union_ms"] for g in selected]),
    }


def correlation(left, right):
    pairs = [(r[left], r[right]) for r in valid if r[left] is not None and r[right] is not None]
    xs, ys = zip(*pairs, strict=True)
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    denominator = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
    return (
        round(sum((x - mx) * (y - my) for x, y in pairs) / denominator, 4) if denominator else None
    )


summary = {
    "as_of": data["at"],
    "percentile_definition": "nearest-rank ceil(n*p)",
    "receipt_count": len(receipts),
    "unique_cases": len({r["case_id"] for r in receipts}),
    "unique_turns": len({r["turn_id"] for r in receipts}),
    "complete": len(valid),
    "statuses": dict(Counter(r["observation_status"] for r in metrics)),
    "job_statuses": dict(Counter(r["job_status"] for r in metrics)),
    "case_statuses": dict(Counter(r["case_status"] for r in metrics)),
    "case_types": dict(Counter(r["case_type"] for r in metrics)),
    "decisions": dict(Counter(r["decision"] for r in metrics)),
    "models": dict(Counter(r["model"] + "/" + r["effort"] for r in metrics)),
    "metrics": {field: stats([r[field] for r in valid]) for field in fields},
    "groups": groups,
    "business_labels_overlapping": business,
    "response_count": len(rounds),
    "tool_batches": sum(r["tool_batches"] for r in metrics),
    "repeated_control_cases": sum(r["control_batches"] > 1 for r in metrics),
    "first_truncated_cases": sum(r["first_output_truncated"] for r in metrics),
    "truncated_batches": sum(r["truncated_batches"] for r in metrics),
    "exact_repeat_candidate_cases": sum(bool(r["exact_repeat_candidates"]) for r in metrics),
    "error_marker_cases": sum(bool(r["error_marker_batches"]) for r in metrics),
    "error_marker_batches": sum(r["error_marker_batches"] for r in metrics),
    "compaction_cases": sum(bool(r["compactions"]) for r in receipts),
    "sdk_compaction_cases": sum(
        any("compact" in str(i.get("item_type", "")).lower() for i in r["sdk_items"])
        for r in receipts
    ),
    "explicit_layer_path_cases": {
        layer: sum(
            any(
                any(path.startswith(layer + "/") for path in tool.get("research_resources", []))
                for tool in r["tools"]
            )
            for r in receipts
        )
        for layer in ("d1", "d2", "d3")
    },
    "provider_calls": sum(len(r["provider_calls"]) for r in receipts),
    "projection_fraction": stats(
        [r["projection_ms"] / r["sdk_ms"] for r in valid if r["sdk_ms"]], 6
    ),
    "tool_fraction_total": sum(r["tool_union_ms"] for r in valid) / sum(r["sdk_ms"] for r in valid),
    "descriptive_pearson_not_causal": {
        key: correlation(key, "sdk_ms")
        for key in ("rounds", "last_input_tokens", "tool_output_bytes", "web_batches")
    },
    "last_cached_fraction": stats(
        [r["last_cached_input_tokens"] / r["last_input_tokens"] for r in valid], 6
    ),
    "last_gap_fraction": stats(
        [
            r["last_after_tool_gap_ms"] / r["sdk_ms"]
            for r in valid
            if r["last_after_tool_gap_ms"] is not None
        ]
    ),
    "raw_receipt_bytes": data["bytes"],
    "sample_target_met": len(valid) >= 20
    and any(r["case_type"] == "POLICY_HIT_REVIEW" for r in valid)
    and any(r["case_type"] != "POLICY_HIT_REVIEW" for r in valid),
    "stop_reason": "deadline_reached_without_POLICY_HIT_REVIEW_or_trade_intent",
    "negative_interval_cases": [
        r["case_id"] for r in valid if r["sdk_minus_observed_tools_ms"] < 0
    ],
    "slowest": sorted(valid, key=lambda r: r["sdk_ms"], reverse=True)[:8],
}
(ROOT / "analysis_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
(ROOT / "sample_coverage.json").write_text(
    json.dumps(
        {
            "as_of": data["at"],
            "groups": groups,
            "stop_condition_met": False,
            "collection_ended": True,
            "stop_reason": summary["stop_reason"],
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
report = ROOT / "natural_report.md"
report.write_text(report.read_text(encoding="utf-8").rstrip() + "\n", encoding="utf-8")
print(
    json.dumps(
        {
            k: summary[k]
            for k in (
                "receipt_count",
                "case_types",
                "decisions",
                "metrics",
                "response_count",
                "tool_batches",
                "repeated_control_cases",
                "first_truncated_cases",
                "truncated_batches",
                "error_marker_cases",
                "compaction_cases",
                "tool_fraction_total",
                "descriptive_pearson_not_causal",
                "last_cached_fraction",
                "last_gap_fraction",
            )
        },
        ensure_ascii=False,
    )
)
