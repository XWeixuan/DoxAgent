"""Turn-scoped native usage receipts; never subtract thread totals."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

KEYS = ("input_tokens", "cached_input_tokens", "output_tokens",
        "reasoning_output_tokens", "total_tokens")


def rollout_path(home: Path, thread_id: str) -> Path | None:
    state = home / "state_5.sqlite"
    if not state.is_file():
        return None
    with closing(sqlite3.connect(state.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
        row = db.execute("SELECT rollout_path FROM threads WHERE id=?", (thread_id,)).fetchone()
    return Path(row[0]) if row and row[0] else None


def _reverse_lines(path: Path) -> Iterator[bytes]:
    # Stop at this turn's start even on a long-lived thread.
    with path.open("rb") as stream:
        position = stream.seek(0, 2)
        tail = b""
        while position:
            size = min(position, 65536)
            position -= size
            stream.seek(position)
            chunks = (stream.read(size) + tail).split(b"\n")
            tail = chunks[0]
            yield from reversed(chunks[1:])
        if tail:
            yield tail


def _usage(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    if any(type(value.get(k)) is not int or value[k] < 0 for k in KEYS):
        return None
    if (value["cached_input_tokens"] > value["input_tokens"]
            or value["reasoning_output_tokens"] > value["output_tokens"]
            or value["total_tokens"] != value["input_tokens"] + value["output_tokens"]):
        return None
    return {k: value[k] for k in KEYS}


def read_turn_usage(path: Path, thread_id: str, turn_id: str) -> tuple[dict[str, int] | None, bool]:
    """Deduplicate response IDs and cross-check the native turn accumulator.

    Missing or conflicting evidence is partial, never an invented zero.
    Cached input and reasoning output are subsets, not extra token charges.
    """
    seen: dict[str, dict[str, int]] = {}
    expected = None
    started = False
    valid = True
    for line in _reverse_lines(path):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (ValueError, UnicodeError):
            valid = False
            continue
        if not isinstance(record, dict):
            valid = False
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        if (record.get("type") == "event_msg" and payload.get("type") == "task_started"
                and payload.get("turn_id") == turn_id):
            started = True
            break
        if (record.get("type") != "token_usage_record"
                or payload.get("thread_id") != thread_id or payload.get("turn_id") != turn_id):
            continue
        usage = _usage(payload.get("usage"))
        identity = payload.get("response_id")
        if usage is None or not isinstance(identity, str) or not identity:
            valid = False
            continue
        if not seen:
            expected = _usage(payload.get("turn_token_usage"))
        if identity in seen and seen[identity] != usage:
            valid = False
        seen.setdefault(identity, usage)
    if not seen:
        return None, False
    total = {key: sum(value[key] for value in seen.values()) for key in KEYS}
    return total, started and valid and expected == total
