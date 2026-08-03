"""Replay Wave C and N13 pair-local Apply from a fixed completed registry without LLM calls."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from cdecr.contracts import EventPackage
from cdecr.coreference_rules import merge_packages
from cdecr.cross_document import CrossDocumentEngine
from cdecr.cross_document_contracts import PackagePairBoundary, RecallRoute
from cdecr.package_engine import PackageBoundaryGate
from cdecr.registry import SQLiteCDECRRegistry
from scripts.cdecr_evaluate_n12_package_gold import (
    _connection,
    _current_events,
    _gold_labels,
    _partition_evaluation,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--package-evaluation", type=Path, required=True)
    parser.add_argument("--gold-registry", type=Path, required=True)
    parser.add_argument("--gold-review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _wave_c_edges(connection: sqlite3.Connection) -> list[dict[str, str]]:
    rows = connection.execute(
        """
        SELECT source_package_id, target_package_id, reason, created_at
        FROM package_redirects
        WHERE reason IN ('N12_WAVE_C_M0', 'N12_WAVE_C_M3')
        ORDER BY created_at, source_package_id
        """
    )
    return [dict(row) for row in rows]


def _pre_wave_packages(
    connection: sqlite3.Connection, cutoff: str
) -> dict[str, EventPackage]:
    rows = connection.execute(
        """
        SELECT package_id, payload_json
        FROM (
            SELECT package_id, payload_json,
                   ROW_NUMBER() OVER (
                       PARTITION BY package_id ORDER BY version DESC
                   ) AS ordinal
            FROM event_package_versions
            WHERE created_at < ?
        )
        WHERE ordinal = 1
        """,
        (cutoff,),
    )
    return {
        str(row["package_id"]): EventPackage.model_validate_json(str(row["payload_json"]))
        for row in rows
    }


def _resolve(value: str, redirects: dict[str, str]) -> str:
    seen: set[str] = set()
    while value in redirects and value not in seen:
        seen.add(value)
        value = redirects[value]
    return value


def _old_hard_blocked(boundary: PackagePairBoundary) -> bool:
    return bool(
        boundary.conflicting_artifact_ids
        or boundary.issuer_conflict
        or boundary.reaction_boundary
        or boundary.analyst_boundary
        or boundary.period_boundary
        or boundary.session_boundary
    )


def _cross_gold_counts(
    left: EventPackage,
    right: EventPackage,
    gold: dict[str, str],
) -> tuple[int, int, int]:
    pairs = [
        (left_id, right_id)
        for left_id in left.member_event_ids
        for right_id in right.member_event_ids
        if left_id in gold and right_id in gold
    ]
    tp = sum(gold[left_id] == gold[right_id] for left_id, right_id in pairs)
    fp = sum(gold[left_id] != gold[right_id] for left_id, right_id in pairs)
    return tp, fp, len(pairs)


def _apply_wave_c(
    *,
    engine: CrossDocumentEngine,
    packages: dict[str, EventPackage],
    edges: list[dict[str, str]],
    enforce_v2: bool,
    gold: dict[str, str],
) -> tuple[dict[str, EventPackage], dict[str, str], list[dict[str, Any]]]:
    active = dict(packages)
    redirects: dict[str, str] = {}
    audit: list[dict[str, Any]] = []
    for edge in edges:
        source_id = _resolve(edge["source_package_id"], redirects)
        target_id = _resolve(edge["target_package_id"], redirects)
        if source_id == target_id or source_id not in active or target_id not in active:
            continue
        source = active[source_id]
        target = active[target_id]
        routes, _ = engine._package_pair_signals(source, target, {})
        boundary = engine._package_pair_boundary(source, target, routes)
        blocked = bool(enforce_v2 and boundary.hard_blocked)
        tp, fp, evaluated = _cross_gold_counts(source, target, gold)
        audit.append(
            {
                **edge,
                "resolved_source": source_id,
                "resolved_target": target_id,
                "blocked": blocked,
                "boundary": boundary.model_dump(mode="json"),
                "gold_cross_pairs": evaluated,
                "gold_tp_links": tp,
                "gold_fp_links": fp,
            }
        )
        if blocked:
            continue
        merged = merge_packages(target, source)
        active[target_id] = merged
        active.pop(source_id)
        redirects[source_id] = target_id
    return active, redirects, audit


def _n13_same_pairs(connection: sqlite3.Connection) -> list[dict[str, str]]:
    rows = connection.execute(
        """
        SELECT source_package_id, target_package_id, payload_json
        FROM package_merge_decisions
        WHERE relation = 'SAME_PACKAGE'
        ORDER BY created_at, source_package_id, target_package_id
        """
    )
    result: list[dict[str, str]] = []
    for row in rows:
        payload = json.loads(str(row["payload_json"]))
        if str(payload.get("reason", "")).startswith("N12_WAVE_C_"):
            continue
        result.append(
            {
                "source_package_id": str(row["source_package_id"]),
                "target_package_id": str(row["target_package_id"]),
                "reason": str(payload.get("reason", "")),
            }
        )
    return result


def _same_components(edges: list[tuple[str, str]]) -> list[set[str]]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for left, right in edges:
        adjacency[left].add(right)
        adjacency[right].add(left)
    components: list[set[str]] = []
    seen: set[str] = set()
    for start in sorted(adjacency):
        if start in seen:
            continue
        stack = [start]
        component: set[str] = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(sorted(adjacency[current] - component))
        seen.update(component)
        components.append(component)
    return components


def _apply_n13(
    *,
    engine: CrossDocumentEngine,
    packages: dict[str, EventPackage],
    prior_redirects: dict[str, str],
    decisions: list[dict[str, str]],
    enforce_v2: bool,
    gold: dict[str, str],
) -> tuple[dict[str, EventPackage], list[dict[str, Any]]]:
    active = dict(packages)
    candidates: list[tuple[int, int, str, str, PackagePairBoundary, list[RecallRoute]]] = []
    audit: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for decision in decisions:
        left_id = _resolve(decision["source_package_id"], prior_redirects)
        right_id = _resolve(decision["target_package_id"], prior_redirects)
        pair = tuple(sorted((left_id, right_id)))
        if left_id == right_id or pair in seen or left_id not in active or right_id not in active:
            continue
        seen.add(pair)
        left, right = active[left_id], active[right_id]
        routes, _ = engine._package_pair_signals(left, right, {})
        boundary = engine._package_pair_boundary(left, right, routes)
        hard_blocked = boundary.hard_blocked if enforce_v2 else _old_hard_blocked(boundary)
        tier_a = bool(
            RecallRoute.SHARED_ATOMIC_EVENT in routes
            or boundary.shared_artifact_ids
            or boundary.shared_anchor_ids
        )
        tier_b = bool(
            min(boundary.left_member_count, boundary.right_member_count) <= 4
            and boundary.independent_positive_count >= 2
        )
        eligible = bool(not hard_blocked and (tier_a or tier_b))
        tp, fp, evaluated = _cross_gold_counts(left, right, gold)
        audit.append(
            {
                **decision,
                "resolved_left": left_id,
                "resolved_right": right_id,
                "eligible": eligible,
                "tier": "A" if tier_a else ("B" if tier_b else None),
                "boundary": boundary.model_dump(mode="json"),
                "gold_cross_pairs": evaluated,
                "gold_tp_links": tp,
                "gold_fp_links": fp,
            }
        )
        if eligible:
            candidates.append(
                (
                    2 if tier_a else 1,
                    boundary.independent_positive_count,
                    left_id,
                    right_id,
                    boundary,
                    routes,
                )
            )

    consumed: set[str] = set()
    spokes_by_hub: dict[str, int] = defaultdict(int)
    selected: list[tuple[str, str]] = []
    for _tier, _support, left_id, right_id, _boundary, _routes in sorted(
        candidates,
        key=lambda item: (-item[0], -item[1], item[2], item[3]),
    ):
        left, right = active[left_id], active[right_id]
        hub, spoke = sorted(
            (left, right), key=lambda item: (-len(item.member_event_ids), item.package_id)
        )
        if (
            spoke.package_id in consumed
            or len(spoke.member_event_ids) > 4
            or spokes_by_hub[hub.package_id] >= 4
        ):
            continue
        consumed.add(spoke.package_id)
        spokes_by_hub[hub.package_id] += 1
        selected.append((spoke.package_id, hub.package_id))

    applied_pairs: set[tuple[str, str]] = set()
    gate = PackageBoundaryGate(engine.registry)
    for component in _same_components(selected):
        members = [active[package_id] for package_id in component]
        target = sorted(members, key=lambda item: (-len(item.member_event_ids), item.package_id))[0]
        merged = target
        for source in sorted(members, key=lambda item: item.package_id):
            if source.package_id == target.package_id:
                continue
            merged = merge_packages(merged, source)
        finding = gate.evaluate(merged, engine._package_member_events(merged))
        if finding.severity == "BLOCKING_CONFLICT":
            continue
        applied_pairs.update(
            tuple(sorted(pair))
            for pair in selected
            if pair[0] in component and pair[1] in component
        )
        for source in sorted(members, key=lambda item: item.package_id):
            if source.package_id == target.package_id:
                continue
            active.pop(source.package_id, None)
        active[target.package_id] = merged

    selected_set = {tuple(sorted(pair)) for pair in selected}
    for item in audit:
        pair = tuple(
            sorted((str(item["resolved_left"]), str(item["resolved_right"])))
        )
        item["selected"] = pair in selected_set
        item["applied"] = pair in applied_pairs
    return active, audit


def _predicted(packages: dict[str, EventPackage], gold: dict[str, str]) -> dict[str, str]:
    predicted: dict[str, str] = {}
    for package_id, package in packages.items():
        for event_id in package.member_event_ids:
            if event_id in gold:
                predicted[event_id] = package_id
    return predicted


def main() -> None:
    args = _args()
    connection = _connection(args.registry)
    edges = _wave_c_edges(connection)
    if not edges:
        raise SystemExit("no Wave C redirects found")
    packages = _pre_wave_packages(connection, min(item["created_at"] for item in edges))
    registry = SQLiteCDECRRegistry(args.registry)
    engine = object.__new__(CrossDocumentEngine)
    engine.registry = registry

    package_evaluation = json.loads(args.package_evaluation.read_text(encoding="utf-8"))
    mapping = {
        str(item["current_event_id"]): str(item["gold_event_id"])
        for item in package_evaluation["alignment"]
        if item.get("accepted")
    }
    gold_connection = _connection(args.gold_registry)
    gold_review = json.loads(args.gold_review.read_text(encoding="utf-8"))
    gold_labels = _gold_labels(gold_connection, gold_review)
    gold = {
        current_id: gold_labels[gold_id]
        for current_id, gold_id in mapping.items()
        if gold_id in gold_labels
    }
    events = _current_events(connection)
    n13_decisions = _n13_same_pairs(connection)

    scenarios: dict[str, Any] = {}
    scenario_specs = (
        ("wave_c_keep_n13_off", False, False),
        ("wave_c_keep_n13_on", False, True),
        ("wave_c_v2_boundary_n13_on", True, True),
    )
    for name, wave_v2, n13_on in scenario_specs:
        wave_packages, redirects, wave_audit = _apply_wave_c(
            engine=engine,
            packages=packages,
            edges=edges,
            enforce_v2=wave_v2,
            gold=gold,
        )
        if n13_on:
            final_packages, n13_audit = _apply_n13(
                engine=engine,
                packages=wave_packages,
                prior_redirects=redirects,
                decisions=n13_decisions,
                enforce_v2=wave_v2,
                gold=gold,
            )
        else:
            final_packages, n13_audit = wave_packages, []
        evaluation = _partition_evaluation(gold, _predicted(final_packages, gold), events)
        scenarios[name] = {
            **evaluation,
            "package_count": len(final_packages),
            "wave_c": {
                "input_edges": len(wave_audit),
                "blocked_edges": sum(item["blocked"] for item in wave_audit),
                "audit": wave_audit,
            },
            "n13": {
                "same_decisions": len(n13_audit),
                "eligible": sum(item["eligible"] for item in n13_audit),
                "selected": sum(item["selected"] for item in n13_audit),
                "applied": sum(item["applied"] for item in n13_audit),
                "selected_tp_links": sum(
                    item["gold_tp_links"] for item in n13_audit if item["applied"]
                ),
                "selected_fp_links": sum(
                    item["gold_fp_links"] for item in n13_audit if item["applied"]
                ),
                "audit": n13_audit,
            },
        }

    published = package_evaluation["modes"]["current_actual_packages"]["metrics"]
    replayed = scenarios["wave_c_keep_n13_off"]["metrics"]
    validation = {
        key: replayed[key] == published[key]
        for key in (
            "event_count",
            "true_positive_pairs",
            "false_positive_pairs",
            "false_negative_pairs",
        )
    }
    output = {
        "report_version": "cdecr-package-late-apply-replay-v1",
        "method": {
            "llm_calls": 0,
            "registry": str(args.registry),
            "pre_wave_package_count": len(packages),
            "wave_c_redirect_count": len(edges),
            "n13_same_decision_count": len(n13_decisions),
            "gold_evaluable_atomic_count": len(gold),
            "baseline_validation": validation,
        },
        "scenarios": scenarios,
    }
    if not all(validation.values()):
        raise SystemExit(f"baseline replay does not reproduce published R4 metrics: {validation}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
