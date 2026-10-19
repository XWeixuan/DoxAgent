"""Display membership from a Policy Set's version-pinned research origins."""

import json


def published_origins(source, bundle, store=None, source_document2=None):
    artifact = next(
        (a for a in bundle.get("artifacts", [])
         if a["relative_path"].endswith("/policy_origins.json")), None,
    )
    value = {}
    if artifact is not None:
        _, body = source.body(bundle["run_id"], artifact["artifact_id"])
        value = json.loads(body)
    if not isinstance(value, dict) or any(
        not isinstance(names, list) or any(not isinstance(name, str) for name in names)
        for names in value.values()
    ):
        raise ValueError("invalid published policy_origins")
    if store is not None:
        if source_document2:
            basis = store.get("policy_origin_basis", bundle["ticker"], source_document2["run_id"])
            if basis and basis["source_document2"] == source_document2:
                value = {**basis["policy_origins"], **{k: v for k, v in value.items() if v}}
        repair = store.get("policy_origin_repair", bundle["ticker"], bundle["run_id"])
        if repair:
            value = {**value, **repair["policy_origins"]}
    return value


def memberships(origins, shells):
    """Exact Shell names, stable order, no positional/fuzzy ownership inference."""
    by_name = {}
    for shell in shells:
        by_name.setdefault(shell.get("name", shell.get("core_question")), []).append(
            shell["shell_id"]
        )
    matched, unresolved = [], []
    for name in dict.fromkeys(origins):
        targets = by_name.get(name, [])
        if len(targets) == 1:
            if targets[0] not in matched:
                matched.append(targets[0])
        else:
            unresolved.append(name)
    return matched, unresolved
