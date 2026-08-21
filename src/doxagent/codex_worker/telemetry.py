"""Bounded, reasoning-free telemetry projection for Codex SDK turns."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from doxagent.codex_worker.schema import (
    WorkerLoopEvent,
    WorkerTokenUsage,
    WorkerTurnTelemetry,
)

_KEPT_TYPES = {
    "mcpToolCall",
    "commandExecution",
    "fileChange",
    "dynamicToolCall",
    "collabAgentToolCall",
    "subAgentActivity",
    "webSearch",
}


def project_turn_telemetry(
    *,
    items: Iterable[Any],
    usage: Any | None,
    duration_ms: int | None,
) -> WorkerTurnTelemetry:
    events: list[WorkerLoopEvent] = []
    for item in items:
        value = getattr(item, "root", item)
        item_type = str(getattr(value, "type", type(value).__name__))
        if item_type not in _KEPT_TYPES:
            continue
        events.append(_project_item(len(events), item_type, value))
    failures = [
        f"{item.item_type}:{item.name}:{item.status}"
        for item in events
        if item.status.lower() not in {"completed", "succeeded", "success", "done"}
    ]
    slowest = sorted(
        (item for item in events if item.duration_ms is not None),
        key=lambda item: item.duration_ms or 0,
        reverse=True,
    )[:5]
    return WorkerTurnTelemetry(
        usage=_project_usage(usage),
        sdk_duration_ms=duration_ms,
        mcp_call_count=sum(item.item_type == "mcp_tool_call" for item in events),
        command_call_count=sum(item.item_type == "command" for item in events),
        subagent_call_count=sum(item.item_type == "subagent" for item in events),
        file_change_count=sum(item.item_type == "file_change" for item in events),
        slowest_steps=slowest,
        failures=failures,
        events=events,
    )


def _project_usage(usage: Any | None) -> WorkerTokenUsage:
    value = getattr(usage, "last", None)
    if value is None:
        return WorkerTokenUsage()
    return WorkerTokenUsage(
        input_tokens=_nonnegative(getattr(value, "input_tokens", 0)),
        cached_input_tokens=_nonnegative(getattr(value, "cached_input_tokens", 0)),
        output_tokens=_nonnegative(getattr(value, "output_tokens", 0)),
        reasoning_output_tokens=_nonnegative(getattr(value, "reasoning_output_tokens", 0)),
        total_tokens=_nonnegative(getattr(value, "total_tokens", 0)),
    )


def _project_item(sequence: int, item_type: str, value: Any) -> WorkerLoopEvent:
    status = _enum_value(getattr(value, "status", "completed"))
    duration = getattr(value, "duration_ms", None)
    if item_type == "mcpToolCall":
        server = str(getattr(value, "server", "mcp"))
        tool = str(getattr(value, "tool", "unknown"))
        error = getattr(value, "error", None)
        return WorkerLoopEvent(
            sequence=sequence,
            item_type="mcp_tool_call",
            name=f"{server}.{tool}",
            status=status,
            duration_ms=_optional_nonnegative(duration),
            summary=_bounded(str(error) if error is not None else "completed"),
        )
    if item_type == "commandExecution":
        command = str(getattr(value, "command", ""))
        exit_code = getattr(value, "exit_code", None)
        return WorkerLoopEvent(
            sequence=sequence,
            item_type="command",
            name="shell",
            status=status,
            duration_ms=_optional_nonnegative(duration),
            summary=_bounded(f"{command}; exit={exit_code}"),
        )
    if item_type == "fileChange":
        changes = getattr(value, "changes", [])
        return WorkerLoopEvent(
            sequence=sequence,
            item_type="file_change",
            name="apply_patch",
            status=status,
            summary=f"{len(changes)} file change(s)",
        )
    if item_type in {"collabAgentToolCall", "subAgentActivity"}:
        name = _enum_value(getattr(value, "tool", getattr(value, "kind", "subagent")))
        return WorkerLoopEvent(
            sequence=sequence,
            item_type="subagent",
            name=name,
            status=status,
            summary="subagent activity",
        )
    if item_type == "dynamicToolCall":
        return WorkerLoopEvent(
            sequence=sequence,
            item_type="dynamic_tool_call",
            name=str(getattr(value, "tool", "unknown")),
            status=status,
            duration_ms=_optional_nonnegative(duration),
            summary="completed",
        )
    return WorkerLoopEvent(
        sequence=sequence,
        item_type="web_search",
        name="web_search",
        status=status,
        summary=_bounded(str(getattr(value, "query", ""))),
    )


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _bounded(value: str, limit: int = 240) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def _nonnegative(value: object) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0, int(value))
    return 0


def _optional_nonnegative(value: object) -> int | None:
    if value is None:
        return None
    return _nonnegative(value)
