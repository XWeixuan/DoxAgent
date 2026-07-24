"""Compact, reversible representation helpers for model-facing JSON payloads."""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter

_ID_CONTAINER_KEYS = {
    "anchor_entities",
    "artifact_candidate_ids",
    "location_or_asset_ids",
    "package_anchor_ids",
    "principal_participant_ids",
}


def compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _id_bearing_key(key: str | None) -> bool:
    return bool(
        key
        and (
            key.endswith("_id")
            or key.endswith("_ids")
            or key in _ID_CONTAINER_KEYS
        )
    )


def intern_repeated_ids(payload: dict[str, object]) -> dict[str, object]:
    """Intern repeated long IDs when the resulting wire payload is actually smaller.

    The returned ``refs`` map is request-local and read-only. Candidate/output IDs
    such as ``m1/a1/e1/p1`` are too short to qualify and therefore remain unchanged.
    """

    counts: Counter[str] = Counter()

    def collect(value: object, parent_key: str | None = None) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                collect(item, str(key))
        elif isinstance(value, list):
            for item in value:
                collect(item, parent_key)
        elif (
            isinstance(value, str)
            and len(value) >= 18
            and _id_bearing_key(parent_key)
        ):
            counts[value] += 1

    collect(payload)
    repeated = sorted(value for value, count in counts.items() if count >= 2)
    if not repeated:
        return payload
    short_by_full = {value: f"r{index}" for index, value in enumerate(repeated, start=1)}

    def replace(value: object, parent_key: str | None = None) -> object:
        if isinstance(value, dict):
            return {key: replace(item, str(key)) for key, item in value.items()}
        if isinstance(value, list):
            return [replace(item, parent_key) for item in value]
        if (
            isinstance(value, str)
            and _id_bearing_key(parent_key)
            and value in short_by_full
        ):
            return short_by_full[value]
        return value

    transformed = replace(copy.deepcopy(payload))
    assert isinstance(transformed, dict)
    candidate = {
        "refs": {
            short_id: full_id
            for full_id, short_id in short_by_full.items()
        },
        **transformed,
    }
    return candidate if len(compact_json(candidate)) < len(compact_json(payload)) else payload


def wire_ref_metadata(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or not isinstance(payload.get("refs"), dict):
        return {"wire_ref_count": 0}
    refs = payload["refs"]
    return {
        "wire_ref_count": len(refs),
        "wire_ref_map_hash": hashlib.sha256(
            compact_json(refs).encode("utf-8")
        ).hexdigest(),
    }
