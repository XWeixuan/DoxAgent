"""Replay Wave C and N13 pair-local Apply from a fixed completed registry without LLM calls."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from cdecr.bulk_epoch.indexes import MultiKeyBoundedIndex
from cdecr.contracts import EventPackage
from cdecr.coreference_rules import merge_packages, stable_id
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
    parser.add_argument("--gold-registry", type=Path)
    parser.add_argument("--gold-review", type=Path)
    parser.add_argument(
        "--mutual-comparison",
        type=Path,
        help="Restrict replay to the current-event Gold labels in a mutual-run comparison.",
    )
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


def _wave_c_bounded_pairs(
    engine: CrossDocumentEngine,
    packages: dict[str, EventPackage],
    *,
    pair_cap: int = 64,
) -> list[tuple[EventPackage, EventPackage, PackagePairBoundary, float]]:
    """Rebuild the frozen Wave C candidate plan without writing audits or calling a model."""

    def index_keys(package: EventPackage) -> list[str]:
        keys: list[str] = []
        if package.anchor_artifact_id:
            keys.append(f"artifact:{package.anchor_artifact_id}")
        keys.extend(f"anchor:{value}" for value in package.package_anchor_ids)
        keys.extend(f"entity:{value}" for value in package.anchor_entities)
        if package.anchor_period_id:
            keys.append(f"period:{package.anchor_period_id}")
        for event in engine._package_member_events(package):
            keys.append(
                "identity:"
                + stable_id("wave-c-identity", event.identity_profile.model_dump(mode="json"))
            )
            for mention_id in event.mention_ids:
                mention = engine.registry.get_mention(mention_id)
                if mention is not None:
                    keys.append(f"source:{mention.message_id}")
        return sorted(set(keys))

    candidate_index = MultiKeyBoundedIndex()
    keys_by_package = {
        package_id: index_keys(package) for package_id, package in packages.items()
    }
    for package_id in sorted(packages):
        candidate_index.add(package_id, keys_by_package[package_id])
    rows: list[tuple[EventPackage, EventPackage, PackagePairBoundary, float]] = []
    seen: set[tuple[str, str]] = set()
    for left_id in sorted(packages):
        for right_id in candidate_index.query(
            keys_by_package[left_id], limit=16, exclude=left_id
        ):
            pair = tuple(sorted((left_id, right_id)))
            if pair in seen:
                continue
            seen.add(pair)
            left, right = packages[left_id], packages[right_id]
            routes, _ = engine._package_pair_signals(left, right, {})
            boundary = engine._package_pair_boundary(left, right, routes)
            if boundary.hard_blocked:
                continue
            strong = set(routes).intersection(
                {
                    RecallRoute.CANONICAL_ARTIFACT,
                    RecallRoute.PACKAGE_ANCHOR,
                    RecallRoute.PARENT_CONTEXT,
                    RecallRoute.SAME_SOURCE_MEMBER,
                    RecallRoute.MEMBER_IDENTITY,
                }
            )
            if not strong:
                continue
            score = float(len(strong))
            score += 0.5 if RecallRoute.CORE_ENTITY in routes else 0.0
            score += 0.5 if RecallRoute.TIME_WINDOW in routes else 0.0
            rows.append((left, right, boundary, score))
    rows.sort(key=lambda row: (-row[3], row[0].package_id, row[1].package_id))
    per_fragment: dict[str, int] = defaultdict(int)
    bounded: list[tuple[EventPackage, EventPackage, PackagePairBoundary, float]] = []
    for row in rows:
        left, right, _, _ = row
        if per_fragment[left.package_id] >= 4 or per_fragment[right.package_id] >= 4:
            continue
        bounded.append(row)
        per_fragment[left.package_id] += 1
        per_fragment[right.package_id] += 1
        if len(bounded) >= pair_cap:
            break
    return bounded


def _install_package_read_cache(engine: CrossDocumentEngine) -> None:
    """Cache immutable package-derived reads during the offline replay.

    The state key includes the package version and members, so Apply-time merged
    packages cannot reuse candidate-planning values from an older package shape.
    """

    def state_key(package: EventPackage) -> tuple[Any, ...]:
        return (
            package.package_id,
            package.version,
            tuple(package.member_event_ids),
            tuple(package.package_anchor_ids),
            package.anchor_artifact_id,
        )

    original_members = engine._package_member_events
    original_anchors = engine._trusted_package_anchor_ids
    original_fields = engine._trusted_package_field_ids
    original_sessions = engine._package_trading_sessions
    original_measures = engine._package_market_measures
    original_pure_market = engine._package_is_pure_market
    member_cache: dict[tuple[Any, ...], Any] = {}
    anchor_cache: dict[tuple[Any, ...], Any] = {}
    field_cache: dict[tuple[tuple[Any, ...], frozenset[str]], Any] = {}
    session_cache: dict[tuple[Any, ...], Any] = {}
    measure_cache: dict[tuple[Any, ...], Any] = {}
    pure_market_cache: dict[tuple[Any, ...], Any] = {}

    def members(package: EventPackage) -> Any:
        key = state_key(package)
        if key not in member_cache:
            member_cache[key] = original_members(package)
        return member_cache[key]

    def anchors(package: EventPackage) -> Any:
        key = state_key(package)
        if key not in anchor_cache:
            anchor_cache[key] = original_anchors(package)
        return anchor_cache[key]

    def fields(package: EventPackage, namespaces: set[str]) -> Any:
        key = (state_key(package), frozenset(namespaces))
        if key not in field_cache:
            field_cache[key] = original_fields(package, namespaces)
        return field_cache[key]

    def sessions(package: EventPackage) -> Any:
        key = state_key(package)
        if key not in session_cache:
            session_cache[key] = original_sessions(package)
        return session_cache[key]

    def measures(package: EventPackage) -> Any:
        key = state_key(package)
        if key not in measure_cache:
            measure_cache[key] = original_measures(package)
        return measure_cache[key]

    def pure_market(package: EventPackage) -> Any:
        key = state_key(package)
        if key not in pure_market_cache:
            pure_market_cache[key] = original_pure_market(package)
        return pure_market_cache[key]

    engine._package_member_events = members  # type: ignore[method-assign]
    engine._trusted_package_anchor_ids = anchors  # type: ignore[method-assign]
    engine._trusted_package_field_ids = fields  # type: ignore[method-assign]
    engine._package_trading_sessions = sessions  # type: ignore[method-assign]
    engine._package_market_measures = measures  # type: ignore[method-assign]
    engine._package_is_pure_market = pure_market  # type: ignore[method-assign]


def _relaxed_m0_edges(
    bounded: list[tuple[EventPackage, EventPackage, PackagePairBoundary, float]],
) -> list[dict[str, str]]:
    return [
        {
            "source_package_id": left.package_id,
            "target_package_id": right.package_id,
            "reason": "N12_WAVE_C_M0_RELAXED_REPLAY",
        }
        for left, right, boundary, _ in bounded
        if boundary.shared_artifact_ids or boundary.shared_anchor_ids
    ]


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
    _install_package_read_cache(engine)

    package_evaluation = json.loads(args.package_evaluation.read_text(encoding="utf-8"))
    mutual_comparison: dict[str, Any] | None = None
    if args.mutual_comparison is not None:
        mutual_comparison = json.loads(args.mutual_comparison.read_text(encoding="utf-8"))
        gold = {
            str(item["current_event_id"]): str(item["gold_label"])
            for item in mutual_comparison["aligned_pairs"]
        }
    else:
        if args.gold_registry is None or args.gold_review is None:
            raise SystemExit(
                "--gold-registry and --gold-review are required without --mutual-comparison"
            )
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
    bounded_pairs = _wave_c_bounded_pairs(engine, packages)
    actual_m3_edges = [item for item in edges if item["reason"] == "N12_WAVE_C_M3"]
    relaxed_m0_edges = _relaxed_m0_edges(bounded_pairs)
    relaxed_edges_by_pair = {
        tuple(sorted((item["source_package_id"], item["target_package_id"]))): item
        for item in [*actual_m3_edges, *relaxed_m0_edges]
    }
    relaxed_edges = [relaxed_edges_by_pair[pair] for pair in sorted(relaxed_edges_by_pair)]

    scenarios: dict[str, Any] = {}
    scenario_specs = (
        ("wave_c_keep_n13_off", edges, True, False),
        ("wave_c_keep_n13_on", edges, True, True),
        ("wave_c_relaxed_m0_n13_off", relaxed_edges, True, False),
    )
    for name, scenario_edges, wave_v2, n13_on in scenario_specs:
        wave_packages, redirects, wave_audit = _apply_wave_c(
            engine=engine,
            packages=packages,
            edges=scenario_edges,
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

    if mutual_comparison is not None:
        published = mutual_comparison["current"]["metrics"]
    else:
        published = package_evaluation["modes"]["current_actual_packages"]["metrics"]
    baseline_name = (
        "wave_c_keep_n13_on"
        if connection.execute(
            "SELECT 1 FROM package_redirects WHERE reason = 'N13_SAME_PACKAGE_COMPONENT' LIMIT 1"
        ).fetchone()
        else "wave_c_keep_n13_off"
    )
    replayed = scenarios[baseline_name]["metrics"]
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
            "wave_c_fixed_candidate_pair_count": len(bounded_pairs),
            "wave_c_relaxed_m0_edge_count": len(relaxed_m0_edges),
            "n13_same_decision_count": len(n13_decisions),
            "gold_evaluable_atomic_count": len(gold),
            "baseline_scenario": baseline_name,
            "baseline_validation": validation,
            "prompt_replay_limitation": (
                "No LLM was called. Frozen M3 SAME decisions are reused; the restored Prompt is "
                "verified structurally, not re-decided semantically."
            ),
        },
        "scenarios": scenarios,
    }
    if not all(validation.values()):
        raise SystemExit(f"baseline replay does not reproduce published R4 metrics: {validation}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
