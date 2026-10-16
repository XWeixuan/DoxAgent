"""Version-pinned research derivation shared across Cases and scheduler restarts."""

import json
from collections import OrderedDict
from pathlib import Path
from threading import RLock

from .w3_research_assets import derive_research_assets

_lock = RLock()
_cache = OrderedDict()


def instruction_assets(assets):
    names = {"w3/AGENTS.md", "w3/skills/skill.md"}
    if names.issubset(assets):
        return {k: v for k, v in assets.items() if k in names}
    return {k: v for k, v in assets.items() if k.startswith("w3/")}


def research_assets(context, journal):
    identity = getattr(context, "research_identity", None)
    if not identity:
        return derive_research_assets(context, policy_origins=context.policy_origins)
    with _lock:
        if identity in _cache:
            _cache.move_to_end(identity)
            return _cache[identity]
        root = Path(journal.path).parent / "w3-assets"
        target = root / (identity + ".json")
        if target.is_file():
            raw = json.loads(target.read_text(encoding="utf-8"))
            result = (raw[0], raw[1], raw[2])
        else:
            result = derive_research_assets(context, policy_origins=context.policy_origins)
            from doxagent.codex_worker.workspace_store import LocalWorkspaceStore

            root.mkdir(parents=True, exist_ok=True)
            LocalWorkspaceStore._atomic_write(
                target, json.dumps(result, ensure_ascii=False).encode()
            )
        _cache[identity] = result
        while len(_cache) > 16:
            _cache.popitem(last=False)
        return result
