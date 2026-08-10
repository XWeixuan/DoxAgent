"""Runtime-independent deterministic segmentation for cleaned semantic outputs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

_RECORD_LIST_KEYS = {
    "awards",
    "documents",
    "events",
    "items",
    "opportunities",
    "records",
    "results",
    "updates",
}
_TABLE_LIST_KEYS = {"data", "filings", "observations", "ohlcv", "rows", "values"}
_DATE_KEYS = {"date", "datetime", "period", "time", "timestamp"}


@dataclass(frozen=True)
class SegmentedObservation:
    locator: str
    title: str
    block_type: str
    content: Any


def segment_cleaned_output(output: dict[str, Any]) -> list[SegmentedObservation]:
    blocks: list[SegmentedObservation] = []
    for key, value in output.items():
        locator = f"/{_pointer(key)}"
        if isinstance(value, list):
            blocks.extend(_segment_list(key, value, locator))
        elif isinstance(value, dict) and key == "series":
            for series_key, series_value in value.items():
                blocks.append(
                    SegmentedObservation(
                        locator=f"{locator}/{_pointer(str(series_key))}",
                        title=str(series_key),
                        block_type="time_series",
                        content=series_value,
                    )
                )
        elif isinstance(value, str) and len(value) > 4_000:
            blocks.extend(_segment_text(value, locator, key))
        else:
            blocks.append(
                SegmentedObservation(
                    locator=locator,
                    title=_content_title(value, fallback=key),
                    block_type="json" if not isinstance(value, str) else "text",
                    content=value,
                )
            )
    return blocks


def _segment_list(key: str, values: list[Any], locator: str) -> list[SegmentedObservation]:
    if not values:
        return []
    if key in _RECORD_LIST_KEYS and all(isinstance(item, dict) for item in values):
        return [
            SegmentedObservation(
                locator=f"{locator}/{index}",
                title=_content_title(item, fallback=f"{key} {index + 1}"),
                block_type="json",
                content=item,
            )
            for index, item in enumerate(values)
        ]
    if all(isinstance(item, dict) for item in values):
        block_type = "time_series" if _looks_time_series(values) else "table"
        chunk_size = 50 if key in _TABLE_LIST_KEYS else 25
        blocks = []
        for start in range(0, len(values), chunk_size):
            end = min(len(values), start + chunk_size)
            rows = values[start:end]
            columns = sorted({str(column) for row in rows for column in row})
            blocks.append(
                SegmentedObservation(
                    locator=f"{locator}/{start}-{end - 1}",
                    title=f"{key} {start + 1}-{end}",
                    block_type=block_type,
                    content={"columns": columns, "rows": rows},
                )
            )
        return blocks
    return [
        SegmentedObservation(
            locator=locator,
            title=key,
            block_type="json",
            content=values,
        )
    ]


def _segment_text(text: str, locator: str, title: str) -> list[SegmentedObservation]:
    paragraphs = [item.strip() for item in text.splitlines() if item.strip()]
    if len(paragraphs) <= 1:
        return [
            SegmentedObservation(locator=locator, title=title, block_type="text", content=text)
        ]
    blocks = []
    current: list[str] = []
    current_chars = 0
    for paragraph in paragraphs:
        if current and (len(current) >= 8 or current_chars + len(paragraph) > 6_000):
            blocks.append("\n\n".join(current))
            current = []
            current_chars = 0
        current.append(paragraph)
        current_chars += len(paragraph)
    if current:
        blocks.append("\n\n".join(current))
    return [
        SegmentedObservation(
            locator=f"{locator}/paragraphs/{index}",
            title=f"{title} paragraphs {index + 1}",
            block_type="text",
            content=content,
        )
        for index, content in enumerate(blocks)
    ]


def _looks_time_series(values: list[dict[str, Any]]) -> bool:
    return bool(values) and any(key in values[0] for key in _DATE_KEYS)


def _content_title(value: Any, *, fallback: str) -> str:
    if isinstance(value, dict):
        for key in ("title", "name", "document_number", "series_id", "award_id", "id"):
            candidate = value.get(key)
            if candidate not in (None, ""):
                return str(candidate)[:240]
    return fallback[:240]


def _pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def content_chars(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    return len(json.dumps(content, ensure_ascii=False, default=str))
