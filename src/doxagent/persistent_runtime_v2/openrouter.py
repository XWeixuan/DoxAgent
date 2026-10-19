"""W2R1-only OpenRouter dispatch; all other and historical rounds stay on Bailian."""

from __future__ import annotations

import json
from hashlib import sha256
from time import perf_counter
from typing import Any

import httpx
from pydantic import ValidationError

from cdecr.model_boundary import bailian_strict_wire_schema

from .transport import (
    BailianRuntimeResponsesClient,
    RuntimeResponsesError,
    RuntimeResponsesRequest,
    RuntimeResponsesResult,
    T,
    _cache_optimized_input,
    _validation_error_summary,
    repair_known_json_string_quotes,
)

HAIKU_MODEL = "anthropic/claude-haiku-5.5"
HAIKU_ENDPOINT = "google-vertex/global"


def build_w2_r1_cached_request(request: RuntimeResponsesRequest[Any]) -> tuple[dict[str, Any], str]:
    """Preserve the tested business text, splitting only at the stable prefix."""
    if request.cache_context_keys != ("runtime_policy_projection",):
        raise ValueError("W2R1 explicit cache requires the complete Policy projection")
    schema = bailian_strict_wire_schema(request.output_model.model_json_schema())
    contract = (
        "\n\n# Exact Output Contract\n"
        "Return exactly one JSON object matching the schema below. Use every "
        "top-level key listed in properties, including empty arrays and default "
        "values. Never rename a key, substitute a legacy key, add a key, wrap the "
        "object, or emit Markdown.\n"
        + json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    system = request.instructions + contract
    full, _ = _cache_optimized_input(request.payload, request.cache_context_keys)
    stable, _ = _cache_optimized_input(
        {key: request.payload[key] for key in request.cache_context_keys},
        request.cache_context_keys,
    )
    tail = "\n\n# Current Case Input\n{}"
    prefix = stable.removesuffix(tail)
    fingerprint = sha256((system + "\0" + prefix).encode()).hexdigest()
    control = {"type": "ephemeral", "ttl": request.cache_ttl or "1h"}
    return {
        "model": request.model or HAIKU_MODEL,
        "provider": {
            "only": [request.provider_endpoint or HAIKU_ENDPOINT],
            "allow_fallbacks": False,
            "require_parameters": True,
        },
        "reasoning": {"effort": request.reasoning_effort or "high"},
        "messages": [
            {
                "role": "system",
                "content": [{"type": "text", "text": system, "cache_control": dict(control)}],
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prefix, "cache_control": dict(control)},
                    {"type": "text", "text": full[len(prefix) :]},
                ],
            },
        ],
        "session_id": "w2r1-" + fingerprint,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": request.schema_name, "strict": True, "schema": schema},
        },
        "max_tokens": 16384,
        "stream": True,
        "stream_options": {"include_usage": True},
    }, fingerprint


class W2R1OpenRouterClient:
    """Provider selection comes from frozen round settings, never current defaults."""

    def __init__(
        self,
        bailian: BailianRuntimeResponsesClient,
        *,
        api_key: str,
        w2_r1_model: str = HAIKU_MODEL,
        client: httpx.Client | None = None,
    ) -> None:
        if w2_r1_model == HAIKU_MODEL and not api_key:
            raise ValueError("OPENROUTER_API_KEY is required for W2R1 Haiku")
        self.bailian = bailian
        self.model = bailian.model
        self.reasoning_effort = bailian.reasoning_effort
        self.timeout_seconds = bailian.timeout_seconds
        self.w2_r1_model = w2_r1_model
        self._api_key = api_key
        self._client = client or httpx.Client()

    def close(self) -> None:
        self._client.close()

    def model_settings(self) -> dict[str, Any]:
        settings = self.bailian.model_settings()
        if self.w2_r1_model == HAIKU_MODEL:
            settings["rounds"]["W2:R1"].update(
                model=HAIKU_MODEL,
                reasoning_effort="high",
                provider="openrouter",
                provider_endpoint=HAIKU_ENDPOINT,
                cache_ttl="1h",
            )
        return settings

    def complete(self, request: RuntimeResponsesRequest[T]) -> RuntimeResponsesResult[T]:
        if request.provider != "openrouter":
            return self.bailian.complete(request)
        if (request.metadata.get("lane"), request.metadata.get("round")) != ("W2", "R1"):
            raise RuntimeResponsesError(
                "provider_route_invalid", "OpenRouter route is W2R1 only", retryable=False
            )
        if request.inherit_context or request.replay_parent:
            raise RuntimeResponsesError(
                "provider_route_invalid", "W2R1 requires explicit inputs", retryable=False
            )
        if not self._api_key:
            raise RuntimeResponsesError(
                "provider_auth_failed", "OPENROUTER_API_KEY is missing", retryable=False
            )
        body, fingerprint = build_w2_r1_cached_request(request)
        started = perf_counter()
        content: list[str] = []
        response_id = ""
        usage: dict[str, Any] = {}
        finish_reason = None
        observed_provider = None

        def receipt() -> dict[str, Any]:
            details = usage.get("prompt_tokens_details") or {}
            return dict(
                response_id=response_id or None,
                raw_output="".join(content),
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
                cached_input_tokens=details.get("cached_tokens"),
                cache_write_input_tokens=details.get("cache_write_tokens"),
                provider_usage={**usage, "response_provider": observed_provider},
            )

        try:
            with self._client.stream(
                "POST",
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": "Bearer " + self._api_key},
                json=body,
                timeout=request.timeout_seconds or self.bailian.w2_r1_timeout_seconds,
            ) as response:
                if response.status_code != 200:
                    status = response.status_code
                    response.read()
                    code = {
                        401: "provider_auth_failed",
                        403: "provider_auth_failed",
                        402: "provider_balance_unavailable",
                        429: "provider_rate_limited",
                        408: "provider_timeout",
                        504: "provider_timeout",
                    }.get(status, "provider_request_failed")
                    raise RuntimeResponsesError(
                        code,
                        f"OpenRouter returned HTTP {status}",
                        retryable=status in (408, 429) or status >= 500,
                    )
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    if not data:
                        continue
                    chunk = json.loads(data)
                    if chunk.get("error"):
                        raise RuntimeResponsesError(
                            "provider_stream_failed",
                            "OpenRouter stream returned an error",
                            retryable=True,
                        )
                    response_id = chunk.get("id") or response_id
                    observed_provider = chunk.get("provider") or observed_provider
                    usage = chunk.get("usage") or usage
                    for choice in chunk.get("choices", []):
                        finish_reason = choice.get("finish_reason") or finish_reason
                        text = (choice.get("delta") or {}).get("content")
                        if text:
                            content.append(text)
        except RuntimeResponsesError as exc:
            exc.usage.update(receipt())
            raise
        except httpx.TimeoutException as exc:
            raise RuntimeResponsesError(
                "provider_timeout", "OpenRouter request timed out", retryable=True, usage=receipt()
            ) from exc
        except httpx.TransportError as exc:
            raise RuntimeResponsesError(
                "provider_request_failed",
                "OpenRouter transport failed",
                retryable=True,
                usage=receipt(),
            ) from exc
        except (ValueError, TypeError) as exc:
            raise RuntimeResponsesError(
                "provider_stream_failed",
                "Invalid OpenRouter stream",
                retryable=True,
                usage=receipt(),
            ) from exc

        raw = "".join(content)
        if not response_id or not raw.strip() or finish_reason != "stop":
            raise RuntimeResponsesError(
                "incomplete_structured_output",
                "OpenRouter response incomplete",
                retryable=True,
                usage=receipt(),
            )
        normalized, warnings = repair_known_json_string_quotes(raw, request.schema_name)
        try:
            value = request.output_model.model_validate_json(normalized)
        except (ValidationError, ValueError) as exc:
            raise RuntimeResponsesError(
                "structured_output_validation_failed",
                _validation_error_summary(exc),
                retryable=True,
                usage=receipt(),
            ) from exc
        observed = receipt()
        return RuntimeResponsesResult(
            value=value,
            response_id=response_id,
            latency_ms=round((perf_counter() - started) * 1000),
            input_tokens=observed["input_tokens"],
            output_tokens=observed["output_tokens"],
            reasoning_tokens=(usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
            cached_input_tokens=observed["cached_input_tokens"],
            prefix_fingerprint=fingerprint,
            raw_output=raw,
            validation_warnings=tuple(warnings),
            cache_write_input_tokens=observed["cache_write_input_tokens"],
            provider_usage=observed["provider_usage"],
        )
