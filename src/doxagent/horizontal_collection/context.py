"""Role-scoped context artifact generated from the horizontal bundle."""

from __future__ import annotations

import json

from doxagent.horizontal_collection.schema import HorizontalCollectionBundle


def render_horizontal_context(bundle: HorizontalCollectionBundle, *, target_prefix: str) -> str:
    selected = [
        value.model_dump(mode="json")
        for value in bundle.state_values
        if value.collection_target_id.startswith(target_prefix)
    ]
    target_status = [
        result.model_dump(mode="json")
        for result in bundle.manifest.target_results
        if result.collection_target_id.startswith(target_prefix)
    ]
    return json.dumps(
        {
            "schema_version": "d1-horizontal-context-v1",
            "state_values": selected,
            "target_status": target_status,
            "instructions": (
                "Use promoted state_values as governed measurements. EMPTY, FAILED, and "
                "UNAVAILABLE targets are explicit unknowns and must not be replaced by zero."
            ),
        },
        ensure_ascii=False,
        indent=2,
    )
