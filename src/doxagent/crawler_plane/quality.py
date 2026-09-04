"""Advisory observation-body diagnostics for crawler executions."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from statistics import median
from typing import Protocol


class _Observation(Protocol):
    body: str


def quality_summary(observations: Sequence[_Observation]) -> dict[str, object]:
    bodies = [item.body.strip() for item in observations]
    lengths = [len(item) for item in bodies]
    if not bodies:
        return {
            "observation_count": 0,
            "body_length_min": 0,
            "body_length_median": 0,
            "body_length_max": 0,
            "duplicate_body_ratio": 0.0,
            "common_text_ratio": 0.0,
            "top_repeated_lines": [],
        }
    normalized = [" ".join(item.split()).casefold() for item in bodies]
    duplicates = sum(count - 1 for count in Counter(normalized).values() if count > 1)
    line_counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    for body in bodies:
        lines = {" ".join(line.split()) for line in body.splitlines() if line.strip()}
        for line in lines:
            key = line.casefold()
            line_counts[key] += 1
            display.setdefault(key, line)
    threshold = max(2, math.ceil(len(bodies) * 0.7))
    repeated = [key for key, count in line_counts.items() if count >= threshold]
    repeated.sort(key=lambda key: (-line_counts[key], -len(display[key]), key))
    common_chars = sum(len(display[key]) for key in repeated)
    median_length = float(median(lengths))
    return {
        "observation_count": len(bodies),
        "body_length_min": min(lengths),
        "body_length_median": median_length,
        "body_length_max": max(lengths),
        "duplicate_body_ratio": round(duplicates / len(bodies), 4),
        "common_text_ratio": round(min(1.0, common_chars / max(1.0, median_length)), 4),
        "top_repeated_lines": [display[key][:500] for key in repeated[:5]],
    }


def quality_warnings(summary: dict[str, object]) -> list[str]:
    duplicate_value = summary.get("duplicate_body_ratio", 0.0)
    common_value = summary.get("common_text_ratio", 0.0)
    duplicate_ratio = float(duplicate_value) if isinstance(duplicate_value, (int, float)) else 0.0
    common_ratio = float(common_value) if isinstance(common_value, (int, float)) else 0.0
    if duplicate_ratio >= 0.5 or common_ratio >= 0.5:
        return ["possible_template_noise"]
    return []


__all__ = ["quality_summary", "quality_warnings"]
