"""Structural scheduling helpers; relationship meaning remains an agent decision."""

from __future__ import annotations

import re
from collections import defaultdict

from .schema_v21 import EditRequest, Review
from .state_v21 import canonical


def source_directory(path):
    return path.rsplit("/policies/", 1)[0] if "/policies/" in path else path.rsplit("/", 1)[0]


def directory_order(directory):
    ordinal = re.search(r"/(\d+)$", directory)
    return (int(ordinal[1]) if ordinal else 0, directory)


def directory_batches(items):
    groups = defaultdict(list)
    for item in items:
        groups[source_directory(item["path"])].append(item)
    ordered = sorted(groups, key=directory_order)
    return [
        [
            item
            for directory in ordered[i : i + 5]
            for item in sorted(groups[directory], key=lambda x: x["path"])
        ]
        for i in range(0, len(ordered), 5)
    ]


def parse_review(raw, previous=None):
    """Preserve omitted legacy edits, but honor an explicit empty pending list."""
    data = dict(raw)
    diagnostics = []
    edits = data.get("edit_requests", (previous or {}).get("edit_requests", []))
    healthy, seen = [], set()
    for item in edits if isinstance(edits, list) else [edits]:
        try:
            value = EditRequest.model_validate(item).model_dump(mode="json")
            key = canonical(value)
            if key not in seen:
                healthy.append(value)
                seen.add(key)
        except ValueError as exc:
            diagnostics.append({"error": f"invalid edit request:{exc}"})
    data["edit_requests"] = healthy
    return Review.model_validate(data), diagnostics


def edit_batches(review, policies):
    """One dispatch per request, anchored to its first unambiguous policy path."""
    by_id = defaultdict(list)
    for path, policy in policies.items():
        by_id[policy["policy_id"]].append(path)
    groups, diagnostics = defaultdict(list), []
    for edit in (review or {}).get("edit_requests", []):
        paths = []
        for ref in edit["policies"]:
            matches = [ref] if ref in policies else by_id.get(ref, [])
            if len(matches) != 1:
                diagnostics.append({"error": f"edit target missing/ambiguous:{ref}", "edit": edit})
                break
            paths.append(matches[0])
        else:
            normalized = {**edit, "policies": list(dict.fromkeys(paths))}
            groups[source_directory(paths[0])].append(normalized)
    ordered = sorted(groups, key=directory_order)
    return [
        {
            "focus_directories": ordered[i : i + 5],
            "edit_requests": [e for d in ordered[i : i + 5] for e in groups[d]],
            "batch": list(
                dict.fromkeys(e["policies"][0] for d in ordered[i : i + 5] for e in groups[d])
            ),
            "related_targets": list(
                dict.fromkeys(
                    p for d in ordered[i : i + 5] for e in groups[d] for p in e["policies"]
                )
            ),
        }
        for i in range(0, len(ordered), 5)
    ], diagnostics


def edit_inheritance(policy_id, targets, policies, fallback):
    """Prefer the explicit target version; retain identities from accepted versions."""
    versions = [policies[p] for p in targets if policies[p]["policy_id"] == policy_id]
    versions.extend(p for p in policies.values() if p["policy_id"] == policy_id)
    if policy_id in fallback:
        versions.append(fallback[policy_id])
    if not versions:
        return None
    conditions = {}
    for version in versions:
        for condition in version["activation_conditions"]:
            conditions.setdefault(condition["condition_id"], condition)
    return {**versions[0], "activation_conditions": list(conditions.values())}
