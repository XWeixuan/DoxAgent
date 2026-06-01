"""Centralized LangSmith provider wrapping."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from doxagent.gateway.schema import ProviderName

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

        return wrap_openai(client, tracing_extra=tracing_extra)
    if provider is ProviderName.ANTHROPIC:
        from langsmith.wrappers import wrap_anthropic

        return wrap_anthropic(client, tracing_extra=tracing_extra)
    return client
