"""Audit-only reconstruction from ResearchResult/Topic ownership, never Policy refs."""


def trace_origins(run, drafts, inherited=None):
    topology = run.get("prepared", {}).get("topology", {})
    shells = {s["name"] for s in topology.get("shells", [])}
    owners = topology.get("research_owners", {})
    aliases = topology.get("route_aliases", {})
    paths = {}
    for group in (drafts, run.get("basis", {}), run.get("final_candidates", {})):
        for path, draft in group.items():
            paths[path] = draft.get("policy", draft)["policy_id"]
    evidence = {key: list(value) for key, value in (inherited or {}).items()}
    issues = []
    for agenda_name, result_name in (("agenda", "main_results"),
                                      ("supplement_agenda", "supplement_results")):
        topics = {}
        for topic in run.get(agenda_name, {}).get("topics", []):
            topics.setdefault(topic["name"], []).append(topic["owner"])
        for results in run.get(result_name, {}).values():
            for result in results:
                for path in result["policies"]:
                    pid = paths.get(path)
                    if pid is None:
                        issues.append({"path": path, "reason": "DRAFT_ID_MISSING"})
                        continue
                    for owner in topics.get(result["topic"], []):
                        slot = aliases.get(owner, owner)
                        shell = owners.get(slot, slot if slot in shells else None)
                        if shell not in shells:
                            shell = None  # OPEN topics retain evidence, without invented Shells.
                        item = dict(run_id=run["run_id"], path=path, topic=result["topic"],
                                    owner=owner, shell=shell,
                                    document2_ref=run.get("prepared", {}).get("document2_ref"))
                        evidence.setdefault(pid, [])
                        if item not in evidence[pid]:
                            evidence[pid].append(item)
    replacements = run.get("coverage", {}).get("replacements", [])
    successful = run.get("replacement_receipt", {}).get("successful", [])
    # Replacement order need not be topological. Propagate only committed groups.
    for _ in range(len(successful) + 1):
        changed = False
        for index in successful:
            group = replacements[index]
            source = [item for before in group["before"]
                      for item in evidence.get(paths.get(before, before), [])]
            for after in group["after"]:
                pid = paths.get(after)
                if pid is None:
                    issues.append({"path": after, "reason": "REPLACEMENT_DRAFT_ID_MISSING"})
                    continue
                target = evidence.setdefault(pid, [])
                for item in source:
                    if item not in target:
                        target.append(item)
                        changed = True
        if not changed:
            break
    final = {p["policy_id"] for p in run.get("final", [])}
    return (
        {pid: sorted({item["shell"] for item in evidence.get(pid, []) if item["shell"]})
         for pid in sorted(final)},
        {pid: evidence.get(pid, []) for pid in sorted(final)},
        issues,
    )
