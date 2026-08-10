"""Deterministic output-profile cleaning before Observation segmentation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from doxagent.data_runtime.contracts import DataToolContract

_DROP_AT_ANY_DEPTH = {
    "debug",
    "debug_info",
    "raw",
    "raw_data",
    "raw_payload",
    "raw_response",
    "request_echo",
    "response_headers",
}
_TOP_LEVEL_ENVELOPE_KEYS = {
    "as_of",
    "cik",
    "endpoint",
    "fallback_for",
    "input",
    "interval",
    "metadata",
    "page_info",
    "pagination",
    "provider",
    "query",
    "request",
    "request_params",
    "record_count",
    "record_type",
    "semantic_group",
    "source_coordinates",
    "source_url",
    "symbol",
    "ticker",
    "unofficial_source",
    "warnings",
}
_TOOL_ENVELOPE_KEYS = {
    "ibkr.contract_search": {"transport", "contract_count"},
    "ibkr.market_history": {"transport", "con_id", "period", "bar", "outside_rth"},
    "ibkr.market_snapshot": {"transport", "con_id", "market_data_type"},
}


@dataclass(frozen=True)
class ProfiledOutput:
    content: dict[str, Any]
    source_coordinates: Any | None
    envelope: dict[str, Any]
    removed_paths: tuple[str, ...]


def apply_output_profile(
    contract: DataToolContract,
    output: dict[str, Any],
) -> ProfiledOutput:
    """Remove provider wrappers without deleting governed business fields."""

    source_coordinates = deepcopy(output.get("source_coordinates"))
    envelope: dict[str, Any] = {"output_profile": contract.output_profile}
    removed: list[str] = []
    content: dict[str, Any] = {}
    source_capture = contract.canonical_tool_id == "source_capture"
    source_capture_envelope = {"url", "title", "source", "note"}
    tool_envelope = _TOOL_ENVELOPE_KEYS.get(contract.canonical_tool_id, set())
    for key, value in output.items():
        path = f"/{key}"
        if (
            key in _TOP_LEVEL_ENVELOPE_KEYS
            or key in tool_envelope
            or (source_capture and key in source_capture_envelope)
        ):
            if key != "source_coordinates" and value not in (None, "", [], {}):
                envelope[key] = _clean_value(value, path=path, removed=removed)
            removed.append(path)
            continue
        cleaned = _clean_value(value, path=path, removed=removed)
        if cleaned not in (None, "", [], {}):
            content[key] = cleaned
        elif value in (None, "", [], {}):
            removed.append(path)
    return ProfiledOutput(
        content=content,
        source_coordinates=source_coordinates,
        envelope=envelope,
        removed_paths=tuple(dict.fromkeys(removed)),
    )


def _clean_value(value: Any, *, path: str, removed: list[str]) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            child_path = f"{path}/{key}"
            if key in _DROP_AT_ANY_DEPTH:
                removed.append(child_path)
                continue
            child = _clean_value(item, path=child_path, removed=removed)
            if child in (None, "", [], {}):
                removed.append(child_path)
                continue
            cleaned[str(key)] = child
        return cleaned
    if isinstance(value, (list, tuple)):
        cleaned_items = []
        for index, item in enumerate(value):
            child = _clean_value(item, path=f"{path}/{index}", removed=removed)
            if child not in (None, "", [], {}):
                cleaned_items.append(child)
        return cleaned_items
    return deepcopy(value)
