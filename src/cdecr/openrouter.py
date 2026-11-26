"""CDECR OpenRouter adapter with an ordered, restricted provider fallback."""

from __future__ import annotations

import copy
import json
from time import perf_counter

from openai import AsyncOpenAI, OpenAI

from cdecr.config import CDECRSettings
from cdecr.model_boundary import compact_wire_schema
from cdecr.models import (
    ModelAdapterError,
    ModelTier,
    _cached_input_usage_value,
    _reasoning_usage_value,
    _safe_model_error,
    _structured_result_from_text,
    _usage_value,
)
from cdecr.ports import ResponsesModelRequest, StructuredModelRequest

MODEL = "deepseek/deepseek-v4.1-flash-1017"
ENDPOINT = "sail-research/fp4"
FALLBACK_ENDPOINT = "ambient/fp4"


class OpenRouterStructuredModelClient:
    def __init__(self, *, settings: CDECRSettings, tier: ModelTier, package: bool = False):
        if settings.openrouter_api_key is None:
            raise ValueError("OPENROUTER_API_KEY is required for CDECR OpenRouter")
        self.tier = tier
        self.model = (
            settings.package_v3_model if package else getattr(settings, "model_" + tier.value)
        )
        self.reasoning_effort = (
            settings.package_v3_reasoning_effort
            if package
            else getattr(settings, "model_" + tier.value + "_reasoning_effort")
        )
        self._client = OpenAI(
            api_key=settings.openrouter_api_key.get_secret_value(),
            base_url=settings.openrouter_base_url,
            timeout=settings.model_timeout_seconds,
            max_retries=0,
        )
        self._async_client = AsyncOpenAI(
            api_key=settings.openrouter_api_key.get_secret_value(),
            base_url=settings.openrouter_base_url,
            timeout=settings.model_timeout_seconds,
            max_retries=0,
        )

    def _kwargs(self, request):
        if request.previous_response_id:
            raise ModelAdapterError(tier=self.tier, code="openrouter_previous_response_unsupported")
        messages = (
            copy.deepcopy(request.input)
            if isinstance(request, ResponsesModelRequest)
            else [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ]
        )
        schema = compact_wire_schema(request.json_schema)
        messages.append(
            {
                "role": "user",
                "content": "Return exactly one JSON object matching this schema, without Markdown: "
                + json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
            }
        )
        effort = (
            request.reasoning_effort
            if isinstance(request, ResponsesModelRequest)
            else self.reasoning_effort
        )
        return dict(
            model=self.model,
            messages=messages,
            response_format={"type": "json_object"},
            extra_body={
                "provider": {
                    "order": [ENDPOINT, FALLBACK_ENDPOINT],
                    "only": [ENDPOINT, FALLBACK_ENDPOINT],
                    "allow_fallbacks": True,
                    "require_parameters": True,
                },
                "reasoning": {"effort": effort},
            },
        )

    def _result(self, response, request, started):
        usage = response.usage
        result = _structured_result_from_text(
            tier=self.tier,
            model=self.model,
            text=response.choices[0].message.content,
            input_tokens=_usage_value(usage, "prompt_tokens"),
            output_tokens=_usage_value(usage, "completion_tokens"),
            reasoning_tokens=_reasoning_usage_value(usage),
            request_id=response.id,
            started_at=started,
        )
        return result.model_copy(
            update={
                "cached_input_tokens": _cached_input_usage_value(usage),
                "transport": "chat_json_object",
                "output_mode": "json_object",
                "effective_reasoning_effort": request.reasoning_effort
                if isinstance(request, ResponsesModelRequest)
                else self.reasoning_effort,
            }
        )

    def complete(self, request: StructuredModelRequest):
        return self._complete(request)

    def complete_response(self, request: ResponsesModelRequest):
        return self._complete(request)

    def _complete(self, request):
        from cdecr.usage_capture import call

        started = perf_counter()
        kwargs = self._kwargs(request)
        try:
            response = call(
                self._client.chat.completions.create,
                _usage_provider="openrouter",
                _usage_node=request.schema_name,
                **kwargs,
            )
        except Exception as exc:
            raise _safe_model_error(exc, self.tier, started_at=started) from exc
        return self._result(response, request, started)

    async def acomplete(self, request: StructuredModelRequest):
        from cdecr.usage_capture import acall

        started = perf_counter()
        kwargs = self._kwargs(request)
        try:
            response = await acall(
                self._async_client.chat.completions.create,
                _usage_provider="openrouter",
                _usage_node=request.schema_name,
                **kwargs,
            )
        except Exception as exc:
            raise _safe_model_error(exc, self.tier, started_at=started) from exc
        return self._result(response, request, started)
