from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PHASE0_REPORT = ROOT / ".tmp/cdecr/runtime_optimization/phase0_30_20260725_b_report.json"
DEFAULT_PHASE0_DB = ROOT / ".tmp/cdecr/runtime_optimization/phase0_30_20260725_b.sqlite3"
DEFAULT_PHASE12_REPORT = (
    ROOT / ".tmp/cdecr/runtime_optimization/phase12_shadow_30_20260725_report.json"
)
DEFAULT_PHASE12_DB = ROOT / ".tmp/cdecr/runtime_optimization/phase12_shadow_30_20260725.sqlite3"
DEFAULT_COMPARISON_OUTPUT = (
    ROOT / "dev_plan/CDECR/experiments/runtime_optimization_phase0_phase12_comparison.json"
)
DEFAULT_PHASE12_OUTPUT = (
    ROOT / "dev_plan/CDECR/experiments/runtime_optimization_phase12_30_summary.json"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare the two frozen 30-document CDECR optimization runs."
    )
    parser.add_argument("--phase0-report", type=Path, default=DEFAULT_PHASE0_REPORT)
    parser.add_argument("--phase0-db", type=Path, default=DEFAULT_PHASE0_DB)
    parser.add_argument("--phase12-report", type=Path, default=DEFAULT_PHASE12_REPORT)
    parser.add_argument("--phase12-db", type=Path, default=DEFAULT_PHASE12_DB)
    parser.add_argument("--comparison-output", type=Path, default=DEFAULT_COMPARISON_OUTPUT)
    parser.add_argument("--phase12-output", type=Path, default=DEFAULT_PHASE12_OUTPUT)
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def change(old: int | float, new: int | float) -> dict[str, int | float | None]:
    delta = new - old
    return {
        "phase0": old,
        "phase12": new,
        "delta": delta,
        "delta_ratio": round(delta / old, 6) if old else None,
    }


def failure_breakdown(report: dict[str, Any]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for document in report["documents"]:
        if document.get("document_status") == "FAILED" or document.get("event_status") == "FAILED":
            counts[f"{document.get('failure_stage')}.{document.get('error_code')}"] += 1
    return dict(sorted(counts.items()))


def successful_ids(report: dict[str, Any], field: str, value: str) -> set[str]:
    return {
        document["message_id"] for document in report["documents"] if document.get(field) == value
    }


def decision_audit_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        decision_type: count
        for decision_type, count in connection.execute(
            """
            SELECT decision_type, COUNT(*)
            FROM decision_audits
            GROUP BY decision_type
            ORDER BY decision_type
            """
        )
    }


def mention_ids(connection: sqlite3.Connection) -> set[str]:
    return {
        mention_id for (mention_id,) in connection.execute("SELECT mention_id FROM event_mentions")
    }


def current_atomic_groups(connection: sqlite3.Connection) -> dict[str, set[str]]:
    groups: dict[str, set[str]] = defaultdict(set)
    rows = connection.execute(
        """
        SELECT h.event_id, m.mention_id
        FROM atomic_event_heads AS h
        JOIN atomic_event_mentions AS m
          ON m.event_id = h.event_id
         AND m.event_version = h.current_version
        """
    )
    for event_id, mention_id in rows:
        groups[event_id].add(mention_id)
    return groups


def current_package_groups(connection: sqlite3.Connection) -> dict[str, set[str]]:
    atomic_mentions = current_atomic_groups(connection)
    groups: dict[str, set[str]] = defaultdict(set)
    for event_id, package_id in connection.execute(
        "SELECT event_id, package_id FROM active_package_memberships"
    ):
        groups[package_id].update(atomic_mentions.get(event_id, set()))
    return groups


def co_cluster_pairs(
    groups: dict[str, set[str]], allowed_mentions: set[str]
) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for members in groups.values():
        comparable = sorted(members & allowed_mentions)
        pairs.update(combinations(comparable, 2))
    return pairs


def jaccard(left: set[Any], right: set[Any]) -> float:
    union = left | right
    return round(len(left & right) / len(union), 6) if union else 1.0


def structure_comparison(
    phase0_connection: sqlite3.Connection,
    phase12_connection: sqlite3.Connection,
) -> dict[str, Any]:
    phase0_mentions = mention_ids(phase0_connection)
    phase12_mentions = mention_ids(phase12_connection)
    common_mentions = phase0_mentions & phase12_mentions

    phase0_atomic_pairs = co_cluster_pairs(
        current_atomic_groups(phase0_connection), common_mentions
    )
    phase12_atomic_pairs = co_cluster_pairs(
        current_atomic_groups(phase12_connection), common_mentions
    )
    phase0_package_pairs = co_cluster_pairs(
        current_package_groups(phase0_connection), common_mentions
    )
    phase12_package_pairs = co_cluster_pairs(
        current_package_groups(phase12_connection), common_mentions
    )

    return {
        "mention_identity": {
            "phase0_count": len(phase0_mentions),
            "phase12_count": len(phase12_mentions),
            "common_count": len(common_mentions),
            "jaccard": jaccard(phase0_mentions, phase12_mentions),
        },
        "atomic_co_cluster_pairs_on_common_mentions": {
            "phase0_count": len(phase0_atomic_pairs),
            "phase12_count": len(phase12_atomic_pairs),
            "common_count": len(phase0_atomic_pairs & phase12_atomic_pairs),
            "jaccard": jaccard(phase0_atomic_pairs, phase12_atomic_pairs),
        },
        "package_co_cluster_pairs_on_common_mentions": {
            "phase0_count": len(phase0_package_pairs),
            "phase12_count": len(phase12_package_pairs),
            "common_count": len(phase0_package_pairs & phase12_package_pairs),
            "jaccard": jaccard(phase0_package_pairs, phase12_package_pairs),
        },
    }


def shadow_payload_summary(connection: sqlite3.Connection) -> dict[str, Any]:
    by_stage: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "sample_count": 0,
            "baseline_payload_bytes": 0,
            "optimized_payload_bytes": 0,
            "estimated_savings_bytes": 0,
            "wire_ref_count": 0,
        }
    )
    rows = connection.execute(
        """
        SELECT subject_id, payload_json
        FROM decision_audits
        WHERE decision_type = 'WIRE_PAYLOAD_SHADOW'
        """
    )
    for subject_id, payload_json in rows:
        stage = subject_id.split(":", 1)[0]
        payload = json.loads(payload_json)
        summary = by_stage[stage]
        summary["sample_count"] += 1
        for field in (
            "baseline_payload_bytes",
            "optimized_payload_bytes",
            "estimated_savings_bytes",
            "wire_ref_count",
        ):
            summary[field] += int(payload.get(field, 0))

    result: dict[str, Any] = {}
    for stage, summary in sorted(by_stage.items()):
        baseline = summary["baseline_payload_bytes"]
        net_savings = baseline - summary["optimized_payload_bytes"]
        result[stage] = {
            **summary,
            "net_savings_bytes": net_savings,
            "net_savings_ratio": round(net_savings / baseline, 6) if baseline else 0.0,
            "mode": "shadow_not_sent",
        }
    return result


def boundary_summary(report: dict[str, Any]) -> dict[str, dict[str, int]]:
    return {
        name: {
            "evaluated_cases": int(payload["evaluated_cases"]),
            "violations": int(payload["violations"]),
        }
        for name, payload in report["boundaries"].items()
    }


def stage_comparison(
    phase0: dict[str, Any], phase12: dict[str, Any]
) -> dict[str, dict[str, dict[str, int | float | None]]]:
    stages = sorted(set(phase0["stage_metrics"]) | set(phase12["stage_metrics"]))
    fields = (
        "call_count",
        "input_tokens",
        "output_tokens",
        "payload_bytes",
        "model_latency_p50_ms",
        "model_latency_p95_ms",
        "repair_count",
    )
    result: dict[str, dict[str, dict[str, int | float | None]]] = {}
    for stage in stages:
        old_metrics = phase0["stage_metrics"].get(stage, {})
        new_metrics = phase12["stage_metrics"].get(stage, {})
        result[stage] = {
            field: change(old_metrics.get(field, 0), new_metrics.get(field, 0)) for field in fields
        }
    return result


def run() -> None:
    args = parse_args()
    phase0 = load_json(args.phase0_report)
    phase12 = load_json(args.phase12_report)

    phase0_connection = sqlite3.connect(args.phase0_db)
    phase12_connection = sqlite3.connect(args.phase12_db)
    try:
        structure = structure_comparison(phase0_connection, phase12_connection)
        phase0_audits = decision_audit_counts(phase0_connection)
        phase12_audits = decision_audit_counts(phase12_connection)
        shadow = shadow_payload_summary(phase12_connection)
    finally:
        phase0_connection.close()
        phase12_connection.close()

    phase0_budget = phase0["call_budget"]
    phase12_budget = phase12["call_budget"]
    phase0_rerun_calls = phase0["idempotency"]["rerun_model_call_delta"]
    phase12_rerun_calls = phase12["idempotency"]["rerun_model_call_delta"]
    phase0_first_pass_calls = phase0_budget["call_count"] - phase0_rerun_calls
    phase12_first_pass_calls = phase12_budget["call_count"] - phase12_rerun_calls

    phase0_doc_success = successful_ids(phase0, "document_status", "SUCCEEDED")
    phase12_doc_success = successful_ids(phase12, "document_status", "SUCCEEDED")
    phase0_event_success = successful_ids(phase0, "event_status", "SUCCEEDED")
    phase12_event_success = successful_ids(phase12, "event_status", "SUCCEEDED")

    comparison = {
        "artifact_version": "cdecr-runtime-optimization-comparison-v1",
        "run_date": "2026-07-25",
        "commits": {
            "phase0_implementation": "dd6c580",
            "phase0_result": "08180dc",
            "phase12_candidate": "3e71b01",
            "phase12_shadow_safe": "57633b9",
        },
        "corpus": {
            "manifest_version": phase0["corpus_manifest_version"],
            "selected_documents": phase0["selected_document_count"],
            "same_manifest_version": (
                phase0["corpus_manifest_version"] == phase12["corpus_manifest_version"]
            ),
            "same_selected_document_count": (
                phase0["selected_document_count"] == phase12["selected_document_count"]
            ),
        },
        "performance": {
            "first_pass_wall_clock_ms": change(
                phase0["first_pass_wall_clock_ms"],
                phase12["first_pass_wall_clock_ms"],
            ),
            "total_wall_clock_ms": change(
                phase0["total_wall_clock_ms"], phase12["total_wall_clock_ms"]
            ),
            "first_pass_model_calls": change(phase0_first_pass_calls, phase12_first_pass_calls),
            "total_model_calls": change(phase0_budget["call_count"], phase12_budget["call_count"]),
            "input_tokens": change(phase0_budget["input_tokens"], phase12_budget["input_tokens"]),
            "output_tokens": change(
                phase0_budget["output_tokens"], phase12_budget["output_tokens"]
            ),
            "request_payload_bytes": change(
                phase0_budget["request_payload_bytes"],
                phase12_budget["request_payload_bytes"],
            ),
            "model_latency_ms_sum": change(
                phase0_budget["latency_ms"], phase12_budget["latency_ms"]
            ),
            "repair_calls": change(phase0_budget["repair_count"], phase12_budget["repair_count"]),
            "queue_wait_ms_sum": change(
                phase0_budget["queue_wait_ms"], phase12_budget["queue_wait_ms"]
            ),
        },
        "outcome": {
            "completed_documents": change(
                phase0["completed_document_count"],
                phase12["completed_document_count"],
            ),
            "completed_cross_document_runs": change(
                phase0["completed_event_count"], phase12["completed_event_count"]
            ),
            "failed_documents": change(
                phase0["failed_document_count"], phase12["failed_document_count"]
            ),
            "atomic_events": change(phase0["atomic_event_count"], phase12["atomic_event_count"]),
            "packages": change(phase0["package_count"], phase12["package_count"]),
            "document_success_overlap": {
                "phase0": len(phase0_doc_success),
                "phase12": len(phase12_doc_success),
                "common": len(phase0_doc_success & phase12_doc_success),
                "phase0_only": len(phase0_doc_success - phase12_doc_success),
                "phase12_only": len(phase12_doc_success - phase0_doc_success),
                "jaccard": jaccard(phase0_doc_success, phase12_doc_success),
            },
            "cross_document_success_overlap": {
                "phase0": len(phase0_event_success),
                "phase12": len(phase12_event_success),
                "common": len(phase0_event_success & phase12_event_success),
                "phase0_only": len(phase0_event_success - phase12_event_success),
                "phase12_only": len(phase12_event_success - phase0_event_success),
                "jaccard": jaccard(phase0_event_success, phase12_event_success),
            },
            "valid_mention_schema_rate": change(
                phase0["valid_mention_schema_rate"],
                phase12["valid_mention_schema_rate"],
            ),
            "valid_evidence_span_rate": change(
                phase0["valid_evidence_span_rate"],
                phase12["valid_evidence_span_rate"],
            ),
            "phase0_acceptance_passed": phase0["acceptance_passed"],
            "phase12_acceptance_passed": phase12["acceptance_passed"],
        },
        "failure_breakdown": {
            "phase0": failure_breakdown(phase0),
            "phase12": failure_breakdown(phase12),
        },
        "structure_comparison": structure,
        "boundaries": {
            "phase0": boundary_summary(phase0),
            "phase12": boundary_summary(phase12),
        },
        "idempotency": {
            "rerun_model_calls": change(phase0_rerun_calls, phase12_rerun_calls),
            "phase0": phase0["idempotency"],
            "phase12": phase12["idempotency"],
        },
        "audit_counts": {
            "phase0": phase0_audits,
            "phase12": phase12_audits,
        },
        "wire_candidate_shadow": shadow,
        "stage_comparison": stage_comparison(phase0, phase12),
        "interpretation_limits": [
            (
                "Both runs use the same frozen 30-document manifest but "
                "real-model outputs are stochastic."
            ),
            (
                "Aggregate token and stage totals are not paired because "
                "successful documents and generated mention/candidate sets differ."
            ),
            (
                "The candidate dictionary and long-ID wire representation was "
                "shadow-only in the official second run."
            ),
            "Shadow payload byte savings are not validated token or cost savings.",
            "Neither run passed the full business acceptance gate.",
        ],
    }

    phase12_summary = {
        "artifact_version": "cdecr-runtime-optimization-phase12-shadow-30-v1",
        "run_date": "2026-07-25",
        "git_commit_under_test": "57633b9",
        "corpus": {
            "manifest_version": phase12["corpus_manifest_version"],
            "selected_documents": phase12["selected_document_count"],
        },
        "ignored_artifacts": {
            "registry": relative(args.phase12_db),
            "registry_sha256": sha256(args.phase12_db),
            "report": relative(args.phase12_report),
            "report_sha256": sha256(args.phase12_report),
        },
        "outcome": {
            "acceptance_passed": phase12["acceptance_passed"],
            "completed_documents": phase12["completed_document_count"],
            "failed_documents": phase12["failed_document_count"],
            "completed_cross_document_runs": phase12["completed_event_count"],
            "mention_count": structure["mention_identity"]["phase12_count"],
            "atomic_event_count": phase12["atomic_event_count"],
            "package_count": phase12["package_count"],
            "valid_mention_schema_rate": phase12["valid_mention_schema_rate"],
            "valid_evidence_span_rate": phase12["valid_evidence_span_rate"],
        },
        "failure_breakdown": failure_breakdown(phase12),
        "performance": {
            "first_pass_wall_clock_ms": phase12["first_pass_wall_clock_ms"],
            "total_wall_clock_ms": phase12["total_wall_clock_ms"],
            "first_pass_model_call_count": phase12_first_pass_calls,
            "model_call_count": phase12_budget["call_count"],
            "input_tokens": phase12_budget["input_tokens"],
            "output_tokens": phase12_budget["output_tokens"],
            "model_latency_ms_sum": phase12_budget["latency_ms"],
            "queue_wait_ms_sum": phase12_budget["queue_wait_ms"],
            "request_payload_bytes_sum": phase12_budget["request_payload_bytes"],
            "repair_call_count": phase12_budget["repair_count"],
        },
        "idempotency": phase12["idempotency"],
        "wire_candidate_shadow": shadow,
        "known_limitations": comparison["interpretation_limits"],
    }

    for path, payload in (
        (args.comparison_output, comparison),
        (args.phase12_output, phase12_summary),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(path)


if __name__ == "__main__":
    run()
