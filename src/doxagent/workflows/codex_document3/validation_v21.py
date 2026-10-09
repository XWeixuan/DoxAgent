"""Local salvage, durable identity allocation and structural replacement only."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import PurePosixPath
from typing import get_args, get_origin
from uuid import uuid4

from pydantic import BaseModel, TypeAdapter, ValidationError

from .schema_v21 import Agenda, ConditionDraftV3, PolicyDraftV3, PolicyV3
from .state_v21 import canonical, digest


def safe_path(path):
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts or "\\" in path or ":" in path:
        raise ValueError("unsafe canonical reference")
    return p.as_posix()


def records(text, model):
    good, bad = [], []
    for line, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
            parsed = model.model_validate(value)
            good.append((line, parsed))
            extras = set(value) - set(model.model_fields)
            if extras:
                bad.append(
                    {"line": line, "warning": "ignored extra fields", "fields": sorted(extras)}
                )
        except (ValueError, TypeError, ValidationError) as exc:
            bad.append({"line": line, "error": str(exc)[:500], "raw": raw})
    return good, bad


def structured(text, model):
    """Salvage independently typed list records inside a readable work document."""
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("work document must be an object")
    issues = []
    extras = sorted(set(raw) - set(model.model_fields))
    if extras:
        issues.append({"warning": "ignored extra fields", "fields": extras})
    for name, field in model.model_fields.items():
        annotation = field.annotation
        if get_origin(annotation) is not list or not isinstance(raw.get(name), list):
            continue
        item_type = get_args(annotation)[0]
        if item_type is dict or item_type is object:
            continue
        valid = []
        for ordinal, value in enumerate(raw[name]):
            try:
                checked = TypeAdapter(item_type).validate_python(value)
                valid.append(
                    checked.model_dump(mode="json") if isinstance(checked, BaseModel) else checked
                )
            except ValueError as exc:
                issues.append({"field": name, "ordinal": ordinal, "error": str(exc)[:300]})
        raw[name] = valid
    return model.model_validate(raw), issues


def normalize_agenda(raw, owners, route_aliases=None, *, fallback_owner=None):
    """Resolve all Topics to real owner IDs with one explicit open fallback."""
    owners = {slot: name for slot, name in owners.items() if slot != "GLOBAL"}
    fallback_owner = fallback_owner or ("OPEN_RESEARCH" if "OPEN_RESEARCH" in owners else "OPEN")
    if fallback_owner not in owners:
        raise ValueError("Agenda fallback must be a real research owner")
    agenda = Agenda.model_validate(raw)
    groups = defaultdict(list)
    for topic in agenda.topics:
        groups[topic.name].append(topic)
    topics, issues, routes = [], [], {}
    aliases = defaultdict(list)
    for slot, name in owners.items():
        aliases[name].append(slot)
    for name, group in groups.items():
        if len({canonical(t.model_dump()) for t in group}) != 1:
            issues.append(f"conflicting topic:{name}")
            continue
        topic = group[0]
        targets = (
            [topic.owner]
            if topic.owner in owners
            else [route_aliases[topic.owner]]
            if route_aliases and topic.owner in route_aliases
            else aliases[topic.owner]
        )
        if topic.owner == "OPEN" and "OPEN" not in owners:
            targets = [fallback_owner]
            issues.append(f"legacy OPEN routed to {fallback_owner}:{name}")
        if topic.owner == "GLOBAL":
            targets = []
        slot = targets[0] if len(targets) == 1 and targets[0] in owners else fallback_owner
        if not targets or len(targets) != 1 or targets[0] not in owners:
            issues.append(f"owner routed to {fallback_owner}:{name}")
        routes[name] = slot
        topics.append(topic.model_copy(update={"owner": slot}))
    by_name = {t.name: t for t in topics}
    seen, waves = set(), []
    for wave in agenda.waves:
        current = []
        for name in wave:
            if name not in by_name or name in seen:
                continue
            seen.add(name)
            if current and (routes[current[0]] != routes[name] or len(current) == 3):
                waves.append(current)
                current = []
            current.append(name)
        if current:
            waves.append(current)
    for slot in owners:
        missing = [t.name for t in topics if routes[t.name] == slot and t.name not in seen]
        waves.extend(missing[i : i + 3] for i in range(0, len(missing), 3))
    return Agenda(topics=topics, waves=waves, not_selected=agenda.not_selected), issues


def accept_policy(raw, *, state, run_id, path, inherited=None):
    """IDs come from the accepted path ledger, never economic-content hashes."""
    path = safe_path(path)
    previous = state.get_draft(run_id, path)
    original_hash = digest(raw)
    if previous and previous.get("raw_hash") == original_hash:
        return PolicyV3.model_validate(previous["policy"]), previous.get("errors", [])
    old = previous["policy"] if previous else inherited
    errors = []
    if not isinstance(raw, dict):
        return None, ["Policy is not an object"]
    raw = dict(raw)
    known = {c["condition_id"]: c for c in (old or {}).get("activation_conditions", [])}
    conditions = raw.get("activation_conditions")
    if not isinstance(conditions, list):
        return (PolicyV3.model_validate(old) if old else None), ["missing Conditions"]
    if (
        old
        and conditions
        and all(not c.get("condition_id") for c in conditions if isinstance(c, dict))
    ):
        return PolicyV3.model_validate(old), ["accepted rewrite lost all Condition IDs"]
    groups = defaultdict(list)
    for c in conditions:
        if isinstance(c, dict) and isinstance(c.get("condition_id"), str) and c.get("condition_id"):
            groups[c["condition_id"]].append(c)
    conflict = {key for key, group in groups.items() if len({canonical(c) for c in group}) > 1}
    used = set()
    accepted = []
    maximum = previous.get("max_condition", 0) if previous else 0
    if old:
        maximum = max(maximum, state.maximum_condition(old["policy_id"]))
    maximum = max([maximum, *[int(c[1:]) for c in known if re.fullmatch(r"C\d+", c)]])
    for candidate in conditions:
        cid = candidate.get("condition_id") if isinstance(candidate, dict) else None
        if cid is not None and not isinstance(cid, str):
            errors.append("invalid Condition ID type")
            continue
        if cid in conflict:
            if cid not in used and cid in known and inherited:
                accepted.append(known[cid])
                used.add(cid)
            errors.append(f"conflicting Condition:{cid}")
            continue
        if cid and cid in used:
            continue
        try:
            c = ConditionDraftV3.model_validate(candidate).model_dump(mode="json")
            if cid and old and cid not in known:
                raise ValueError("unknown explicit Condition ID; new Conditions must omit ID")
            if not cid:
                maximum += 1
                while f"C{maximum}" in used:
                    maximum += 1
                cid = f"C{maximum}"
            elif re.fullmatch(r"C\d+", cid):
                maximum = max(maximum, int(cid[1:]))
            c["condition_id"] = cid
            accepted.append(c)
            used.add(cid)
        except (ValueError, TypeError) as exc:
            errors.append(str(exc)[:300])
            if inherited and cid in known and cid not in used:
                accepted.append(known[cid])
                used.add(cid)
    raw["activation_conditions"] = accepted
    raw["policy_id"] = (old or {}).get("policy_id") or raw.get("policy_id") or f"pol_{uuid4().hex}"
    try:
        draft = PolicyDraftV3.model_validate(raw)
        policy = PolicyV3.model_validate(draft.model_dump(mode="json"))
    except ValueError as exc:
        return (PolicyV3.model_validate(old) if old else None), [*errors, str(exc)[:300]]
    state.save_draft(
        run_id,
        path,
        {
            "policy": policy.model_dump(mode="json"),
            "max_condition": maximum,
            "raw_hash": original_hash,
            "errors": errors,
            "ignored_fields": sorted(set(raw) - set(PolicyDraftV3.model_fields)),
        },
    )
    return policy, errors


def replace_structurally(basis, candidates, replacements):
    """Preflight every group and roll back all conflicting groups to a fixed point."""
    base = {p.policy_id: p for p in basis}
    failed = {}
    before_count = Counter(x for r in replacements for x in set(r.before))
    after_count = Counter(x for r in replacements for x in set(r.after))
    id_count = Counter(
        candidates[x].policy_id for r in replacements for x in set(r.after) if x in candidates
    )
    for i, r in enumerate(replacements):
        ids = [candidates[x].policy_id for x in r.after if x in candidates]
        if any(x not in base or before_count[x] > 1 for x in r.before):
            failed[i] = "unknown or overlapping before"
        elif any(x not in candidates or after_count[x] > 1 for x in r.after):
            failed[i] = "missing or overlapping after"
        elif len(ids) != len(set(ids)) or any(id_count[x] > 1 for x in ids):
            failed[i] = "conflicting after Policy ID"
    while True:
        removed = {x for i, r in enumerate(replacements) if i not in failed for x in r.before}
        kept = set(base) - removed
        collision = [
            i
            for i, r in enumerate(replacements)
            if i not in failed and any(candidates[x].policy_id in kept for x in r.after)
        ]
        if not collision:
            break
        failed.update({i: "after ID collides with retained basis" for i in collision})
    final = [p for p in basis if p.policy_id not in removed]
    final.extend(
        candidates[x] for i, r in enumerate(replacements) if i not in failed for x in r.after
    )
    return final, {
        "successful": [i for i in range(len(replacements)) if i not in failed],
        "failed": failed,
    }


def final_identities(policies):
    groups = defaultdict(list)
    for policy in policies:
        groups[policy.policy_id].append(policy)
    accepted, conflicts = [], []
    for policy_id, group in groups.items():
        if len({canonical(p.model_dump(mode="json")) for p in group}) > 1:
            conflicts.append(policy_id)
        else:
            accepted.append(group[0])
    return accepted, conflicts
