"""Display-only references into a Policy's exact, indexed source D2.

No filesystem/HTTP lookup, fuzzy matching, or fallback to current activation.
"""

import json
import re

SHELL = re.compile(r"context/document3/v21/d2/shell-(\d{4,})\.json(?:#(/[^#]*)?)?")
DOCUMENT = re.compile(r"context/document3/v21/shared/document2\.json#/shells/(\d+)(/[^#]*)?")
SECTIONS = {
    "expectation_baseline": ("BASELINE", "baseline"),
    "realization_factors": ("FACTORS", "factors"),
    "potential_gaps": ("GAPS", "gaps"),
}


def _pointer_exists(value, parts):
    for part in parts:
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return False
    return True


def related_expectations(store, ticker, detail, seq=None):
    source = detail.get("source_document2")
    if not source:
        return []
    if seq is None:
        with store.connect() as db:
            seq = store.highwater(db)
    document = store.get("document_ref", ticker, source["run_id"], seq)
    if not document or any(
        document.get(k) != source.get(k) for k in ("run_id", "artifact_id", "content_sha256")
    ):
        return []
    policy = detail["policy"]
    candidates = []
    for ref in policy.get("ref", []):
        match = SHELL.fullmatch(ref)
        if match:
            # D3 inputs_v21 enumerates source D2 shells from 1. These are
            # filenames, not the zero-based/outcome-ordered reading Shell IDs.
            candidates.append((None, int(match[1]) - 1, match[2] or "", ref, None))
        else:
            match = DOCUMENT.fullmatch(ref)
            if match:
                candidates.append((None, int(match[1]), match[2] or "", ref, None))
    for ref in policy.get("source_refs", []):
        candidates.append(
            (
                ref["shell_id"],
                None,
                "",
                f"{ref['shell_id']} / {ref['expectation_id']} / {ref['gap_id']}",
                ref,
            )
        )
    if not candidates:
        return []
    run = source["run_id"]
    # Shell summaries contain only metadata and content references, not full reports.
    relation = store.snapshot_table(seq)
    with store.connect() as db:
        shells = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT payload FROM " + relation + " WHERE kind='shell' AND ticker=? AND parent=? "
                "AND valid_from<=? AND (valid_to IS NULL OR valid_to>?)",
                (ticker, run, seq, seq),
            )
        ]
    by_id = {s["shell_id"]: s for s in shells}
    by_pointer = {}
    for shell in shells:
        pointer = re.fullmatch(re.escape(run) + r"#/shells/(\d+)", shell.get("view_id", ""))
        if pointer and shell.get("status") != "FAILED":
            by_pointer[int(pointer[1])] = shell
    targets, requests = [], []
    for identity, ordinal, pointer, raw, legacy in candidates:
        shell = by_id.get(identity) if identity else by_pointer.get(ordinal)
        if shell is None:
            continue
        unit_id, suffix = None, []
        if legacy:
            unit_id = legacy["expectation_id"]
        elif pointer:
            parts = pointer.split("/")[1:]
            if len(parts) < 2 or parts[0] != "units" or not parts[1].isdigit():
                continue
            unit_id = f"unit-{int(parts[1]):04d}"
            suffix = parts[2:]
        if unit_id is not None:
            requests.append(("unit", ticker, f"{run}:{shell['shell_id']}:{unit_id}"))
        targets.append((shell, unit_id, suffix, raw, legacy))
    units = {}
    requests = list(dict.fromkeys(requests))
    for offset in range(0, len(requests), 500):
        units.update(store.batch_get(requests[offset : offset + 500], seq))
    resolved = {}
    for shell, unit_id, suffix, raw, legacy in targets:
        unit = (
            units.get(("unit", ticker, f"{run}:{shell['shell_id']}:{unit_id}")) if unit_id else None
        )
        if unit_id and unit is None:
            continue
        content, item, item_name = "ALL", None, None
        if legacy:
            gap = next((g for g in unit["potential_gaps"] if g["gap_id"] == legacy["gap_id"]), None)
            if gap is None:
                continue
            content, item, item_name = "GAPS", gap["gap_id"], gap["possible_occurrence"]
        elif suffix:
            if not _pointer_exists(unit, suffix):
                continue
            if suffix[0] in SECTIONS:
                content, prefix = SECTIONS[suffix[0]]
                if len(suffix) > 1:
                    if not suffix[1].isdigit() or int(suffix[1]) >= len(unit[suffix[0]]):
                        continue
                    index = int(suffix[1])
                    item, item_name = f"{prefix}-{index}", unit[suffix[0]][index]["name"]
            elif suffix[0] == "state":
                content = "STATE"
                if (
                    len(suffix) >= 3
                    and suffix[1] in {"parameters", "values"}
                    and suffix[2].isdigit()
                ):
                    index = int(suffix[2])
                    if index >= len(unit["state"][suffix[1]]):
                        continue
                    if suffix[1] == "values":
                        parameter = unit["state"]["values"][index]["parameter"]
                        index = next(
                            (
                                i
                                for i, p in enumerate(unit["state"]["parameters"])
                                if p["name"] == parameter
                            ),
                            -1,
                        )
                    if index < 0:
                        continue
                    item, item_name = f"state-{index}", unit["state"]["parameters"][index]["name"]
                elif len(suffix) > 1:
                    continue
            else:
                continue
        key = (shell["shell_id"], unit_id, content, item)
        if key not in resolved:
            resolved[key] = dict(
                run_id=run,
                shell_id=shell["shell_id"],
                shell_name=shell.get("name", shell.get("core_question", shell["shell_id"])),
                unit_id=unit_id,
                unit_name=(unit.get("name", unit.get("proposition")) if unit else None),
                content=content,
                item=item,
                item_name=item_name,
                refs=[],
            )
        if raw not in resolved[key]["refs"]:
            resolved[key]["refs"].append(raw)
    return list(resolved.values())


def enrich_policy(store, ticker, detail, seq=None):
    return {**detail, "related_expectations": related_expectations(store, ticker, detail, seq)}
