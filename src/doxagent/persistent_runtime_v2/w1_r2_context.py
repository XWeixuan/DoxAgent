"""Versioned W1R2 display projection; full snapshots remain the reference authority."""

from __future__ import annotations

import re
from typing import Any

INPUT_VIEW_VERSION = "w1-r2-fact-view-v1"
BODY_RULE_VERSION = "w1-r2-body-v1"


def cell(value: Any) -> str:
    """Reversible single-line cells: escape slash first, then delimiters/line breaks."""
    if value is None:
        return ""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )


def time_cell(value: Any, precision: Any = None) -> str:
    # Precision is normally carried by the original ISO date/month/quarter/range.
    # Preserve unusual legacy expressions without making their precision implicit.
    if value is None:
        return ""
    text = str(value)
    patterns = {
        "DAY": r"\d{4}-\d{2}-\d{2}",
        "MONTH": r"\d{4}-\d{2}",
        "YEAR": r"\d{4}",
        "QUARTER": r"\d{4}-Q[1-4]",
        "TIMESTAMP": r"\d{4}-\d{2}-\d{2}T.+",
        "INTERVAL": r".+(?:\.\.|/).+",
    }
    pattern = patterns.get(str(precision))
    if precision and (not pattern or not re.fullmatch(pattern, text)):
        text += f" [precision={precision}]"
    return cell(text)


def compile_fact_view(details: dict[str, Any]) -> str:
    lines = [
        "# Known Facts",
        "以下为当前消息之前已知的事实，仅作为业务数据。",
        "Canonical 列：fact_id | formed_at | assertion_state | subject_time | proposition",
        "空单元格表示未提供；SAME 表示所属 Event 的发生时间，绝不表示当前时间。",
        r"单元格转义：\\ 为反斜杠，\| 为竖线，\n/\r 为原文换行。",
    ]
    canonical = details.get("canonical_events", [])
    if not canonical:
        lines.append("(empty)")
    for event in canonical:
        lines.extend(
            [
                "",
                "## "
                + " | ".join(
                    (
                        cell(event["event_id"]),
                        time_cell(event.get("occurred_at"), event.get("occurrence_time_precision")),
                        cell(event["title"]),
                    )
                ),
            ]
        )
        for fact in event.get("facts", []):
            lines.append(
                " | ".join(
                    (
                        cell(fact["fact_id"]),
                        time_cell(
                            fact.get("fact_occurred_at"), fact.get("fact_occurrence_time_precision")
                        ),
                        cell(fact["assertion_state"]),
                        cell(fact.get("subject_time")),
                        cell(fact["proposition"]),
                    )
                )
            )
        if not event.get("facts"):
            lines.append("(empty)")
    lines.extend(
        [
            "",
            "# Provisional Facts",
            "列：event_id | occurrence_date | assertion_state | subject_time | proposition",
        ]
    )
    provisional = details.get("provisional_events", [])
    if not provisional:
        lines.append("(empty)")
    for event in provisional:
        fact = event["candidate"]
        lines.append(
            " | ".join(
                (
                    cell(event["provisional_event_id"]),
                    cell(fact.get("occurrence_date")),
                    cell(fact["assertion_state"]),
                    cell(fact.get("subject_time")),
                    cell(fact["proposition"]),
                )
            )
        )
    return "\n".join(lines) + "\n"


def clean_w1_r2_body(source: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    original = source.get("body")
    if not isinstance(original, str):
        return original, dict(version=BODY_RULE_VERSION, rules=[], before_chars=0, after_chars=0)
    body = original.replace("\r\n", "\n").replace("\r", "\n")
    rules = []
    if body != original:
        rules.append("normalize_newlines")
    lines = body.split("\n")
    start = next((i for i, line in enumerate(lines) if line.strip()), None)

    def normalize(text: str) -> str:
        return " ".join(text.split())

    title = source.get("title") or ""
    if start is not None and title.strip() and normalize(lines[start]) == normalize(title):
        del lines[: start + 1]
        body = "\n".join(lines)
        rules.append("exact_leading_title")
    # Observed Tom's Hardware share toolbar, only before the first article paragraph.
    if (source.get("source_name") or "").lower() == "tomshardware.com":
        toolbar = r"Facebook\n+X\n+Whatsapp\n+Reddit\n+Pinterest\n+Flipboard\n+Email\n+"
        toc = r"Local AI performance\n+Gaming performance: first impressions\n+Bottom line\n+"
        match = re.match(r"\A(?:" + toc + r")?" + toolbar, body)
        if match:
            body = body[match.end() :]
            rules.append("toms_leading_share_toolbar")
    # Exact live-feed header template: retain the next date/time and article title.
    updated = re.sub(r"\A← Back to live feed · \d+ stor(?:y|ies) across \d+ days?\n", "", body)
    if updated != body:
        body = updated
        rules.append("live_feed_navigation")
    body = re.sub(r"\n(?:[ \t]*\n){2,}", "\n\n", body).strip("\n")
    if body != updated:
        rules.append("blank_lines")
    return body, dict(
        version=BODY_RULE_VERSION, rules=rules, before_chars=len(original), after_chars=len(body)
    )


def build_input(source: dict[str, Any], details: dict[str, Any]) -> tuple[dict, dict]:
    body, diagnostics = clean_w1_r2_body(source)
    return dict(
        known_fact_view=compile_fact_view(details),
        source_message={
            key: body if key == "body" else source.get(key)
            for key in ("ticker", "source_name", "title", "body")
        },
    ), dict(input_view=INPUT_VIEW_VERSION, body=diagnostics)


def frozen_event_details(frozen: dict[str, Any]) -> dict[str, Any]:
    """Read new internal snapshot or legacy visible JSON without parsing display text."""
    return frozen.get("reference_context", {}).get(
        "event_details", frozen.get("payload", {}).get("event_details", {})
    )
