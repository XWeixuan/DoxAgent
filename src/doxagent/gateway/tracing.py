"""Centralized LangSmith provider wrapping."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from doxagent.gateway.schema import ProviderName

LANGSMITH_WRAPPED_ATTR = "_doxagent_langsmith_wrapped"
_WRAPPED_CLIENT_IDS: set[int] = set()

TRACE_METADATA_KEYS = (
    "ticker",
    "agent_name",
    "run_id",
    "task_type",
    "workflow_node",
    "skill_versions",
    "prompt_versions",
    "react_step",
    "react_compaction",
    "runtime",
    "provider",
    "model",
)


def tracing_extra_from_metadata(metadata: dict[str, str]) -> dict[str, dict[str, str]]:
    return {"metadata": {key: metadata[key] for key in TRACE_METADATA_KEYS if key in metadata}}


def run_name_from_metadata(metadata: dict[str, str]) -> str:
    agent_name = _run_name_part(metadata.get("agent_name"), fallback="agent")
    workflow_node = _run_name_part(
        metadata.get("workflow_node") or metadata.get("task_type"),
        fallback="task",
    )
    loop_index = _loop_index(metadata.get("react_step"))
    return f"{agent_name}.{workflow_node}.LOOP{loop_index}"


def langsmith_extra_from_metadata(metadata: dict[str, str]) -> dict[str, str]:
    return {"name": run_name_from_metadata(metadata)}


def mark_langsmith_wrapped(client: Any) -> Any:
    _WRAPPED_CLIENT_IDS.add(id(client))
    try:
        setattr(client, LANGSMITH_WRAPPED_ATTR, True)
    except Exception:
        pass
    return client


def is_langsmith_wrapped(client: Any) -> bool:
    return bool(getattr(client, LANGSMITH_WRAPPED_ATTR, False)) or id(client) in _WRAPPED_CLIENT_IDS


@contextmanager
def langsmith_tracing_context(metadata: dict[str, str]) -> Iterator[None]:
    """Attach DoxAgent task metadata to LangSmith wrapper-created child runs."""

    try:
        from langsmith.run_helpers import tracing_context
    except Exception:
        yield
        return

    with tracing_context(metadata=tracing_extra_from_metadata(metadata)["metadata"]):
        yield


def wrap_provider_client(
    provider: ProviderName,
    client: Any,
    *,
    tracing_enabled: bool,
    tracing_extra: Any | None = None,
) -> Any:
    if not tracing_enabled:
        return client

    if provider in {ProviderName.OPENAI, ProviderName.BAILIAN}:
        from langsmith.wrappers import wrap_openai

        return mark_langsmith_wrapped(wrap_openai(client, tracing_extra=tracing_extra))
    if provider is ProviderName.ANTHROPIC:
        from langsmith.wrappers import wrap_anthropic

        return mark_langsmith_wrapped(wrap_anthropic(client, tracing_extra=tracing_extra))
    return client


def _run_name_part(value: str | None, *, fallback: str) -> str:
    raw = (value or fallback).strip() or fallback
    return "".join(
        character if character.isalnum() or character in {"_", "-"} else "_"
        for character in raw
    )


def _loop_index(value: str | None) -> int:
    try:
        parsed = int(value or "1")
    except ValueError:
        return 1
    return parsed if parsed > 0 else 1
