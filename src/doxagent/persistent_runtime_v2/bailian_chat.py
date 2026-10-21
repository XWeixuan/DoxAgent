"""Bailian GLM JSON-object protocol; schema remains strict at our boundary."""

from __future__ import annotations

from time import perf_counter

from .openrouter import _recall_system
from .transport import (
    RuntimeResponsesError,
    RuntimeResponsesResult,
    _cache_optimized_input,
    _validation_error_summary,
)


def chat_body(request):
    text, fingerprint = _cache_optimized_input(request.payload, request.cache_context_keys)
    messages = [{"role": "system", "content": _recall_system(request)}]
    if request.replay_parent:
        messages.extend(
            (
                {"role": "user", "content": request.replay_parent[0]},
                {"role": "assistant", "content": request.replay_parent[1]},
            )
        )
    elif request.inherit_context or request.previous_response_id:
        raise RuntimeResponsesError(
            "parent_context_missing",
            "GLM requires explicit frozen parent messages",
            retryable=False,
        )
    messages.append({"role": "user", "content": text})
    if request.reasoning_effort not in {"low", "high", "max"}:
        raise RuntimeResponsesError(
            "provider_route_invalid", "GLM-5.3 requires low/high/max effort", retryable=False
        )
    # GLM-5.3 always thinks. Its native effort is explicit in the frozen profile.
    return {
        "model": request.model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": 16384,
        "extra_body": {"enable_thinking": True},
        "reasoning_effort": request.reasoning_effort,
    }, fingerprint


def complete(client, request):
    body, fingerprint = chat_body(request)
    started, observed, parts, response_id, finished = perf_counter(), {}, [], "", False

    def receipt():
        details = observed.get("prompt_tokens_details") or {}
        return {
            "input_tokens": observed.get("prompt_tokens"),
            "output_tokens": observed.get("completion_tokens"),
            "cached_input_tokens": details.get("cached_tokens"),
            "provider_usage": observed,
            "response_id": response_id or None,
        }

    try:
        stream = client.chat.completions.create(**body, timeout=request.timeout_seconds or 120)
        with stream:
            for chunk in stream:
                response_id = chunk.id or response_id
                if chunk.usage:
                    observed.update(chunk.usage.model_dump(exclude_none=True))
                for choice in chunk.choices:
                    if choice.delta.content:
                        parts.append(choice.delta.content)
                    if choice.finish_reason:
                        finished = choice.finish_reason == "stop"
    except Exception as exc:
        raise RuntimeResponsesError(
            "provider_request_failed",
            "Bailian GLM stream did not complete",
            retryable=True,
            usage=receipt(),
        ) from exc
    text = "".join(parts)
    known = receipt()
    if not finished or not response_id:
        raise RuntimeResponsesError(
            "provider_request_failed",
            "Bailian GLM returned an incomplete result",
            retryable=True,
            usage=known,
        )
    try:
        value = request.output_model.model_validate_json(text)
    except ValueError as exc:
        raise RuntimeResponsesError(
            "structured_output_validation_failed",
            _validation_error_summary(exc),
            retryable=True,
            usage={**known, "raw_output": text},
        ) from exc
    return RuntimeResponsesResult(
        value=value,
        response_id=response_id,
        latency_ms=round((perf_counter() - started) * 1000),
        input_tokens=known["input_tokens"],
        output_tokens=known["output_tokens"],
        cached_input_tokens=known["cached_input_tokens"],
        reasoning_tokens=(observed.get("completion_tokens_details") or {}).get("reasoning_tokens"),
        provider_usage=observed,
        raw_output=text,
        prefix_fingerprint=fingerprint,
    )
