"""Build deterministic operational diagnostics for a CDECR acceptance registry."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _rows(connection: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    return list(connection.execute(query))


def main() -> int:
    args = _args()
    connection = sqlite3.connect(args.registry)
    connection.row_factory = sqlite3.Row
    machine = json.loads(args.report.read_text(encoding="utf-8"))

    model_rows = _rows(
        connection,
        """
        SELECT COALESCE(stage, '<none>') AS stage,
               COUNT(*) AS calls,
               SUM(COALESCE(input_tokens, 0)) AS input_tokens,
               SUM(COALESCE(output_tokens, 0)) AS output_tokens,
               SUM(latency_ms) AS latency_ms,
               SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) AS failed_calls
        FROM model_calls
        GROUP BY COALESCE(stage, '<none>')
        ORDER BY input_tokens DESC
        """,
    )
    total_input = sum(int(row["input_tokens"] or 0) for row in model_rows)
    total_output = sum(int(row["output_tokens"] or 0) for row in model_rows)
    total_latency = sum(int(row["latency_ms"] or 0) for row in model_rows)
    stages = [
        {
            **dict(row),
            "input_share": (int(row["input_tokens"] or 0) / total_input if total_input else 0),
            "output_share": (
                int(row["output_tokens"] or 0) / total_output if total_output else 0
            ),
            "latency_share": (
                int(row["latency_ms"] or 0) / total_latency if total_latency else 0
            ),
        }
        for row in model_rows
    ]
    failures = [
        dict(row)
        for row in _rows(
            connection,
            """
            SELECT COALESCE(stage, '<none>') AS stage, error_code, COUNT(*) AS count
            FROM model_calls
            WHERE status = 'FAILED'
            GROUP BY COALESCE(stage, '<none>'), error_code
            ORDER BY count DESC
            """,
        )
    ]
    tiers = [
        dict(row)
        for row in _rows(
            connection,
            """
            SELECT tier, model, COUNT(*) AS calls,
                   SUM(COALESCE(input_tokens, 0)) AS input_tokens,
                   SUM(COALESCE(output_tokens, 0)) AS output_tokens,
                   SUM(latency_ms) AS latency_ms,
                   SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) AS failed_calls
            FROM model_calls
            GROUP BY tier, model
            ORDER BY tier, model
            """,
        )
    ]

    mentions = [
        json.loads(str(row["payload_json"]))
        for row in _rows(connection, "SELECT payload_json FROM event_mentions")
    ]
    evidence = [record for mention in mentions for record in mention.get("evidence_records", [])]
    evidence_status = Counter(str(record.get("status")) for record in evidence)
    field_methods = Counter(
        str(row["method"])
        for row in _rows(connection, "SELECT method FROM canonical_field_links")
    )
    atomic_actions = Counter(
        str(row["action"])
        for row in _rows(connection, "SELECT action FROM atomic_assignment_decisions")
    )
    package_actions = Counter(
        str(row["action"])
        for row in _rows(connection, "SELECT action FROM package_assignment_decisions")
    )
    n13_relations = Counter(
        str(row["relation"])
        for row in _rows(connection, "SELECT relation FROM package_merge_decisions")
    )
    audit_counts = Counter(
        str(row["decision_type"])
        for row in _rows(connection, "SELECT decision_type FROM decision_audits")
    )

    atomic_sizes = [
        int(row["size"])
        for row in _rows(
            connection,
            """
            SELECT COUNT(*) AS size
            FROM atomic_event_heads h
            JOIN atomic_event_mentions m
              ON m.event_id = h.event_id AND m.event_version = h.current_version
            GROUP BY h.event_id
            """,
        )
    ]
    package_members: dict[str, list[str]] = defaultdict(list)
    for row in _rows(connection, "SELECT package_id, event_id FROM active_package_memberships"):
        package_members[str(row["package_id"])].append(str(row["event_id"]))
    package_payloads = [
        json.loads(str(row["payload_json"]))
        for row in _rows(
            connection,
            """
            SELECT v.payload_json
            FROM event_package_versions v
            JOIN event_package_heads h
              ON h.package_id = v.package_id AND h.current_version = v.version
            """,
        )
    ]
    payload = {
        "report_version": "cdecr-acceptance-diagnostics-v1",
        "machine_summary": {
            key: machine.get(key)
            for key in (
                "selected_document_count",
                "completed_document_count",
                "completed_event_count",
                "failed_document_count",
                "atomic_event_count",
                "package_count",
                "first_pass_wall_clock_ms",
                "total_wall_clock_ms",
                "acceptance_passed",
            )
        },
        "model_totals": {
            "calls": sum(int(row["calls"]) for row in model_rows),
            "input_tokens": total_input,
            "output_tokens": total_output,
            "latency_ms": total_latency,
        },
        "stages": stages,
        "tiers": tiers,
        "model_failures": failures,
        "document_failures": [
            dict(row)
            for row in _rows(
                connection,
                "SELECT message_id, status, error_code FROM document_processing_runs "
                "WHERE status = 'FAILED'",
            )
        ],
        "cross_document_failures": [
            dict(row)
            for row in _rows(
                connection,
                "SELECT message_id, status, error_code FROM cross_document_runs "
                "WHERE status = 'FAILED'",
            )
        ],
        "mention_count": len(mentions),
        "evidence": {
            "count": len(evidence),
            "status_counts": dict(evidence_status),
            "verified_rate": evidence_status["VERIFIED"] / len(evidence) if evidence else 1.0,
        },
        "field_links": {"count": sum(field_methods.values()), "methods": dict(field_methods)},
        "atomic": {
            "assignment_actions": dict(atomic_actions),
            "cluster_count": len(atomic_sizes),
            "largest_cluster": max(atomic_sizes, default=0),
            "multi_mention_clusters": sum(size > 1 for size in atomic_sizes),
        },
        "package": {
            "assignment_actions": dict(package_actions),
            "n13_relations": dict(n13_relations),
            "cluster_count": len(package_members),
            "largest_cluster": max((len(value) for value in package_members.values()), default=0),
            "multi_atomic_clusters": sum(len(value) > 1 for value in package_members.values()),
            "anchor_id_packages": sum(
                bool(item.get("package_anchor_ids")) for item in package_payloads
            ),
            "primary_anchor_packages": sum(
                bool(item.get("primary_anchor_id")) for item in package_payloads
            ),
            "anchor_conflict_packages": sum(
                bool(item.get("anchor_conflict")) for item in package_payloads
            ),
        },
        "audit_counts": dict(audit_counts),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["model_totals"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
