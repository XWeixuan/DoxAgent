"""Comparable wire-preserving sort anchors."""

import re
from datetime import UTC, datetime


def occurrence_anchor(value, precision):
    if not value or precision == "UNKNOWN":
        return ""
    text = str(value).strip()
    if precision == "INTERVAL":
        text = re.split(r"/|~|至|\.\.", text, maxsplit=1)[0].strip()
    quarter = re.fullmatch(r"(\d{4})-?Q([1-4])", text, re.I)
    if quarter:
        text = f"{quarter[1]}-{(int(quarter[2]) - 1) * 3 + 1:02d}-01"
    elif re.fullmatch(r"\d{4}", text):
        text += "-01-01"
    elif re.fullmatch(r"\d{4}-\d{2}", text):
        text += "-01"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC).isoformat()
    except ValueError:
        return ""


def message_anchor(message):
    return (
        f"{message['stream_published_at']}|{message['stream_offset']:020d}|"
        f"{message['member_index']:020d}"
    )
