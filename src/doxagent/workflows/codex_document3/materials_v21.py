"""Stable per-material indexes and small per-task navigation manifests."""

from __future__ import annotations

from doxagent.codex_runtime.context_index import attach_index

from .state_v21 import canonical, digest


def reference_path(ref, content):
    extension = "json" if ref.endswith(".json") else "txt"
    return f"context/document3/v21/references/{digest(ref)[:20]}-{digest(content)[:20]}.{extension}"


def attach_material_indexes(files, mapping, metadata, *, task_root, cache):
    sources = []
    for item in mapping:
        path, ref = item["local_path"], item["ref"]
        info = {
            **metadata.get(ref, {}),
            "original_ref": ref,
            "local_path": path,
            "projection_status": "available",
        }
        identity = digest({"ref": ref, "sha256": digest(files[path]), "source": info})[:24]
        if identity not in cache:
            derived = {}
            try:
                import json

                context = {path: json.loads(files[path])}
            except (ValueError, TypeError):
                context = {path: files[path]}
            navigation = attach_index(
                derived,
                root=f"context/document3/v21/context_index/materials/{identity}",
                context=context,
                sources={path: info},
            )
            cache[identity] = (derived, navigation)
        derived, navigation = cache[identity]
        files.update(derived)
        sources.append({"ref": ref, **navigation})
    index_path = f"{task_root}/index.json"
    overview_path = f"{task_root}/overview.md"
    files[index_path] = canonical({"schema_version": "context-index-refs-v1", "sources": sources})
    files[overview_path] = (
        "# Context navigation\n\nFull inputs remain in read_mapping. "
        "Stable per-material indexes:\n\n"
        + "\n".join(
            f"- {entry['ref']}: {entry.get('index_path', entry.get('warning', 'unavailable'))}"
            for entry in sources
        )
    )
    return {
        "index_path": index_path,
        "overview_path": overview_path,
        "sources": sources,
        "usage": (
            "Select a source index and use its read_context.py; "
            "read_mapping always provides full text."
        ),
    }
