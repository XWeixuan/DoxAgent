"""Gateway-facing model usage recorder."""

from __future__ import annotations

from datetime import UTC, datetime

from doxagent.gateway.schema import ModelRequest, ModelResponse
from doxagent.model_usage.repository import (
    ModelUsageRepository,
    model_usage_repository_from_settings,
)
from doxagent.model_usage.schema import JsonObject, ModelUsageEvent
from doxagent.settings import DoxAgentSettings


class ModelUsageRecorder:
    """Persist provider-neutral model usage events from ModelGateway responses."""

    def __init__(self, repository: ModelUsageRepository) -> None:
        self.repository = repository

    @classmethod
    def from_settings(
        cls,
        settings: DoxAgentSettings | None = None,
    ) -> ModelUsageRecorder:
        return cls(model_usage_repository_from_settings(settings))

    def record_response(
        self,
        request: ModelRequest,
        response: ModelResponse,
    ) -> ModelUsageEvent:
        event = model_usage_event_from_gateway(request, response)
        return self.repository.save_event(event)


def model_usage_event_from_gateway(
    request: ModelRequest,
    response: ModelResponse,
) -> ModelUsageEvent:
    usage = response.usage or response.audit.usage
    metadata = {**request.metadata, **response.audit.metadata}
    raw_usage: JsonObject = usage.model_dump(mode="json") if usage is not None else {}
    input_tokens = _nonnegative_int(raw_usage.get("input_tokens"))
    output_tokens = _nonnegative_int(raw_usage.get("output_tokens"))
    total_tokens = _nonnegative_int(raw_usage.get("total_tokens"))
    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens
    retry_count = max(0, int(response.audit.retry_count or 0))
    error = response.error
    status = "failed" if error is not None else "retried" if retry_count > 0 else "succeeded"
    return ModelUsageEvent(
        provider=response.audit.provider.value,
        model=response.audit.model or request.model,
        status=status,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        retry_count=retry_count,
        fallback_used=response.audit.fallback_used,
        latency_seconds=response.audit.latency_seconds,
        ticker=_metadata_value(metadata, "ticker"),
        run_id=_metadata_value(metadata, "run_id"),
        workflow_node=_metadata_value(metadata, "workflow_node"),
        runtime_node=_metadata_value(metadata, "runtime_node", "node"),
        agent_name=_metadata_value(metadata, "agent_name"),
        task_type=_metadata_value(metadata, "task_type"),
        source_message_id=_metadata_value(metadata, "source_message_id"),
        execution_id=_metadata_value(metadata, "execution_id", "runtime_execution_id"),
        error_code=error.code if error is not None else None,
        error_message=error.message if error is not None else None,
        metadata=metadata,
        raw_usage=raw_usage,
        created_at=datetime.now(UTC),
    )


def _metadata_value(metadata: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _nonnegative_int(value: object) -> int:
    if value is None or isinstance(value, bool):
        return 0
    try:
        if isinstance(value, int):
            parsed = value
        elif isinstance(value, float):
            parsed = int(value)
        elif isinstance(value, str):
            parsed = int(value)
        else:
            return 0
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)
