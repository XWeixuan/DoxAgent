"""Deterministic, derived UTF-8 navigation; original context stays fully available."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

PAGE_SIZE = 6000


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def pages(text, size=PAGE_SIZE):
    result = []
    while text:
        end = min(len(text), size)
        if end < len(text):
            boundary = text.rfind("\n", 0, end)
            if boundary >= size // 2:
                end = boundary + 1
        result.append(text[:end])
        text = text[end:]
    return result or [""]


def sections(text):
    """Markdown headings outside fenced code, with character offsets for exact slices."""
    headings, offset, fence = [], 0, None
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        marker = re.match(r"(`{3,}|~{3,})", stripped)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token[0]
            elif token[0] == fence:
                fence = None
        elif fence is None and (match := re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line.rstrip())):
            headings.append(dict(title=match.group(2), level=len(match.group(1)), start=offset))
        offset += len(line)
    for i, heading in enumerate(headings):
        heading["end"] = next(
            (h["start"] for h in headings[i + 1 :] if h["level"] <= heading["level"]), len(text)
        )
        heading["id"] = f"s{i + 1}"
    return headings


def build_index(context, *, root, sources=None):
    """Return workspace-relative files. Sources optionally provide original ref/role/SHA."""
    files, groups = {}, dict(fields=[], reports=[], units=[], records=[])
    emitted = set()
    aliases = {}
    sources = {**context.get("research_asset_sources", {}), **(sources or {})}
    for key, role in (
        ("primary_source", context.get("source_role")),
        ("original_domain_report", context.get("reviewer_role")),
    ):
        if role and str(role).lower() in sources:
            sources[key] = sources[str(role).lower()]

    def add(pointer, value, group, role=None, source=None):
        if pointer in emitted:
            return
        emitted.add(pointer)
        text = value if isinstance(value, str) else _json(value)
        identity = hashlib.sha256((pointer + "\0" + text).encode("utf-8")).hexdigest()[:20]
        asset = f"assets/{identity}.txt" if isinstance(value, str) else f"records/{identity}.json"
        files[f"{root}/{asset}"] = text
        chunks = pages(text)
        page_paths = []
        for n, chunk in enumerate(chunks, 1):
            path = f"pages/{identity}/{n}.txt"
            files[f"{root}/{path}"] = chunk
            page_paths.append(path)
        heading_index = sections(text) if isinstance(value, str) else []
        for h in heading_index:
            h["pages"] = []
            for n, chunk in enumerate(pages(text[h["start"] : h["end"]]), 1):
                path = f"pages/{identity}/{h['id']}/{n}.txt"
                files[f"{root}/{path}"] = chunk
                h["pages"].append(path)
        entry = dict(
            id=identity,
            pointer=pointer,
            group=group,
            role=role or pointer,
            type=type(value).__name__,
            characters=len(text),
            count=len(value) if isinstance(value, (dict, list)) else None,
            path=asset,
            pages=page_paths,
            sections=heading_index,
            content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            source=source,
        )
        files[f"{root}/entries/{identity}.json"] = _json(entry)
        files[
            f"{root}/pointers/{hashlib.sha256(pointer.encode('utf-8')).hexdigest()[:20]}.json"
        ] = _json(entry)
        groups[group].append(
            {k: entry[k] for k in ("id", "pointer", "role", "type", "characters", "count")}
        )

    def walk(value, pointer="", ancestry=()):
        if isinstance(value, dict):
            for key, item in value.items():
                escaped = key.replace("~", "~0").replace("/", "~1")
                child = pointer + "/" + escaped
                route = ancestry + (key,)
                if (
                    ancestry == ("global_research",)
                    and key
                    in {
                        "future_nodes",
                        "entity_relations",
                        "entity_network_report",
                        "d1_product_status",
                        "d1_product_sources",
                    }
                    and key in context
                    and item == context[key]
                ):
                    aliases[child] = "/" + escaped
                    continue
                if not pointer:
                    add(
                        child,
                        item,
                        "fields",
                        role=str(sources.get(key, {}).get("role", key)),
                        source=sources.get(key),
                    )
                if key in {"research_asset_sources", "d1_product_sources"}:
                    continue
                if isinstance(item, str) and (
                    len(item) > 1200
                    or "reports" in route
                    or key in {"primary_source", "original_domain_report", "entity_network_report"}
                ):
                    # A top-level string already has a fields entry; also expose in report catalog.
                    if child in emitted:
                        entry = json.loads(
                            files[
                                f"{root}/pointers/{hashlib.sha256(child.encode()).hexdigest()[:20]}.json"
                            ]
                        )
                        groups["reports"].append(
                            {
                                k: entry[k]
                                for k in ("id", "pointer", "role", "type", "characters", "count")
                            }
                        )
                    else:
                        add(child, item, "reports", role="/".join(route), source=sources.get(key))
                if (
                    key
                    in {
                        "state",
                        "parameters",
                        "values",
                        "expectation_baseline",
                        "realization_factors",
                        "potential_gaps",
                        "late_additions",
                        "open_discovery_late_additions",
                        "open_discovery_resolution",
                        "selections",
                    }
                    and pointer
                ):
                    add(child, item, "records", role="/".join(route))
                walk(item, child, route)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                child = pointer + "/" + str(i)
                if isinstance(item, dict):
                    if ancestry and ancestry[-1] == "units":
                        add(
                            child,
                            item,
                            "units",
                            role=str(item.get("name", item.get("expectation_id", child))),
                        )
                    elif ancestry and ancestry[-1] in {
                        "late_additions",
                        "open_discovery_late_additions",
                        "open_discovery_resolution",
                        "selections",
                        "parameters",
                        "values",
                        "expectation_baseline",
                        "realization_factors",
                        "potential_gaps",
                        "candidates",
                    }:
                        add(
                            child,
                            item,
                            "records",
                            role=str(item.get("unit", ""))
                            + ":"
                            + str(item.get("name", item.get("candidate", child))),
                        )
                walk(item, child, ancestry)

    walk(context)
    index = dict(schema_version="context-index-v1", page_size=PAGE_SIZE, groups={})
    for alias, original in aliases.items():
        original_path = f"{root}/pointers/{hashlib.sha256(original.encode()).hexdigest()[:20]}.json"
        alias_path = f"{root}/pointers/{hashlib.sha256(alias.encode()).hexdigest()[:20]}.json"
        files[alias_path] = files[original_path]
    if aliases:
        index["aliases"] = aliases
    overview = [
        "# Context navigation",
        "",
        "Full original inputs remain available. All derived files are UTF-8.",
        "",
    ]
    for group, entries in groups.items():
        paths, current, length = [], [], 0
        batches = []
        for entry in entries:
            size = len(_json(entry))
            projected = _json(
                dict(page=999999, total_pages=999999, has_more=True, entries=current + [entry])
            )
            if current and len(projected) > PAGE_SIZE:
                batches.append(current)
                current, length = [], 0
            current.append(entry)
            length += size
        if current:
            batches.append(current)
        for n, batch in enumerate(batches, 1):
            path = f"catalog/{group}/{n}.json"
            files[f"{root}/{path}"] = _json(
                dict(page=n, total_pages=len(batches), has_more=n < len(batches), entries=batch)
            )
            paths.append(path)
        index["groups"][group] = dict(total_pages=len(paths), catalog=f"catalog/{group}")
        overview.append(f"- {group}: {len(entries)} entries / {len(paths)} catalog pages")
    overview += [
        "",
        "Use read_context.py list --group reports (or fields/units/records), "
        "then read --asset ID --page 1.",
        "Use read --pointer '/canonical_shell/units/0' --page 1; "
        "--section s1 selects a report heading.",
        "",
        "PowerShell fallback: Get-Content -LiteralPath "
        "'<index directory>/pages/<id>/1.txt' -Encoding utf8",
        "",
    ]
    overview.extend(
        f"- {alias}: same complete material as {original}" for alias, original in aliases.items()
    )
    files[f"{root}/index.json"] = _json(index)
    files[f"{root}/overview.md"] = "\n".join(overview)
    files[f"{root}/read_context.py"] = (
        Path(__file__).with_name("context_reader.py").read_text(encoding="utf-8")
    )
    return files


def attach_index(files, *, root, context=None, sources=None):
    """Best effort; callers always retain their original inputs on index failure."""
    try:
        if context is None:
            context = {}
            for path, content in files.items():
                if (
                    "/assets/" in path
                    or "/schemas/" in path
                    or path.endswith((".schema.json", "AGENTS.md", "agent.md"))
                    or path.startswith("output/")
                ):
                    continue
                try:
                    context[path] = json.loads(content)
                except (ValueError, TypeError):
                    context[path] = content
        derived = build_index(context, root=root, sources=sources)
        files.update(derived)
        return dict(
            index_path=f"{root}/index.json",
            overview_path=f"{root}/overview.md",
            reader_path=f"{root}/read_context.py",
            usage=(
                f"python {root}/read_context.py list --group reports; "
                "read --asset ID --page 1. UTF-8 pages and original full inputs are available."
            ),
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return dict(warning=f"context_index_unavailable: {exc}")
