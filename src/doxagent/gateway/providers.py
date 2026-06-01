"""Provider SDK adapters for the Model Gateway."""

from collections.abc import Mapping
from time import perf_counter
from typing import Any

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from doxagent.gateway.schema import (
    GatewayError,
    MessageRole,
    ModelAuditSummary,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ProviderName,
    ResponseFormat,
)
from doxagent.gateway.tracing import langsmith_tracing_context


def _usage_from_mapping(value: Mapping[str, Any] | None) -> ModelUsage | None:
    if value is None:
        return None
    input_tokens = value.get("input_tokens") or value.get("prompt_tokens")
    output_tokens = value.get("output_tokens") or value.get("completion_tokens")
    total_tokens = value.get("total_tokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = int(input_tokens) + int(output_tokens)
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _model_dump_or_raw(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    return value


def _normalize_exception(provider: ProviderName, exc: Exception) -> GatewayError:
    return GatewayError(
        code=exc.__class__.__name__,
        message=str(exc) or "Provider call failed.",
        retryable=True,
        provider=provider,
    )


def _output_items(raw: Any) -> list[Any]:
    if isinstance(raw, dict):
        output = raw.get("output")
        return output if isinstance(output, list) else []
    return []


def _mapping_get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _extract_responses_message_text(response: Any, raw: Any) -> str | None:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text:
        return output_text
    chunks: list[str] = []
    for item in _output_items(raw):
        if _mapping_get(item, "type") != "message":
            continue
        content = _mapping_get(item, "content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            text = _mapping_get(block, "text")
            if isinstance(text, str):
                chunks.append(text)
    return "".join(chunks) if chunks else None


def _extract_reasoning_summary(raw: Any) -> list[str]:
    summaries: list[str] = []
    for item in _output_items(raw):
        if _mapping_get(item, "type") != "reasoning":
            continue
        summary = _mapping_get(item, "summary", [])
        if not isinstance(summary, list):
            continue
        for block in summary:
            text = _mapping_get(block, "text")
            if isinstance(text, str) and text:
                summaries.append(text)
    return summaries


def _raw_with_reasoning_summary(raw: Any) -> Any:
    summaries = _extract_reasoning_summary(raw)
    if not summaries:
        return raw
    if isinstance(raw, dict):
        enriched = dict(raw)
        enriched["reasoning_summary"] = summaries
        return enriched
    return {"raw": raw, "reasoning_summary": summaries}


class OpenAIModelClient:
    def __init__(self, client: AsyncOpenAI | Any) -> None:
        self.client = client

    async def complete(self, request: ModelRequest) -> ModelResponse:
        started_at = perf_counter()
        try:
            with langsmith_tracing_context(request.metadata):
                response = await self.client.responses.create(**self._request_kwargs(request))
            raw = _model_dump_or_raw(response)
            usage = _usage_from_mapping(raw.get("usage") if isinstance(raw, dict) else None)
            audit = ModelAuditSummary(
                provider=ProviderName.OPENAI,
                model=request.model,
                latency_seconds=perf_counter() - started_at,
                metadata=request.metadata,
                usage=usage,
            )
            return ModelResponse(
                text=getattr(response, "output_text", None),
                raw=raw,
                usage=usage,
                audit=audit,
            )
        except Exception as exc:
            audit = ModelAuditSummary(
                provider=ProviderName.OPENAI,
                model=request.model,
                latency_seconds=perf_counter() - started_at,
                metadata=request.metadata,
            )
            return ModelResponse(audit=audit, error=_normalize_exception(ProviderName.OPENAI, exc))

    def _request_kwargs(self, request: ModelRequest) -> dict[str, Any]:
        input_messages = [
            {"role": message.role.value, "content": message.content}
            for message in self._messages_with_system(request)
        ]
        kwargs: dict[str, Any] = {
            "model": request.model,
            "input": input_messages,
        }
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_tokens is not None:
            kwargs["max_output_tokens"] = request.max_tokens
        if request.timeout_seconds is not None:
            kwargs["timeout"] = request.timeout_seconds
        if request.response_format is ResponseFormat.JSON:
            kwargs["text"] = {"format": {"type": "json_object"}}
        return kwargs

    def _messages_with_system(self, request: ModelRequest) -> list[ModelMessage]:
        if request.system_prompt is None:
            return request.messages
        return [
            ModelMessage(role=MessageRole.SYSTEM, content=request.system_prompt),
            *request.messages,
        ]


class BailianResponsesModelClient(OpenAIModelClient):
    """DashScope Bailian Responses API adapter using OpenAI-compatible SDK calls."""

    def __init__(self, client: AsyncOpenAI | Any, *, enable_thinking: bool = True) -> None:
        super().__init__(client)
        self.enable_thinking = enable_thinking

    async def complete(self, request: ModelRequest) -> ModelResponse:
        started_at = perf_counter()
        try:
            with langsmith_tracing_context(request.metadata):
                response = await self.client.responses.create(**self._request_kwargs(request))
            raw = _model_dump_or_raw(response)
            raw = _raw_with_reasoning_summary(raw)
            usage = _usage_from_mapping(raw.get("usage") if isinstance(raw, dict) else None)
            audit = ModelAuditSummary(
                provider=ProviderName.BAILIAN,
                model=request.model,
                latency_seconds=perf_counter() - started_at,
                metadata=request.metadata,
                usage=usage,
            )
            return ModelResponse(
                text=_extract_responses_message_text(response, raw),
                raw=raw,
                usage=usage,
                audit=audit,
            )
        except Exception as exc:
            audit = ModelAuditSummary(
                provider=ProviderName.BAILIAN,
                model=request.model,
                latency_seconds=perf_counter() - started_at,
                metadata=request.metadata,
            )
            return ModelResponse(audit=audit, error=_normalize_exception(ProviderName.BAILIAN, exc))

    def _request_kwargs(self, request: ModelRequest) -> dict[str, Any]:
        kwargs = super()._request_kwargs(request)
        kwargs["extra_body"] = {"enable_thinking": self.enable_thinking}
        return kwargs


class AnthropicModelClient:
    def __init__(self, client: AsyncAnthropic | Any) -> None:
        self.client = client

    async def complete(self, request: ModelRequest) -> ModelResponse:
        started_at = perf_counter()
        try:
            with langsmith_tracing_context(request.metadata):
                response = await self.client.messages.create(**self._request_kwargs(request))
            raw = _model_dump_or_raw(response)
            usage = _usage_from_mapping(raw.get("usage") if isinstance(raw, dict) else None)
            audit = ModelAuditSummary(
                provider=ProviderName.ANTHROPIC,
                model=request.model,
                latency_seconds=perf_counter() - started_at,
                metadata=request.metadata,
                usage=usage,
            )
            return ModelResponse(
                text=self._extract_text(raw),
                raw=raw,
                usage=usage,
                audit=audit,
            )
        except Exception as exc:
            audit = ModelAuditSummary(
                provider=ProviderName.ANTHROPIC,
                model=request.model,
                latency_seconds=perf_counter() - started_at,
                metadata=request.metadata,
            )
            return ModelResponse(
                audit=audit,
                error=_normalize_exception(ProviderName.ANTHROPIC, exc),
            )

    def _request_kwargs(self, request: ModelRequest) -> dict[str, Any]:
        messages = [
            {"role": message.role.value, "content": message.content}
            for message in request.messages
            if message.role is not MessageRole.SYSTEM
        ]
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens or 1024,
        }
        if request.system_prompt is not None:
            kwargs["system"] = request.system_prompt
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.timeout_seconds is not None:
            kwargs["timeout"] = request.timeout_seconds
        return kwargs

    def _extract_text(self, raw: Any) -> str:
        if not isinstance(raw, dict):
            return ""
        blocks = raw.get("content") or []
        text_blocks = [
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(text_blocks)
