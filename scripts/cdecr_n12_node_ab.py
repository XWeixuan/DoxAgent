"""Run a read-mostly N12 legacy/wire node A/B on recorded package candidates."""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from collections import Counter
from pathlib import Path

from cdecr.cli import _cross_document_engine
from cdecr.config import CDECRSettings
from cdecr.cross_document import _AuditedModels
from cdecr.cross_document_contracts import (
    PackageAssignmentDecision,
    PackageCandidate,
    RecallRoute,
)
from cdecr.registry import SQLiteCDECRRegistry
from cdecr.single_document_contracts import ModelCallSummary


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-tasks", type=int)
    return parser.parse_args()


def _recorded_candidates(path: Path) -> list[tuple[str, list[str]]]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """
            SELECT event_id, payload_json
            FROM (
                SELECT event_id, payload_json,
                       ROW_NUMBER() OVER (
                           PARTITION BY event_id ORDER BY created_at DESC
                       ) AS ordinal
                FROM package_assignment_decisions
            )
            WHERE ordinal = 1
            """
        ).fetchall()
    result: list[tuple[str, list[str]]] = []
    for event_id, raw in rows:
        payload = json.loads(str(raw))
        package_ids = list(
            dict.fromkeys(
                str(item["candidate_package_id"])
                for item in payload.get("candidate_assessments", [])
                if isinstance(item, dict) and item.get("candidate_package_id")
            )
        )
        if package_ids:
            result.append((str(event_id), package_ids))
    return sorted(result, key=lambda item: (-len(item[1]), item[0]))


def _summaries(values: list[ModelCallSummary]) -> dict[str, int]:
    return {
        "calls": len(values),
        "input_tokens": sum(item.input_tokens or 0 for item in values),
        "output_tokens": sum(item.output_tokens or 0 for item in values),
        "latency_ms": sum(item.latency_ms for item in values),
        "request_payload_bytes": sum(item.request_payload_bytes or 0 for item in values),
        "request_items": sum(item.request_item_count or 0 for item in values),
        "wire_refs": sum(item.wire_ref_count or 0 for item in values),
    }


def main() -> int:
    args = _args()
    registry = SQLiteCDECRRegistry(args.registry)
    recorded = _recorded_candidates(args.registry)
    if args.max_tasks is not None:
        recorded = recorded[: max(1, args.max_tasks)]

    events = {}
    seeds = {}
    unresolved: dict[str, list[PackageCandidate]] = {}
    skipped: Counter[str] = Counter()
    base_settings = CDECRSettings()
    engine_for_build = _cross_document_engine(
        base_settings.model_copy(
            update={"sqlite_path": args.registry, "n12_wire_protocol": "shadow"}
        ),
        registry,
    )
    for event_id, package_ids in recorded:
        event = registry.get_current_atomic_event(event_id)
        if event is None:
            skipped["missing_event"] += 1
            continue
        candidates: list[PackageCandidate] = []
        seen_roots: set[str] = set()
        for package_id in package_ids:
            root = registry.resolve_package_root(package_id) or package_id
            if root in seen_roots:
                continue
            package = registry.get_current_package(root)
            if package is None:
                skipped["missing_package"] += 1
                continue
            seen_roots.add(root)
            candidates.append(
                PackageCandidate(
                    package=package,
                    recall_routes=[RecallRoute.INCUMBENT_MEMBERSHIP],
                    recall_score=0.5,
                )
            )
        if not candidates:
            skipped["no_current_candidates"] += 1
            continue
        mentions = engine_for_build._event_mentions(event)
        seed, _, _, _ = engine_for_build._package_seed_and_key(event, mentions)
        events[event_id] = event
        seeds[event_id] = seed
        unresolved[event_id] = candidates

    if not unresolved:
        raise SystemExit("no replayable N12 tasks")
    source = registry.list_sources(limit=1)[0]
    decisions_by_mode: dict[str, dict[str, PackageAssignmentDecision]] = {}
    calls_by_mode: dict[str, dict[str, int]] = {}
    run_ids: dict[str, str] = {}
    for mode in ("legacy", "canary"):
        settings = base_settings.model_copy(
            update={"sqlite_path": args.registry, "n12_wire_protocol": mode}
        )
        engine = _cross_document_engine(settings, registry)
        run_id = str(uuid.uuid4())
        run_ids[mode] = run_id
        registry.start_cross_document_trace(
            trace_id=run_id,
            message_id=source.message_id,
            engine_version="n12-node-ab-v1",
            prompt_version="n12-dictionary-node-ab-v1",
            model_config={**engine.model_config, "evaluation_only": True},
        )
        summaries: list[ModelCallSummary] = []
        models = _AuditedModels(
            registry=registry,
            run_id=run_id,
            embedding_client=engine.embedding_client,
            m2_client=engine.m2_client,
            m3_client=engine.m3_client,
            model_m1=engine.model_m1,
            model_m2=engine.model_m2,
            model_m3=engine.model_m3,
            summaries=summaries,
        )
        decisions = engine._joint_package_decisions(
            unresolved=unresolved,
            events=events,
            seeds=seeds,
            models=models,
        )
        registry.finish_cross_document_trace(run_id, status="REUSED")
        decisions_by_mode[mode] = decisions
        calls_by_mode[mode] = _summaries(summaries)

    legacy = decisions_by_mode["legacy"]
    canary = decisions_by_mode["canary"]
    common = sorted(set(legacy).intersection(canary))
    relation_total = 0
    relation_agree = 0
    selected_agree = 0
    exact_agree = 0
    per_task = []
    for event_id in common:
        left = legacy[event_id]
        right = canary[event_id]
        left_relations = {
            item.candidate_package_id: item.relation.value
            for item in left.candidate_assessments
        }
        right_relations = {
            item.candidate_package_id: item.relation.value
            for item in right.candidate_assessments
        }
        comparable = sorted(set(left_relations).intersection(right_relations))
        matches = sum(left_relations[value] == right_relations[value] for value in comparable)
        relation_total += len(comparable)
        relation_agree += matches
        same_selected = left.selected_member_package_id == right.selected_member_package_id
        selected_agree += same_selected
        same_exact = left_relations == right_relations and same_selected
        exact_agree += same_exact
        per_task.append(
            {
                "event_id": event_id,
                "candidate_count": len(comparable),
                "relation_agreement": matches / len(comparable) if comparable else 1.0,
                "selected_target_agreement": same_selected,
                "exact_decision_agreement": same_exact,
            }
        )

    with sqlite3.connect(args.registry) as connection:
        normalizations = {
            mode: connection.execute(
                """
                SELECT COUNT(*) FROM decision_audits
                WHERE run_id = ? AND decision_type = 'PACKAGE_N12_NORMALIZATION'
                """,
                (run_ids[mode],),
            ).fetchone()[0]
            for mode in ("legacy", "canary")
        }
        batch_degraded = {
            mode: connection.execute(
                """
                SELECT COUNT(*) FROM decision_audits
                WHERE run_id = ? AND decision_type = 'PACKAGE_N12_BATCH_DEGRADED'
                """,
                (run_ids[mode],),
            ).fetchone()[0]
            for mode in ("legacy", "canary")
        }

    legacy_calls = calls_by_mode["legacy"]
    canary_calls = calls_by_mode["canary"]
    report = {
        "report_version": "cdecr-n12-node-ab-v1",
        "registry": str(args.registry),
        "recorded_task_count": len(recorded),
        "replayed_task_count": len(unresolved),
        "candidate_ref_count": sum(len(values) for values in unresolved.values()),
        "skipped": dict(skipped),
        "run_ids": run_ids,
        "legacy": {
            "task_coverage": len(legacy) / len(unresolved),
            "normalization_audits": normalizations["legacy"],
            "batch_degraded": batch_degraded["legacy"],
            **legacy_calls,
        },
        "canary": {
            "task_coverage": len(canary) / len(unresolved),
            "normalization_audits": normalizations["canary"],
            "batch_degraded": batch_degraded["canary"],
            **canary_calls,
        },
        "comparison": {
            "common_task_count": len(common),
            "relation_agreement": relation_agree / relation_total if relation_total else 1.0,
            "selected_target_agreement": selected_agree / len(common) if common else 1.0,
            "exact_task_agreement": exact_agree / len(common) if common else 1.0,
            "input_token_savings_ratio": (
                1 - canary_calls["input_tokens"] / legacy_calls["input_tokens"]
                if legacy_calls["input_tokens"]
                else 0.0
            ),
            "payload_savings_ratio": (
                1
                - canary_calls["request_payload_bytes"]
                / legacy_calls["request_payload_bytes"]
                if legacy_calls["request_payload_bytes"]
                else 0.0
            ),
        },
        "decisions": {
            mode: {
                event_id: decision.model_dump(mode="json")
                for event_id, decision in sorted(decisions_by_mode[mode].items())
            }
            for mode in ("legacy", "canary")
        },
        "tasks": per_task,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if key not in {"tasks", "decisions"}
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
