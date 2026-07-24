"""Shared model-boundary adapters for temporal values and JSON schemas."""

from __future__ import annotations

import copy
import json
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

_EASTERN = ZoneInfo("America/New_York")
_TEMPORAL_KEYS = {
    "published_at",
    "event_start",
    "event_end",
    "report_date",
    "start_date",
    "end_date",
    "as_of_date",
    "start",
    "end",
}
_ISO_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T\s].*)?$")
_NATURAL_FORMATS = (
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%d %b %Y",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y %I:%M %p",
    "%B %d, %Y %I:%M %p",
    "%b %d, %Y %I:%M %p",
)


def model_datetime(value: datetime) -> str:
    """Render a timestamp in the model's common local timeline without a zone label."""

    if value.tzinfo is not None and value.utcoffset() is not None:
        value = value.astimezone(_EASTERN).replace(tzinfo=None)
    return value.isoformat(timespec="seconds")


def _normalize_temporal_string(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return value
    iso_candidate = stripped.replace("Z", "+00:00")
    if _ISO_PREFIX.match(stripped):
        try:
            if "T" in iso_candidate or " " in iso_candidate:
                return model_datetime(datetime.fromisoformat(iso_candidate))
            return date.fromisoformat(iso_candidate).isoformat()
        except ValueError:
            pass
    for pattern in _NATURAL_FORMATS:
        try:
            parsed = datetime.strptime(stripped, pattern)
        except ValueError:
            continue
        if any(token in pattern for token in ("%H", "%I")):
            return parsed.isoformat(timespec="seconds")
        return parsed.date().isoformat()
    return value


def normalize_temporal_values(value: object, *, parent_key: str | None = None) -> object:
    """Normalize model-facing temporal leaves while leaving narrative text untouched."""

    if isinstance(value, datetime):
        return model_datetime(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {
            key: normalize_temporal_values(item, parent_key=str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [normalize_temporal_values(item, parent_key=parent_key) for item in value]
    if isinstance(value, str) and parent_key in _TEMPORAL_KEYS:
        return _normalize_temporal_string(value)
    return value


def normalize_json_prompt(value: str) -> str:
    """Normalize JSON request payloads; non-JSON repair prose is preserved verbatim."""

    try:
        payload = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value
    normalized = normalize_temporal_values(payload)
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def relax_temporal_schema(schema: object) -> object:
    """Prepare the model-facing schema without changing validation semantics.

    The in-process DTO retains presentation metadata because callers can use
    the root title for schema routing. Wire-only compaction is applied by the
    provider adapter. Descriptions, defaults, examples, required fields,
    enums, and all validation constraints remain.
    """

    value = copy.deepcopy(schema)

    def visit(item: object) -> None:
        if isinstance(item, dict):
            if item.get("format") in {"date", "date-time", "time"}:
                item.pop("format", None)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return value


def compact_wire_schema(schema: object) -> object:
    """Remove only generated titles from the provider-facing schema copy."""

    value = copy.deepcopy(schema)

    def visit(item: object) -> None:
        if isinstance(item, dict):
            item.pop("title", None)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return value
