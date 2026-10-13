"""Publication sidecar for research provenance, without changing Policy semantics."""

from __future__ import annotations

import re


def policy_origins(run, drafts, inherited=None):
    shells = {s["slot"]: s["name"] for s in run["prepared"]["topology"]["shells"]}
    origins = {k: set(v) for k, v in (inherited or {}).items()}
    paths = {}
    for path, draft in drafts.items():
        pid = draft["policy"]["policy_id"]
        paths[path] = pid
        match = re.search(r"/v21/(?:research|supplement)/([^/]+)/", path)
        source = {shells[match[1]]} if match and match[1] in shells else set()
        origins.setdefault(pid, set()).update(source | set(draft.get("origin_shells", [])))
    for group in run.get("coverage", {}).get("replacements", []):
        source = set().union(*(origins.get(paths.get(p, p), set()) for p in group["before"]))
        for path in group["after"]:
            origins.setdefault(paths.get(path, path), set()).update(source)
    return {p["policy_id"]: sorted(origins.get(p["policy_id"], set())) for p in run["final"]}


def edit_origins(run, drafts, editable, targets):
    expanded = {**run, "final": list(editable.values()), "coverage": {}}
    origins = policy_origins(expanded, drafts, run.get("base_policy_origins", {}))
    return sorted(
        set().union(
            *(set(origins.get(editable[p]["policy_id"], [])) for p in targets if p in editable)
        )
    )
