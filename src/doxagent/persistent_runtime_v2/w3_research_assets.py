"""Lossless research slices. Navigation is derived; research remains authoritative."""

from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from threading import RLock

from .journal import digest

_cache = OrderedDict()
_cache_lock = RLock()


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def _name(value):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip(" .") or "untitled"
    return cleaned[:70] + ("_" + digest(str(value))[:8] if len(cleaned) > 70 else "")


def natural_chapters(text):
    headings, offset, fence = [], 0, None
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        token = re.match(r"(`{3,}|~{3,})", stripped)
        if token:
            marker = token[1][0]
            if fence is None:
                fence = (marker, len(token[1]))
            elif marker == fence[0] and len(token[1]) >= fence[1]:
                fence = None
        if fence is None and not token:
            match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
            if match:
                headings.append((offset, len(match[1]), match[2]))
        offset += len(line)
    levels = sorted({h[1] for h in headings})
    depth = levels[1] if len(levels) > 1 else (levels[0] if levels else 1)
    starts = [(p, title) for p, level, title in headings if level <= depth]
    if not starts or starts[0][0] != 0:
        starts.insert(0, (0, "前言"))
    parts = [
        (title, text[start : starts[i + 1][0] if i + 1 < len(starts) else len(text)])
        for i, (start, title) in enumerate(starts)
    ]
    assert "".join(part for _, part in parts) == text
    return parts


def derive_research_assets(context, *, policy_origins=None):
    """Return immutable file content and a compact index, without summaries."""
    d2 = context.document2.model_dump(mode="json")
    d3 = context.policy_set.model_dump(mode="json")
    original_d2 = getattr(context, "original_document2", None) or _json(d2)
    original_d3 = getattr(context, "original_policy_set", None) or _json(d3)
    key = digest(
        ["w3-slices-v1", context.document1, original_d2, original_d3, policy_origins or {}]
    )
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    files = {
        "originals/document1.md": context.document1,
        "originals/document2.json": original_d2,
        "originals/policy_set.json": original_d3,
    }
    index = dict(document1=[], document2=[], document3=[], shells=[])
    for n, (title, body) in enumerate(natural_chapters(context.document1), 1):
        path = f"d1/{n:03d}_{_name(title)}.md"
        files[path] = body
        index["document1"].append(dict(number=n, title=title, path=path))
    shell_paths = {}
    for sn, shell in enumerate(d2.get("shells", []), 1):
        shell_id = shell.get("shell_id") or shell.get("name") or str(sn)
        title = shell.get("name") or shell.get("title") or shell_id
        directory = f"{sn:03d}_{_name(title)}"
        shell_paths[shell_id] = directory
        shell_paths[title] = directory
        shell_info = {k: v for k, v in shell.items() if k != "units"}
        index["shells"].append(dict(number=sn, title=title, identity=shell_id, content=shell_info))
        for un, unit in enumerate(shell.get("units", []), 1):
            unit_id = unit.get("unit_id") or unit.get("expectation_id")
            unit_title = unit.get("name") or unit.get("title") or unit_id or f"Unit {un}"
            path = f"d2/{directory}/{_name(unit_id or f'{un:03d}')}_{_name(unit_title)}.json"
            files[path] = _json(unit)
            index["document2"].append(
                dict(
                    number=un,
                    identity=unit_id,
                    title=unit_title,
                    shell=shell_id,
                    path=path,
                    scope=unit.get("scope"),
                    derived_locator=unit_id is None,
                )
            )
    for policy in d3.get("policies", []):
        pid, title = policy["policy_id"], policy.get("title", policy["policy_id"])
        origins = list(dict.fromkeys((policy_origins or {}).get(pid, [])))
        known = [s for s in origins if s in shell_paths]
        directory = (
            shell_paths[known[0]]
            if len(known) == 1 and len(origins) == 1
            else "000_cross_shell"
            if len(known) > 1
            else "000_unassigned"
        )
        path = f"d3/{directory}/{_name(pid)}_{_name(title)}.json"
        files[path] = _json(policy)
        index["document3"].append(
            dict(
                identity=pid,
                title=title,
                shells=known,
                path=path,
                association_kind="research_origin" if known else "unassigned",
                scope=policy.get("match_scope"),
            )
        )
    files["index.json"] = _json(index)
    files["manifest.json"] = _json(
        dict(
            asset_key=key,
            derivation="w3-slices-v1",
            files={p: hashlib.sha256(v.encode()).hexdigest() for p, v in files.items()},
        )
    )
    result = key, files, index
    with _cache_lock:
        _cache[key] = result
        while len(_cache) > 8:
            _cache.popitem(last=False)
    return result
